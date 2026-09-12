"""Protected, read-only Streamlit administration dashboard."""

import json
from pathlib import Path

import streamlit as st

from action_safety import ACTION_REGISTRY, CONTROLLED_ACTIONS, INSUFFICIENT_EVIDENCE, REQUIRES_HUMAN
from config import (
    ADMIN_PASSWORD_HASH,
    ADMIN_USERNAME,
    BUSINESS_API_BASE_URL,
    BUSINESS_PROVIDER,
    GOOGLE_API_KEY,
    INPUT_MAX_LENGTH,
    MAX_CONVERSATION_HISTORY,
    MAX_ORCHESTRATION_STEPS,
    RAG_RELEVANCE_THRESHOLD,
    validate_configuration,
)
from evaluate_agent import run_evaluation
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


def render_admin_area(context: TenantContext):
    """Render the protected dashboard and return whether admin mode is active."""

    st.sidebar.divider()
    admin_requested = st.sidebar.checkbox("Admin area", key="admin_requested")
    if not admin_requested:
        return False

    if not admin_access_allowed(st.session_state):
        st.subheader("Administrator sign in")
        st.caption("This area contains protected operational information.")
        with st.expander("Temporary configuration diagnostic"):
            st.json({
                "admin_username_present": bool(ADMIN_USERNAME),
                "admin_password_hash_present": bool(ADMIN_PASSWORD_HASH),
                "google_api_key_present": bool(GOOGLE_API_KEY),
                "password_hash_has_pbkdf2_prefix": ADMIN_PASSWORD_HASH.startswith(
                    "pbkdf2_sha256$"
                ),
            })
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
    readiness = production_readiness(context)
    health = health_status(context)
    if configuration_errors:
        st.error("Configuration needs attention. Sensitive values are not displayed.")

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

    st.subheader("Integration")
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
    try:
        evaluation = run_evaluation()
        st.dataframe([
            {"Metric": "Controlled scenarios", "Value": evaluation["total"]},
            {"Metric": "Passed", "Value": evaluation["passed"]},
            {"Metric": "Evaluation status", "Value": "Passed" if not evaluation["failed"] else "Review required"},
        ], hide_index=True, use_container_width=True)
        st.caption("Controlled evaluation suite, not a statistically representative production benchmark.")
    except (OSError, ValueError, json.JSONDecodeError):
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
