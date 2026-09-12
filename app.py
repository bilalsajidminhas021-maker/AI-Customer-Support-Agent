import logging
import traceback

import streamlit as st
from pathlib import Path
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from agent import SupportAgent
from action_safety import (
    ALLOWED,
    ACCOUNT_LOOKUP,
    BILLING_LOOKUP,
    CREATE_SUPPORT_CASE,
    INSUFFICIENT_EVIDENCE,
    NOT_ALLOWED,
    ORDER_LOOKUP,
    POLICY_LOOKUP,
    REQUIRES_HUMAN,
    evaluate_action,
)
from audit import ExecutionTrace, log_audit_event
from config import (
    BUSINESS_EMAIL,
    BUSINESS_NAME,
    ESCALATION_ENABLED,
    GOOGLE_API_KEY,
    KNOWLEDGE_BASE_PATH,
    SUPPORT_HOURS,
    INPUT_MAX_LENGTH,
    MAX_CONVERSATION_HISTORY,
    MAX_ORCHESTRATION_STEPS,
)
from admin import render_admin_area
from security import validate_customer_input
from tenant_context import DEFAULT_TENANT_CONTEXT
from decision import (
    BUSINESS_TOOL,
    CLARIFY,
    ESCALATE,
    RAG,
    SAFE_FALLBACK,
    decide_action,
    detect_deterministic_route,
    detect_deterministic_intent,
    extract_identifier,
)
from tools import (
    classify_support_case,
    create_support_case_once,
    execute_tool,
    validate_support_case_result,
    validate_tool_result,
)
from orchestration import ControlledOrchestrator
from knowledge import classify_document, retrieve_knowledge


MAX_HISTORY_MESSAGES = MAX_CONVERSATION_HISTORY
MAX_CONTEXT_MESSAGES = 8
MAX_CONTEXT_MESSAGE_LENGTH = 800


def map_decision_to_safe_action(decision_action, selected_tool=None):
    """Map only deterministic decisions to controlled safe operations."""

    if decision_action == BUSINESS_TOOL:
        return {
            "order_lookup": ORDER_LOOKUP,
            "billing_lookup": BILLING_LOOKUP,
            "account_lookup": ACCOUNT_LOOKUP,
        }.get(selected_tool)
    if decision_action == RAG:
        return POLICY_LOOKUP
    if decision_action == ESCALATE:
        return CREATE_SUPPORT_CASE
    return None


def evaluate_decision_safety(
    decision_action,
    selected_tool=None,
    identifier=None,
    required_information=None,
):
    """Evaluate a deterministic decision without executing its operation."""

    safe_action = map_decision_to_safe_action(decision_action, selected_tool)
    if safe_action is None:
        eligibility = NOT_ALLOWED if decision_action == SAFE_FALLBACK else None
        return {
            "action": None,
            "eligibility": eligibility,
            "reason": "No executable business action is selected.",
            "required_information": list(required_information or []),
            "recommended_action": None,
        }

    return evaluate_action(
        safe_action,
        identifier=identifier,
        required_information=required_information,
    )


def add_conversation_message(role, content):
    """Store bounded, display-safe conversation messages in session state."""

    if not content:
        return

    history = st.session_state.setdefault("conversation_history", [])
    history.append({
        "role": role,
        "content": str(content).strip(),
    })
    del history[:-MAX_HISTORY_MESSAGES]


def get_conversation_context():
    """Return only a small recent context window for model prompts."""

    history = st.session_state.get("conversation_history", [])
    context_lines = []

    for message in history[-MAX_CONTEXT_MESSAGES:]:
        role = message.get("role", "unknown").upper()
        content = message.get("content", "")
        content = str(content)[:MAX_CONTEXT_MESSAGE_LENGTH]
        context_lines.append(f"{role}: {content}")

    return "\n".join(context_lines)


def get_recent_identifier(tool_name):
    """Find the most recent valid identifier for a selected business tool."""

    history = st.session_state.get("conversation_history", [])

    for message in reversed(history):
        identifier = extract_identifier(
            message.get("content", ""),
            tool_name
        )
        if identifier:
            return identifier

    return None


def handle_escalation_case(
    question,
    conversation_context,
    intent,
    trace,
    evidence=None,
    required_information=None,
):
    """Create and describe one controlled support case for an escalation."""

    fallback = (
        "Your request requires human support. Please contact the support team."
    )
    case_fields = classify_support_case(question, intent=intent)

    if not ESCALATION_ENABLED:
        trace.update(
            response_method="escalation_disabled",
            case_creation_outcome="disabled",
            status="completed",
        )
        return fallback, False

    try:
        result = create_support_case_once(
            st.session_state,
            customer_request=question,
            conversation_context=conversation_context,
            evidence=evidence,
            required_information=required_information,
            **case_fields,
        )
        validation = validate_support_case_result(result)
    except Exception:
        validation = {
            "valid": False,
            "status": "failure",
            "message": "The support case could not be created right now.",
            "data": None,
        }
        result = {}

    if not validation["valid"]:
        trace.update(
            response_method="human_escalation",
            case_category=case_fields["category"],
            case_priority=case_fields["priority"],
            case_creation_outcome="failed",
            status="completed",
            error="Support case creation failed.",
        )
        return fallback, False

    trace.update(
        response_method="human_escalation_case",
        case_id=result.get("case_id"),
        case_status=result.get("status"),
        case_priority=result.get("priority"),
        case_category=result.get("category"),
        recommended_action=result.get("recommended_action"),
        evidence_status=result.get("evidence", {}).get("status"),
        case_creation_outcome=(
            "duplicate_reused"
            if result.get("duplicate")
            else "created"
        ),
        status="completed",
    )
    answer = (
        "Your request has been sent to human support. "
        f"Case ID: {result['case_id']}. Status: {result['status']}."
    )
    return answer, True


