import json
import time
from datetime import datetime, timezone

from config import AUDIT_STORAGE_PATH, TENANT_ID
from security import redact_secrets


AUDIT_LOG_FILE = AUDIT_STORAGE_PATH


class ExecutionTrace:
    """Collect meaningful request milestones and write one structured event."""

    def __init__(self, question=None, tenant_id=None):
        self.event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id or TENANT_ID,
            "question": question,
            "intent": None,
            "action": None,
            "selected_decision": None,
            "tool": None,
            "identifier_source": None,
            "required_information": [],
            "decision_status": None,
            "decision_reason": None,
            "tool_result": None,
            "integration_provider": None,
            "integration_status": None,
            "response_method": None,
            "orchestration_run_id": None,
            "orchestration_steps": [],
            "orchestration_current_step": None,
            "orchestration_max_steps": None,
            "orchestration_failure_count": 0,
            "orchestration_completion_status": None,
            "orchestration_final_outcome": None,
            "orchestration_recovery": None,
            "validated_rag_sources": [],
            "case_id": None,
            "case_status": None,
            "case_priority": None,
            "case_category": None,
            "case_creation_outcome": None,
            "event_type": None,
            "previous_status": None,
            "new_status": None,
            "recommended_action": None,
            "evidence_status": None,
            "fingerprint": None,
            "safe_action": None,
            "eligibility": None,
            "safety_reason": None,
            "status": "started",
            "error": None,
            "operation": None,
            "duration_ms": None,
            "timings_ms": {},
        }

    def update(self, **values):
        self.event.update(values)

    def record_duration(self, operation, started_at):
        """Record a non-sensitive elapsed duration for one operation."""

        try:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
        except (TypeError, ValueError):
            return None
        timings = self.event.setdefault("timings_ms", {})
        if isinstance(timings, dict):
            timings[str(operation)] = duration_ms
        self.event["duration_ms"] = duration_ms
        return duration_ms

    def write(self):
        try:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as log_file:
                json.dump(redact_secrets(self.event), log_file, ensure_ascii=True, default=str)
                log_file.write("\n")
        except Exception:
            return False

        return True


def log_audit_event(
    question=None,
    action=None,
    intent=None,
    selected_tool=None,
    tool_success=None,
    response_method=None,
    status=None,
    error=None,
    error_type=None,
    message=None,
    case_id=None,
    case_status=None,
    case_priority=None,
    case_category=None,
    case_creation_outcome=None,
    event_type=None,
    previous_status=None,
    new_status=None,
    recommended_action=None,
    evidence_status=None,
    fingerprint=None,
    safe_action=None,
    eligibility=None,
    safety_reason=None,
    tenant_id=None,
    operation=None,
    integration_provider=None,
    integration_status=None,
    duration_ms=None,
    timings_ms=None,
):
    """Append one audit event without allowing logging failures to escape."""

    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": tenant_id or TENANT_ID,
        "question": question,
        "action": action,
        "intent": intent,
        "selected_tool": selected_tool,
        "tool_success": tool_success,
        "response_method": response_method,
        "status": status,
        "error": error,
        "error_type": error_type,
        "message": message,
        "case_id": case_id,
        "case_status": case_status,
        "case_priority": case_priority,
        "case_category": case_category,
        "case_creation_outcome": case_creation_outcome,
        "event_type": event_type,
        "previous_status": previous_status,
        "new_status": new_status,
        "recommended_action": recommended_action,
        "evidence_status": evidence_status,
        "fingerprint": fingerprint,
        "safe_action": safe_action,
        "eligibility": eligibility,
        "safety_reason": safety_reason,
        "operation": operation,
        "integration_provider": integration_provider,
        "integration_status": integration_status,
        "duration_ms": duration_ms,
        "timings_ms": timings_ms if isinstance(timings_ms, dict) else {},
    }

    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as log_file:
            json.dump(redact_secrets(event), log_file, ensure_ascii=True, default=str)
            log_file.write("\n")
    except Exception:
        return False

    return True