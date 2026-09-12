import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit
import tools
from case_storage import JsonCaseStorage
from tools import (
    CASE_RECOMMENDED_ACTIONS,
    CASE_EVIDENCE_STATUSES,
    classify_case_evidence,
    classify_support_case,
    create_support_case_once,
    get_support_case,
    recommend_case_action,
    update_support_case,
)


class Day10CaseManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.storage = JsonCaseStorage(
            Path(self.temp_directory.name) / "support_cases.json"
        )
        self.audit_directory = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.audit_directory.name) / "audit.jsonl"
        self.storage_patch = patch.object(tools, "CASE_STORAGE", self.storage)
        self.audit_patch = patch.object(audit, "AUDIT_LOG_FILE", str(self.audit_path))
        self.storage_patch.start()
        self.audit_patch.start()

    def tearDown(self):
        self.audit_patch.stop()
        self.storage_patch.stop()
        self.audit_directory.cleanup()
        self.temp_directory.cleanup()

    def create_case(self, question="I was charged twice and I want a refund"):
        return create_support_case_once(
            {},
            question,
            **classify_support_case(question),
        )

    def test_new_case_has_canonical_and_legacy_statuses(self):
        result = self.create_case()

        self.assertEqual(result["lifecycle_status"], "OPEN")
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["evidence"]["status"], "UNVERIFIED")
        self.assertEqual(result["recommended_action"], "BILLING_REVIEW")
        self.assertIn(result["recommended_action"], CASE_RECOMMENDED_ACTIONS)

    def test_case_persists_across_storage_objects(self):
        result = self.create_case()
        reloaded = JsonCaseStorage(self.storage.path).get(result["case_id"])

        self.assertEqual(reloaded["case_id"], result["case_id"])
        self.assertEqual(reloaded["lifecycle_status"], "OPEN")

    def test_duplicate_protection_persists_across_sessions(self):
        first = self.create_case()
        second = self.create_case()

        self.assertTrue(second["duplicate"])
        self.assertEqual(second["case_id"], first["case_id"])

    def test_lifecycle_transitions_are_controlled_and_persisted(self):
        result = self.create_case()
        case_id = result["case_id"]
        statuses = (
            "IN_REVIEW",
            "WAITING_FOR_CUSTOMER",
            "IN_REVIEW",
            "RESOLVED",
            "CLOSED",
        )

        for status in statuses:
            result = update_support_case(case_id, status, "review update")
            self.assertTrue(result["success"])
            self.assertEqual(result["lifecycle_status"], status)

        self.assertEqual(result["status"], "pending_handoff")
        self.assertEqual(len(result["status_history"]), 6)
        self.assertFalse(
            update_support_case(case_id, "IN_REVIEW", "reopen closed case")["success"]
        )

        persisted = get_support_case(case_id)
        self.assertEqual(persisted["lifecycle_status"], "CLOSED")
        self.assertEqual(len(persisted["status_history"]), 6)

    def test_invalid_transition_is_rejected(self):
        result = self.create_case()
        update = update_support_case(
            result["case_id"],
            "RESOLVED",
            "skip review",
        )

        self.assertFalse(update["success"])
        self.assertEqual(update["status"], "invalid_transition")

    def test_case_update_changes_timestamp_and_history(self):
        result = self.create_case()
        original_updated_at = result["updated_at"]
        update = update_support_case(
            result["case_id"],
            "IN_REVIEW",
            "assigned to reviewer",
        )

        self.assertNotEqual(update["updated_at"], original_updated_at)
        self.assertEqual(update["status_history"][-1]["reason"], "assigned to reviewer")
        self.assertEqual(
            self.storage.get(result["case_id"])["lifecycle_status"],
            "IN_REVIEW",
        )

    def test_recommended_actions_are_controlled(self):
        expectations = (
            ("BILLING", "UNVERIFIED", None, "BILLING_REVIEW"),
            ("SECURITY", "UNVERIFIED", None, "ACCOUNT_REVIEW"),
            ("ORDER", "UNVERIFIED", None, "ORDER_REVIEW"),
            ("COMPLAINT", "UNVERIFIED", None, "HUMAN_REVIEW"),
            ("GENERAL", "UNVERIFIED", ["account ID"], "CUSTOMER_CLARIFICATION"),
            ("GENERAL", "VERIFIED", None, "POLICY_REVIEW"),
        )
        for category, evidence_status, required, expected in expectations:
            with self.subTest(category=category):
                self.assertEqual(
                    recommend_case_action(category, evidence_status, required),
                    expected,
                )

    def test_evidence_requires_approved_policy_metadata(self):
        self.assertEqual(
            classify_case_evidence()["status"],
            "UNVERIFIED",
        )
        self.assertEqual(
            classify_case_evidence({
                "valid": True,
                "sources": ["employee_handbook.pdf"],
                "evidence_items": [{
                    "source": "employee_handbook.pdf",
                    "customer_policy": False,
                    "relevance_score": 0.99,
                }],
            })["status"],
            "UNVERIFIED",
        )
        verified = classify_case_evidence({
            "valid": True,
            "sources": ["customer_policy__refunds.pdf"],
            "evidence_items": [{
                "source": "customer_policy__refunds.pdf",
                "customer_policy": True,
                "relevance_score": 0.91,
            }],
        })
        self.assertEqual(verified["status"], "VERIFIED")
        self.assertIn(verified["status"], CASE_EVIDENCE_STATUSES)

    def test_summary_does_not_claim_refund_entitlement(self):
        result = self.create_case()

        self.assertEqual(result["summary"]["issue"], "duplicate_charge")
        self.assertEqual(result["summary"]["requested_outcome"], "refund")
        self.assertNotIn("entitled", json.dumps(result).lower())
        self.assertNotIn("approved", json.dumps(result).lower())

    def test_malformed_json_and_missing_cases_fail_safely(self):
        self.storage.path.write_text("not-json", encoding="utf-8")

        self.assertIsNone(self.storage.get("CASE-2026-MISSING"))
        self.assertEqual(
            get_support_case("CASE-2026-MISSING")["status"],
            "not_found",
        )

    def test_failed_creation_is_not_persisted(self):
        result = create_support_case_once(
            {},
            "I need support",
            category="NOT_ALLOWED",
            priority="NORMAL",
            reason="invalid classification",
        )

        self.assertFalse(result["success"])
        self.assertEqual(self.storage.path.exists(), False)

    def test_audit_failure_does_not_break_case_creation(self):
        with patch.object(audit, "AUDIT_LOG_FILE", str(self.audit_directory.name)):
            result = self.create_case("I need a human representative")

        self.assertTrue(result["success"])
        self.assertIsNotNone(self.storage.get(result["case_id"]))

    def test_missing_case_update_is_controlled(self):
        result = update_support_case(
            "CASE-2026-MISSING",
            "IN_REVIEW",
            "not found",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "not_found")


if __name__ == "__main__":
    unittest.main()
