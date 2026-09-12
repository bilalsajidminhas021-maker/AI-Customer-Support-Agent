# --------------------------------------------------
# Mock Business Tools
# --------------------------------------------------

# These functions simulate real company APIs.
#
# In a production system, these functions could be
# replaced with actual:
#
# - Order Management APIs
# - Billing APIs
# - Account Management APIs
#
# The agent does not directly access databases.
# It selects a tool, and this module executes it.


import json
import re
from collections.abc import MutableMapping
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from audit import log_audit_event
from case_storage import JsonCaseStorage
from config import (
    BUSINESS_API_BASE_URL,
    BUSINESS_API_KEY,
    BUSINESS_API_TIMEOUT,
    BUSINESS_DATA_PATH,
    BUSINESS_PROVIDER,
    SUPPORT_CASE_STORAGE_PATH,
)
from tenant_context import DEFAULT_TENANT_CONTEXT
from integrations import (
    ConfigurationErrorOrderProvider,
    DemoOrderProvider,
    OrderDataProvider,
    ProviderResult,
    RestOrderProvider,
)


BUSINESS_DATA = {}


def _load_business_data():
    """Load local records retained by the billing and account tools."""

    try:
        with Path(BUSINESS_DATA_PATH).expanduser().open(
            "r", encoding="utf-8"
        ) as data_file:
            data = json.load(data_file)
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


BUSINESS_DATA = _load_business_data()
BILLING_RECORDS = BUSINESS_DATA.get("billing_records", {})
ACCOUNTS = BUSINESS_DATA.get("accounts", {})


def _create_order_provider() -> OrderDataProvider:
    if BUSINESS_PROVIDER == "demo":
        return DemoOrderProvider(BUSINESS_DATA_PATH)
    if BUSINESS_PROVIDER == "rest":
        return RestOrderProvider(
            BUSINESS_API_BASE_URL,
            BUSINESS_API_KEY,
            BUSINESS_API_TIMEOUT,
        )
    return ConfigurationErrorOrderProvider()


ORDER_PROVIDER = _create_order_provider()

_TOOL_REQUIRED_FIELDS = {
    "order_lookup": {"order_id", "data"},
    "billing_lookup": {"account_id", "data"},
    "account_lookup": {"account_id", "data"},
}

_DATA_REQUIRED_FIELDS = {
    "order_lookup": {
        "status",
        "tracking_number",
        "expected_delivery",
    },
    "billing_lookup": {
        "invoice",
        "amount",
        "payment_status",
        "billing_date",
    },
    "account_lookup": {"account_status", "membership", "email"},
}

SUPPORT_CASE_STATUSES = frozenset({"open", "pending_handoff"})
SUPPORT_CASE_LIFECYCLE_STATUSES = frozenset({
    "OPEN",
    "IN_REVIEW",
    "WAITING_FOR_CUSTOMER",
    "RESOLVED",
    "CLOSED",
})
SUPPORT_CASE_TRANSITIONS = {
    "OPEN": {"IN_REVIEW", "WAITING_FOR_CUSTOMER"},
    "IN_REVIEW": {"WAITING_FOR_CUSTOMER", "RESOLVED"},
    "WAITING_FOR_CUSTOMER": {"IN_REVIEW"},
    "RESOLVED": {"CLOSED"},
    "CLOSED": set(),
}
SUPPORT_CASE_PRIORITIES = frozenset({"HIGH", "NORMAL"})
SUPPORT_CASE_CATEGORIES = frozenset({
    "BILLING",
    "ORDER",
    "ACCOUNT",
    "COMPLAINT",
    "SECURITY",
    "GENERAL",
})
SUPPORT_CASE_CACHE_KEY = "support_case_fingerprints"
MAX_SUPPORT_CASE_CACHE_ENTRIES = 8
CASE_EVIDENCE_STATUSES = frozenset({
    "VERIFIED",
    "UNVERIFIED",
    "NOT_APPLICABLE",
})
CASE_RECOMMENDED_ACTIONS = frozenset({
    "HUMAN_REVIEW",
    "BILLING_REVIEW",
    "ACCOUNT_REVIEW",
    "ORDER_REVIEW",
    "POLICY_REVIEW",
    "CUSTOMER_CLARIFICATION",
})
CASE_STORAGE = JsonCaseStorage(
    SUPPORT_CASE_STORAGE_PATH,
    tenant_id=DEFAULT_TENANT_CONTEXT.tenant_id,
)


