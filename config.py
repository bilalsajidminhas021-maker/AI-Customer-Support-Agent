import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


class ConfigurationError(ValueError):
    """Raised when a security-sensitive configuration value is invalid."""


def _bounded_int(name, default, minimum, maximum):
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer.") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _bounded_float(name, default, minimum, maximum):
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}.")
    return value


GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
TENANT_ID = os.getenv("TENANT_ID", "default").strip()
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "Customer Support")
BUSINESS_EMAIL = os.getenv("BUSINESS_EMAIL", "")
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "knowledge_base")
BUSINESS_DATA_PATH = os.getenv("BUSINESS_DATA_PATH", "business_data.json")
SUPPORT_CASE_STORAGE_PATH = os.getenv(
    "SUPPORT_CASE_STORAGE_PATH",
    "support_cases.json",
)
AUDIT_STORAGE_PATH = os.getenv("AUDIT_STORAGE_PATH", "execution_audit.jsonl")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
BUSINESS_PROVIDER = os.getenv("BUSINESS_PROVIDER", "demo").strip().lower()
BUSINESS_API_BASE_URL = os.getenv("BUSINESS_API_BASE_URL", "").strip()
BUSINESS_API_KEY = os.getenv("BUSINESS_API_KEY")
try:
    BUSINESS_API_TIMEOUT = float(os.getenv("BUSINESS_API_TIMEOUT", "5"))
except (TypeError, ValueError):
    BUSINESS_API_TIMEOUT = 5.0
SUPPORT_HOURS = os.getenv(
    "SUPPORT_HOURS",
    "Monday-Friday, 9:00 AM-5:00 PM"
)
ESCALATION_ENABLED = os.getenv(
    "ESCALATION_ENABLED",
    "true"
).strip().lower() in {"true", "1", "yes", "on"}

try:
    INPUT_MAX_LENGTH = _bounded_int("INPUT_MAX_LENGTH", 4000, 100, 100000)
    MAX_CONVERSATION_HISTORY = _bounded_int("MAX_CONVERSATION_HISTORY", 12, 1, 100)
    MAX_ORCHESTRATION_STEPS = _bounded_int("MAX_ORCHESTRATION_STEPS", 3, 1, 10)
    RAG_RELEVANCE_THRESHOLD = _bounded_float("RAG_RELEVANCE_THRESHOLD", 0.35, 0.0, 1.0)
except ConfigurationError as exc:
    CONFIGURATION_ERROR = str(exc)
    INPUT_MAX_LENGTH = 4000
    MAX_CONVERSATION_HISTORY = 12
    MAX_ORCHESTRATION_STEPS = 3
    RAG_RELEVANCE_THRESHOLD = 0.35
else:
    CONFIGURATION_ERROR = None


@dataclass(frozen=True)
class SecurityLimits:
    input_max_length: int = INPUT_MAX_LENGTH
    max_conversation_history: int = MAX_CONVERSATION_HISTORY
    max_orchestration_steps: int = MAX_ORCHESTRATION_STEPS
    rag_relevance_threshold: float = RAG_RELEVANCE_THRESHOLD


def validate_configuration():
    """Return safe, non-secret configuration errors for the admin surface."""

    errors = []
    if not TENANT_ID or len(TENANT_ID) > 80:
        errors.append("TENANT_ID must be a non-empty value of at most 80 characters.")
    if not ADMIN_USERNAME:
        errors.append("ADMIN_USERNAME must be configured.")
    if BUSINESS_PROVIDER not in {"demo", "rest"}:
        errors.append("BUSINESS_PROVIDER is not supported.")
    if BUSINESS_PROVIDER == "rest" and not BUSINESS_API_BASE_URL:
        errors.append("BUSINESS_API_BASE_URL is required for the REST provider.")
    for name, value in (
        ("KNOWLEDGE_BASE_PATH", KNOWLEDGE_BASE_PATH),
        ("BUSINESS_DATA_PATH", BUSINESS_DATA_PATH),
        ("SUPPORT_CASE_STORAGE_PATH", SUPPORT_CASE_STORAGE_PATH),
        ("AUDIT_STORAGE_PATH", AUDIT_STORAGE_PATH),
    ):
        if not str(value).strip():
            errors.append(f"{name} must not be empty.")
    if CONFIGURATION_ERROR:
        errors.append(CONFIGURATION_ERROR)
    return errors
