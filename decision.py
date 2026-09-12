import re


RAG = "RAG"
BUSINESS_TOOL = "BUSINESS_TOOL"
ESCALATE = "ESCALATE"
CLARIFY = "CLARIFY"
SAFE_FALLBACK = "SAFE_FALLBACK"


_INTENT_ALIASES = {
    "order_issue": "order_tracking",
    "order_tracking": "order_tracking",
    "billing": "billing",
    "account_help": "account",
    "account": "account",
    "policy": "policy_or_faq",
    "product_help": "policy_or_faq",
    "troubleshooting": "policy_or_faq",
    "general_support": "policy_or_faq",
    "policy_or_faq": "policy_or_faq",
    "human_escalation": "human_escalation",
    "unsupported": "unsupported",
    "reject": "unsupported",
    "unknown_or_uncertain": "unknown_or_uncertain",
}

_TOOL_BY_INTENT = {
    "order_tracking": "order_lookup",
    "billing": "billing_lookup",
    "account": "account_lookup",
}

_IDENTIFIER_PATTERNS = {
    "order_lookup": re.compile(r"\bORD-\d+\b", re.IGNORECASE),
    "billing_lookup": re.compile(r"\bACC-\d+\b", re.IGNORECASE),
    "account_lookup": re.compile(r"\bACC-\d+\b", re.IGNORECASE),
}

_HUMAN_REQUEST_PATTERN = re.compile(
    r"\b(?:speak|talk|connect|chat)\s+(?:to|with)\s+(?:a\s+)?human\b"
    r"|\bhuman\s+(?:agent|representative|support)\b",
    re.IGNORECASE,
)

_ESCALATION_REQUEST_PATTERNS = (
    re.compile(
        r"\brefund\s+(?:my|the)?\s*order\b"
        r"|\b(?:want|need|request|requesting|process|issue|give me|"
        r"can i get)\s+(?:a\s+)?refund\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcharged\s+(?:me\s+)?twice\b"
        r"|\b(?:dispute|disputing)\s+(?:this|the|a)?\s*charge\b"
        r"|\b(?:billing|invoice)\s+dispute\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:file|make|lodge|submit)\s+(?:a\s+)?complaint\b"
        r"|\b(?:want|need)\s+to\s+complain\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:compromised|unauthorized\s+access|accessed\s+my\s+account|"
        r"hacked|account\s+security)\b",
        re.IGNORECASE,
    ),
)

_ORDER_LOOKUP_PATTERN = re.compile(
    r"\b(?:check|look\s+up|find)\s+(?:my|the)?\s*order\b"
    r"|\b(?:track|tracking|status|where\s+is|when\s+will|arrive|missing|"
    r"late|delayed|overdue)\b"
    r"|\bORD-\d+\b",
    re.IGNORECASE,
)

_BILLING_LOOKUP_PATTERN = re.compile(
    r"\b(?:invoice|charge|charged|billing\s+record|payment\s+status)\b",
    re.IGNORECASE,
)

_ACCOUNT_LOOKUP_PATTERN = re.compile(
    r"\b(?:account\s+status|account\s+details|membership)\b"
    r"|\bACC-\d+\b",
    re.IGNORECASE,
)

_SUPPORTED_DOMAIN_PATTERN = re.compile(
    r"\b(?:order|delivery|shipping|package|invoice|billing|payment|"
    r"account|membership|product|device|return|warranty|policy|"
    r"subscription|support)\b",
    re.IGNORECASE,
)

_UNSUPPORTED_REQUEST_PATTERN = re.compile(
    r"\b(?:book|reserve|schedule|buy|purchase|sell|compose|generate|"
    r"calculate|solve|translate|joke|poem|football|sports?|quantum|"
    r"weather|recipe)\b",
    re.IGNORECASE,
)


def normalize_intent(intent):
    """Map existing model labels to the small canonical intent set."""

    return _INTENT_ALIASES.get(
        str(intent or "").strip().lower(),
        "unknown_or_uncertain"
    )


