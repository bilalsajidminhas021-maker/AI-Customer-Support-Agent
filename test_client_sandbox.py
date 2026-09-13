"""Focused validation for the fictional NovaTech Store client sandbox."""

import json
import unittest
from pathlib import Path

from action_safety import CREATE_SUPPORT_CASE, NOT_ALLOWED, REQUIRES_HUMAN, evaluate_action
from decision import (
    BUSINESS_TOOL,
    ESCALATE,
    SAFE_FALLBACK,
    decide_action,
    detect_deterministic_route,
)
from integrations import ProviderResult
from security import redact_secrets
from tools import classify_support_case, lookup_order, validate_tool_result


SANDBOX = Path(__file__).parent / "client_sandbox"
REQUIRED_SCENARIO_FIELDS = {
    "scenario_id", "customer_message", "intent", "expected_route",
    "expected_tool_or_rag_behavior", "expected_escalation_behavior",
    "expected_safety_behavior", "expected_outcome",
}


class FakeProvider:
    provider_type = "sandbox_fake"

    def __init__(self, result):
        self.result = result

    def get_order(self, order_id):
        return self.result


def load_json(name):
    with (SANDBOX / name).open(encoding="utf-8") as file:
        return json.load(file)


class ClientSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load_json("test_scenarios.json")["scenarios"]
        cls.expected = load_json("expected_results.json")["expectations"]

    def test_sandbox_data_and_expectations_are_valid(self):
        self.assertEqual(len(self.scenarios), 20)
        scenario_ids = {scenario["scenario_id"] for scenario in self.scenarios}
        self.assertEqual(len(scenario_ids), 20)
        self.assertEqual(scenario_ids, set(self.expected))
        for scenario in self.scenarios:
            self.assertTrue(REQUIRED_SCENARIO_FIELDS.issubset(scenario))
            self.assertIn(scenario["expected_route"], {"RAG", BUSINESS_TOOL, ESCALATE, SAFE_FALLBACK})
            self.assertEqual(self.expected[scenario["scenario_id"]]["action"], scenario["expected_route"])

    def test_deterministic_routing_matches_sandbox_expectations(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario["scenario_id"]):
                route = detect_deterministic_route(scenario["customer_message"])
                decision = decide_action(
                    scenario["intent"], scenario["customer_message"], routed_action=route,
                )
                expected = self.expected[scenario["scenario_id"]]
                self.assertEqual(decision["action"], expected["action"])
                if "tool" in expected:
                    self.assertEqual(decision["tool"], expected["tool"])
                if "identifier" in expected:
                    self.assertEqual(decision["identifier"], expected["identifier"])

    def test_escalations_and_restricted_actions_remain_human_controlled(self):
        for scenario_id in ("refund_request", "large_refund", "billing_dispute", "security_issue", "explicit_human_request", "complaint"):
            scenario = next(item for item in self.scenarios if item["scenario_id"] == scenario_id)
            expected = self.expected[scenario_id]
            fields = classify_support_case(scenario["customer_message"])
            self.assertEqual(evaluate_action(CREATE_SUPPORT_CASE)["eligibility"], REQUIRES_HUMAN)
            self.assertEqual(fields["category"], expected["case_category"])
            if "case_priority" in expected:
                self.assertEqual(fields["priority"], expected["case_priority"])
        self.assertEqual(evaluate_action("REFUND")["eligibility"], NOT_ALLOWED)
        self.assertEqual(evaluate_action("COMPENSATION")["eligibility"], NOT_ALLOWED)

    def test_provider_failure_contract_is_safe_and_normalized(self):
        failures = {
            "not_found": "No order was found.",
            "timeout": "The business integration timed out.",
            "authentication_failure": "The business integration could not authenticate.",
            "http_error": "The business integration returned an error.",
            "malformed": "The business system returned malformed order data.",
            "unavailable": "The business integration is currently unavailable.",
        }
        for status, message in failures.items():
            with self.subTest(status=status):
                result = lookup_order("ORD-1001", FakeProvider(ProviderResult(status=status, message=message, provider_type="sandbox_fake")))
                validation = validate_tool_result("order_lookup", result)
                self.assertFalse(result["success"])
                self.assertEqual(result["integration_status"], status)
                self.assertTrue(validation["valid"])
                self.assertNotIn("API_KEY", json.dumps(result))

    def test_successful_provider_result_is_validated(self):
        provider = FakeProvider(ProviderResult(
            status="success", provider_type="sandbox_fake",
            data={"status": "Shipped", "tracking_number": "TRK-SANDBOX-1", "expected_delivery": "September 10, 2026"},
        ))
        result = lookup_order("ORD-1001", provider)
        validation = validate_tool_result("order_lookup", result)
        self.assertTrue(result["success"])
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["status"], "success")

    def test_prompt_injection_scenario_stays_secret_safe(self):
        scenario = next(item for item in self.scenarios if item["scenario_id"] == "prompt_injection")
        decision = decide_action(scenario["intent"], scenario["customer_message"])
        self.assertEqual(decision["action"], SAFE_FALLBACK)
        self.assertEqual(evaluate_action("REFUND")["eligibility"], NOT_ALLOWED)
        self.assertNotIn("demo-secret", redact_secrets("API_KEY=demo-secret"))


if __name__ == "__main__":
    unittest.main()
