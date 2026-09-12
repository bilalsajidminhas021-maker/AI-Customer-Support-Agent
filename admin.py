"""Protected, read-only Streamlit administration dashboard."""

import json
from pathlib import Path

import streamlit as st

from action_safety import ACTION_REGISTRY, CONTROLLED_ACTIONS, INSUFFICIENT_EVIDENCE, REQUIRES_HUMAN
from config import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    BUSINESS_API_BASE_URL,
    BUSINESS_EMAIL,
    BUSINESS_DATA_PATH,
    BUSINESS_NAME,
    BUSINESS_PROVIDER,
    ESCALATION_ENABLED,
    INPUT_MAX_LENGTH,
    KNOWLEDGE_BASE_PATH,
    MAX_CONVERSATION_HISTORY,
    MAX_ORCHESTRATION_STEPS,
    RAG_RELEVANCE_THRESHOLD,
    validate_configuration,
)
from evaluate_agent import run_evaluation
from knowledge import knowledge_base_status
from operational import health_status, production_readiness
from security import authenticate_admin
from tenant_context import TenantContext
from tools import CASE_STORAGE


def admin_access_allowed(session_state):
    """Return whether a session has authenticated admin access."""

    return session_state.get("admin_authenticated") is True


def admin_login(session_state, username, password):
    """Set only a boolean authentication marker in Streamlit session state."""

    authenticated = authenticate_admin(
        username,
        password,
        ADMIN_USERNAME,
        ADMIN_PASSWORD_HASH,
    )
    session_state["admin_authenticated"] = authenticated
    return authenticated


def admin_logout(session_state):
    session_state["admin_authenticated"] = False
    session_state.pop("admin_password", None)


def _recent_safe_audit_events(context):
    events = []
    try:
        with context.audit_path.open("r", encoding="utf-8") as audit_file:
            for line in audit_file.readlines()[-20:]:
                event = json.loads(line)
                if event.get("tenant_id", "default") != context.tenant_id:
                    continue
                events.append({
                    key: event.get(key)
                    for key in (
                        "timestamp", "event_type", "action", "status",
                        "safe_action", "eligibility", "operation",
                        "duration_ms", "timings_ms",
                    )
                })
    except (OSError, json.JSONDecodeError):
        return []
    return events


def _case_counts():
    cases = CASE_STORAGE.list_cases()
    counts = {"Total": len(cases)}
    for status in ("OPEN", "IN_REVIEW", "WAITING_FOR_CUSTOMER", "RESOLVED", "CLOSED"):
        counts[status.replace("_", " ").title()] = sum(
            case.get("lifecycle_status") == status for case in cases
        )
    return counts


def _readiness_status(ok, *, warning=False):
    if ok:
        return "READY"
    return "WARNING" if warning else "NOT READY"


def build_go_live_checklist(readiness, knowledge, evaluation):
    """Build safe, state-derived go-live findings for administrators."""

    checks = readiness["checks"]
    knowledge_documents_available = knowledge["pdf_count"] > 0
    evaluation_ready = bool(evaluation and not evaluation.get("failed"))
    return [
        {
            "Item": "Business configuration",
            "Status": _readiness_status(checks["configuration"]["ok"]),
            "Details": "Environment configuration is valid." if checks["configuration"]["ok"] else "Review the configuration settings shown above.",
        },
        {
            "Item": "Knowledge base",
            "Status": _readiness_status(knowledge_documents_available, warning=knowledge["directory_exists"]),
            "Details": f"{knowledge['pdf_count']} PDF document(s) available." if knowledge_documents_available else "Add approved PDF documents to the configured knowledge-base directory.",
        },
        {
            "Item": "Knowledge index",
            "Status": _readiness_status(knowledge["index_available"], warning=knowledge_documents_available),
            "Details": "The current session has a searchable index." if knowledge["index_available"] else "Open the customer workflow and build the index from readable PDFs.",
        },
        {
            "Item": "Business/order data",
            "Status": _readiness_status(checks["business_data"]["usable"]),
            "Details": "Business data is available." if checks["business_data"]["usable"] else "Provide the configured business data file.",
        },
        {
            "Item": "Order integration",
            "Status": _readiness_status(checks["provider"]["ok"]),
            "Details": checks["provider"]["message"].capitalize() + ".",
        },
        {
            "Item": "Security configuration",
            "Status": _readiness_status(checks["api_key"]["ok"] and checks["admin_security"]["ok"]),
            "Details": "Required security configuration is present." if checks["api_key"]["ok"] and checks["admin_security"]["ok"] else "Configure the required application and administrator security settings.",
        },
        {
            "Item": "Admin authentication",
            "Status": _readiness_status(checks["admin_security"]["ok"]),
            "Details": "Administrator authentication is configured." if checks["admin_security"]["ok"] else "Configure the administrator password hash in deployment secrets.",
        },
        {
            "Item": "Audit/observability",
            "Status": _readiness_status(checks["audit_storage"]["usable"]),
            "Details": "Audit storage is configured." if checks["audit_storage"]["usable"] else "Configure an available audit storage path.",
        },
        {
            "Item": "Evaluation readiness",
            "Status": _readiness_status(evaluation_ready, warning=evaluation is not None),
            "Details": "Controlled evaluation passed." if evaluation_ready else "Run and review the controlled evaluation suite.",
        },
        {
            "Item": "Deployment readiness",
            "Status": _readiness_status(readiness["ready"]),
            "Details": "All required operational checks passed." if readiness["ready"] else "Resolve the failed readiness checks shown below.",
        },
    ]


