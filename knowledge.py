"""Shared knowledge-document classification and retrieval helpers."""

import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path

from config import RAG_RELEVANCE_THRESHOLD


DEFAULT_RELEVANCE_THRESHOLD = 0.35

_CATEGORY_TOKENS = {
    "shipping": "shipping",
    "delivery": "shipping",
    "late_delivery": "late_delivery",
    "late-delivery": "late_delivery",
    "late": "late_delivery",
    "returns": "returns",
    "return": "returns",
    "refunds": "refunds",
    "refund": "refunds",
    "cancellations": "cancellations",
    "cancellation": "cancellations",
    "cancel": "cancellations",
    "warranty": "warranty",
    "general_support": "general_support",
    "general-support": "general_support",
}

_CUSTOMER_POLICY_PREFIX = re.compile(
    r"^customer[_-]policy(?:__|[-_])",
    re.IGNORECASE,
)


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
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return []


def knowledge_base_status(knowledge_base_path, *, index_available=False):
    """Return safe document and index readiness details."""

    directory = Path(knowledge_base_path).expanduser()
    documents = discover_knowledge_base_files(knowledge_base_path)
    customer_policy_documents = [
        path for path in documents
        if classify_document(path.name).get("customer_policy") is True
    ]
    return {
        "directory_configured": bool(str(knowledge_base_path).strip()),
        "directory_exists": directory.is_dir(),
        "pdf_count": len(documents),
        "document_names": [path.name for path in documents],
        "customer_policy_pdf_count": len(customer_policy_documents),
        "index_available": bool(index_available),
        "ready": bool(documents and index_available),
    }


def normalize_retrieval_query(query):
    """Normalize query whitespace and remove business identifiers."""

    normalized = re.sub(r"\b(?:ORD|ACC)-\d+\b", " ", str(query or ""), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", normalized).strip()


def classify_document(source_name):
    """Derive conservative metadata from an explicit document filename."""

    filename = os.path.basename(str(source_name or ""))
    stem = os.path.splitext(filename)[0].lower()
    normalized_stem = stem.replace("-", "_")

    category = "unknown"
    for token, token_category in sorted(
        _CATEGORY_TOKENS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if token.replace("-", "_") in normalized_stem:
            category = token_category
            break

    customer_policy = bool(_CUSTOMER_POLICY_PREFIX.match(stem))
    document_type = "customer_policy" if customer_policy else "unknown"

    if any(
        marker in normalized_stem
        for marker in ("employee", "handbook", "internal")
    ):
        document_type = "internal"
        customer_policy = False

    if customer_policy and category == "unknown":
        category = "general_support"

    return {
        "source": filename or "unknown",
        "page": None,
        "chunk_index": None,
        "document_type": document_type,
        "category": category,
        "customer_policy": customer_policy,
    }


def _metadata_copy(document):
    metadata = getattr(document, "metadata", {})
    return dict(metadata) if isinstance(metadata, dict) else {}


def _document_key(document):
    metadata = _metadata_copy(document)
    content = str(getattr(document, "page_content", ""))
    stable_fields = (
        metadata.get("source"),
        metadata.get("page"),
        metadata.get("chunk_index"),
        hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    return stable_fields


def _context_for(documents):
    parts = []
    for document in documents:
        metadata = _metadata_copy(document)
        source = metadata.get("source", "unknown")
        page = metadata.get("page")
        page_label = f", page {page}" if page is not None else ""
        category = metadata.get("category", "unknown")
        parts.append(
            f"[SOURCE DOCUMENT: {source}{page_label}; CATEGORY: {category}]\n\n"
            f"{getattr(document, 'page_content', '')}"
        )
    return "\n\n".join(parts)


def retrieve_knowledge(
    vector_db,
    query,
    *,
    category=None,
    policy_only=False,
    top_k=15,
    max_chunks=12,
    max_chunks_per_source=4,
):
    """Retrieve deduplicated, traceable evidence from the shared vector index."""

    normalized_query = normalize_retrieval_query(query)
    if not normalized_query:
        return {
            "valid": False,
            "status": "missing_query",
            "message": "The knowledge query was empty.",
            "query": normalized_query,
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
            "score_available": False,
        }

    candidate_count = max(top_k * 3, 30) if category or policy_only else top_k
    scored = True
    try:
        retrieved = vector_db.similarity_search_with_relevance_scores(
            normalized_query,
            k=candidate_count,
        )
    except AttributeError:
        scored = False
        retrieved = [
            (document, None)
            for document in vector_db.similarity_search(
                normalized_query,
                k=candidate_count,
            )
        ]

    selected = []
    seen = set()
    per_source = defaultdict(int)

    for document, relevance_score in retrieved:
        metadata = _metadata_copy(document)
        if category and metadata.get("category") != category:
            continue
        if policy_only and metadata.get("customer_policy") is not True:
            continue
        if scored and not isinstance(relevance_score, (int, float)):
            continue
        if scored and relevance_score < RAG_RELEVANCE_THRESHOLD:
            continue

        key = _document_key(document)
        source = metadata.get("source", "unknown")
        if key in seen or per_source[source] >= max_chunks_per_source:
            continue

        seen.add(key)
        per_source[source] += 1
        selected.append((document, relevance_score))
        if len(selected) >= max_chunks:
            break

    documents = [document for document, _ in selected]
    evidence_items = []
    sources = []
    for document, relevance_score in selected:
        metadata = _metadata_copy(document)
        source = metadata.get("source", "unknown")
        if source not in sources:
            sources.append(source)
        evidence_items.append({
            "source": source,
            "page": metadata.get("page"),
            "chunk_index": metadata.get("chunk_index"),
            "category": metadata.get("category", "unknown"),
            "document_type": metadata.get("document_type", "unknown"),
            "customer_policy": metadata.get("customer_policy", False),
            "relevance_score": relevance_score,
        })

    if not documents:
        message = (
            "UNCERTAIN: The knowledge base does not contain sufficiently "
            "relevant approved evidence for this question."
        )
        if policy_only and not scored:
            message = (
                "UNCERTAIN: Policy evidence could not be verified because "
                "retrieval relevance scores are unavailable."
            )
        return {
            "valid": False,
            "status": "insufficient_evidence",
            "message": message,
            "query": normalized_query,
            "documents": [],
            "evidence_items": [],
            "context": "",
            "sources": [],
            "score_available": scored,
        }

    return {
        "valid": True,
        "status": "success",
        "message": None,
        "query": normalized_query,
        "documents": documents,
        "evidence_items": evidence_items,
        "context": _context_for(documents),
        "sources": sources,
        "score_available": scored,
    }
