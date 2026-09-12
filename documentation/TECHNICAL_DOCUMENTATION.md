# Technical Documentation

## 1. Executive Summary
**Status: IMPLEMENTED.** This project is a reusable customer-support automation MVP. It grounds answers in business documents, uses controlled business tools, escalates sensitive work to humans, and records safe operational evidence.

## 2. Problem Statement
**Status: IMPLEMENTED.** The platform addresses repetitive support work while limiting unsupported promises, unsafe tool execution, and ungrounded policy answers.

## 3. Objectives
**Status: IMPLEMENTED.** Provide configurable support identity, evidence-backed answers, read-only business lookups, human handoff, case persistence, safety controls, and measurable reliability.

## 4. Product Differentiation
**Status: IMPLEMENTED.** The platform separates model assistance from deterministic authority. Evidence validation, action allowlisting, result validation, and human review are explicit boundaries.

## 5. System Architecture
**Status: IMPLEMENTED.** Customer -> security and tenant context -> session memory -> deterministic preflight -> decision -> controlled orchestration -> action safety -> RAG, tool, or case -> result validation -> response -> audit.

## 6. Technology Stack
**Status: IMPLEMENTED.** Python, Streamlit, Gemini/LangChain, FAISS, sentence-transformers, pypdf, JSON storage, standard-library REST, and unittest. No additional Day 14 dependency is required.

## 7. Module Responsibilities
**Status: IMPLEMENTED.** `app.py` owns the Streamlit workflow; `agent.py` owns model classification and grounded generation; `decision.py` owns deterministic decisions; `orchestration.py` bounds multi-step order workflows; `action_safety.py` owns eligibility; `tools.py` validates and executes controlled operations; `knowledge.py` owns retrieval metadata; `case_storage.py` persists cases; `audit.py` records structured events; `security.py` redacts and bounds input; `admin.py` provides protected read-only operations; `operational.py` provides readiness checks.

## 8. End-to-End Agent Workflow
**Status: IMPLEMENTED.** Input is bounded, context is assembled, deterministic routes are checked first, model classification is used only where needed, safety is evaluated before execution, outputs are validated, and the final response is stored in session memory.

## 9. Conversation Memory
**Status: IMPLEMENTED.** Streamlit session state stores bounded role/content history. Recent valid identifiers support follow-up order or account requests. Conversation history is not persisted to disk.

## 10. Knowledge/RAG Architecture
**Status: IMPLEMENTED.** PDFs are extracted page by page and chunks retain source, page, chunk, category, document type, policy eligibility, and relevance metadata. Internal documents cannot satisfy policy-only retrieval.

## 11. Decision Boundary
**Status: IMPLEMENTED.** `decision.py` selects `RAG`, `BUSINESS_TOOL`, `ESCALATE`, `CLARIFY`, or `SAFE_FALLBACK`. There is one routing boundary, not a second router.

## 12. Controlled Orchestration
**Status: IMPLEMENTED.** Order workflows have bounded steps and failures. A validated order result may be combined with verified policy evidence; invalid results terminate safely.

## 13. Business Tools
**Status: IMPLEMENTED.** Order, billing, and account lookups use validated identifiers and normalized result schemas. Mutating business actions are absent.

## 14. External Integration Architecture
**Status: FOUNDATION/PARTIAL.** `OrderDataProvider` supports a demo provider and a generic REST adapter with safe timeout, HTTP, authentication, malformed-response, and unavailable statuses. Specific commerce and CRM connectors are planned.

## 15. Human Handoff
**Status: IMPLEMENTED.** Sensitive requests map to `CREATE_SUPPORT_CASE` and `REQUIRES_HUMAN`. Case creation is validated before confirmation is shown.

## 16. Persistent Case Management
**Status: IMPLEMENTED.** The JSON adapter supports tenant-aware create, get, update, duplicate lookup, lifecycle transitions, bounded summaries, evidence status, and controlled recommended actions.

## 17. Action Safety
**Status: IMPLEMENTED.** The allowlist supports read-only lookups, policy lookup, and human case creation. Unknown, destructive, or unsupported actions fail closed.

## 18. Security Architecture
**Status: FOUNDATION/PARTIAL.** Environment-backed settings, PBKDF2 local admin authentication, input length limits, untrusted-content instructions, configuration-controlled paths, tenant metadata, and recursive secret redaction are implemented. Enterprise identity, distributed rate limits, and managed secret infrastructure are not.

## 19. Tenant/Business Context
**Status: FOUNDATION/PARTIAL.** `TenantContext` centralizes business identity and configured data boundaries. It is suitable for a single-business deployment and future adapter work, not full SaaS tenancy.

## 20. Admin Controls
**Status: IMPLEMENTED.** Authenticated administrators receive Business Setup, Knowledge Base inventory and readiness, Integration readiness, Production Readiness, a state-derived Go-Live Checklist, Overview, Cases, AI & Safety, Observability, Evaluation, and detailed readiness information. The view reuses environment-backed configuration, the existing knowledge discovery and FAISS session index, and `operational.py` checks. It is read-only: it does not write secrets, accept arbitrary paths, or create a second vector store. Secrets, credentials, raw customer questions, and unnecessary PII are excluded.

## 21. Audit & Observability
**Status: IMPLEMENTED.** JSONL audit events include safe status fields, tenant identity, optional operation names, elapsed duration, and per-operation timing. Audit failure is non-fatal.

## 22. Evaluation Framework
**Status: IMPLEMENTED.** `evaluation_cases.json` and `evaluate_agent.py` define 14 controlled structured scenarios covering routing, escalation, safety, evidence, tenant boundaries, prompt injection, unsupported requests, and admin access.

## 23. Reliability Testing
**Status: IMPLEMENTED.** Day 13 tests cover REST failures, missing evidence, low relevance, empty knowledge, case storage failure, audit failure, invalid actions and identifiers, oversized input, prompt injection, and tenant isolation.

## 24. Deployment Preparation
**Status: FOUNDATION/PARTIAL.** `operational.py` exposes safe health and readiness status. Deployment still requires managed secrets, production persistence, monitoring, backups, identity review, and environment-specific validation.

The Administrator Go-Live Checklist summarizes actual configuration, knowledge, provider, security, audit, evaluation, and deployment state. A warning or not-ready item includes a short corrective action. Environment values and secrets remain deployment configuration and must stay outside Git/source code.

## 25. Reusability Across Clients
**Status: FOUNDATION/PARTIAL.** Businesses can change environment settings, knowledge documents, local business data, provider configuration, and case paths without rebuilding routing. Client-specific adapters remain planned.

## 26. Commercial Value
**Status: IMPLEMENTED.** The platform can reduce repetitive first-line support, provide grounded answers, connect read-only business data, escalate complex cases, and retain an auditable case trail.

## 27. Current Limitations
**Status: FOUNDATION/PARTIAL.** JSON storage is local; evaluation is small and controlled; local admin auth is not SSO; tenant isolation is configuration-based; only demo and generic REST order integrations exist; no irreversible business actions execute.

## 28. Future Integration Roadmap
**Status: PLANNED.** Shopify, WooCommerce, CRM, WhatsApp, helpdesk, email, enterprise SSO, production database, distributed rate limiting, deployment automation, broader evaluation data, and advanced analytics.
