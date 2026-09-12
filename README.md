# AI Customer Support Platform

## Product at a glance
An operational AI customer-support platform for ecommerce, SaaS, online stores, and service businesses that need grounded answers, controlled business lookups, human escalation, and safe operational visibility.

**Repository:** https://github.com/bilalsajidminhas021-maker/AI-Customer-Support-Agent

**Live demo:** Streamlit Community Cloud deployment is active for this repository. The deployment URL is intentionally not stored in source control; use the deployment URL supplied with the client demonstration environment.

## Overview
A reusable Streamlit support platform that combines grounded knowledge retrieval, controlled business tools, human handoff, persistent cases, and deterministic safety boundaries. It is designed to demonstrate business value without giving a language model unrestricted authority over refunds, cancellations, accounts, or payments.

## Problem
Businesses receive repetitive support requests, but automation must not invent policy, expose private data, or perform irreversible actions without review.

## Key Capabilities
- Approved-document RAG with source, page, chunk, category, and relevance metadata
- Deterministic routing for support, business lookup, escalation, clarification, and fallback
- Demo business data and a generic REST order provider
- Bounded conversation memory and controlled orchestration
- Persistent support cases with lifecycle and duplicate protection
- Action allowlist, human-review states, evidence validation, tenant context, admin authentication, redaction, and audit timing
- Controlled evaluation and reliability tests

## Who it is for
Businesses with repetitive support questions and a need for reliable first-line assistance: ecommerce teams, SaaS support teams, online stores, service businesses, and SMB or mid-market operations.

## Client demonstration
Use [documentation/CLIENT_DEMO_GUIDE.md](documentation/CLIENT_DEMO_GUIDE.md) for a 10-15 minute walkthrough covering order lookup, follow-up memory, grounded policy answers, human escalation, safety boundaries, and the Administrator dashboard. Use [documentation/CLIENT_ONBOARDING.md](documentation/CLIENT_ONBOARDING.md) to prepare a new business configuration.

## Architecture
`CUSTOMER -> SECURITY / TENANT CONTEXT -> MEMORY / CONTEXT -> DETERMINISTIC PREFLIGHT -> DECISION -> CONTROLLED ORCHESTRATION -> ACTION SAFETY -> RAG / BUSINESS TOOL / CASE -> RESULT VALIDATION -> FINAL RESPONSE -> AUDIT`

## Technology Stack
Python, Streamlit, Gemini through LangChain, FAISS, sentence-transformers, pypdf, standard-library JSON/REST adapters, and unittest.

## Agent Workflow
The model may classify or generate grounded responses, but deterministic decision and action-safety layers remain authoritative. Unknown actions fail closed. Missing identifiers clarify. Unsupported requests receive a safe fallback.

## Knowledge/RAG System
PDFs are extracted page by page, chunked, embedded, and retrieved with relevance filtering. Customer-policy documents use an explicit filename convention such as `customer_policy__late_delivery.pdf`. Internal documents cannot satisfy policy evidence requirements. Missing or low-quality evidence produces uncertainty rather than an invented answer.

## Business Tools & Integrations
`tools.py` provides order, billing, and account lookups. `integrations.py` defines a replaceable order provider interface with `demo` and generic `rest` implementations. Shopify, WooCommerce, CRM, WhatsApp, and helpdesk connectors are planned, not implemented.

## Human Escalation & Case Management
Refunds, disputes, complaints, account-security issues, and other sensitive requests create controlled human-review cases. Cases are stored by the JSON adapter with lifecycle transitions, evidence status, bounded context, tenant-aware fingerprints, and safe validation.

## Action Safety
The implemented action registry contains read-only lookups, policy lookup, and support-case creation. Eligibility states are `ALLOWED`, `NOT_ALLOWED`, `REQUIRES_HUMAN`, and `INSUFFICIENT_EVIDENCE`. Refunds, cancellations, payment changes, and account mutations are not executable actions.

## Security
Secrets come from environment configuration. Admin passwords use PBKDF2 hashes. Retrieved content is untrusted data, not authorization. Customer input is bounded, audit output is redacted, and tenant paths are configuration-controlled. Enterprise SSO and production identity remain future work.

## Tenant/Business Reusability
`tenant_context.py` centralizes business identity and configured storage boundaries. The current deployment is tenant-aware foundation code, not full SaaS multi-tenancy.

## Admin Controls
The protected admin area provides Business Setup, Knowledge Base status, Integrations readiness, Production Readiness, a state-derived Go-Live Checklist, Overview, Cases, AI & Safety, Observability, Evaluation, and readiness details. Business Setup remains an environment/deployment configuration view; it does not write settings or secrets into source code. Knowledge Base status reports the configured directory, PDF inventory, customer-policy document count, and the current session's existing search-index availability. The checklist reports actionable READY, WARNING, or NOT READY states from actual system checks. It never displays API keys, password hashes, authorization headers, raw questions, or credentials.

