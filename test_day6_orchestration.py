import unittest
from types import SimpleNamespace

from decision import (
    BUSINESS_TOOL,
    CLARIFY,
    ESCALATE,
    SAFE_FALLBACK,
    decide_action,
    detect_deterministic_intent,
    detect_deterministic_route,
)
from orchestration import (
    ControlledOrchestrator,
    requires_policy_evidence,
)
from knowledge import classify_document, retrieve_knowledge


VALID_ORDER = {
    "success": True,
    "tool": "order_lookup",
    "order_id": "ORD-1001",
    "data": {
        "status": "Shipped",
        "tracking_number": "TRK-458921",
        "expected_delivery": "September 10, 2026",
    },
}


class FakeVectorStore:
    def __init__(self, results):
        self.results = results
        self.query = None

    def similarity_search_with_relevance_scores(self, query, k):
        self.query = query
        return self.results[:k]


def fake_document(content, source, page, chunk_index, category, customer_policy):
    return SimpleNamespace(
        page_content=content,
        metadata={
            "source": source,
            "page": page,
            "chunk_index": chunk_index,
            "document_type": "customer_policy" if customer_policy else "internal",
            "category": category,
            "customer_policy": customer_policy,
        },
    )


class Day5RegressionTests(unittest.TestCase):
    def test_terminal_routes_and_deterministic_order(self):
        self.assertEqual(
            detect_deterministic_route("I need a human representative"),
            "escalate",
        )
        self.assertEqual(
            detect_deterministic_intent("Track order ORD-1001"),
            "order_tracking",
        )
        self.assertEqual(
            decide_action("order_tracking", "Track order ORD-1001")["action"],
            BUSINESS_TOOL,
        )
        self.assertEqual(
            decide_action("order_tracking", "Where is my order?", identifier_context="ORD-1001")["action"],
            BUSINESS_TOOL,
        )
        self.assertEqual(
            decide_action("order_tracking", "Where is my order?")["action"],
            CLARIFY,
        )
        self.assertEqual(
            decide_action("unsupported", "Book me a flight")["action"],
            SAFE_FALLBACK,
        )
        self.assertEqual(
            decide_action("human_escalation", "I need a human")["action"],
            ESCALATE,
        )


class Day6OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tool_calls = 0
        self.policy_calls = 0

    def execute_tool(self, tool_name, identifier):
        self.tool_calls += 1
        return VALID_ORDER

    def validate_tool_result(self, tool_name, result):
        return {
            "valid": True,
            "status": "success",
            "message": None,
            "data": result["data"],
        }

    def retrieve_policy(self, question):
        self.policy_calls += 1
        return {
            "documents": [object()],
            "context": "Late delivery policy: contact support for available options.",
            "sources": ["delivery-policy.pdf"],
            "evidence_items": [{
                "source": "delivery-policy.pdf",
                "page": 2,
                "chunk_index": 1,
                "category": "late_delivery",
                "document_type": "customer_policy",
                "customer_policy": True,
                "relevance_score": 0.92,
            }],
        }

    def generate_response(self, question, context):
        self.assertIn("VERIFIED BUSINESS RESULT", context)
        self.assertIn("VERIFIED KNOWLEDGE-BASE EVIDENCE", context)
        return "Verified order and policy response."

    def format_tool_response(self, data, identifier):
        return f"Order {identifier}: {data['status']}"

    def test_successful_order_policy_workflow_records_actions(self):
        result = ControlledOrchestrator().run_order_workflow(
            "My order is late. What are my options?",
            "ORD-1001",
            self.execute_tool,
            self.validate_tool_result,
            self.retrieve_policy,
            self.generate_response,
            self.format_tool_response,
        )

        self.assertEqual(result.response, "Verified order and policy response.")
        self.assertEqual(result.response_method, "business_tool_rag")
        self.assertEqual(result.state.current_step, 2)
        self.assertEqual(
            [step["action"] for step in result.state.action_history],
            [BUSINESS_TOOL, "RAG"],
        )
        self.assertEqual(result.state.completion_status, "completed")
        self.assertEqual(result.state.failure_count, 0)
        self.assertEqual(self.tool_calls, 1)
        self.assertEqual(self.policy_calls, 1)

    def test_step_limit_stops_before_unapproved_next_action(self):
        result = ControlledOrchestrator(max_steps=1).run_order_workflow(
            "My order is late. What are my options?",
            "ORD-1001",
            self.execute_tool,
            self.validate_tool_result,
            self.retrieve_policy,
            self.generate_response,
            self.format_tool_response,
        )

        self.assertEqual(result.state.final_outcome, "safe_fallback")
        self.assertEqual(result.state.current_step, 1)
        self.assertEqual(self.policy_calls, 0)

    def test_invalid_tool_result_is_terminal_and_not_retried(self):
        def malformed_tool(tool_name, identifier):
            self.tool_calls += 1
            return {"success": True, "data": {}}

        result = ControlledOrchestrator().run_order_workflow(
            "My order is late. What are my options?",
            "ORD-1001",
            malformed_tool,
            lambda tool_name, raw: {
                "valid": False,
                "status": "malformed",
                "message": "The business system returned incomplete data.",
                "data": None,
            },
            self.retrieve_policy,
            self.generate_response,
            self.format_tool_response,
        )

        self.assertEqual(result.state.final_outcome, "safe_fallback")
        self.assertEqual(result.response_method, "business_tool_validation")
        self.assertEqual(self.tool_calls, 1)
        self.assertEqual(self.policy_calls, 0)

    def test_policy_detection_is_controlled(self):
        self.assertTrue(requires_policy_evidence("My order is late. What are my options?"))
        self.assertFalse(requires_policy_evidence("Track order ORD-1001"))


