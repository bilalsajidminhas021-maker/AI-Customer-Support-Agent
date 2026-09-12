"""Lightweight production-readiness and health checks without secret output."""

from pathlib import Path

from config import (
    ADMIN_PASSWORD_HASH,
    BUSINESS_API_BASE_URL,
    BUSINESS_API_KEY,
    BUSINESS_PROVIDER,
    GOOGLE_API_KEY,
    validate_configuration,
)
from tenant_context import TenantContext


def _path_status(path, expected):
    candidate = Path(path).expanduser()
    return {
        "configured": bool(str(path).strip()),
        "exists": candidate.exists(),
        "expected": expected,
        "usable": candidate.is_dir() if expected == "directory" else candidate.is_file() or not candidate.exists(),
    }


def production_readiness(context: TenantContext, *, knowledge_index_available=False):
    """Return safe readiness findings; never include secret values."""

    provider_endpoint_configured = bool(
        BUSINESS_PROVIDER == "demo" or BUSINESS_API_BASE_URL
    )
    provider_authentication_configured = bool(
        BUSINESS_PROVIDER == "demo" or BUSINESS_API_KEY
    )
    checks = {
        "configuration": {"ok": not validate_configuration(), "issues": validate_configuration()},
        "api_key": {"ok": bool(GOOGLE_API_KEY), "message": "configured" if GOOGLE_API_KEY else "API key is missing"},
        "admin_security": {"ok": bool(ADMIN_PASSWORD_HASH), "message": "configured" if ADMIN_PASSWORD_HASH else "Admin password hash is missing"},
        "provider": {
            "ok": provider_endpoint_configured and provider_authentication_configured,
            "provider_type": BUSINESS_PROVIDER,
            "endpoint_configured": provider_endpoint_configured,
            "authentication_configured": provider_authentication_configured,
            "message": "configured" if provider_endpoint_configured and provider_authentication_configured else "Provider configuration is incomplete",
        },
        "knowledge_base": _path_status(context.knowledge_base_path, "directory"),
        "knowledge_index": {
            "ok": bool(knowledge_index_available),
            "available": bool(knowledge_index_available),
            "message": "available" if knowledge_index_available else "Knowledge index is not available",
        },
        "business_data": _path_status(context.business_data_path, "file"),
        "case_storage": _path_status(context.case_storage_path, "file"),
        "audit_storage": _path_status(context.audit_storage_path, "file"),
    }
    checks["knowledge_base"]["usable"] = checks["knowledge_base"]["exists"]
    checks["business_data"]["usable"] = checks["business_data"]["exists"]
    checks["case_storage"]["usable"] = checks["case_storage"]["configured"]
    checks["audit_storage"]["usable"] = checks["audit_storage"]["configured"]
    failed = [name for name, result in checks.items() if not result.get("ok", result.get("usable", False))]
    return {"ready": not failed, "failed_checks": failed, "checks": checks}


def health_status(context: TenantContext, *, knowledge_index_available=False):
    """Return non-sensitive operational status for local deployment checks."""

    readiness = production_readiness(
        context,
        knowledge_index_available=knowledge_index_available,
    )
    return {
        "status": "healthy" if readiness["ready"] else "degraded",
        "configuration": readiness["checks"]["configuration"]["ok"],
        "knowledge_base": readiness["checks"]["knowledge_base"]["usable"],
        "knowledge_index": readiness["checks"]["knowledge_index"]["available"],
        "business_provider": readiness["checks"]["provider"]["ok"],
        "case_storage": readiness["checks"]["case_storage"]["usable"],
        "audit_storage": readiness["checks"]["audit_storage"]["usable"],
        "security_configuration": readiness["checks"]["admin_security"]["ok"],
    }
