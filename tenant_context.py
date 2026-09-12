"""Configuration-controlled business context for the local deployment."""

from dataclasses import dataclass
from pathlib import Path
import re

from config import (
    AUDIT_STORAGE_PATH,
    BUSINESS_DATA_PATH,
    BUSINESS_EMAIL,
    BUSINESS_NAME,
    KNOWLEDGE_BASE_PATH,
    SUPPORT_CASE_STORAGE_PATH,
    SUPPORT_HOURS,
    TENANT_ID,
)


_TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    business_name: str
    business_email: str
    support_hours: str
    knowledge_base_path: str
    business_data_path: str
    case_storage_path: str
    audit_storage_path: str

    def validate(self):
        if not _TENANT_ID_PATTERN.fullmatch(self.tenant_id):
            raise ValueError("Invalid tenant context.")
        if any(not str(path).strip() for path in (
            self.knowledge_base_path,
            self.business_data_path,
            self.case_storage_path,
            self.audit_storage_path,
        )):
            raise ValueError("Tenant storage paths must be configured.")
        return self

    @property
    def case_path(self):
        return Path(self.case_storage_path).expanduser()

    @property
    def audit_path(self):
        return Path(self.audit_storage_path).expanduser()


def configured_tenant_context():
    """Build the only tenant context permitted by this deployment config."""

    return TenantContext(
        tenant_id=TENANT_ID,
        business_name=BUSINESS_NAME,
        business_email=BUSINESS_EMAIL,
        support_hours=SUPPORT_HOURS,
        knowledge_base_path=KNOWLEDGE_BASE_PATH,
        business_data_path=BUSINESS_DATA_PATH,
        case_storage_path=SUPPORT_CASE_STORAGE_PATH,
        audit_storage_path=AUDIT_STORAGE_PATH,
    ).validate()


DEFAULT_TENANT_CONTEXT = configured_tenant_context()