def render_conversation_history():
    """Render the current session conversation without persisting it."""

    history = st.session_state.get("conversation_history", [])
    if not history:
        return

    st.subheader("💬 Conversation")
    for message in history:
        with st.chat_message(message["role"]):
            st.write(message["content"])


def retrieve_rag_evidence(
    vector_db,
    retrieval_query,
    category=None,
    policy_only=False,
):
    """Retrieve general-support evidence through the shared knowledge layer."""

    return retrieve_knowledge(
        vector_db,
        retrieval_query,
        category=category,
        policy_only=policy_only,
        top_k=15,
        max_chunks=12,
    )


st.session_state.setdefault("conversation_history", [])
st.session_state.setdefault("pending_tool", None)
st.session_state.setdefault("support_case_fingerprints", {})


def discover_knowledge_base_files(knowledge_base_path):
    """Return PDF knowledge documents from the configured directory."""

    directory = Path(knowledge_base_path).expanduser()

    if not directory.is_dir():
        return []

    try:
        return sorted(
            (
                path for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() == ".pdf"
            ),
            key=lambda path: path.name.lower()
        )
    except OSError:
        return []


def extract_pdf_documents(sources):
    """Extract and chunk readable PDFs from uploaded or local sources."""

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50
    )
    documents = []

    for source_name, source in sources:
        try:
            reader = PdfReader(source)
        except Exception:
            st.warning(
                f"Could not read {source_name}. "
                "Please provide a valid PDF file."
            )
            log_audit_event(
                status="failed",
                message=f"Could not read PDF: {source_name}."
            )
            continue

        document_metadata = classify_document(source_name)
        source_document_count = len(documents)

        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text()
            except Exception:
                text = None

            if not text or not text.strip():
                continue

            for chunk_index, chunk in enumerate(
                text_splitter.split_text(text),
                start=1,
            ):
                metadata = dict(document_metadata)
                metadata.update({
                    "page": page_number,
                    "chunk_index": chunk_index,
                })
                documents.append(
                    Document(
                        page_content=chunk,
                        metadata=metadata,
                    )
                )

        if len(documents) == source_document_count:
            st.warning(
                f"Could not extract readable text from {source_name}"
            )
            log_audit_event(
                status="failed",
                message=f"PDF contained no readable text: {source_name}."
            )

    return documents


@st.cache_resource
def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )


@st.cache_resource
def create_vector_db(document_payload):
    documents = [
        Document(page_content=content, metadata=dict(metadata))
        for content, metadata in document_payload
    ]
    return FAISS.from_documents(documents, get_embeddings())


def get_document_payload(documents):
    return tuple(
        (
            document.page_content,
            tuple(sorted(document.metadata.items())),
        )
        for document in documents
    )


# --------------------------------------------------
# Load environment variables
# --------------------------------------------------

# --------------------------------------------------
# Streamlit configuration
# --------------------------------------------------

st.set_page_config(
    page_title="AI Automation Agent",
    page_icon="🤖",
    layout="wide"
)

st.title("AI Customer Support")
st.caption(
    "Get answers from approved business information, check your order, "
    "or connect with human support when a request needs review."
)

if render_admin_area(DEFAULT_TENANT_CONTEXT):
    st.stop()

st.write(
    "Upload documents, ask questions, compare information, "
    "and analyze your documents using AI-powered RAG."
)


# --------------------------------------------------
# Check API key
# --------------------------------------------------

if not GOOGLE_API_KEY:

    log_audit_event(
        status="failed",
        error="GOOGLE_API_KEY is not configured."
    )
    st.error(
        "Google API key is not configured. "
        "Please add GOOGLE_API_KEY to your .env file."
    )

    st.stop()


# --------------------------------------------------
# Create agent
# --------------------------------------------------

try:
    agent = SupportAgent(GOOGLE_API_KEY)
except Exception:
    log_audit_event(
        status="failed",
        error="Unexpected error while initializing the support agent."
    )
    st.error(
        "The support agent could not be started. "
        "Please try again later."
    )
    st.stop()


# --------------------------------------------------
# Sidebar
# --------------------------------------------------