def _legacy_status_for_lifecycle(lifecycle_status):
    return "open" if lifecycle_status == "OPEN" else "pending_handoff"


def summarize_support_case(question, category):
    """Build a bounded summary from the request without model inference."""

    text = str(question or "").strip().lower()
    issue = "general_support"
    if (
        "charged twice" in text
        or "charged me twice" in text
        or "duplicate" in text
    ):
        issue = "duplicate_charge"
    elif any(term in text for term in ("refund", "money back")):
        issue = "refund_request"
    elif "compensation" in text:
        issue = "compensation_request"
    elif "replacement" in text:
        issue = "replacement_request"
    elif any(term in text for term in ("cancel", "cancellation")):
        issue = "cancellation_request"
    elif any(term in text for term in ("compromised", "hacked", "unauthorized")):
        issue = "security_compromise"
    elif "complaint" in text or "complain" in text:
        issue = "complaint"
    elif any(term in text for term in ("order", "delivery", "shipping")):
        issue = "order_issue"
    elif "account" in text:
        issue = "account_issue"

    requested_outcome = None
    for outcome, terms in (
        ("refund", ("refund", "money back")),
        ("compensation", ("compensation",)),
        ("replacement", ("replacement", "replace")),
        ("cancellation", ("cancel", "cancellation")),
    ):
        if any(term in text for term in terms):
            requested_outcome = outcome
            break

    return {
        "issue": issue,
        "requested_outcome": requested_outcome,
        "classification": str(category or "GENERAL").strip().upper(),
    }


def classify_case_evidence(evidence=None):
    """Classify only approved, source-labelled policy evidence as verified."""

    if not isinstance(evidence, dict):
        return {
            "status": "UNVERIFIED",
            "sources": [],
            "items": [],
        }

    sources = evidence.get("sources")
    items = evidence.get("evidence_items")
    if (
        evidence.get("valid") is not True
        or not isinstance(sources, list)
        or not sources
        or not isinstance(items, list)
        or len(items) != len(sources)
        or any(
            not isinstance(source, str) or not source.strip()
            for source in sources
        )
        or any(
            not isinstance(item, dict)
            or not item.get("source")
            or item.get("customer_policy") is not True
            or not isinstance(item.get("relevance_score"), (int, float))
            for item in items
        )
    ):
        return {
            "status": "UNVERIFIED",
            "sources": [],
            "items": [],
        }

    return {
        "status": "VERIFIED",
        "sources": [str(source)[:240] for source in sources[:12]],
        "items": [dict(item) for item in items[:12]],
    }


def recommend_case_action(category, evidence_status, required_information=None):
    """Return one deterministic, controlled action for a support case."""

    if required_information:
        return "CUSTOMER_CLARIFICATION"

    normalized_category = str(category or "").strip().upper()
    if normalized_category == "SECURITY":
        return "ACCOUNT_REVIEW"
    if normalized_category == "BILLING":
        return "BILLING_REVIEW"
    if normalized_category == "ORDER":
        return "ORDER_REVIEW"
    if normalized_category == "COMPLAINT":
        return "HUMAN_REVIEW"
    if str(evidence_status or "").strip().upper() == "VERIFIED":
        return "POLICY_REVIEW"
    return "HUMAN_REVIEW"


