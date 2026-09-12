"""Controlled, deterministic orchestration for supported customer workflows."""

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4
import re

from decision import BUSINESS_TOOL, CLARIFY, ESCALATE, RAG, SAFE_FALLBACK


_ALLOWED_ACTIONS = frozenset({
    BUSINESS_TOOL,
    RAG,
    CLARIFY,
    ESCALATE,
    SAFE_FALLBACK,
})

_POLICY_REQUEST_PATTERN = re.compile(
    r"\b(?:option|options|policy|policies|return|refund|cancel|late|"
    r"delayed|overdue|missing|compensation|what can I do|what should I do)\b",
    re.IGNORECASE,
)


@dataclass
class OrchestrationState:
    """Execution state kept separate from customer conversation memory."""

    run_id: str = field(default_factory=lambda: uuid4().hex)
    current_step: int = 0
    max_steps: int = 3
    action_history: list[dict[str, Any]] = field(default_factory=list)
    validated_tool_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    validated_rag_evidence: list[dict[str, Any]] = field(default_factory=list)
    failure_count: int = 0
    completion_status: str = "in_progress"
    final_outcome: str | None = None
    recovery: str | None = None


@dataclass
class OrchestrationResult:
    """Customer-facing result plus non-sensitive execution details."""

    state: OrchestrationState
    response: str
    response_method: str
    tool_validation: dict[str, Any] | None = None
    tool_data: dict[str, Any] | None = None
    rag_evidence: dict[str, Any] | None = None


def requires_policy_evidence(question: str) -> bool:
    """Return whether an order question needs policy evidence to answer."""

    return bool(_POLICY_REQUEST_PATTERN.search(str(question or "")))


def validate_rag_evidence(evidence: Any) -> dict[str, Any]:
    """Accept only non-empty, source-labelled evidence from the retriever."""

    if not isinstance(evidence, dict):
        return {
            "valid": False,
            "status": "malformed",
            "message": "The knowledge system returned an unexpected response.",
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
        }

    if evidence.get("valid") is False:
        return {
            "valid": False,
            "status": evidence.get("status", "insufficient_evidence"),
            "message": evidence.get(
                "message",
                "UNCERTAIN: The knowledge base does not contain sufficient evidence.",
            ),
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
        }

    documents = evidence.get("documents")
    evidence_items = evidence.get("evidence_items")
    context = evidence.get("context")
    sources = evidence.get("sources")

    if (
        not isinstance(documents, list)
        or not documents
        or not isinstance(evidence_items, list)
        or len(evidence_items) != len(documents)
    ):
        return {
            "valid": False,
            "status": "missing",
            "message": "I could not find reliable policy information for that request.",
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
        }

    if not isinstance(context, str) or not context.strip():
        return {
            "valid": False,
            "status": "malformed",
            "message": "The retrieved policy evidence could not be verified.",
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
        }

    if (
        not isinstance(sources, list)
        or not sources
        or any(
            not isinstance(source, str)
            or not source.strip()
            or source == "Unknown document"
            for source in sources
        )
    ):
        return {
            "valid": False,
            "status": "malformed",
            "message": "The retrieved policy evidence could not be verified.",
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
        }

    if any(
        not isinstance(item, dict)
        or not item.get("source")
        or item.get("customer_policy") is not True
        or item.get("relevance_score") is None
        for item in evidence_items
    ):
        return {
            "valid": False,
            "status": "unapproved_evidence",
            "message": "UNCERTAIN: The retrieved source is not approved customer-policy evidence.",
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
        }

    return {
        "valid": True,
        "status": "success",
        "message": None,
        "documents": documents,
        "evidence_items": evidence_items,
        "context": context,
        "sources": sources,
    }