with st.sidebar:

    st.header("Support options")

    if st.button("Clear conversation"):
        st.session_state["conversation_history"] = []
        st.session_state["pending_tool"] = None
        st.session_state["support_case_fingerprints"] = {}
        st.rerun()

    st.markdown("""
    - Ask questions about products and policies
    - Check orders, invoices, and account details
    - Compare uploaded documents
    - Receive source-backed answers when available
    - Send complex requests to human support
    """)

    st.divider()

    st.caption(f"Support hours: {SUPPORT_HOURS}")
    if BUSINESS_EMAIL:
        st.caption(f"Contact: {BUSINESS_EMAIL}")


# --------------------------------------------------
# Knowledge base and optional document uploads
# --------------------------------------------------

st.subheader("Knowledge base")

knowledge_base_files = discover_knowledge_base_files(
    KNOWLEDGE_BASE_PATH
)

if knowledge_base_files:
    st.info(
        f"Approved support information is ready ({len(knowledge_base_files)} document(s))."
    )
else:
    st.warning(
        "No approved support documents are currently available. "
        "You can upload a PDF below to continue."
    )

st.subheader("Add support documents")

uploaded_files = st.file_uploader(
    "Upload one or more additional PDF documents",
    type=["pdf"],
    accept_multiple_files=True
)

if not uploaded_files and not knowledge_base_files:

    st.info(
        "Add at least one readable PDF to the knowledge base or upload one "
        "to begin."
    )


# --------------------------------------------------
# Process knowledge-base and uploaded documents
# --------------------------------------------------

