"""Replaceable business-data providers for customer support tools."""

from dataclasses import dataclass
import io
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ORDER_FIELDS = ("status", "tracking_number", "expected_delivery")


@dataclass(frozen=True)
class ProviderResult:
    """Safe, normalized result returned by every business-data provider."""

    status: str
    data: dict[str, Any] | None = None
    message: str | None = None
    provider_type: str = "unknown"


class OrderDataProvider(Protocol):
    provider_type: str

    def get_order(self, order_id: str) -> ProviderResult:
        """Return a normalized order result without exposing raw payloads."""


def _normalized_order(order_id: str, record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None

    normalized = {
        "status": record.get("status"),
        "tracking_number": record.get("tracking_number"),
        "expected_delivery": record.get("expected_delivery"),
    }
    if any(
        not isinstance(value, str) or not value.strip()
        for value in normalized.values()
    ):
        return None

    normalized["order_id"] = order_id
    if isinstance(record.get("customer"), str) and record["customer"].strip():
        normalized["customer"] = record["customer"].strip()
    return normalized


class DemoOrderProvider:
    """Local demo provider kept separate from production integration code."""

    provider_type = "demo"

    def __init__(self, data_path: str):
        self.data_path = data_path

    def get_order(self, order_id: str) -> ProviderResult:
        try:
            with Path(self.data_path).expanduser().open(
                "r", encoding="utf-8"
            ) as data_file:
                payload = json.load(data_file)
        except (OSError, json.JSONDecodeError):
            return ProviderResult(
                status="unavailable",
                message="The business integration is currently unavailable.",
                provider_type=self.provider_type,
            )

        orders = payload.get("orders") if isinstance(payload, dict) else None
        record = orders.get(order_id) if isinstance(orders, dict) else None
        if record is None:
            return ProviderResult(
                status="not_found",
                message=f"No order was found for order ID {order_id}.",
                provider_type=self.provider_type,
            )

        data = _normalized_order(order_id, record)
        if data is None:
            return ProviderResult(
                status="malformed",
                message="The business system returned incomplete order data.",
                provider_type=self.provider_type,
            )
        return ProviderResult(
            status="success",
            data=data,
            provider_type=self.provider_type,
        )


class ConfigurationErrorOrderProvider:
    """Provider used when provider selection is not a supported value."""

    provider_type = "configuration"

    def get_order(self, order_id: str) -> ProviderResult:
        return ProviderResult(
            status="configuration_error",
            message="The business integration provider is not configured.",
            provider_type=self.provider_type,
        )


class RestOrderProvider:
    """REST adapter that converts an external order API into our stable schema."""

    provider_type = "rest"

    def __init__(self, base_url: str, api_key: str | None, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if isinstance(api_key, str) else None
        self.timeout = timeout

    def get_order(self, order_id: str) -> ProviderResult:
        if not self.base_url or not self.api_key or self.timeout <= 0:
            return ProviderResult(
                status="configuration_error",
                message="The business integration is not configured.",
                provider_type=self.provider_type,
            )

        request = Request(
            f"{self.base_url}/orders/{quote(order_id, safe='')}",
            headers={
                "Accept": "application/json",
                "X-API-Key": self.api_key,
            },
            method="GET",
        )
        raw_body = None
        parse_succeeded = False
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw_body = response.read()
                payload = json.load(io.BytesIO(raw_body))
        except HTTPError as error:
            if error.code in {401, 403}:
                status = "authentication_failure"
            elif error.code == 404:
                status = "not_found"
            else:
                status = "http_error"
            message = (
                "The business integration could not authenticate."
                if status == "authentication_failure"
                else f"No order was found for order ID {order_id}."
                if status == "not_found"
                else "The business integration returned an error."
            )
            return ProviderResult(
                status=status,
                message=message,
                provider_type=self.provider_type,
            )
        except TimeoutError:
            return ProviderResult(
                status="timeout",
                message="The business integration timed out.",
                provider_type=self.provider_type,
            )
        except (URLError, OSError):
            return ProviderResult(
                status="unavailable",
                message="The business integration is currently unavailable.",
                provider_type=self.provider_type,
            )
        except (json.JSONDecodeError, TypeError, ValueError, UnicodeError):
            return ProviderResult(
                status="malformed",
                message="The business system returned malformed order data.",
                provider_type=self.provider_type,
            )

        record = payload.get("order", payload) if isinstance(payload, dict) else None
        if isinstance(record, dict) and record.get("found") is False:
            return ProviderResult(
                status="not_found",
                message=f"No order was found for order ID {order_id}.",
                provider_type=self.provider_type,
            )
        if isinstance(record, dict):
            record = {
                "status": record.get("status", record.get("order_status")),
                "tracking_number": record.get(
                    "tracking_number", record.get("trackingNumber")
                ),
                "expected_delivery": record.get(
                    "expected_delivery", record.get("expectedDelivery")
                ),
                "customer": record.get("customer"),
            }
        extracted_values = {
            "status": record.get("status") if isinstance(record, dict) else None,
            "tracking_number": (
                record.get("tracking_number") if isinstance(record, dict) else None
            ),
            "expected_delivery": (
                record.get("expected_delivery") if isinstance(record, dict) else None
            ),
        }
        required_checks_passed = isinstance(record, dict) and all(
            isinstance(value, str) and value.strip()
            for value in extracted_values.values()
        )
        data = _normalized_order(order_id, record)
        if data is None:
            return ProviderResult(
                status="malformed",
                message="The business system returned incomplete order data.",
                provider_type=self.provider_type,
            )
        return ProviderResult(
            status="success",
            data=data,
            provider_type=self.provider_type,
        )