def classify_support_case(question, intent=None):
    """Map existing escalation signals to controlled case fields."""

    text = str(question or "").strip().lower()
    canonical_intent = str(intent or "").strip().lower()

    if any(term in text for term in (
        "compromised",
        "unauthorized access",
        "accessed my account",
        "hacked",
        "security",
    )):
        return {
            "category": "SECURITY",
            "priority": "HIGH",
            "reason": "Account security issue requires human review.",
        }

    if canonical_intent in {"billing", "billing_dispute"} or any(
        term in text for term in (
            "refund",
            "charged twice",
            "charged me twice",
            "dispute this charge",
            "billing dispute",
            "invoice dispute",
        )
    ):
        return {
            "category": "BILLING",
            "priority": "HIGH",
            "reason": "Billing dispute or refund request requires human review.",
        }

    if "complaint" in text or "compensation" in text:
        return {
            "category": "COMPLAINT",
            "priority": "NORMAL",
            "reason": "Customer complaint requires human review.",
        }

    if canonical_intent in {"order_issue", "order_tracking"} or any(
        term in text for term in (
            "order",
            "delivery",
            "shipping",
            "package",
        )
    ):
        return {
            "category": "ORDER",
            "priority": "NORMAL",
            "reason": "Order support issue requires human review.",
        }

    if canonical_intent in {"account", "account_help"}:
        return {
            "category": "ACCOUNT",
            "priority": "NORMAL",
            "reason": "Account support issue requires human review.",
        }

    return {
        "category": "GENERAL",
        "priority": "NORMAL",
        "reason": "Customer request requires human support.",
    }


def create_support_case(
    customer_request,
    category,
    priority,
    reason,
    conversation_context="",
    evidence=None,
    required_information=None,
    tenant_id="default",
):
    """Create a local support case with controlled fields."""

    normalized_category = str(category or "").strip().upper()
    normalized_priority = str(priority or "").strip().upper()
    normalized_request = str(customer_request or "").strip()
    normalized_reason = str(reason or "").strip()

    if (
        not normalized_request
        or normalized_category not in SUPPORT_CASE_CATEGORIES
        or normalized_priority not in SUPPORT_CASE_PRIORITIES
        or not normalized_reason
    ):
        return {
            "success": False,
            "tool": "support_case_create",
            "status": "invalid_request",
            "message": "The support case could not be created right now.",
        }

    created_at = datetime.now(timezone.utc).isoformat()
    case_id = f"CASE-{datetime.now(timezone.utc).year}-{uuid4().hex[:8].upper()}"
    bounded_context = str(conversation_context or "")[:6400]
    evidence_data = classify_case_evidence(evidence)
    summary = summarize_support_case(
        normalized_request,
        normalized_category,
    )
    recommended_action = recommend_case_action(
        normalized_category,
        evidence_data["status"],
        required_information,
    )

    return {
        "success": True,
        "tool": "support_case_create",
        "case_id": case_id,
        "tenant_id": tenant_id,
        "status": "open",
        "lifecycle_status": "OPEN",
        "priority": normalized_priority,
        "category": normalized_category,
        "reason": normalized_reason,
        "customer_request": normalized_request,
        "conversation_context": bounded_context,
        "summary": summary,
        "evidence": evidence_data,
        "recommended_action": recommended_action,
        "created_at": created_at,
        "updated_at": created_at,
        "fingerprint": support_case_request_fingerprint(
            normalized_request,
            normalized_category,
            normalized_reason,
            tenant_id=tenant_id,
        ),
        "status_history": [{
            "from": None,
            "to": "OPEN",
            "at": created_at,
            "reason": "case_created",
        }],
    }


