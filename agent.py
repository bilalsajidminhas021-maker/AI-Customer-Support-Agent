from langchain_google_genai import ChatGoogleGenerativeAI
from security import UNTRUSTED_CONTENT_INSTRUCTION


class SupportAgent:
    """
    Agentic decision layer for the AI customer support automation system.

    The agent performs three levels of decision-making:

    1. Action routing:
       support / compare / escalate / reject

    2. Customer-support intent detection:
       policy / product_help / troubleshooting /
       order_issue / account_help / billing / general_support

    3. Business tool selection:
       order_lookup / billing_lookup / account_lookup / none
    """

    def __init__(self, api_key):

        self.api_key = api_key

        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=api_key,
            temperature=0
        )

    # --------------------------------------------------
    # Agent Action Routing
    # --------------------------------------------------

    def decide_action(self, question, conversation_context=""):

        prompt = f"""
You are the intelligent routing layer of a professional
AI customer support automation system.

Your job is to classify the customer's request into exactly
ONE action.

Available actions:

1. "support"

Use this when the request is a normal customer-support
question that can reasonably be answered using the
available knowledge documents.

This includes:

- company policies
- product information
- product usage
- troubleshooting
- delivery information
- order information
- account information
- billing information
- general support questions

Examples:

- What is your return policy?
- How do I use this product?
- How do I configure this device?
- Why is my device not working?
- What are the delivery options?
- How can I update my account email?
- Where can I find my invoice?
- What payment methods do you accept?

IMPORTANT:

Normal informational questions about orders, accounts,
or billing should be classified as "support".

They should NOT automatically be escalated.

2. "compare"

Use this when the customer explicitly asks to compare
two or more uploaded documents, products, policies,
candidates, or pieces of information.

Examples:

- Compare these two policies.
- What is different between these documents?
- Which product has better features?
- Find similarities between these documents.

3. "escalate"

Use this when the request requires human intervention,
human authorization, dispute handling, or sensitive
case resolution.

Examples:

- I want a refund.
- I was charged twice and want my money back.
- I dispute this charge.
- Someone accessed my account.
- My account has been compromised.
- I want to file a complaint.
- I need a human representative.
- Cancel my order immediately.
- Give me compensation for my problem.

IMPORTANT:

Do NOT escalate a normal informational question merely
because it involves billing, an account, or an order.

Escalate only when human intervention, authorization,
dispute resolution, refund handling, complaint handling,
or a sensitive issue is required.

4. "reject"

Use this when the request is completely unrelated to
customer support or the available documents.

Examples:

- Write me a poem.
- Who won a football match?
- Tell me a joke.
- Explain quantum physics.

IMPORTANT ROUTING RULES:

- Normal support question → support
- Explicit comparison → compare
- Human intervention / dispute / refund / complaint /
  sensitive issue → escalate
- Completely unrelated request → reject

Recent conversation context:
{conversation_context}

Question:
{question}

Respond with EXACTLY ONE word:

support
compare
escalate
reject
"""

        result = self.llm.invoke(prompt)

        action = result.content.strip().lower()

        valid_actions = [
            "support",
            "compare",
            "escalate",
            "reject"
        ]

        if action not in valid_actions:
            action = "support"

        return action

    # --------------------------------------------------
    # Support Intent Detection
    # --------------------------------------------------

    def detect_intent(self, question, conversation_context=""):

        prompt = f"""
You are an intent classification component of a professional
AI customer support automation system.

Classify the customer's request into exactly ONE intent.

Available intents:

- "policy"

Questions about company policies, rules, terms,
returns, cancellations, warranties, or procedures.

- "product_help"

Questions about product features, specifications,
usage, setup, or capabilities.

- "troubleshooting"

Problems where the customer needs help fixing something
that is not working correctly.

- "order_issue"

Questions or problems related to orders, delivery,
shipping, tracking, or missing packages.

- "account_help"

Questions about account access, login, password,
profile, or account settings.

- "billing"

Questions about invoices, charges, payments,
subscriptions, or billing information.

- "general_support"
- "unknown_or_uncertain"

General customer-support questions that do not clearly
belong to another category.

- "unknown_or_uncertain"

Use this when the request does not match a known support
intent reliably.

Examples:

Question: How can I return my product?
Intent: policy

Question: How do I configure this device?
Intent: product_help

Question: My device keeps shutting down.
Intent: troubleshooting

Question: Where is my order?
Intent: order_issue

Question: I forgot my password.
Intent: account_help

Question: Where can I find my invoice?
Intent: billing

Question: Why do you offer this service?
Intent: general_support

Recent conversation context:
{conversation_context}

Customer question:
{question}

Respond with EXACTLY ONE intent name.
"""

        result = self.llm.invoke(prompt)

        intent = result.content.strip().lower()

        valid_intents = [
            "policy",
            "product_help",
            "troubleshooting",
            "order_issue",
            "account_help",
            "billing",
            "general_support",
            "unknown_or_uncertain"
        ]

        if intent not in valid_intents:
            intent = "unknown_or_uncertain"

        return intent

    # --------------------------------------------------
    # Business Tool Selection
    # --------------------------------------------------

    def select_tool(self, intent):
        """
        Select the appropriate business tool based on
        the already detected customer-support intent.

        This method is deterministic and does NOT call Gemini.
        """

        if intent in {"order_issue", "order_tracking"}:
            return "order_lookup"

        elif intent == "billing":
            return "billing_lookup"

        elif intent in {"account_help", "account"}:
            return "account_lookup"

        else:
            return "none"

    # --------------------------------------------------
    # RAG Answer Generation
    # --------------------------------------------------

    def answer_from_context(
        self,
        question,
        context,
        conversation_context=""
    ):

        prompt = f"""
You are an AI document-analysis agent.

Your job is to answer the user's question using ONLY the
document evidence provided below.

IMPORTANT RULES:

0. {UNTRUSTED_CONTENT_INSTRUCTION}

1. Treat every SOURCE DOCUMENT as a separate document.

2. NEVER assume that two documents belong to the same person,
   organization, or entity unless the evidence explicitly
   establishes this.

3. Always pay attention to the source document name.

4. If the user asks about multiple documents, analyze the
   documents separately before comparing them.

5. If information from one requested document is not present
   in the provided context, clearly say that the information
   was not retrieved.

6. Do NOT invent information.

7. Do NOT make assumptions based on names, emails, or other
   similar-looking information.

8. If the evidence is conflicting or ambiguous, clearly
   identify the conflict.

9. When useful, mention the source document that supports
   your answer.

10. If there is insufficient evidence to determine something,
    answer "UNCERTAIN" and briefly explain why.

11. Begin the response with exactly one evidence status:
    "VERIFIED:", "PARTIALLY VERIFIED:", or "UNCERTAIN:".

12. Use "VERIFIED" only when the supplied evidence directly supports
    the requested answer. Use "PARTIALLY VERIFIED" when some facts are
    supported but an important requested detail is missing. Use
    "UNCERTAIN" when the knowledge evidence is insufficient.

13. Never use the VERIFIED BUSINESS RESULT as a substitute for missing
    KNOWLEDGE EVIDENCE or policy.

DOCUMENT EVIDENCE:

{context}

RECENT CONVERSATION CONTEXT:

{conversation_context}

USER QUESTION:

{question}

Provide a clear and concise answer based ONLY on the
document evidence above.
"""

        result = self.llm.invoke(prompt)

        return result.content

    # --------------------------------------------------
    # Document Comparison
    # --------------------------------------------------

    def compare_from_context(self, question, context):

        prompt = f"""
You are a professional AI customer support analysis agent.

    {UNTRUSTED_CONTENT_INSTRUCTION}

The user wants a comparison based ONLY on the supplied
document evidence.

RULES:

1. Treat every SOURCE DOCUMENT separately.
2. Never mix information between documents.
3. Identify each document by its source name.
4. Compare only information actually present in the evidence.
5. Do not invent missing information.
6. Clearly identify similarities and differences.
7. If information is missing, say:
   "Not available in retrieved evidence."
8. If evidence conflicts, clearly explain the conflict.
9. Give a professional, structured response.

DOCUMENT EVIDENCE:

{context}

USER REQUEST:

{question}

Return a clear comparison with:

- Documents compared
- Key similarities
- Key differences
- Missing or uncertain information
- Source documents supporting the findings
"""

        result = self.llm.invoke(prompt)

        return result.content

    # --------------------------------------------------
    # Human Escalation
    # --------------------------------------------------

    def escalate_request(self, question):

        return f"""
🚨 HUMAN SUPPORT REQUIRED

The AI agent has identified this request as requiring
human assistance.

Customer Request:
{question}

Status:
Escalated to human support.

Reason:
This request requires human intervention such as
billing disputes, refund handling, complaints,
account security issues, or other support matters.

A human support representative should review this request.
"""