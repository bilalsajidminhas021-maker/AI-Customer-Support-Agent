import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import audit
import config
import operational
from action_safety import INSUFFICIENT_EVIDENCE, NOT_ALLOWED, evaluate_action
from case_storage import JsonCaseStorage
from evaluate_agent import run_evaluation
from integrations import RestOrderProvider
from knowledge import retrieve_knowledge
from operational import health_status, production_readiness
from security import UNTRUSTED_CONTENT_INSTRUCTION, validate_customer_input
from tenant_context import TenantContext
from tools import classify_support_case, create_support_case_once


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class EmptyVectorStore:
    def similarity_search_with_relevance_scores(self, query, k):
        return []


class Day13EvaluationTests(unittest.TestCase):
    def test_audit_supports_non_sensitive_timing_fields(self):
        trace = audit.ExecutionTrace("check order ORD-1001")
        started_at = __import__("time").perf_counter()
        duration_ms = trace.record_duration("decision", started_at)
        self.assertIsInstance(duration_ms, float)
        self.assertIn("decision", trace.event["timings_ms"])

    def test_controlled_evaluation_dataset_passes(self):
        report = run_evaluation()
        self.assertEqual(report["total"], 14)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["pass_rate"], 1.0)
        self.assertEqual(report["disclaimer"], "This is a controlled evaluation suite, not a statistically representative production benchmark.")

    @patch("integrations.urlopen", side_effect=TimeoutError)
    def test_rest_timeout_is_safe(self, mocked_urlopen):
        result = RestOrderProvider("https://business.example", "secret", 1).get_order("ORD-1001")
        self.assertEqual(result.status, "timeout")
        mocked_urlopen.assert_called_once()

    @patch("integrations.urlopen")
    def test_rest_authentication_and_http_failures_are_safe(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError("https://business.example", 401, "unauthorized", {}, None)
        self.assertEqual(
            RestOrderProvider("https://business.example", "secret", 1).get_order("ORD-1001").status,
            "authentication_failure",
        )
        mocked_urlopen.side_effect = HTTPError("https://business.example", 500, "server", {}, None)
        self.assertEqual(
            RestOrderProvider("https://business.example", "secret", 1).get_order("ORD-1001").status,
            "http_error",
        )

    @patch("integrations.urlopen", return_value=FakeResponse(b"not-json"))
    def test_rest_malformed_response_is_safe(self, mocked_urlopen):
        result = RestOrderProvider("https://business.example", "secret", 1).get_order("ORD-1001")
        self.assertEqual(result.status, "malformed")
        self.assertNotIn("secret", str(result))

    def test_missing_provider_configuration_is_safe(self):
        result = RestOrderProvider("", None, 0).get_order("ORD-1001")
        self.assertEqual(result.status, "configuration_error")

    def test_empty_and_low_relevance_knowledge_fail_closed(self):
        empty = retrieve_knowledge(EmptyVectorStore(), "return policy")
        self.assertFalse(empty["valid"])
        self.assertEqual(empty["status"], "insufficient_evidence")

        class LowScoreStore:
            def similarity_search_with_relevance_scores(self, query, k):
                return [(type("Doc", (), {"page_content": "text", "metadata": {"source": "customer_policy__returns.pdf", "customer_policy": True, "category": "returns", "page": 1, "chunk_index": 1}})(), 0.01)]

        low = retrieve_knowledge(LowScoreStore(), "return policy", policy_only=True)
        self.assertFalse(low["valid"])
        self.assertEqual(evaluate_action("POLICY_LOOKUP", policy_dependent=True)["eligibility"], INSUFFICIENT_EVIDENCE)

    def test_case_storage_failure_and_audit_failure_are_non_fatal(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonCaseStorage(Path(directory) / "cases.json", tenant_id="tenant-a")
            fields = classify_support_case("I need a human representative")
            with patch.object(storage, "create", return_value=None):
                result = create_support_case_once({}, "I need a human representative", **fields, storage=storage)
            self.assertFalse(result["success"])

        with patch.object(audit, "open", side_effect=OSError("audit unavailable")):
            self.assertFalse(audit.log_audit_event(action="ORDER_LOOKUP"))

    def test_invalid_action_identifier_and_input_are_safe(self):
        self.assertEqual(evaluate_action("DELETE_ACCOUNT")["eligibility"], NOT_ALLOWED)
        self.assertEqual(evaluate_action("ORDER_LOOKUP", identifier="ACC-1001")["eligibility"], INSUFFICIENT_EVIDENCE)
        value, error = validate_customer_input("x" * 4001, 4000)
        self.assertIsNone(value)
        self.assertTrue(error)

    def test_prompt_injection_does_not_gain_authority(self):
        self.assertIn("untrusted", UNTRUSTED_CONTENT_INSTRUCTION.lower())
        self.assertEqual(evaluate_action("REFUND")["eligibility"], NOT_ALLOWED)

    def test_tenant_isolation_failure_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            tenant_a = JsonCaseStorage(path, tenant_id="tenant-a")
            tenant_b = JsonCaseStorage(path, tenant_id="tenant-b")
            fields = classify_support_case("I need a human representative")
            created = create_support_case_once({}, "I need a human representative", **fields, storage=tenant_a)
            self.assertIsNone(tenant_b.get(created["case_id"]))
            self.assertFalse(create_support_case_once({}, "I need a human representative", **fields, storage=tenant_b)["duplicate"])

    def test_readiness_and_health_never_expose_secrets(self):
        context = TenantContext(
            "day13", "Demo", "support@example.com", "weekdays",
            "missing-knowledge", "missing-data.json", "cases.json", "audit.jsonl",
        ).validate()
        with patch.object(operational, "GOOGLE_API_KEY", None), patch.object(operational, "ADMIN_PASSWORD_HASH", ""):
            readiness = production_readiness(context)
            health = health_status(context)
        serialized = json.dumps({"readiness": readiness, "health": health})
        self.assertIn("API key is missing", serialized)
        self.assertNotIn("GOOGLE_API_KEY=", serialized)
        self.assertEqual(health["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
