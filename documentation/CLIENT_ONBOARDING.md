# Client Onboarding

This platform is configured for one business deployment at a time through environment and deployment settings. The Administrator area provides visibility into readiness; it does not write secrets into source code or accept arbitrary filesystem paths.

## Business Information

Provide:

- business name
- support email
- support hours and timezone
- escalation preferences and the situations that require human review
- tenant identifier for the deployment
- preferred customer-facing language and response tone

These values are deployment configuration. They should be reviewed by the business owner before go-live.

## Knowledge Base

Provide authoritative, current, customer-facing documents such as:

- customer support policies
- frequently asked questions
- delivery and shipping policies
- returns and refund policies
- warranty information
- troubleshooting and product documentation

Use approved PDFs in the configured knowledge-base directory. Customer-policy files should follow the existing naming convention, for example `customer_policy__late_delivery.pdf`. Internal handbooks and employee-only documents should not be used as customer-policy evidence.

The existing pipeline extracts PDF text page by page, creates chunks, adds metadata, builds the FAISS index, and applies relevance and customer-policy filtering. The Administrator Knowledge Base section reports directory status, document count, policy-document count, document names, and current index readiness.

Before onboarding is complete, confirm that documents are:

- accurate and approved by the business
- written for customers where policy answers depend on them
- current and free of conflicting versions
- safe to expose through a grounded support answer

## Business Data / Integration

The current platform supports two order-provider modes:

- **Demo provider:** reads the included local business data for demonstrations.
- **REST provider:** calls a configured order endpoint through the existing provider interface and normalizes status, tracking number, and expected delivery fields.

A client should provide the API documentation, endpoint contract, required order lookup identifier, response field mapping, timeout expectations, and safe failure behavior. Configure endpoint and authentication values through deployment secrets or environment configuration. Never place credentials, API keys, bearer tokens, or headers in this repository.

The Administrator Integrations section reports provider type, endpoint status, authentication status, and readiness without displaying credential values. Live external calls are not required merely to render the dashboard.

## Administrator

Provide an administrator owner and an approved operational contact. Configure the administrator username and PBKDF2 password hash through deployment secrets. Do not send or commit the plaintext password or hash in documentation, source code, screenshots, or support tickets.

The administrator uses the protected dashboard to review Business Setup, Knowledge Base status, Integrations, cases, AI & Safety, Observability, Evaluation, Production Readiness, and the Go-Live Checklist.

## Validation Before Go-Live

Review the dashboard and acceptance evidence for:

- valid business configuration
- knowledge-base directory and approved PDF readiness
- current knowledge index availability
- business/order data availability
- provider endpoint and authentication configuration
- Google/Gemini application configuration
- administrator security configuration
- support-case storage
- audit logging and observability
- controlled evaluation results
- all actionable Go-Live Checklist items

A READY status reflects an actual check. WARNING and NOT READY statuses should be resolved or explicitly accepted by the business owner before demonstration or production use.

The verified repository baseline includes 119 automated tests, including 22 production-acceptance tests. These checks are evidence of implementation behavior, not a substitute for deployment-specific user acceptance testing.

## Deployment

The current deployment approach is Streamlit Community Cloud connected to the Git repository. Deployment secrets are configured in the Streamlit Cloud Secrets interface. The repository intentionally does not contain live secret values or a hard-coded deployment URL.

Deployment steps:

1. Connect the repository and deploy the Streamlit application.
2. Configure required non-secret environment/deployment settings.
3. Configure API keys, provider credentials, and the PBKDF2 administrator hash in the deployment secret store.
4. Confirm the application opens and customer flows work with deployment-specific data.
5. Authenticate to the Administrator area.
6. Review the readiness sections and Go-Live Checklist.
7. Run the focused and full automated validation before a client-facing release.

Production deployments should separately plan managed persistence, monitoring, backups, identity controls, rate limiting, and incident response. The current MVP is not full SaaS infrastructure.