def render_admin_area(context: TenantContext):
    """Render the protected dashboard and return whether admin mode is active."""

    st.sidebar.divider()
    admin_requested = st.sidebar.checkbox("Admin area", key="admin_requested")
    if not admin_requested:
        return False

    if not admin_access_allowed(st.session_state):
        st.subheader("Administrator sign in")
        st.caption("This area contains protected operational information.")
        username = st.text_input("Username", key="admin_username")
        password = st.text_input("Password", type="password", key="admin_password")
        if st.button("Sign in", key="admin_sign_in"):
            if admin_login(st.session_state, username, password):
                st.rerun()
            st.error("Invalid administrator credentials.")
        return True

    st.sidebar.success("Administrator authenticated")
    if st.sidebar.button("Log out", key="admin_log_out"):
        admin_logout(st.session_state)
        st.rerun()

    st.header("Business administration")
    configuration_errors = validate_configuration()
    knowledge = knowledge_base_status(
        context.knowledge_base_path,
        index_available=st.session_state.get("knowledge_index_available", False),
    )
    readiness = production_readiness(
        context,
        knowledge_index_available=knowledge["index_available"],
    )
    health = health_status(
        context,
        knowledge_index_available=knowledge["index_available"],
    )
    evaluation = None
    try:
        evaluation = run_evaluation()
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    if configuration_errors:
        st.error("Configuration needs attention. Sensitive values are not displayed.")

    st.subheader("Business Setup")
    st.dataframe([
        {"Setting": "Business name", "Value": BUSINESS_NAME},
        {"Setting": "Business email", "Value": BUSINESS_EMAIL or "Not configured"},
        {"Setting": "Support hours", "Value": context.support_hours},
        {"Setting": "Escalation", "Value": "Enabled" if ESCALATION_ENABLED else "Disabled"},
        {"Setting": "Knowledge base path", "Value": KNOWLEDGE_BASE_PATH},
        {"Setting": "Business data path", "Value": BUSINESS_DATA_PATH},
        {"Setting": "Order provider", "Value": BUSINESS_PROVIDER},
        {"Setting": "Provider status", "Value": readiness["checks"]["provider"]["message"].capitalize()},
    ], hide_index=True, use_container_width=True)

    st.subheader("Knowledge Base")
    st.dataframe([
        {"Check": "Directory", "Status": "Ready" if knowledge["directory_exists"] else "Missing"},
        {"Check": "PDF documents", "Status": knowledge["pdf_count"]},
        {"Check": "Customer-policy PDFs", "Status": knowledge["customer_policy_pdf_count"]},
        {"Check": "Index/cache", "Status": "Available" if knowledge["index_available"] else "Not available"},
        {"Check": "Readiness", "Status": "Ready" if knowledge["ready"] else "Needs attention"},
    ], hide_index=True, use_container_width=True)
    if knowledge["document_names"]:
        st.caption("Available documents: " + ", ".join(knowledge["document_names"]))
    elif knowledge["directory_exists"]:
        st.info("No PDF documents are available in the configured knowledge-base directory.")
    else:
        st.warning("The configured knowledge-base directory is not available.")

    st.subheader("Integrations")
    provider = readiness["checks"]["provider"]
    st.dataframe([
        {"Setting": "Provider type", "Value": provider["provider_type"]},
        {"Setting": "Configuration status", "Value": provider["message"].capitalize()},
        {"Setting": "Endpoint", "Value": "Configured" if provider["endpoint_configured"] else "Not configured"},
        {"Setting": "Authentication", "Value": "Configured" if provider["authentication_configured"] else "Not configured"},
        {"Setting": "Readiness", "Value": "READY" if provider["ok"] else "NOT READY"},
    ], hide_index=True, use_container_width=True)

    st.subheader("Production Readiness")
    readiness_rows = [
        {"Check": "Administrator authentication", "Status": _readiness_status(readiness["checks"]["admin_security"]["ok"])},
        {"Check": "Google/Gemini API", "Status": _readiness_status(readiness["checks"]["api_key"]["ok"])},
        {"Check": "Knowledge base", "Status": _readiness_status(readiness["checks"]["knowledge_base"]["usable"])},
        {"Check": "Knowledge index", "Status": _readiness_status(readiness["checks"]["knowledge_index"]["available"])},
        {"Check": "Business data", "Status": _readiness_status(readiness["checks"]["business_data"]["usable"])},
        {"Check": "Order provider", "Status": _readiness_status(readiness["checks"]["provider"]["ok"])},
        {"Check": "Audit logging", "Status": _readiness_status(readiness["checks"]["audit_storage"]["usable"])},
        {"Check": "Support case storage", "Status": _readiness_status(readiness["checks"]["case_storage"]["usable"])},
        {"Check": "Evaluation suite", "Status": _readiness_status(bool(evaluation and not evaluation.get("failed")), warning=evaluation is not None)},
    ]
    st.dataframe(readiness_rows, hide_index=True, use_container_width=True)
    st.metric("Overall readiness", "READY" if readiness["ready"] else "NOT READY")

    st.subheader("Go-Live Checklist")
    st.dataframe(
        build_go_live_checklist(readiness, knowledge, evaluation),
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Overview")
    overview = st.columns(5)
    overview[0].metric("Business", context.business_name)
    overview[1].metric("Tenant", context.tenant_id)
    overview[2].metric("System status", health["status"].title())
    overview[3].metric("Knowledge base", "Ready" if health["knowledge_base"] else "Missing")
    overview[4].metric("Integration", "Ready" if health["business_provider"] else "Needs setup")
    st.caption("Support hours: " + context.support_hours)

    st.subheader("Cases")
    counts = _case_counts()
    case_columns = st.columns(len(counts))
    for column, (label, value) in zip(case_columns, counts.items()):
        column.metric(label, value)

    st.subheader("AI & Safety")
    st.dataframe([
        {"Setting": "RAG relevance threshold", "Value": RAG_RELEVANCE_THRESHOLD},
        {"Setting": "Conversation history limit", "Value": MAX_CONVERSATION_HISTORY},
        {"Setting": "Orchestration step limit", "Value": MAX_ORCHESTRATION_STEPS},
        {"Setting": "Input length limit", "Value": INPUT_MAX_LENGTH},
        {"Setting": "Allowed actions", "Value": ", ".join(sorted(CONTROLLED_ACTIONS))},
        {"Setting": "Human-required actions", "Value": "CREATE_SUPPORT_CASE"},
        {"Setting": "Evidence behavior", "Value": INSUFFICIENT_EVIDENCE},
        {"Setting": "Action registry", "Value": str(ACTION_REGISTRY)},
        {"Setting": "Human-required status", "Value": REQUIRES_HUMAN},
    ], hide_index=True, use_container_width=True)

    st.subheader("Integration details")
    st.dataframe([
        {"Setting": "Provider type", "Value": BUSINESS_PROVIDER},
        {"Setting": "Connection status", "Value": "Configured" if readiness["checks"]["provider"]["ok"] else "Needs setup"},
        {"Setting": "External endpoint configured", "Value": bool(BUSINESS_API_BASE_URL) if BUSINESS_PROVIDER == "rest" else False},
    ], hide_index=True, use_container_width=True)

    st.subheader("Observability")
    events = _recent_safe_audit_events(context)
    if events:
        st.dataframe(events, hide_index=True, use_container_width=True)
    else:
        st.info("No recent structured events are available.")

    st.subheader("Evaluation")
    if evaluation is not None:
        st.dataframe([
            {"Metric": "Controlled scenarios", "Value": evaluation["total"]},
            {"Metric": "Passed", "Value": evaluation["passed"]},
            {"Metric": "Evaluation status", "Value": "Passed" if not evaluation["failed"] else "Review required"},
        ], hide_index=True, use_container_width=True)
        st.caption("Controlled evaluation suite, not a statistically representative production benchmark.")
    else:
        st.warning("The controlled evaluation report is unavailable.")

    with st.expander("Production readiness details"):
        st.json({
            "ready": readiness["ready"],
            "failed_checks": readiness["failed_checks"],
            "health": health,
            "knowledge_base_path_exists": Path(context.knowledge_base_path).is_dir(),
            "business_data_path_exists": Path(context.business_data_path).is_file(),
        })
    return True
