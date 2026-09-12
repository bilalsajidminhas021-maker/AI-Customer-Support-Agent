import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import operational
from admin import build_go_live_checklist
from knowledge import knowledge_base_status
from operational import production_readiness
from tenant_context import TenantContext


class Day14OnboardingTests(unittest.TestCase):
    def make_context(self, directory):
        root = Path(directory)
        return TenantContext(
            "onboarding",
            "Example Business",
            "support@example.com",
            "Monday-Friday",
            str(root / "knowledge"),
            str(root / "business_data.json"),
            str(root / "cases.json"),
            str(root / "audit.jsonl"),
        ).validate()

    def test_knowledge_status_lists_pdfs_and_handles_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            knowledge_path = Path(directory) / "knowledge"
            knowledge_path.mkdir()
            (knowledge_path / "customer_policy__returns.pdf").write_bytes(b"pdf")
            (knowledge_path / "notes.txt").write_text("ignored", encoding="utf-8")

            status = knowledge_base_status(str(knowledge_path), index_available=True)
            self.assertTrue(status["directory_exists"])
            self.assertEqual(status["pdf_count"], 1)
            self.assertEqual(status["customer_policy_pdf_count"], 1)
            self.assertEqual(status["document_names"], ["customer_policy__returns.pdf"])
            self.assertTrue(status["ready"])

            empty = knowledge_base_status(str(Path(directory) / "empty"))
            self.assertFalse(empty["directory_exists"])
            self.assertEqual(empty["pdf_count"], 0)
            self.assertFalse(empty["ready"])

    def test_provider_readiness_distinguishes_demo_and_rest_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.make_context(directory)
            with patch.object(operational, "BUSINESS_PROVIDER", "demo"):
                demo = production_readiness(context)
            self.assertTrue(demo["checks"]["provider"]["ok"])
            self.assertTrue(demo["checks"]["provider"]["endpoint_configured"])
            self.assertTrue(demo["checks"]["provider"]["authentication_configured"])

            with patch.object(operational, "BUSINESS_PROVIDER", "rest"), patch.object(
                operational, "BUSINESS_API_BASE_URL", ""
            ), patch.object(operational, "BUSINESS_API_KEY", None):
                rest = production_readiness(context)
            self.assertFalse(rest["checks"]["provider"]["ok"])
            self.assertFalse(rest["checks"]["provider"]["endpoint_configured"])
            self.assertFalse(rest["checks"]["provider"]["authentication_configured"])

    def test_checklist_reflects_missing_knowledge_and_evaluation(self):
        readiness = {
            "ready": False,
            "checks": {
                "configuration": {"ok": True},
                "api_key": {"ok": True},
                "admin_security": {"ok": True},
                "provider": {
                    "ok": True,
                    "provider_type": "demo",
                    "endpoint_configured": True,
                    "authentication_configured": True,
                    "message": "configured",
                },
                "knowledge_base": {"usable": False},
                "knowledge_index": {"available": False},
                "business_data": {"usable": True},
                "audit_storage": {"usable": True},
                "case_storage": {"usable": True},
            },
        }
        knowledge = {
            "directory_exists": True,
            "pdf_count": 0,
            "index_available": False,
        }
        checklist = build_go_live_checklist(readiness, knowledge, None)
        by_item = {item["Item"]: item for item in checklist}
        self.assertEqual(by_item["Knowledge base"]["Status"], "WARNING")
        self.assertIn("Add approved PDF", by_item["Knowledge base"]["Details"])
        self.assertEqual(by_item["Knowledge index"]["Status"], "NOT READY")
        self.assertEqual(by_item["Evaluation readiness"]["Status"], "NOT READY")
        self.assertEqual(by_item["Deployment readiness"]["Status"], "NOT READY")

    def test_readiness_output_contains_statuses_but_not_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self.make_context(directory)
            with patch.object(operational, "GOOGLE_API_KEY", "api-secret"), patch.object(
                operational, "ADMIN_PASSWORD_HASH", "password-secret"
            ):
                readiness = production_readiness(context)
            serialized = str(readiness)
            self.assertNotIn("api-secret", serialized)
            self.assertNotIn("password-secret", serialized)
            self.assertIn("configured", serialized)


if __name__ == "__main__":
    unittest.main()