def extract_identifier(question, tool_name):
    """Extract only a valid identifier for the selected business tool."""

    pattern = _IDENTIFIER_PATTERNS.get(tool_name)
    if not pattern:
        return None

    match = pattern.search(str(question or ""))
    return match.group(0).upper() if match else None


def detect_deterministic_route(question):
    """Return a route for requests whose handling is unambiguous."""

    text = str(question or "")

    if _HUMAN_REQUEST_PATTERN.search(text):
        return "escalate"

    if any(pattern.search(text) for pattern in _ESCALATION_REQUEST_PATTERNS):
        return "escalate"

    return None


def detect_deterministic_intent(question):
    """Return only intents that can be resolved without model inference."""

    text = str(question or "")

    if _ORDER_LOOKUP_PATTERN.search(text):
        return "order_tracking"

    if _BILLING_LOOKUP_PATTERN.search(text):
        return "billing"

    if _ACCOUNT_LOOKUP_PATTERN.search(text):
        return "account"

    if (
        _UNSUPPORTED_REQUEST_PATTERN.search(text)
        and not _SUPPORTED_DOMAIN_PATTERN.search(text)
    ):
        return "unsupported"

    return None


def decide_action(
    intent,
    question="",
    escalation_enabled=True,
    routed_action=None,
    identifier_context=None,
):
    """Return the structured deterministic decision for a request."""

    canonical_intent = normalize_intent(intent)
    route = str(routed_action or "").strip().lower()

    if route == "escalate" or canonical_intent == "human_escalation":
        action = ESCALATE if escalation_enabled else SAFE_FALLBACK
        return {
            "intent": canonical_intent,
            "action": action,
            "selected_decision": action,
            "tool": None,
            "identifier": None,
            "identifier_source": None,
            "required_information": [],
            "status": "ready" if action == ESCALATE else "fallback",
            "reason": (
                "Human support was explicitly requested."
                if action == ESCALATE
                else "Human escalation is disabled."
            ),
        }

    if route == "reject" or canonical_intent == "unsupported":
        return {
            "intent": canonical_intent,
            "action": SAFE_FALLBACK,
            "selected_decision": SAFE_FALLBACK,
            "tool": None,
            "identifier": None,
            "identifier_source": None,
            "required_information": [],
            "status": "fallback",
            "reason": "The request is outside supported capabilities.",
        }

    tool_name = _TOOL_BY_INTENT.get(canonical_intent)
    if tool_name:
        identifier = extract_identifier(question, tool_name)
        identifier_source = "current_message" if identifier else None
        if not identifier and identifier_context:
            identifier = extract_identifier(identifier_context, tool_name)
            identifier_source = "conversation_context" if identifier else None
        action = BUSINESS_TOOL if identifier else CLARIFY
        return {
            "intent": canonical_intent,
            "action": action,
            "selected_decision": action,
            "tool": tool_name,
            "identifier": identifier,
            "identifier_source": identifier_source,
            "required_information": [] if identifier else [
                "order ID" if tool_name == "order_lookup" else "account ID"
            ],
            "status": "ready" if identifier else "needs_information",
            "reason": (
                "A valid identifier is available for the selected tool."
                if identifier
                else "The selected business tool requires an identifier."
            ),
        }

    if canonical_intent == "policy_or_faq":
        return {
            "intent": canonical_intent,
            "action": RAG,
            "selected_decision": RAG,
            "tool": None,
            "identifier": None,
            "identifier_source": None,
            "required_information": [],
            "status": "ready",
            "reason": "The request matches a knowledge-base support intent.",
        }

    return {
        "intent": canonical_intent,
        "action": SAFE_FALLBACK,
        "selected_decision": SAFE_FALLBACK,
        "tool": None,
        "identifier": None,
        "identifier_source": None,
        "required_information": [],
        "status": "fallback",
        "reason": "The request could not be mapped to a supported action.",
    }