if uploaded_files or knowledge_base_files:

    if uploaded_files:
        st.success(
            f"{len(uploaded_files)} additional document(s) uploaded "
            "successfully! ✅"
        )

    if uploaded_files:
        st.write("### Additional Uploaded Documents")

        for file in uploaded_files:
            st.write(f"📄 {file.name}")

    sources = [
        (path.name, path)
        for path in knowledge_base_files
    ]
    sources.extend(
        (uploaded_file.name, uploaded_file)
        for uploaded_file in uploaded_files
    )
    documents = extract_pdf_documents(sources)


    # --------------------------------------------------
    # Check extracted documents
    # --------------------------------------------------

    if not documents:

        log_audit_event(
            status="failed",
            message="No readable text was found in the uploaded PDFs."
        )
        st.error(
            "No readable text was found in the uploaded documents."
        )

        st.stop()


    st.info(
        f"📚 Created {len(documents)} text chunks "
        f"from {len(sources)} document(s)."
    )


    document_payload = get_document_payload(documents)

    with st.spinner("Preparing documents for search..."):
        try:
            vector_db = create_vector_db(document_payload)
        except Exception:
            log_audit_event(
                status="failed",
                error="Unexpected error while preparing document search."
            )
            st.error(
                "The documents could not be prepared for search. "
                "Please try again later."
            )
            st.stop()


    st.success(
        "Documents indexed successfully! ✅"
    )


    # --------------------------------------------------
    # Question section
    # --------------------------------------------------

    render_conversation_history()

    st.subheader("💬 Ask Your AI Agent")

    question = st.text_input(
        "How can we help?",

        placeholder=(
            "Example: Where is my order ORD-1001?"
        )
    )


    # --------------------------------------------------
    # Run agent
    # --------------------------------------------------

    if st.button(
        "🚀 Run Agent",
        type="primary"
    ):

        # --------------------------------------------------
        # Validate question
        # --------------------------------------------------

        question, input_error = validate_customer_input(
            question,
            max_length=INPUT_MAX_LENGTH,
        )
        if input_error:

            st.warning(input_error)

            st.stop()

        add_conversation_message("user", question)
        conversation_context = get_conversation_context()
        pending_tool = st.session_state.get("pending_tool")
        trace = ExecutionTrace(question)
        deterministic_decision = None


        # ==================================================
        # LEVEL 1 — AGENT ACTION ROUTING
        # ==================================================

        with st.spinner(
            "🤖 Agent is analyzing your request..."
        ):

            try:
                action = detect_deterministic_route(question)
                if not action:
                    deterministic_intent = detect_deterministic_intent(
                        question
                    )
                    if deterministic_intent:
                        deterministic_tools = {
                            "order_tracking": "order_lookup",
                            "billing": "billing_lookup",
                            "account": "account_lookup",
                        }
                        deterministic_tool = deterministic_tools.get(
                            deterministic_intent
                        )
                        identifier_context = get_recent_identifier(
                            deterministic_tool or ""
                        )
                        deterministic_decision = decide_action(
                            deterministic_intent,
                            question,
                            escalation_enabled=ESCALATION_ENABLED,
                            identifier_context=identifier_context,
                        )
                        action = "support"
                    else:
                        action = agent.decide_action(
                            question,
                            conversation_context=conversation_context
                        )
            except Exception as exc:
                error_type = type(exc).__name__
                logging.error(
                    "Action routing failed: exception_type=%s "
                    "exception_message=%s\n%s",
                    error_type,
                    str(exc),
                    traceback.format_exc()
                )
                trace.update(
                    status="failed",
                    error="Unexpected error during action routing.",
                    error_type=error_type
                )
                trace.write()
                log_audit_event(
                    question=question,
                    status="failed",
                    error="Unexpected error during action routing.",
                    error_type=error_type
                )
                st.error(
                    "The request could not be processed right now. "
                    "Please try again later."
                )
                st.stop()

        trace.update(action=action)


        st.info(
            "Your request is being handled."
        )


        # ==================================================
        # COMPARE
        # ==================================================

        if action == "compare":

            st.info(
                "🔄 Agent selected: Document Comparison"
            )


            # --------------------------------------------------
            # Retrieve documents
            # --------------------------------------------------

            with st.spinner(
                "🔎 Searching across uploaded documents..."
            ):

                try:
                    retrieval_query = question
                    if conversation_context:
                        retrieval_query = (
                            f"{question}\n\n"
                            f"Recent conversation context:\n"
                            f"{conversation_context}"
                        )
                    retrieval_result = retrieve_rag_evidence(
                        vector_db,
                        retrieval_query,
                    )
                    retrieved_docs = retrieval_result["documents"]
                except Exception:
                    trace.update(
                        action=action,
                        response_method="comparison",
                        status="failed",
                        error="Unexpected error during document retrieval."
                    )
                    trace.write()
                    log_audit_event(
                        question=question,
                        action=action,
                        response_method="comparison",
                        status="failed",
                        error="Unexpected error during document retrieval."
                    )
                    st.error(
                        "The documents could not be searched right now. "
                        "Please try again later."
                    )
                    st.stop()


            # --------------------------------------------------
            # Group retrieved chunks by source
            # --------------------------------------------------

            documents_by_source = {}

            for doc in retrieved_docs:

                source = doc.metadata.get(
                    "source",
                    "Unknown document"
                )

                if source not in documents_by_source:

                    documents_by_source[source] = []

                documents_by_source[source].append(
                    doc
                )


            # --------------------------------------------------
            # Select chunks from each source
            # --------------------------------------------------

            selected_docs = []

            for source, docs in documents_by_source.items():

                selected_docs.extend(
                    docs[:4]
                )


            selected_docs = selected_docs[:12]

            retrieved_docs = selected_docs


            # --------------------------------------------------
            # Identify sources
            # --------------------------------------------------

            retrieved_sources = []

            for doc in retrieved_docs:

                source = doc.metadata.get(
                    "source",
                    "Unknown document"
                )

                if source not in retrieved_sources:

                    retrieved_sources.append(
                        source
                    )


            # --------------------------------------------------
            # Display retrieved sources
            # --------------------------------------------------

            st.write(
                "### 🔎 Retrieved Information"
            )

            if retrieved_sources:

                st.success(
                    "Information retrieved from:"
                )

                for source in retrieved_sources:

                    st.write(
                        f"📄 {source}"
                    )

            else:

                st.warning(
                    "No document source was identified."
                )


            # --------------------------------------------------
            # Build source-aware context
            # --------------------------------------------------

            context_parts = []

            for doc in retrieved_docs:

                source = doc.metadata.get(
                    "source",
                    "Unknown document"
                )

                context_parts.append(
                    f"""
[SOURCE DOCUMENT: {source}]

{doc.page_content}
"""
                )


            context = "\n\n".join(
                context_parts
            )


            # --------------------------------------------------
            # Generate comparison
            # --------------------------------------------------

            with st.spinner(
                "🧠 Agent is comparing the retrieved evidence..."
            ):

                try:
                    answer = agent.compare_from_context(
                        question,
                        context
                    )
                except Exception:
                    trace.update(
                        action=action,
                        response_method="comparison",
                        status="failed",
                        error="Unexpected error during document comparison."
                    )
                    trace.write()
                    log_audit_event(
                        question=question,
                        action=action,
                        response_method="comparison",
                        status="failed",
                        error="Unexpected error during document comparison."
                    )
                    st.error(
                        "The document comparison could not be completed. "
                        "Please try again later."
                    )
                    st.stop()


            # --------------------------------------------------
            # Display response
            # --------------------------------------------------

            st.subheader(
                "🤖 Agent Response"
            )

            st.write(
                answer
            )
            add_conversation_message("assistant", answer)

            trace.update(
                response_method="comparison",
                status="completed"
            )
            trace.write()

            st.stop()


        # ==================================================
        # REJECT
        # ==================================================

        if action == "reject":

            answer = (
                "This request is outside the current capabilities of the "
                "agent."
            )
            add_conversation_message("assistant", answer)

            trace.update(
                response_method="rejected",
                status="completed"
            )
            trace.write()

            st.info(answer)

            st.stop()


        # ==================================================
        # ESCALATE
        # ==================================================

        if action == "escalate":

            try:
                safety_result = evaluate_action(CREATE_SUPPORT_CASE)
            except Exception:
                safety_result = {
                    "action": CREATE_SUPPORT_CASE,
                    "eligibility": NOT_ALLOWED,
                    "reason": "The safety check could not be completed.",
                    "required_information": [],
                    "recommended_action": None,
                }
            trace.update(
                safe_action=safety_result["action"],
                eligibility=safety_result["eligibility"],
                safety_reason=safety_result["reason"],
            )
            log_audit_event(
                question=question,
                action=action,
                safe_action=safety_result["action"],
                eligibility=safety_result["eligibility"],
                safety_reason=safety_result["reason"],
                status="completed",
            )
            if safety_result["eligibility"] != REQUIRES_HUMAN:
                message = (
                    "Your request requires human support, but the safety "
                    "check could not be completed. Please contact support."
                )
                trace.update(
                    response_method="safe_fallback",
                    status="failed",
                    error="Support-case safety evaluation failed.",
                )
                log_audit_event(
                    question=question,
                    action=action,
                    safe_action=safety_result["action"],
                    eligibility=safety_result["eligibility"],
                    safety_reason=safety_result["reason"],
                    response_method="safe_fallback",
                    status="failed",
                    error="Support-case safety evaluation failed.",
                )
                add_conversation_message("assistant", message)
                trace.write()
                st.warning(message)
                st.stop()

            with st.spinner("🚨 Preparing support case..."):
                answer, case_created = handle_escalation_case(
                    question,
                    conversation_context,
                    intent=None,
                    trace=trace,
                )


            st.subheader(
                "🚨 Human Support Escalation"
            )

            if case_created:
                st.success(answer)
            else:
                st.warning(answer)
            add_conversation_message("assistant", answer)
            trace.write()

            st.stop()


        # ==================================================
        # SUPPORT
        # ==================================================

        if action == "support":

            # --------------------------------------------------
            # LEVEL 2 — CUSTOMER INTENT DETECTION
            # --------------------------------------------------

            with st.spinner(
                "🎯 Detecting customer-support intent..."
            ):

                if deterministic_decision:
                    intent = deterministic_decision["intent"]
                else:
                    try:
                        intent = agent.detect_intent(
                            question,
                            conversation_context=conversation_context
                        )
                    except Exception:
                        trace.update(
                            action=action,
                            status="failed",
                            error="Unexpected error during intent detection."
                        )
                        trace.write()
                        log_audit_event(
                            question=question,
                            action=action,
                            status="failed",
                            error="Unexpected error during intent detection."
                        )
                        st.error(
                            "We could not determine how to handle your request. "
                            "Please try again later."
                        )
                        st.stop()

            trace.update(intent=intent)

            if deterministic_decision:
                decision = deterministic_decision
            else:
                pending_intents = {
                    "order_lookup": "order_tracking",
                    "billing_lookup": "billing",
                    "account_lookup": "account",
                }
                pending_identifier = extract_identifier(
                    question,
                    pending_tool or ""
                )
                if pending_identifier and pending_tool in pending_intents:
                    intent = pending_intents[pending_tool]

                context_tools = {
                    "order_issue": "order_lookup",
                    "order_tracking": "order_lookup",
                    "billing": "billing_lookup",
                    "account_help": "account_lookup",
                    "account": "account_lookup",
                }
                identifier_context = get_recent_identifier(
                    context_tools.get(intent, pending_tool or "")
                )

                decision = decide_action(
                    intent,
                    question,
                    escalation_enabled=ESCALATION_ENABLED,
                    routed_action=action,
                    identifier_context=identifier_context,
                )
            intent = decision["intent"]
            decision_action = decision["action"]
            selected_tool = decision["tool"]
            identifier = decision["identifier"]
            trace.update(
                intent=intent,
                action=decision_action,
                tool=selected_tool,
                selected_decision=decision["selected_decision"],
                identifier_source=decision["identifier_source"],
                required_information=decision["required_information"],
                decision_status=decision["status"],
                decision_reason=decision["reason"],
            )

            if decision_action in {BUSINESS_TOOL, RAG, ESCALATE}:
                try:
                    safety_result = evaluate_decision_safety(
                        decision_action,
                        selected_tool=selected_tool,
                        identifier=identifier,
                        required_information=decision["required_information"],
                    )
                except Exception:
                    safety_result = {
                        "action": None,
                        "eligibility": NOT_ALLOWED,
                        "reason": "The safety check could not be completed.",
                        "required_information": [],
                        "recommended_action": None,
                    }
                trace.update(
                    safe_action=safety_result["action"],
                    eligibility=safety_result["eligibility"],
                    safety_reason=safety_result["reason"],
                )
                log_audit_event(
                    question=question,
                    action=action,
                    intent=intent,
                    selected_tool=selected_tool,
                    safe_action=safety_result["action"],
                    eligibility=safety_result["eligibility"],
                    safety_reason=safety_result["reason"],
                    status="completed",
                )
                expected_eligibility = (
                    REQUIRES_HUMAN
                    if decision_action == ESCALATE
                    else ALLOWED
                )
                if safety_result["eligibility"] != expected_eligibility:
                    message = (
                        "I could not safely process that request. "
                        "Please provide the required information or contact "
                        "human support."
                    )
                    add_conversation_message("assistant", message)
                    trace.update(
                        response_method="safe_fallback",
                        status="failed",
                        error="Action safety evaluation blocked execution.",
                    )
                    trace.write()
                    st.info(message)
                    st.stop()

            if decision_action == CLARIFY:
                identifier_label = (
                    "order ID such as ORD-1001"
                    if selected_tool == "order_lookup"
                    else "account ID such as ACC-1001"
                )
                message = (
                    f"Please provide your {identifier_label} so I can "
                    "check that information."
                )
                st.session_state["pending_tool"] = selected_tool
                add_conversation_message("assistant", message)
                trace.update(
                    response_method="clarification",
                    status="completed",
                    error="Required business-tool identifier was not provided."
                )
                trace.write()
                st.info(message)
                st.stop()

            if decision_action == SAFE_FALLBACK:
                message = (
                    "I’m not confident I can answer that reliably from the "
                    "available support capabilities."
                )
                add_conversation_message("assistant", message)
                trace.update(
                    response_method="safe_fallback",
                    status="completed"
                )
                trace.write()
                st.info(message)
                st.stop()

            if decision_action == ESCALATE:
                message, case_created = handle_escalation_case(
                    question,
                    conversation_context,
                    intent=intent,
                    trace=trace,
                )
                add_conversation_message("assistant", message)
                trace.write()
                if case_created:
                    st.success(message)
                else:
                    st.info(message)
                st.stop()

            # ==================================================
            # BUSINESS TOOL EXECUTION
            # ==================================================

            business_tools = [
                "order_lookup",
                "billing_lookup",
                "account_lookup"
            ]

            if selected_tool in business_tools:
                if not identifier:
                    log_audit_event(
                        question=question,
                        action=action,
                        intent=intent,
                        selected_tool=selected_tool,
                        tool_success=False,
                        status="failed",
                        message="Required business-tool identifier was not provided."
                    )
                    trace.update(
                        intent=intent,
                        tool=selected_tool,
                        tool_result="missing_identifier",
                        response_method="clarification",
                        status="completed",
                    )
                    trace.write()
                    st.session_state["pending_tool"] = selected_tool
                    add_conversation_message(
                        "assistant",
                        "Please provide the required identifier so I can "
                        "check that information."
                    )
                    st.info(
                        "Please provide the required identifier so I can "
                        "check that information."
                    )
                    st.stop()

                if selected_tool == "order_lookup":
                    def retrieve_order_policy(policy_question):
                        retrieval_query = policy_question
                        if conversation_context:
                            retrieval_query = (
                                f"{policy_question}\n\n"
                                f"Recent conversation context:\n"
                                f"{conversation_context}"
                            )
                        return retrieve_rag_evidence(
                            vector_db,
                            retrieval_query,
                            category="late_delivery",
                            policy_only=True,
                        )

                    def generate_order_response(response_question, context):
                        return agent.answer_from_context(
                            response_question,
                            context,
                            conversation_context=conversation_context
                        )

                    def format_order_response(order_data, order_identifier):
                        return (
                            f"Order {order_identifier}: {order_data['status']}. "
                            f"Expected delivery: {order_data['expected_delivery']}. "
                            f"Tracking number: {order_data['tracking_number']}."
                        )

                    orchestrator = ControlledOrchestrator(
                        max_steps=MAX_ORCHESTRATION_STEPS,
                        max_failures=1
                    )

                    with st.spinner(
                        "🛠️ Agent is completing the controlled order workflow..."
                    ):
                        workflow_result = orchestrator.run_order_workflow(
                            question=question,
                            identifier=identifier,
                            execute_tool=execute_tool,
                            validate_tool_result=validate_tool_result,
                            retrieve_policy=retrieve_order_policy,
                            generate_response=generate_order_response,
                            format_tool_response=format_order_response,
                        )

                    workflow_state = workflow_result.state
                    validation = workflow_result.tool_validation or {}
                    if validation.get("status") == "not_found":
                        st.session_state["pending_tool"] = None

                    trace.update(
                        intent=intent,
                        tool=selected_tool,
                        tool_result=validation.get("status"),
                        integration_provider=validation.get("provider_type"),
                        integration_status=validation.get("integration_status"),
                        response_method=workflow_result.response_method,
                        orchestration_run_id=workflow_state.run_id,
                        orchestration_steps=workflow_state.action_history,
                        orchestration_current_step=workflow_state.current_step,
                        orchestration_max_steps=workflow_state.max_steps,
                        orchestration_failure_count=workflow_state.failure_count,
                        orchestration_completion_status=(
                            workflow_state.completion_status
                        ),
                        orchestration_final_outcome=workflow_state.final_outcome,
                        orchestration_recovery=workflow_state.recovery,
                        validated_rag_sources=workflow_state.validated_rag_evidence,
                        status=(
                            "completed"
                            if workflow_state.final_outcome != "safe_fallback"
                            else "failed"
                        ),
                        error=(
                            workflow_state.recovery
                            if workflow_state.final_outcome == "safe_fallback"
                            else None
                        ),
                    )
                    trace.write()

                    st.write("### Order information")

                    if workflow_result.tool_data:
                        order_data = workflow_result.tool_data
                        st.success(
                            f"Order {identifier}: {order_data['status']}"
                        )
                        st.write(
                            f"📦 Expected delivery: "
                            f"{order_data['expected_delivery']}"
                        )
                        st.write(
                            f"🔎 Tracking number: "
                            f"{order_data['tracking_number']}"
                        )

                    if workflow_result.rag_evidence:
                        st.write("### Source information")
                        for source in workflow_result.rag_evidence.get(
                            "sources",
                            []
                        ):
                            st.write(f"📄 {source}")

                    if (
                        workflow_result.response_method == "business_tool"
                        and workflow_result.tool_data
                    ):
                        st.session_state["conversation_history"] = [
                            history_message
                            for history_message in st.session_state.get(
                                "conversation_history",
                                [],
                            )
                            if not (
                                history_message.get("role") == "assistant"
                                and history_message.get("content")
                                == "The business system returned malformed order data."
                            )
                        ]

                    answer = workflow_result.response
                    add_conversation_message("assistant", answer)
                    if workflow_state.final_outcome == "safe_fallback":
                        st.warning(answer)
                    else:
                        st.subheader("🤖 Agent Response")
                        st.write(answer)
                    st.stop()

                with st.spinner(
                    "🛠️ Agent is checking the business system..."
                ):

                    try:
                        tool_result = execute_tool(
                            selected_tool,
                            identifier
                        )
                    except Exception:
                        trace.update(
                            intent=intent,
                            tool=selected_tool,
                            tool_result="failure",
                            status="failed",
                            error="Unexpected error during business-tool execution."
                        )
                        trace.write()
                        log_audit_event(
                            question=question,
                            action=action,
                            intent=intent,
                            selected_tool=selected_tool,
                            tool_success=False,
                            status="failed",
                            error="Unexpected error during business-tool execution."
                        )
                        st.error(
                            "The requested customer information could not be "
                            "retrieved right now. Please try again later."
                        )
                        st.stop()

                validation = validate_tool_result(
                    selected_tool,
                    tool_result
                )
                validation_data = validation.get("data") if isinstance(validation, dict) else None
                tool_success = validation["status"] == "success"
                if validation["valid"] and validation["status"] == "not_found":
                    st.session_state["pending_tool"] = None
                trace.update(
                    intent=intent,
                    tool=selected_tool,
                    tool_result=validation["status"],
                    integration_provider=validation.get("provider_type"),
                    integration_status=validation.get("integration_status"),
                    response_method=(
                        "business_tool"
                        if tool_success
                        else "business_tool_validation"
                    ),
                    status="completed" if validation["valid"] else "failed",
                    error=validation["message"] if not tool_success else None
                )
                trace.write()

                st.write("### Business information")

                tool_labels = {
                    "order_lookup": "Order Lookup Tool",
                    "billing_lookup": "Billing Lookup Tool",
                    "account_lookup": "Account Lookup Tool"
                }

                st.info(
                    tool_labels[selected_tool]
                )

                if not tool_success:
                    message = validation["message"]
                    if not message:
                        message = (
                            "I could not verify that business information. "
                            "Please try again later."
                        )

                    add_conversation_message(
                        "assistant",
                        message
                    )
                    if validation["status"] == "not_found":
                        st.warning(message)
                    else:
                        st.error(message)

                    st.stop()

                tool_data = validation["data"]

                if selected_tool == "order_lookup":

                    assistant_message = (
                        f"Order {identifier}: {tool_data['status']}. "
                        f"Expected delivery: {tool_data['expected_delivery']}. "
                        f"Tracking number: {tool_data['tracking_number']}."
                    )

                    st.success(
                        f"Order {identifier}: "
                        f"{tool_data['status']}"
                    )

                    st.write(
                        f"📦 Expected delivery: "
                        f"{tool_data['expected_delivery']}"
                    )

                    st.write(
                        f"🔎 Tracking number: "
                        f"{tool_data['tracking_number']}"
                    )

                elif selected_tool == "billing_lookup":

                    assistant_message = (
                        f"Invoice: {tool_data['invoice']}. "
                        f"Amount: {tool_data['amount']}. "
                        f"Payment status: {tool_data['payment_status']}. "
                        f"Billing date: {tool_data['billing_date']}."
                    )

                    st.success(
                        f"Invoice: {tool_data['invoice']}"
                    )

                    st.write(
                        f"💰 Amount: {tool_data['amount']}"
                    )

                    st.write(
                        f"💳 Payment status: "
                        f"{tool_data['payment_status']}"
                    )

                    st.write(
                        f"📅 Billing date: "
                        f"{tool_data['billing_date']}"
                    )

                else:

                    assistant_message = (
                        f"Account status: {tool_data['account_status']}. "
                        f"Membership: {tool_data['membership']}."
                    )

                    st.success(
                        f"Account status: "
                        f"{tool_data['account_status']}"
                    )

                    st.write(
                        f"📧 Email: {tool_data['email']}"
                    )

                    st.write(
                        f"⭐ Membership: {tool_data['membership']}"
                    )

                add_conversation_message("assistant", assistant_message)
                st.stop()


            # ==================================================
            # RAG decision
            # ==================================================

            if decision_action != RAG:
                message = (
                    "I’m not confident I can answer that reliably from the "
                    "available support capabilities."
                )
                add_conversation_message("assistant", message)
                trace.update(
                    response_method="safe_fallback",
                    status="completed"
                )
                trace.write()
                st.info(message)
                st.stop()


            # --------------------------------------------------
            # Retrieve relevant chunks
            # --------------------------------------------------

            with st.spinner(
                "🔎 Searching across uploaded documents..."
            ):

                try:
                    retrieval_query = question
                    if conversation_context:
                        retrieval_query = (
                            f"{question}\n\n"
                            f"Recent conversation context:\n"
                            f"{conversation_context}"
                        )
                    retrieved_docs = vector_db.similarity_search(
                        retrieval_query,
                        k=15
                    )
                except Exception:
                    log_audit_event(
                        question=question,
                        action=action,
                        response_method="rag",
                        status="failed",
                        error="Unexpected error during document retrieval."
                    )
                    trace.update(
                        intent=intent,
                        response_method="rag",
                        status="failed",
                        error="Unexpected error during document retrieval."
                    )
                    trace.write()
                    st.error(
                        "The documents could not be searched right now. "
                        "Please try again later."
                    )
                    st.stop()


            # --------------------------------------------------
            # Group retrieved chunks by source
            # --------------------------------------------------

            documents_by_source = {}

            for doc in retrieved_docs:

                source = doc.metadata.get(
                    "source",
                    "Unknown document"
                )

                if source not in documents_by_source:

                    documents_by_source[source] = []

                documents_by_source[source].append(
                    doc
                )


            # --------------------------------------------------
            # Select chunks from each source
            # --------------------------------------------------

            selected_docs = []

            for source, docs in documents_by_source.items():

                selected_docs.extend(
                    docs[:4]
                )


            # --------------------------------------------------
            # Limit total context
            # --------------------------------------------------

            selected_docs = selected_docs[:12]

            retrieved_docs = selected_docs

            if not retrieved_docs:
                message = retrieval_result.get(
                    "message",
                    "UNCERTAIN: I could not find sufficiently relevant information "
                    "in the available knowledge base."
                )
                add_conversation_message("assistant", message)
                trace.update(
                    response_method="safe_fallback",
                    status="completed",
                    error="No relevant knowledge-base evidence was retrieved."
                )
                trace.write()
                st.info(message)
                st.stop()


            # --------------------------------------------------
            # Identify retrieved sources
            # --------------------------------------------------

            retrieved_sources = []

            for doc in retrieved_docs:

                source = doc.metadata.get(
                    "source",
                    "Unknown document"
                )

                if source not in retrieved_sources:

                    retrieved_sources.append(
                        source
                    )


            # --------------------------------------------------
            # Retrieval diagnostics
            # --------------------------------------------------

            st.write(
                "### 🔎 Retrieved Information"
            )


            if retrieved_sources:

                st.success(
                    "Information retrieved from:"
                )

                for source in retrieved_sources:

                    st.write(
                        f"📄 {source}"
                    )

            else:

                st.warning(
                    "No document source was identified."
                )


            # --------------------------------------------------
            # Build source-aware context
            # --------------------------------------------------

            context_parts = []

            for doc in retrieved_docs:

                source = doc.metadata.get(
                    "source",
                    "Unknown document"
                )

                page = doc.metadata.get("page")
                chunk_index = doc.metadata.get("chunk_index")
                category = doc.metadata.get("category", "unknown")
                context_parts.append(
                    f"""
[SOURCE DOCUMENT: {source}; PAGE: {page}; CHUNK: {chunk_index}; CATEGORY: {category}]

{doc.page_content}
"""
                )


            context = "\n\n".join(
                context_parts
            )


            # --------------------------------------------------
            # Generate RAG response
            # --------------------------------------------------

            with st.spinner(
                "🧠 Agent is analyzing the retrieved evidence..."
            ):

                try:
                    answer = agent.answer_from_context(
                        question,
                        context,
                        conversation_context=conversation_context
                    )
                except Exception:
                    trace.update(
                        intent=intent,
                        response_method="rag",
                        status="failed",
                        error="Unexpected error during RAG response generation."
                    )
                    trace.write()
                    log_audit_event(
                        question=question,
                        action=action,
                        intent=intent,
                        response_method="rag",
                        status="failed",
                        error="Unexpected error during RAG response generation."
                    )
                    st.error(
                        "The response could not be generated right now. "
                        "Please try again later."
                    )
                    st.stop()


            # --------------------------------------------------
            # Display response
            # --------------------------------------------------

            st.subheader(
                "🤖 Agent Response"
            )

            st.write(
                answer
            )
            add_conversation_message("assistant", answer)

            trace.update(
                intent=intent,
                response_method="rag",
                status="completed"
            )
            trace.write()