class ControlledOrchestrator:
    """Run one allowlisted workflow with a hard step and failure budget."""

    def __init__(self, max_steps: int = 3, max_failures: int = 1):
        if max_steps < 1 or max_failures < 1:
            raise ValueError("Orchestration budgets must be positive.")

        self.state = OrchestrationState(max_steps=max_steps)
        self.max_failures = max_failures

    def _start_action(self, action: str) -> bool:
        if action not in _ALLOWED_ACTIONS:
            self._finish("safe_fallback", "Unsafe action was rejected.")
            return False

        if self.state.current_step >= self.state.max_steps:
            self._finish("safe_fallback", "The workflow reached its step limit.")
            return False

        self.state.current_step += 1
        self.state.action_history.append({
            "step": self.state.current_step,
            "action": action,
            "status": "started",
        })
        return True

    def _finish(self, outcome: str, recovery: str | None = None) -> None:
        self.state.completion_status = "completed"
        self.state.final_outcome = outcome
        self.state.recovery = recovery

    def _fail(self, message: str) -> None:
        self.state.failure_count += 1
        if self.state.action_history:
            self.state.action_history[-1]["status"] = "failed"

        if self.state.failure_count >= self.max_failures:
            self._finish("safe_fallback", message)

    def _succeed(self) -> None:
        if self.state.action_history:
            self.state.action_history[-1]["status"] = "success"

    def run_order_workflow(
        self,
        question: str,
        identifier: str,
        execute_tool: Callable[[str, str], Any],
        validate_tool_result: Callable[[str, Any], dict[str, Any]],
        retrieve_policy: Callable[[str], Any],
        generate_response: Callable[[str, str], str],
        format_tool_response: Callable[[dict[str, Any], str], str],
    ) -> OrchestrationResult:
        """Execute order lookup and, when needed, a verified policy lookup."""

        if not self._start_action(BUSINESS_TOOL):
            return self._safe_result(self.state.recovery or "The workflow could not be started.")

        try:
            raw_result = execute_tool("order_lookup", identifier)
            validation = validate_tool_result("order_lookup", raw_result)
        except Exception:
            validation = {
                "valid": False,
                "status": "failure",
                "message": "The requested customer information could not be retrieved right now.",
                "data": None,
            }

        if not isinstance(validation, dict):
            validation = {
                "valid": False,
                "status": "malformed",
                "message": "The business system returned an unexpected response.",
                "data": None,
            }

        tool_status = validation.get("status")
        tool_success = validation.get("valid") and tool_status == "success"
        if not tool_success:
            self._fail(validation.get("message") or "I could not verify that business information.")
            return OrchestrationResult(
                state=self.state,
                response=validation.get("message") or "I could not verify that business information.",
                response_method="business_tool_validation",
                tool_validation=validation,
            )

        self._succeed()
        tool_data = validation.get("data")
        self.state.validated_tool_results["order_lookup"] = {
            "status": "success",
            "identifier": identifier,
            "data": tool_data,
        }

        if not requires_policy_evidence(question):
            self._finish("business_tool", "No additional policy evidence was required.")
            return OrchestrationResult(
                state=self.state,
                response=format_tool_response(tool_data, identifier),
                response_method="business_tool",
                tool_validation=validation,
                tool_data=tool_data,
            )

        if not self._start_action(RAG):
            return self._safe_result(
                self.state.recovery or "The workflow reached its step limit.",
                validation,
                tool_data,
            )

        try:
            evidence = validate_rag_evidence(retrieve_policy(question))
        except Exception:
            evidence = {
                "valid": False,
                "status": "failure",
                "message": "The policy information could not be retrieved right now.",
                "documents": [],
                "context": "",
                "sources": [],
            }

        if not evidence["valid"]:
            self._fail(evidence["message"])
            return OrchestrationResult(
                state=self.state,
                response=evidence["message"],
                response_method="safe_fallback",
                tool_validation=validation,
                tool_data=tool_data,
                rag_evidence=evidence,
            )

        self._succeed()
        self.state.validated_rag_evidence = evidence["evidence_items"]
        combined_context = (
            "VERIFIED BUSINESS RESULT:\n"
            f"{tool_data}\n\n"
            "VERIFIED KNOWLEDGE-BASE EVIDENCE:\n"
            f"{evidence['context']}"
        )

        try:
            response = generate_response(question, combined_context)
        except Exception:
            self._fail("The response could not be generated right now.")
            return OrchestrationResult(
                state=self.state,
                response="The response could not be generated right now. Please try again later.",
                response_method="safe_fallback",
                tool_validation=validation,
                tool_data=tool_data,
                rag_evidence=evidence,
            )

        self._finish("completed", "Verified business data and policy evidence.")
        return OrchestrationResult(
            state=self.state,
            response=response,
            response_method="business_tool_rag",
            tool_validation=validation,
            tool_data=tool_data,
            rag_evidence=evidence,
        )

    def _safe_result(
        self,
        message: str,
        tool_validation: dict[str, Any] | None = None,
        tool_data: dict[str, Any] | None = None,
    ) -> OrchestrationResult:
        return OrchestrationResult(
            state=self.state,
            response=message,
            response_method="safe_fallback",
            tool_validation=tool_validation,
            tool_data=tool_data,
        )
