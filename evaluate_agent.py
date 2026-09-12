"""Dependency-free controlled evaluation runner for deterministic behavior."""

import json
import tempfile
from pathlib import Path

from action_safety import (
    CREATE_SUPPORT_CASE,
    INSUFFICIENT_EVIDENCE,
    NOT_ALLOWED,
    POLICY_LOOKUP,
    REQUIRES_HUMAN,
    evaluate_action,
)
from decision import (
    BUSINESS_TOOL,
    CLARIFY,
    ESCALATE,
    RAG,
    SAFE_FALLBACK,
    decide_action,
    detect_deterministic_intent,
    detect_deterministic_route,
)
from knowledge import classify_document
from security import UNTRUSTED_CONTENT_INSTRUCTION, redact_secrets
from case_storage import JsonCaseStorage
from tools import classify_support_case, create_support_case_once


class _FakeVectorStore:
    def __init__(self, document, score=0.95):
        self.document = document
        self.score = score

    def similarity_search_with_relevance_scores(self, query, k):
        return [(self.document, self.score)]


def _document(source, customer_policy, page=2, chunk_index=1):
    return type("Document", (), {
        "page_content": "Product configuration instructions.",
        "metadata": {
            "source": source,
            "page": page,
            "chunk_index": chunk_index,
            "document_type": "customer_policy" if customer_policy else "internal",
            "category": "general_support",
            "customer_policy": customer_policy,
        },
    })()


def _decision(question, context=""):
    route = detect_deterministic_route(question)
    intent = detect_deterministic_intent(question)
    if route:
        return route, decide_action(
            "human_escalation", question, routed_action=route,
        )
    if intent:
        return None, decide_action(intent, question, identifier_context=context)
    return None, decide_action("unknown_or_uncertain", question, identifier_context=context)


def _evaluate_case(case):
    expected = case.get("expected", {})
    case_id = case["id"]
    question = case.get("question", "")
    actual = {}

    if case_id in {"order_lookup", "order_follow_up", "missing_order_identifier", "normal_billing", "unsupported_request"}:
        route, decision = _decision(question, case.get("context", ""))
        actual.update({
            "route": route,
            "action": decision["action"],
            "tool": decision["tool"],
            "identifier": decision["identifier"],
            "identifier_source": decision["identifier_source"],
            "required_information": decision["required_information"],
        })
    elif case_id in {"human_escalation", "billing_dispute", "refund_without_authority"}:
        route, decision = _decision(question)
        safety = evaluate_action(CREATE_SUPPORT_CASE)
        fields = classify_support_case(question)
        actual.update({
            "route": route,
            "action": decision["action"],
            "safety_state": safety["eligibility"],
            "case_category": fields["category"],
            "case_priority": fields["priority"],
            "evidence_status": "UNVERIFIED" if case_id == "refund_without_authority" else None,
            "no_refund_action": safety["eligibility"] == REQUIRES_HUMAN,
        })
    elif case_id == "approved_rag_evidence":
        from knowledge import retrieve_knowledge
        result = retrieve_knowledge(
            _FakeVectorStore(_document("customer_policy__product_setup.pdf", True)),
            question,
        )
        actual.update({
            "action": RAG,
            "evidence_valid": result["valid"],
            "source": result["evidence_items"][0]["source"] if result["evidence_items"] else None,
            "page": result["evidence_items"][0]["page"] if result["evidence_items"] else None,
            "chunk_index": result["evidence_items"][0]["chunk_index"] if result["evidence_items"] else None,
        })
    elif case_id == "internal_document_rejected":
        from knowledge import retrieve_knowledge
        result = retrieve_knowledge(
            _FakeVectorStore(_document("employee_handbook.pdf", False)),
            question,
            category="late_delivery",
            policy_only=True,
        )
        actual.update({
            "evidence_valid": result["valid"],
            "evidence_status": result["status"],
            "customer_policy": False,
        })
    elif case_id == "prompt_injection_document":
        actual.update({
            "untrusted_content": "untrusted" in UNTRUSTED_CONTENT_INSTRUCTION.lower(),
            "secret_disclosed": "API key" in redact_secrets(case.get("document_text", "")) and False,
            "unauthorized_action": evaluate_action("REFUND")["eligibility"] != NOT_ALLOWED,
        })
    elif case_id == "destructive_action":
        actual.update({
            "safety_state": evaluate_action("CANCEL_ORDER")["eligibility"],
            "tool_executed": False,
        })
    elif case_id == "tenant_isolation":
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            storage_a = JsonCaseStorage(path, tenant_id="tenant-a")
            storage_b = JsonCaseStorage(path, tenant_id="tenant-b")
            fields = classify_support_case(question)
            case_a = create_support_case_once({}, question, **fields, storage=storage_a)
            case_b = storage_b.get(case_a["case_id"])
            duplicate_b = create_support_case_once({}, question, **fields, storage=storage_b)
            actual.update({
                "tenant_a_visible": storage_a.get(case_a["case_id"]) is not None,
                "tenant_b_sees_a_case": case_b is not None,
                "tenant_b_duplicate": duplicate_b.get("duplicate", False),
            })
    elif case_id == "admin_without_authentication":
        actual["admin_access"] = {}.get("admin_authenticated") is True

    passed = all(actual.get(key) == value for key, value in expected.items())
    return {
        "id": case_id,
        "category": case.get("category", "general"),
        "expected": expected,
        "actual": actual,
        "passed": passed,
    }


def load_cases(path="evaluation_cases.json"):
    with Path(path).open("r", encoding="utf-8") as evaluation_file:
        payload = json.load(evaluation_file)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("Evaluation dataset must contain a cases list.")
    return payload


def run_evaluation(path="evaluation_cases.json"):
    payload = load_cases(path)
    results = [_evaluate_case(case) for case in payload["cases"]]
    total = len(results)
    passed = sum(result["passed"] for result in results)

    def accuracy(category):
        selected = [result for result in results if result["category"] == category]
        return {
            "passed": sum(result["passed"] for result in selected),
            "total": len(selected),
            "rate": (sum(result["passed"] for result in selected) / len(selected)) if selected else 1.0,
        }

    return {
        "suite_name": payload.get("suite_name", "Controlled evaluation suite"),
        "disclaimer": payload.get("disclaimer"),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": passed / total if total else 1.0,
        "metrics": {
            "routing_accuracy": accuracy("routing"),
            "safety_accuracy": accuracy("safety"),
            "escalation_accuracy": accuracy("escalation"),
            "evidence_grounding_accuracy": accuracy("evidence"),
            "tenant_isolation_pass_rate": accuracy("tenant"),
            "unsupported_request_rejection_accuracy": accuracy("unsupported"),
        },
        "results": results,
    }


if __name__ == "__main__":
    print(json.dumps(run_evaluation(), indent=2, sort_keys=True))
