import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from audit import ExecutionTrace
from integrations import (
    ConfigurationErrorOrderProvider,
    DemoOrderProvider,
    ProviderResult,
    RestOrderProvider,
)
from orchestration import ControlledOrchestrator
from tools import (
    _create_order_provider,
    execute_tool,
    lookup_order,
    validate_tool_result,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.payload


class MalformedProvider:
    provider_type = "test"

    def get_order(self, order_id):
        return ProviderResult(
            status="malformed",
            message="The business system returned malformed order data.",
            provider_type=self.provider_type,
        )


class Day8IntegrationTests(unittest.TestCase):
    @patch("tools.BUSINESS_PROVIDER", "demo")
    def test_demo_provider_is_selected_explicitly(self):
        self.assertIsInstance(_create_order_provider(), DemoOrderProvider)

    @patch("tools.BUSINESS_PROVIDER", "rest")
    def test_rest_provider_is_selected_explicitly(self):
        self.assertIsInstance(_create_order_provider(), RestOrderProvider)

    @patch("tools.BUSINESS_PROVIDER", "unknown")
    def test_unknown_provider_is_a_controlled_configuration_error(self):
        provider = _create_order_provider()
        result = provider.get_order("ORD-1001")

        self.assertIsInstance(provider, ConfigurationErrorOrderProvider)
        self.assertEqual(result.status, "configuration_error")
        self.assertNotIn("secret", str(result).lower())

    @patch("tools.ORDER_PROVIDER", DemoOrderProvider("business_data.json"))
    def test_demo_provider_preserves_ord_1001_behavior(self):
        result = execute_tool("order_lookup", "ORD-1001")

        self.assertTrue(result["success"])
        self.assertEqual(result["provider_type"], "demo")
        self.assertEqual(result["data"]["status"], "Shipped")
        self.assertEqual(result["data"]["tracking_number"], "TRK-458921")

    @patch("tools.ORDER_PROVIDER", DemoOrderProvider("business_data.json"))
    def test_missing_order_is_controlled(self):
        result = execute_tool("order_lookup", "ORD-9999")
        validation = validate_tool_result("order_lookup", result)

        self.assertFalse(result["success"])
        self.assertEqual(validation["status"], "not_found")
        self.assertEqual(result["provider_type"], "demo")

    def test_malformed_provider_result_is_not_successful(self):
        result = lookup_order("ORD-1001", MalformedProvider())
        validation = validate_tool_result("order_lookup", result)

        self.assertFalse(result["success"])
        self.assertEqual(validation["status"], "malformed")
        self.assertNotEqual(validation["status"], "success")

    def test_rest_provider_configuration_failure_is_safe(self):
        result = RestOrderProvider("https://business.example", None, 5).get_order(
            "ORD-1001"
        )

        self.assertEqual(result.status, "configuration_error")
        self.assertNotIn("secret", str(result).lower())

    @patch("integrations.urlopen", side_effect=TimeoutError)
    def test_rest_timeout_is_controlled(self, mocked_urlopen):
        result = RestOrderProvider(
            "https://business.example", "secret-token", 1
        ).get_order("ORD-1001")

        self.assertEqual(result.status, "timeout")
        self.assertNotIn("secret-token", str(result))
        mocked_urlopen.assert_called_once()

    @patch("integrations.urlopen")
    def test_rest_authentication_failure_does_not_expose_credentials(
        self,
        mocked_urlopen,
    ):
        mocked_urlopen.side_effect = HTTPError(
            "https://business.example/orders/ORD-1001",
            401,
            "unauthorized",
            {},
            None,
        )
        result = RestOrderProvider(
            "https://business.example", "secret-token", 5
        ).get_order("ORD-1001")

        self.assertEqual(result.status, "authentication_failure")
        self.assertNotIn("secret-token", str(result))

    @patch("integrations.urlopen")
    def test_rest_404_is_not_found(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError(
            "https://business.example/orders/ORD-9999",
            404,
            "not found",
            {},
            None,
        )
        result = RestOrderProvider(
            "https://business.example", "secret-token", 5
        ).get_order("ORD-9999")

        self.assertEqual(result.status, "not_found")
        self.assertEqual(result.message, "No order was found for order ID ORD-9999.")
        self.assertNotIn("secret-token", str(result))

    @patch("integrations.urlopen", return_value=FakeResponse(b"not-json"))
    def test_rest_malformed_response_is_controlled(self, mocked_urlopen):
        result = RestOrderProvider(
            "https://business.example", "secret-token", 5
        ).get_order("ORD-1001")

        self.assertEqual(result.status, "malformed")
        self.assertNotIn("secret-token", str(result))
        mocked_urlopen.assert_called_once()

    @patch(
        "integrations.urlopen",
        return_value=FakeResponse(
            b'{"orderId": "external-1", "order_status": "Shipped", '
            b'"trackingNumber": "TRK-1", "expectedDelivery": "Tomorrow"}'
        ),
    )
    def test_rest_provider_normalizes_external_fields(self, mocked_urlopen):
        result = RestOrderProvider(
            "https://business.example", "secret-token", 5
        ).get_order("ORD-1001")

        self.assertEqual(result.status, "success")
        self.assertEqual(result.data["order_id"], "ORD-1001")
        self.assertEqual(result.data["tracking_number"], "TRK-1")
        mocked_urlopen.assert_called_once()

    @patch("integrations.urlopen")
    def test_rest_request_matches_external_contract(self, mocked_urlopen):
        captured = {}

        def capture_request(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(
                b'{"status": "Shipped", "tracking_number": "TRK-1", '
                b'"expected_delivery": "Tomorrow"}'
            )

        mocked_urlopen.side_effect = capture_request
        result = RestOrderProvider(
            "https://business.example/", "secret-token", 3
        ).get_order("ORD-1001")

        request = captured["request"]
        self.assertEqual(result.status, "success")
        self.assertEqual(
            request.full_url,
            "https://business.example/orders/ORD-1001",
        )
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.headers["X-api-key"], "secret-token")
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertEqual(captured["timeout"], 3)

    @patch("integrations.urlopen")
    def test_rest_http_failure_is_controlled(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError(
            "https://business.example/orders/ORD-1001",
            500,
            "server error",
            {},
            None,
        )
        result = RestOrderProvider(
            "https://business.example", "secret-token", 5
        ).get_order("ORD-1001")

        self.assertEqual(result.status, "http_error")
        self.assertNotIn("secret-token", str(result))

    @patch("integrations.urlopen", side_effect=URLError("connection refused"))
    def test_rest_unavailable_is_controlled(self, mocked_urlopen):
        result = RestOrderProvider(
            "https://business.example", "secret-token", 5
        ).get_order("ORD-1001")

        self.assertEqual(result.status, "unavailable")
        self.assertNotIn("secret-token", str(result))
        mocked_urlopen.assert_called_once()

    def test_audit_metadata_contains_provider_status_but_not_secret(self):
        trace = ExecutionTrace("Track order ORD-1001")
        trace.update(
            integration_provider="rest",
            integration_status="authentication_failure",
        )

        self.assertEqual(trace.event["integration_provider"], "rest")
        self.assertEqual(
            trace.event["integration_status"],
            "authentication_failure",
        )
        self.assertNotIn("secret-token", str(trace.event))

    @patch("tools.ORDER_PROVIDER", DemoOrderProvider("business_data.json"))
    def test_orchestration_uses_tool_without_knowing_provider(self):
        result = ControlledOrchestrator().run_order_workflow(
            "Track order ORD-1001",
            "ORD-1001",
            execute_tool,
            validate_tool_result,
            lambda question: {},
            lambda question, context: "unused",
            lambda data, identifier: f"Order {identifier}: {data['status']}",
        )

        self.assertEqual(result.response, "Order ORD-1001: Shipped")
        self.assertEqual(result.response_method, "business_tool")
        self.assertEqual(result.tool_validation["provider_type"], "demo")


if __name__ == "__main__":
    unittest.main()