def validate_support_case_result(result):
    """Validate a support-case result before it is shown to a customer."""

    required_fields = {
        "case_id",
        "status",
        "priority",
        "category",
        "reason",
        "created_at",
        "lifecycle_status",
        "updated_at",
        "summary",
        "evidence",
        "recommended_action",
        "fingerprint",
        "status_history",
    }
    if not isinstance(result, dict) or result.get("success") is not True:
        return {
            "valid": False,
            "status": "malformed",
            "message": "The support case could not be created right now.",
            "data": None,
        }

    if not required_fields.issubset(result):
        return {
            "valid": False,
            "status": "malformed",
            "message": "The support case could not be verified.",
            "data": None,
        }

    if (
        not isinstance(result["case_id"], str)
        or not re.fullmatch(r"CASE-\d{4}-[A-F0-9]{8}", result["case_id"])
        or result["status"] not in SUPPORT_CASE_STATUSES
        or result["lifecycle_status"] not in SUPPORT_CASE_LIFECYCLE_STATUSES
        or result["priority"] not in SUPPORT_CASE_PRIORITIES
        or result["category"] not in SUPPORT_CASE_CATEGORIES
        or not isinstance(result["reason"], str)
        or not result["reason"].strip()
        or not isinstance(result["created_at"], str)
        or not result["created_at"].strip()
        or not isinstance(result["updated_at"], str)
        or not result["updated_at"].strip()
        or not isinstance(result["summary"], dict)
        or not isinstance(result["evidence"], dict)
        or result["evidence"].get("status") not in CASE_EVIDENCE_STATUSES
        or result["recommended_action"] not in CASE_RECOMMENDED_ACTIONS
        or not isinstance(result["fingerprint"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", result["fingerprint"])
        or not isinstance(result["status_history"], list)
    ):
        return {
            "valid": False,
            "status": "invalid",
            "message": "The support case could not be verified.",
            "data": None,
        }

    return {
        "valid": True,
        "status": "success",
        "message": None,
        "data": {
            field: result[field]
            for field in required_fields
        },
    }


def support_case_request_fingerprint(
    customer_request,
    category,
    reason,
    tenant_id="default",
):
    """Return a non-sensitive fingerprint for one processed request."""

    fingerprint_input = "|".join((
        str(tenant_id or "default").strip(),
        str(customer_request or "").strip().lower(),
        str(category or "").strip().upper(),
        str(reason or "").strip(),
    ))
    return sha256(fingerprint_input.encode("utf-8")).hexdigest()


def create_support_case_once(
    session_state: MutableMapping,
    customer_request,
    category,
    priority,
    reason,
    conversation_context="",
    evidence=None,
    required_information=None,
    storage=None,
    tenant_id=None,
):
    """Create at most one successful case, using session and disk caches."""

    selected_storage = storage or CASE_STORAGE
    effective_tenant_id = str(
        tenant_id or getattr(selected_storage, "tenant_id", "default")
    ).strip()
    if not effective_tenant_id:
        return {
            "success": False,
            "tool": "support_case_create",
            "status": "invalid_tenant",
            "message": "The support case could not be created right now.",
            "duplicate": False,
        }
    fingerprint = support_case_request_fingerprint(
        customer_request,
        category,
        reason,
        tenant_id=effective_tenant_id,
    )
    cache = session_state.setdefault(SUPPORT_CASE_CACHE_KEY, {})
    if fingerprint in cache:
        result = dict(cache[fingerprint])
        result["duplicate"] = True
        log_audit_event(
            event_type="case_duplicate_reused",
            case_id=result.get("case_id"),
            case_status=result.get("lifecycle_status"),
            case_category=result.get("category"),
            case_priority=result.get("priority"),
            recommended_action=result.get("recommended_action"),
            evidence_status=result.get("evidence", {}).get("status"),
            case_creation_outcome="duplicate_reused",
            fingerprint=fingerprint,
            status="completed",
        )
        return result

    persistent_result = selected_storage.find_by_fingerprint(fingerprint)
    if persistent_result is not None:
        validation = validate_support_case_result(persistent_result)
        if validation["valid"]:
            cache[fingerprint] = dict(persistent_result)
            result = dict(persistent_result)
            result["duplicate"] = True
            log_audit_event(
                event_type="case_duplicate_reused",
                case_id=result.get("case_id"),
                case_status=result.get("lifecycle_status"),
                case_category=result.get("category"),
                case_priority=result.get("priority"),
                recommended_action=result.get("recommended_action"),
                evidence_status=result.get("evidence", {}).get("status"),
                case_creation_outcome="duplicate_reused",
                fingerprint=fingerprint,
                status="completed",
            )
            return result

    result = create_support_case(
        customer_request=customer_request,
        category=category,
        priority=priority,
        reason=reason,
        conversation_context=conversation_context,
        evidence=evidence,
        required_information=required_information,
        tenant_id=effective_tenant_id,
    )
    log_audit_event(
        event_type="case_classified",
        case_category=result.get("category"),
        case_priority=result.get("priority"),
        recommended_action=result.get("recommended_action"),
        evidence_status=result.get("evidence", {}).get("status"),
        fingerprint=fingerprint,
        status="completed",
    )
    validation = validate_support_case_result(result)
    if validation["valid"]:
        persisted_result = selected_storage.create(result)
        if persisted_result is None:
            log_audit_event(
                event_type="case_creation_failed",
                case_category=result.get("category"),
                case_priority=result.get("priority"),
                recommended_action=result.get("recommended_action"),
                evidence_status=result.get("evidence", {}).get("status"),
                case_creation_outcome="failed",
                fingerprint=fingerprint,
                status="failed",
                error="Support case persistence failed.",
            )
            return {
                "success": False,
                "tool": "support_case_create",
                "status": "storage_failure",
                "message": "The support case could not be created right now.",
                "duplicate": False,
            }

        cache[fingerprint] = dict(persisted_result)
        while len(cache) > MAX_SUPPORT_CASE_CACHE_ENTRIES:
            del cache[next(iter(cache))]
        result = persisted_result
        log_audit_event(
            event_type="case_created",
            case_id=result.get("case_id"),
            case_status=result.get("lifecycle_status"),
            case_category=result.get("category"),
            case_priority=result.get("priority"),
            recommended_action=result.get("recommended_action"),
            evidence_status=result.get("evidence", {}).get("status"),
            case_creation_outcome="created",
            fingerprint=fingerprint,
            status="completed",
        )
    else:
        log_audit_event(
            event_type="case_creation_failed",
            case_category=category,
            case_priority=priority,
            case_creation_outcome="failed",
            fingerprint=fingerprint,
            status="failed",
            error="Support case validation failed.",
        )

    result["duplicate"] = False
    return result


def get_support_case(case_id, storage=None, tenant_id=None):
    """Retrieve and validate one persisted support case."""

    selected_storage = storage or CASE_STORAGE
    if tenant_id and tenant_id != getattr(selected_storage, "tenant_id", tenant_id):
        return {
            "success": False,
            "status": "not_found",
            "message": "The support case could not be found.",
            "case_id": case_id,
        }
    result = selected_storage.get(case_id)
    if result is None:
        return {
            "success": False,
            "status": "not_found",
            "message": "The support case could not be found.",
            "case_id": case_id,
        }

    validation = validate_support_case_result(result)
    if not validation["valid"]:
        return {
            "success": False,
            "status": "malformed",
            "message": "The support case could not be verified.",
            "case_id": case_id,
        }

    return dict(result)


def update_support_case(case_id, new_status, reason, storage=None, tenant_id=None):
    """Apply one validated lifecycle transition to a persisted case."""

    normalized_status = str(new_status or "").strip().upper()
    if normalized_status not in SUPPORT_CASE_LIFECYCLE_STATUSES:
        return {
            "success": False,
            "status": "invalid_transition",
            "message": "The requested case status is not allowed.",
            "case_id": case_id,
        }

    case = get_support_case(case_id, storage=storage, tenant_id=tenant_id)
    if not case.get("success", True):
        return case

    current_status = case["lifecycle_status"]
    if normalized_status not in SUPPORT_CASE_TRANSITIONS[current_status]:
        return {
            "success": False,
            "status": "invalid_transition",
            "message": "The requested case status transition is not allowed.",
            "case_id": case_id,
        }

    updated_at = datetime.now(timezone.utc).isoformat()
    history = list(case.get("status_history", []))
    history.append({
        "from": current_status,
        "to": normalized_status,
        "at": updated_at,
        "reason": str(reason or "").strip()[:500],
    })
    updated_case = (storage or CASE_STORAGE).update(
        case_id,
        {
            "status": _legacy_status_for_lifecycle(normalized_status),
            "lifecycle_status": normalized_status,
            "updated_at": updated_at,
            "status_history": history,
        },
    )
    if updated_case is None:
        log_audit_event(
            event_type="case_update_failed",
            case_id=case_id,
            previous_status=current_status,
            new_status=normalized_status,
            status="failed",
            error="Support case persistence failed.",
        )
        return {
            "success": False,
            "status": "storage_failure",
            "message": "The support case could not be updated right now.",
            "case_id": case_id,
        }

    validation = validate_support_case_result(updated_case)
    if not validation["valid"]:
        return {
            "success": False,
            "status": "malformed",
            "message": "The updated support case could not be verified.",
            "case_id": case_id,
        }

    log_audit_event(
        event_type="case_updated",
        case_id=case_id,
        case_status=normalized_status,
        previous_status=current_status,
        new_status=normalized_status,
        case_category=updated_case.get("category"),
        case_priority=updated_case.get("priority"),
        recommended_action=updated_case.get("recommended_action"),
        evidence_status=updated_case.get("evidence", {}).get("status"),
        status="completed",
    )
    return updated_case


def _validate_identifier(identifier, prefix, label):
    if not isinstance(identifier, str):
        return None, f"Please provide a valid {label}."

    normalized = identifier.strip().upper()
    if not re.fullmatch(rf"{prefix}-\d+", normalized):
        return None, f"Please provide a valid {label}."

    return normalized, None


# ==================================================
# ORDER LOOKUP TOOL
# ==================================================

def lookup_order(order_id, provider=None):
    """
    Retrieve order information using an order ID.
    """

    order_id, error = _validate_identifier(
        order_id,
        "ORD",
        "order ID"
    )
    if error:
        return {"success": False, "message": error}

    selected_provider = provider or ORDER_PROVIDER
    provider_type = getattr(selected_provider, "provider_type", "unknown")
    try:
        provider_result = selected_provider.get_order(order_id)
    except Exception:
        provider_result = None

    if not isinstance(provider_result, ProviderResult):
        return {
            "success": False,
            "message": "The business integration returned an unexpected response.",
            "integration_status": "malformed",
            "provider_type": provider_type,
        }

    if provider_result.status != "success":
        return {
            "success": False,
            "message": provider_result.message or (
                "The business integration is currently unavailable."
            ),
            "integration_status": provider_result.status,
            "provider_type": provider_result.provider_type,
        }

    return {
        "success": True,
        "tool": "order_lookup",
        "order_id": order_id,
        "data": provider_result.data,
        "integration_status": provider_result.status,
        "provider_type": provider_result.provider_type,
    }


# ==================================================
# BILLING LOOKUP TOOL
# ==================================================

def lookup_billing(account_id):
    """
    Retrieve billing information using an account ID.
    """

    account_id, error = _validate_identifier(
        account_id,
        "ACC",
        "account ID"
    )
    if error:
        return {"success": False, "message": error}

    billing = BILLING_RECORDS.get(account_id)

    if not billing:

        return {
            "success": False,
            "message": (
                f"No billing record was found for "
                f"account ID {account_id}."
            )
        }

    return {
        "success": True,
        "tool": "billing_lookup",
        "account_id": account_id,
        "data": billing
    }


# ==================================================
# ACCOUNT LOOKUP TOOL
# ==================================================

def lookup_account(account_id):
    """
    Retrieve account information using an account ID.
    """

    account_id, error = _validate_identifier(
        account_id,
        "ACC",
        "account ID"
    )
    if error:
        return {"success": False, "message": error}

    account = ACCOUNTS.get(account_id)

    if not account:

        return {
            "success": False,
            "message": (
                f"No account was found for "
                f"account ID {account_id}."
            )
        }

    return {
        "success": True,
        "tool": "account_lookup",
        "account_id": account_id,
        "data": account
    }


# ==================================================
# CENTRAL TOOL DISPATCHER
# ==================================================

def execute_tool(tool_name, identifier):
    """
    Central dispatcher for all business tools.

    The agent selects the tool name.
    This function executes the corresponding
    business operation.

    Parameters
    ----------
    tool_name : str
        Name of the selected business tool.

    identifier : str
        Order ID or Account ID required by the tool.

    Returns
    -------
    dict
        Standardized tool result.
    """

    # --------------------------------------------------
    # Order tool
    # --------------------------------------------------

    if tool_name == "order_lookup":
        return lookup_order(
            identifier
        )


    # --------------------------------------------------
    # Billing tool
    # --------------------------------------------------

    elif tool_name == "billing_lookup":

        return lookup_billing(
            identifier
        )


    # --------------------------------------------------
    # Account tool
    # --------------------------------------------------

    elif tool_name == "account_lookup":

        return lookup_account(
            identifier
        )


    # --------------------------------------------------
    # No valid tool
    # --------------------------------------------------

    else:

        return {
            "success": False,
            "message": (
                f"Unknown business tool: {tool_name}"
            )
        }


def validate_tool_result(tool_name, result):
    """Classify a dispatcher result before it is used in a response."""

    if not isinstance(result, dict) or not isinstance(
        result.get("success"),
        bool
    ):
        return {
            "valid": False,
            "status": "malformed",
            "message": "The business system returned an unexpected response.",
            "data": None,
            "provider_type": None,
            "integration_status": "malformed",
        }

    if not result["success"]:
        message = result.get("message")
        if not isinstance(message, str) or not message.strip():
            return {
                "valid": False,
                "status": "malformed",
                "message": "The business system returned an unexpected failure.",
                "data": None,
            }

        integration_status = result.get("integration_status")
        status = integration_status or (
            "not_found"
            if "no " in message.lower() and " found" in message.lower()
            else "failure"
        )
        return {
            "valid": True,
            "status": status,
            "message": message.strip(),
            "data": None,
            "provider_type": result.get("provider_type"),
            "integration_status": integration_status or status,
        }

    required_fields = _TOOL_REQUIRED_FIELDS.get(tool_name)
    if not required_fields or not required_fields.issubset(result):
        return {
            "valid": False,
            "status": "malformed",
            "message": "The business system returned incomplete data.",
            "data": None,
            "provider_type": result.get("provider_type"),
            "integration_status": "malformed",
        }

    data = result.get("data")
    if not isinstance(data, dict):
        return {
            "valid": False,
            "status": "malformed",
            "message": "The business system returned incomplete data.",
            "data": None,
            "provider_type": result.get("provider_type"),
            "integration_status": "malformed",
        }

    required_data_fields = _DATA_REQUIRED_FIELDS.get(tool_name, set())
    if not required_data_fields.issubset(data):
        return {
            "valid": False,
            "status": "malformed",
            "message": "The business system returned incomplete data.",
            "data": None,
            "provider_type": result.get("provider_type"),
            "integration_status": "malformed",
        }

    return {
        "valid": True,
        "status": "success",
        "message": None,
        "data": data,
        "provider_type": result.get("provider_type"),
        "integration_status": result.get("integration_status", "success"),
    }