import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit
import config
import tools
from action_safety import (
    INSUFFICIENT_EVIDENCE,
    NOT_ALLOWED,
    POLICY_LOOKUP,
    evaluate_action,
)
from admin import admin_login, admin_logout
from admin import admin_access_allowed, admin_login, admin_logout
from case_storage import JsonCaseStorage
from security import (
    UNTRUSTED_CONTENT_INSTRUCTION,
    authenticate_admin,
    hash_password,
    redact_secrets,
    validate_customer_input,
)
from tenant_context import TenantContext
from tools import classify_support_case, create_support_case_once


class Day12SecurityTests(unittest.TestCase):
    def test_admin_authentication_and_logout(self):
        password_hash = hash_password("correct horse")
        self.assertTrue(authenticate_admin("admin", "correct horse", "admin", password_hash))
        self.assertFalse(authenticate_admin("admin", "wrong", "admin", password_hash))

        session_state = {}
        self.assertFalse(admin_access_allowed(session_state))
        with patch("admin.ADMIN_PASSWORD_HASH", password_hash):
            self.assertTrue(admin_login(session_state, "admin", "correct horse"))
            self.assertTrue(session_state["admin_authenticated"])
            admin_logout(session_state)
        self.assertFalse(session_state["admin_authenticated"])
        self.assertFalse(admin_access_allowed(session_state))
        self.assertNotIn("admin_password", session_state)

    def test_tenant_context_is_configuration_controlled(self):
        context = TenantContext(
            "tenant-a", "Business A", "a@example.com", "weekdays",
            "knowledge", "data.json", "cases.json", "audit.jsonl",
        ).validate()
        self.assertEqual(context.tenant_id, "tenant-a")
        with self.assertRaises(ValueError):
            TenantContext("../other", "A", "", "", "k", "d", "c", "a").validate()

    def test_tenant_case_isolation_and_duplicate_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            storage_a = JsonCaseStorage(path, tenant_id="tenant-a")
            storage_b = JsonCaseStorage(path, tenant_id="tenant-b")
            question = "I was charged twice and want a refund"
            fields = classify_support_case(question)
            first = create_support_case_once({}, question, **fields, storage=storage_a)
            second = create_support_case_once({}, question, **fields, storage=storage_b)
            duplicate = create_support_case_once({}, question, **fields, storage=storage_a)

            self.assertNotEqual(first["case_id"], second["case_id"])
            self.assertEqual(first["tenant_id"], "tenant-a")
            self.assertEqual(second["tenant_id"], "tenant-b")
            self.assertTrue(duplicate["duplicate"])
            self.assertIsNone(storage_b.get(first["case_id"]))
            self.assertEqual(len(storage_a.list_cases()), 1)
            self.assertEqual(len(storage_b.list_cases()), 1)

    def test_secret_redaction_and_audit_output(self):
        secret_text = "GOOGLE_API_KEY=google-secret Bearer token-secret"
        redacted = redact_secrets({"message": secret_text, "BUSINESS_API_KEY": "business-secret"})
        self.assertNotIn("google-secret", json.dumps(redacted))
        self.assertNotIn("business-secret", json.dumps(redacted))
        self.assertNotIn("token-secret", json.dumps(redacted))

        with tempfile.TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            with patch.object(audit, "AUDIT_LOG_FILE", str(audit_path)):
                self.assertTrue(audit.log_audit_event(message=secret_text))
            output = audit_path.read_text(encoding="utf-8")
            self.assertNotIn("google-secret", output)
            self.assertNotIn("token-secret", output)

    def test_retrieved_injection_is_untrusted_and_cannot_authorize_refund(self):
        self.assertIn("untrusted", UNTRUSTED_CONTENT_INSTRUCTION.lower())
        self.assertIn("authorization", UNTRUSTED_CONTENT_INSTRUCTION.lower())
        self.assertEqual(evaluate_action("REFUND")["eligibility"], NOT_ALLOWED)
        self.assertEqual(
            evaluate_action(POLICY_LOOKUP, policy_dependent=True)["eligibility"],
            INSUFFICIENT_EVIDENCE,
        )

    def test_input_and_configuration_fail_closed(self):
        value, error = validate_customer_input("x" * 4001, max_length=4000)
        self.assertIsNone(value)
        self.assertTrue(error)
        with self.assertRaises(config.ConfigurationError):
            config._bounded_int("TEST_LIMIT", "invalid", 1, 10)
        with patch.object(config, "BUSINESS_PROVIDER", "unsupported"):
            self.assertTrue(config.validate_configuration())

    def test_existing_action_safety_unknown_action_remains_blocked(self):
        self.assertEqual(evaluate_action("UNKNOWN")["eligibility"], NOT_ALLOWED)


if __name__ == "__main__":
    unittest.main()