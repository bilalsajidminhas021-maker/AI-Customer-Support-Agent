import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import audit
import tools
from action_safety import (
    ACCOUNT_LOOKUP,
    ALLOWED,
    BILLING_LOOKUP,
    CREATE_SUPPORT_CASE,
    INSUFFICIENT_EVIDENCE,
    NOT_ALLOWED,
    ORDER_LOOKUP,
    POLICY_LOOKUP,
    REQUIRES_HUMAN,
    evaluate_action,
)
from case_storage import JsonCaseStorage
from decision import BUSINESS_TOOL, CLARIFY, decide_action, detect_deterministic_route
from tools import classify_support_case, create_support_case_once


class Day11ActionSafetyTests(unittest.TestCase):
    def test_order_lookup_with_valid_identifier_is_allowed(self):
        result = evaluate_action(ORDER_LOOKUP, identifier="ORD-1001")

        self.assertEqual(result["eligibility"], ALLOWED)

    def test_order_lookup_without_identifier_is_blocked(self):
        result = evaluate_action(ORDER_LOOKUP)

        self.assertEqual(result["eligibility"], INSUFFICIENT_EVIDENCE)
        self.assertTrue(result["required_information"])

    def test_account_and_billing_lookup_require_valid_account_identifier(self):
        for action in (ACCOUNT_LOOKUP, BILLING_LOOKUP):
            with self.subTest(action=action):
                self.assertEqual(
                    evaluate_action(action, identifier="ACC-1001")["eligibility"],
                    ALLOWED,
                )
                self.assertEqual(
                    evaluate_action(action, identifier="ORD-1001")["eligibility"],
                    INSUFFICIENT_EVIDENCE,
                )

    def test_policy_lookup_is_read_only_and_does_not_authorize_outcome(self):
        result = evaluate_action(POLICY_LOOKUP)

        self.assertEqual(result["eligibility"], ALLOWED)
        self.assertEqual(result["evidence_status"], "UNVERIFIED")
        self.assertNotIn("entitlement", result)
        self.assertNotIn("approved", result)

    def test_create_support_case_requires_human(self):
        self.assertEqual(
            evaluate_action(CREATE_SUPPORT_CASE)["eligibility"],
            REQUIRES_HUMAN,
        )

    def test_mutation_and_unknown_actions_are_not_allowed(self):
        for action in (
            "REFUND",
            "CANCEL_ORDER",
            "MODIFY_ACCOUNT",
            "UNKNOWN_LLM_ACTION",
        ):
            with self.subTest(action=action):
                self.assertEqual(
                    evaluate_action(action)["eligibility"],
                    NOT_ALLOWED,
                )

    def test_policy_dependent_evidence_without_approval_is_insufficient(self):
        result = evaluate_action(
            POLICY_LOOKUP,
            policy_dependent=True,
        )

        self.assertEqual(result["eligibility"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(result["evidence_status"], "UNVERIFIED")

        result = evaluate_action(
            POLICY_LOOKUP,
            evidence={
                "valid": False,
                "sources": [],
                "evidence_items": [],
            },
        )

        self.assertEqual(result["eligibility"], INSUFFICIENT_EVIDENCE)
        self.assertEqual(result["evidence_status"], "UNVERIFIED")

    def test_verified_approved_policy_evidence_is_accepted(self):
        result = evaluate_action(
            POLICY_LOOKUP,
            evidence={
                "valid": True,
                "sources": ["customer_policy__refunds.pdf"],
                "evidence_items": [{
                    "source": "customer_policy__refunds.pdf",
                    "customer_policy": True,
                    "relevance_score": 0.91,
                }],
            },
        )

        self.assertEqual(result["eligibility"], ALLOWED)
        self.assertEqual(result["evidence_status"], "VERIFIED")
        self.assertNotIn("entitlement", result)
        self.assertNotIn("refund_approved", result)

    def test_employee_handbook_cannot_be_verified(self):
        result = evaluate_action(
            POLICY_LOOKUP,
            evidence={
                "valid": True,
                "sources": ["employee_handbook.pdf"],
                "evidence_items": [{
                    "source": "employee_handbook.pdf",
                    "customer_policy": False,
                    "relevance_score": 0.99,
                }],
            },
        )

        self.assertEqual(result["eligibility"], INSUFFICIENT_EVIDENCE)

    def test_existing_refund_case_is_created_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = JsonCaseStorage(Path(directory) / "support_cases.json")
            with patch.object(tools, "CASE_STORAGE", storage):
                question = "I was charged twice and I want a refund"
                fields = classify_support_case(question)
                first = create_support_case_once({}, question, **fields)
                second = create_support_case_once({}, question, **fields)

        self.assertTrue(first["success"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["case_id"], second["case_id"])
        self.assertEqual(first["evidence"]["status"], "UNVERIFIED")

    def test_invoice_request_remains_non_escalated_and_clarifies(self):
        question = "Can you check my billing?"

        self.assertIsNone(detect_deterministic_route(question))
        decision = decide_action("billing", question)
        self.assertEqual(decision["action"], CLARIFY)

    def test_order_decision_maps_to_read_only_action(self):
        decision = decide_action("order_tracking", "check order ORD-1001")

        self.assertEqual(decision["action"], BUSINESS_TOOL)
        self.assertEqual(decision["tool"], "order_lookup")
        self.assertEqual(
            evaluate_action(ORDER_LOOKUP, decision["identifier"])["eligibility"],
            ALLOWED,
        )

    def test_audit_failure_is_non_fatal(self):
        with patch("audit.open", side_effect=OSError("audit unavailable")):
            self.assertFalse(audit.log_audit_event(
                safe_action=ORDER_LOOKUP,
                eligibility=ALLOWED,
                safety_reason="read-only",
            ))

    def test_safety_result_does_not_execute_any_operation(self):
        result = evaluate_action(ORDER_LOOKUP, identifier="ORD-1001")

        self.assertEqual(set(result), {
            "action",
            "eligibility",
            "reason",
            "required_information",
            "recommended_action",
            "evidence_status",
        })


if __name__ == "__main__":
    unittest.main()
