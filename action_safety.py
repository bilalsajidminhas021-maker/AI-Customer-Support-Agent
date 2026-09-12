"""Deterministic eligibility checks for controlled support operations."""

import re


ALLOWED = "ALLOWED"
NOT_ALLOWED = "NOT_ALLOWED"
REQUIRES_HUMAN = "REQUIRES_HUMAN"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

ORDER_LOOKUP = "ORDER_LOOKUP"
ACCOUNT_LOOKUP = "ACCOUNT_LOOKUP"
BILLING_LOOKUP = "BILLING_LOOKUP"
POLICY_LOOKUP = "POLICY_LOOKUP"
CREATE_SUPPORT_CASE = "CREATE_SUPPORT_CASE"

CONTROLLED_ACTIONS = frozenset({
    ORDER_LOOKUP,
    ACCOUNT_LOOKUP,
    BILLING_LOOKUP,
    POLICY_LOOKUP,
    CREATE_SUPPORT_CASE,
})

ACTION_REGISTRY = {
    ORDER_LOOKUP: {"kind": "read_only", "requires_identifier": "order_id"},
    ACCOUNT_LOOKUP: {"kind": "read_only", "requires_identifier": "account_id"},
    BILLING_LOOKUP: {"kind": "read_only", "requires_identifier": "account_id"},
    POLICY_LOOKUP: {"kind": "evidence_lookup"},
    CREATE_SUPPORT_CASE: {"kind": "human_review"},
}

_ORDER_ID_PATTERN = re.compile(r"^ORD-\d+$", re.IGNORECASE)
_ACCOUNT_ID_PATTERN = re.compile(r"^ACC-\d+$", re.IGNORECASE)


def _result(action, eligibility, reason, required_information=None,
            recommended_action=None, evidence_status=None):
    return {
        "action": action,
        "eligibility": eligibility,
        "reason": reason,
        "required_information": list(required_information or []),
        "recommended_action": recommended_action,
        "evidence_status": evidence_status,
    }


def _validated_evidence(evidence):
    """Accept only evidence already carrying approved validation metadata."""

    if not isinstance(evidence, dict):
        return False

    sources = evidence.get("sources")
    items = evidence.get("evidence_items")
    if (
        evidence.get("valid") is not True
        or not isinstance(sources, list)
        or not sources
        or not isinstance(items, list)
        or len(items) != len(sources)
    ):
        return False

    return all(
        isinstance(source, str)
        and source.strip()
        and isinstance(item, dict)
        and item.get("source")
        and item.get("customer_policy") is True
        and isinstance(item.get("relevance_score"), (int, float))
        for source, item in zip(sources, items)
    )


def evaluate_action(
    requested_action,
    identifier=None,
    required_information=None,
    evidence=None,
    policy_dependent=False,
):
    """Evaluate eligibility without executing or authorizing an operation."""

    action = str(requested_action or "").strip().upper()
    required = list(required_information or [])

    if action not in CONTROLLED_ACTIONS:
        return _result(
            action,
            NOT_ALLOWED,
            "The requested operation is not in the controlled action registry.",
            required,
        )

    if action == CREATE_SUPPORT_CASE:
        return _result(
            action,
            REQUIRES_HUMAN,
            "The requested business resolution requires human handling.",
            required,
            recommended_action="HUMAN_REVIEW",
        )

    if action == POLICY_LOOKUP:
        if evidence is None:
            if policy_dependent:
                return _result(
                    action,
                    INSUFFICIENT_EVIDENCE,
                    "Approved customer-policy evidence is required for this decision.",
                    required,
                    recommended_action="POLICY_REVIEW",
                    evidence_status="UNVERIFIED",
                )
            return _result(
                action,
                ALLOWED,
                "Policy evidence retrieval is a controlled read-only operation.",
                required,
                evidence_status="UNVERIFIED",
            )
        if _validated_evidence(evidence):
            return _result(
                action,
                ALLOWED,
                "Approved customer-policy evidence is available for review.",
                required,
                evidence_status="VERIFIED",
            )
        return _result(
            action,
            INSUFFICIENT_EVIDENCE,
            "Approved customer-policy evidence could not be verified.",
            required,
            recommended_action="POLICY_REVIEW",
            evidence_status="UNVERIFIED",
        )

    if required:
        return _result(
            action,
            INSUFFICIENT_EVIDENCE,
            "Required information is missing before the read-only operation can run.",
            required,
        )

    normalized_identifier = str(identifier or "").strip().upper()
    if action == ORDER_LOOKUP:
        valid_identifier = bool(_ORDER_ID_PATTERN.fullmatch(normalized_identifier))
        label = "order ID"
    else:
        valid_identifier = bool(_ACCOUNT_ID_PATTERN.fullmatch(normalized_identifier))
        label = "account ID"

    if not valid_identifier:
        return _result(
            action,
            INSUFFICIENT_EVIDENCE,
            f"A valid {label} is required before the read-only operation can run.",
            [label],
        )

    return _result(
        action,
        ALLOWED,
        "A validated identifier is available for the read-only operation.",
    )
