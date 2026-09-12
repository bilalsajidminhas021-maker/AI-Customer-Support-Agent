import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import tools
from decision import (
    BUSINESS_TOOL,
    CLARIFY,
    ESCALATE,
    decide_action,
    detect_deterministic_intent,
    detect_deterministic_route,
)
from tools import (
    SUPPORT_CASE_CATEGORIES,
    SUPPORT_CASE_PRIORITIES,
    SUPPORT_CASE_STATUSES,
    classify_support_case,
    create_support_case,
    create_support_case_once,
    validate_support_case_result,
)


class Day9CaseManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.storage_patch = patch.object(
            tools,
            "CASE_STORAGE",
            tools.JsonCaseStorage(
                Path(self.temp_directory.name) / "support_cases.json"
            ),
        )
        self.storage_patch.start()

    def tearDown(self):
        self.storage_patch.stop()
        self.temp_directory.cleanup()

    def test_billing_refund_escalation_routes_before_lookup(self):
        self.assertEqual(
            detect_deterministic_route(
                "i was charged twice and i want a refund"
            ),
            "escalate",
        )

    def test_high_confidence_escalation_phrases_route_to_escalate(self):
        for question in (
            "I want a refund",
            "I was charged twice",
            "I want to dispute this charge",
            "I want to speak to a human",
        ):
            with self.subTest(question=question):
                self.assertEqual(
                    detect_deterministic_route(question),
                    "escalate",
                )

    def test_informational_billing_questions_remain_lookup_requests(self):
        for question in (
            "Where can I find my invoice?",
            "Can you check my billing?",
        ):
            with self.subTest(question=question):
                self.assertIsNone(detect_deterministic_route(question))

        self.assertEqual(
            detect_deterministic_intent("Where can I find my invoice?"),
            "billing",
        )
        self.assertIsNone(
            detect_deterministic_intent("Can you check my billing?")
        )

        decision = decide_action(
            "billing",
            "Can you check my billing?",
        )
        self.assertEqual(decision["action"], CLARIFY)

    def test_order_lookup_behavior_is_unchanged(self):
        question = "check order ORD-1001"

        self.assertIsNone(detect_deterministic_route(question))
        self.assertEqual(
            decide_action("order_tracking", question)["action"],
            BUSINESS_TOOL,
        )

    def test_billing_escalation_creates_open_high_priority_case(self):
        question = "i was charged twice and i want a refund"
        fields = classify_support_case(question)
        result = create_support_case(question, **fields)

        self.assertEqual(result["category"], "BILLING")
        self.assertEqual(result["priority"], "HIGH")
        self.assertEqual(result["status"], "open")

    def test_successful_case_creation_contains_required_fields(self):
        fields = classify_support_case(
            "I was charged twice and want a refund.",
            intent="billing",
        )
        result = create_support_case(
            "I was charged twice and want a refund.",
            **fields,
            conversation_context="USER: I was charged twice.",
        )

        self.assertTrue(result["success"])
        self.assertTrue(validate_support_case_result(result)["valid"])
        for field in (
            "case_id",
            "status",
            "priority",
            "category",
            "reason",
            "created_at",
        ):
            self.assertIn(field, result)

    def test_case_id_format(self):
        fields = classify_support_case("I need a human representative")
        result = create_support_case("I need a human representative", **fields)

        self.assertRegex(result["case_id"], r"^CASE-\d{4}-[A-F0-9]{8}$")

    def test_case_fields_use_controlled_values(self):
        fields = classify_support_case("I need a human representative")
        result = create_support_case("I need a human representative", **fields)

        self.assertIn(result["status"], SUPPORT_CASE_STATUSES)
        self.assertIn(result["priority"], SUPPORT_CASE_PRIORITIES)
        self.assertIn(result["category"], SUPPORT_CASE_CATEGORIES)

    def test_billing_escalation_maps_to_high_billing(self):
        result = classify_support_case(
            "I dispute this charge and want a refund.",
            intent="billing",
        )

        self.assertEqual(result["category"], "BILLING")
        self.assertEqual(result["priority"], "HIGH")

    def test_security_escalation_maps_to_high_security(self):
        result = classify_support_case(
            "Someone accessed my account and it may be compromised."
        )

        self.assertEqual(result["category"], "SECURITY")
        self.assertEqual(result["priority"], "HIGH")

    def test_complaint_escalation_maps_to_complaint(self):
        result = classify_support_case("I want to file a complaint.")

        self.assertEqual(result["category"], "COMPLAINT")
        self.assertEqual(result["priority"], "NORMAL")

    def test_explicit_human_request_maps_to_general(self):
        result = classify_support_case("I need a human representative.")

        self.assertEqual(result["category"], "GENERAL")
        self.assertEqual(result["priority"], "NORMAL")

    def test_malformed_case_result_is_rejected(self):
        validation = validate_support_case_result({"success": True})

        self.assertFalse(validation["valid"])
        self.assertEqual(validation["status"], "malformed")

    def test_missing_case_id_is_rejected(self):
        fields = classify_support_case("I need human support")
        result = create_support_case("I need human support", **fields)
        result.pop("case_id")

        self.assertFalse(validate_support_case_result(result)["valid"])

    def test_invalid_status_is_rejected(self):
        fields = classify_support_case("I need human support")
        result = create_support_case("I need human support", **fields)
        result["status"] = "not_a_status"

        validation = validate_support_case_result(result)
        self.assertFalse(validation["valid"])
        self.assertEqual(validation["status"], "invalid")

    def test_escalation_disabled_remains_safe(self):
        decision = decide_action(
            "human_escalation",
            "I need a human representative.",
            escalation_enabled=False,
        )

        self.assertEqual(decision["action"], "SAFE_FALLBACK")
        self.assertNotEqual(decision["action"], ESCALATE)

    def test_case_creation_failure_is_safe(self):
        result = create_support_case(
            "I need human support",
            category="NOT_ALLOWED",
            priority="NORMAL",
            reason="Invalid classification",
        )

        self.assertFalse(result["success"])
        self.assertFalse(validate_support_case_result(result)["valid"])
        self.assertIn("could not", result["message"].lower())

    def test_duplicate_protection_reuses_successful_case(self):
        session_state = {}
        fields = classify_support_case("I need a human representative")

        first = create_support_case_once(
            session_state,
            "I need a human representative",
            **fields,
        )
        second = create_support_case_once(
            session_state,
            "I need a human representative",
            **fields,
        )

        self.assertTrue(first["success"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["success"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["case_id"], second["case_id"])

    def test_new_request_creates_new_case(self):
        session_state = {}
        first_fields = classify_support_case("I need a human representative")
        second_fields = classify_support_case("I want to file a complaint")

        first = create_support_case_once(
            session_state,
            "I need a human representative",
            **first_fields,
        )
        second = create_support_case_once(
            session_state,
            "I want to file a complaint",
            **second_fields,
        )

        self.assertNotEqual(first["case_id"], second["case_id"])
        self.assertFalse(second["duplicate"])

    def test_context_is_bounded(self):
        fields = classify_support_case("I need human support")
        result = create_support_case(
            "I need human support",
            conversation_context="x" * 10000,
            **fields,
        )

        self.assertLessEqual(len(result["conversation_context"]), 6400)


if __name__ == "__main__":
    unittest.main()