class Day7KnowledgeTests(unittest.TestCase):
    def test_document_classification_is_conservative(self):
        policy = classify_document("customer_policy__late_delivery.pdf")
        handbook = classify_document("employee_handbook.pdf")

        self.assertEqual(policy["category"], "late_delivery")
        self.assertTrue(policy["customer_policy"])
        self.assertFalse(handbook["customer_policy"])
        self.assertEqual(handbook["document_type"], "internal")

    def test_relevant_policy_preserves_traceable_metadata(self):
        document = fake_document(
            "Late delivery options are described here.",
            "customer_policy__late_delivery.pdf",
            3,
            2,
            "late_delivery",
            True,
        )
        store = FakeVectorStore([(document, 0.91), (document, 0.90)])

        result = retrieve_knowledge(
            store,
            "My order ORD-1001 is late. What are my options?",
            category="late_delivery",
            policy_only=True,
        )

        self.assertTrue(result["valid"])
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["evidence_items"][0]["page"], 3)
        self.assertEqual(result["evidence_items"][0]["chunk_index"], 2)
        self.assertNotIn("ORD-1001", store.query)

    def test_general_support_retrieval_remains_available(self):
        document = fake_document(
            "The product setup instructions are described here.",
            "general_support__product_setup.pdf",
            1,
            1,
            "general_support",
            False,
        )
        result = retrieve_knowledge(
            FakeVectorStore([(document, 0.88)]),
            "How do I set up the product?",
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["sources"], ["general_support__product_setup.pdf"])

    def test_internal_handbook_cannot_satisfy_policy_retrieval(self):
        handbook = fake_document(
            "Internal employee delivery procedure.",
            "employee_handbook.pdf",
            4,
            1,
            "late_delivery",
            False,
        )
        result = retrieve_knowledge(
            FakeVectorStore([(handbook, 0.99)]),
            "late delivery options",
            category="late_delivery",
            policy_only=True,
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertTrue(result["message"].startswith("UNCERTAIN:"))

    def test_irrelevant_and_low_score_evidence_is_rejected(self):
        document = fake_document(
            "Unrelated product information.",
            "customer_policy__late_delivery.pdf",
            1,
            1,
            "late_delivery",
            True,
        )
        result = retrieve_knowledge(
            FakeVectorStore([(document, 0.10)]),
            "late delivery options",
            category="late_delivery",
            policy_only=True,
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["documents"], [])


if __name__ == "__main__":
    unittest.main()