## Evaluation & Testing
`evaluation_cases.json` contains 14 controlled scenarios. `evaluate_agent.py` checks structured routing, safety, evidence, tenant, security, and admin behavior. The regression suite covers routing, orchestration, integrations, case management, action safety, onboarding, and production acceptance.

The verified repository baseline is 119/119 automated tests passing, including 22 production-acceptance tests covering realistic customer behavior, prompt injection, input validation, action safety, RAG evidence, provider failures, case lifecycle, tenant boundaries, admin access, audit redaction, and error boundaries. These are controlled automated checks, not a statistically representative production benchmark.

## Local Setup
```text
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Environment Configuration
Copy the shape of `.env` locally and keep it ignored. Configure `GOOGLE_API_KEY`, `BUSINESS_NAME`, `BUSINESS_EMAIL`, `SUPPORT_HOURS`, `TENANT_ID`, `KNOWLEDGE_BASE_PATH`, `BUSINESS_DATA_PATH`, `SUPPORT_CASE_STORAGE_PATH`, `AUDIT_STORAGE_PATH`, `BUSINESS_PROVIDER`, and `ADMIN_PASSWORD_HASH`. For REST use `BUSINESS_API_BASE_URL`, `BUSINESS_API_KEY`, and `BUSINESS_API_TIMEOUT`. Never commit secret values.

The Administrator Business Setup view shows these deployment-controlled values and safe configured/not-configured statuses. Add approved PDF files to the configured knowledge-base directory; the existing application flow extracts and indexes them with the current FAISS and metadata filtering pipeline. The Administrator Knowledge Base view reports readiness but does not accept arbitrary paths or replace the existing index implementation. The Go-Live Checklist is an operational summary, not a substitute for deployment, secret, monitoring, backup, or identity configuration.

Generate an admin hash locally:
```text
python -c "from security import hash_password; print(hash_password(input('Password: ')))"
```

## Demo Scenarios
1. **Order lookup:** `check order ORD-1001` returns demo order details.
2. **Memory:** after the lookup, `When will it arrive?` reuses `ORD-1001` from context.
3. **Human escalation:** `I want to speak to a human` creates a support case.
4. **Billing dispute:** `You charged me twice` becomes a high-priority billing case.
5. **RAG:** ask a factual question covered by an approved document; the response includes source evidence.
6. **Evidence safety:** `Refund my order ORD-1001` does not promise a refund.
7. **Unsupported capability:** `Book me a flight to Dubai` receives a safe fallback.
8. **Prompt injection:** a document instruction attempting to reveal a key is treated as untrusted content.
9. **Admin:** opening the protected area requires authentication and shows safe operational status.
10. **Evaluation:** `python evaluate_agent.py` reports 14/14 current controlled scenarios passing.

These scenarios are a product demonstration, not a representative sample of all production traffic.

## Deployment Preparation
Use `operational.py` for safe health and production-readiness checks. Before deployment, move secrets to a managed secret store, replace JSON persistence with a production database or helpdesk adapter, configure monitoring and backups, review identity and access controls, and test with deployment-specific data. No Docker, CI/CD, or cloud infrastructure is included because the current project does not require it.

## Current Limitations
- Local JSON storage is not a multi-process production database.
- Admin authentication is a lightweight local adapter, not enterprise SSO or advanced RBAC.
- Tenant isolation is configuration-controlled foundation code, not full SaaS infrastructure.
- Evaluation is controlled and small; it is not a statistically representative benchmark.
- Demo data and generic REST order integration are the only business integrations implemented.

## Scope clarity
- **Implemented:** grounded support answers, demo and generic REST order lookup, billing/account lookup, human cases, protected administration, readiness checks, audit-safe observability, and deterministic safety boundaries.
- **Configurable:** business identity, support hours, escalation settings, approved documents, local data paths, provider mode, REST field mapping, and deployment settings.
- **Intentionally restricted:** refunds, cancellations, compensation, payment changes, account mutations, arbitrary tool calls, and unsupported requests.
- **Future/custom work:** commerce connectors, CRM/helpdesk/messaging channels, enterprise SSO, production database infrastructure, and full SaaS tenancy.

## Future Roadmap
Shopify, WooCommerce, CRM, WhatsApp, helpdesk, email, enterprise SSO, production database, distributed rate limiting, richer evaluation data, deployment automation, and advanced analytics.

## Commercial Use Cases
Ecommerce support, SaaS help desks, online stores, service businesses, and SMB or mid-market support teams can reuse the core platform with business-specific documents, data adapters, policies, and escalation workflows.

## Project Status
**IMPLEMENTED:** reusable support MVP, RAG, deterministic routing, controlled tools, human cases, safety, security foundation, admin dashboard, evaluation, reliability tests, and deployment checks.

**FOUNDATION/PARTIAL:** tenant-aware configuration, local admin authentication, JSON persistence, generic REST integration, operational checks, and controlled evaluation.

**PLANNED:** client-specific commerce, CRM, messaging, helpdesk, enterprise identity, production infrastructure, and broader analytics.

The core reusable platform MVP is complete; future work consists primarily of client-specific integrations, production infrastructure, broader evaluation data, and enterprise deployment features.
