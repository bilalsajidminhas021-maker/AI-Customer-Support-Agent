"""Small replaceable JSON storage adapter for support cases."""

import json
import os
import tempfile
from pathlib import Path


class JsonCaseStorage:
    """Persist support cases in a local JSON document without raising errors."""

    def __init__(self, path="support_cases.json", tenant_id="default"):
        self.path = Path(path).expanduser()
        self.tenant_id = str(tenant_id or "").strip()
        if not self.tenant_id:
            raise ValueError("A tenant ID is required for case storage.")

    def _read_cases(self):
        try:
            if not self.path.exists():
                return {}
            with self.path.open("r", encoding="utf-8") as data_file:
                payload = json.load(data_file)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

        if not isinstance(payload, dict):
            return None

        cases = payload.get("cases", {})
        return cases if isinstance(cases, dict) else None

    def _write_cases(self, cases):
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    {"cases": cases},
                    temporary_file,
                    ensure_ascii=True,
                    indent=2,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, self.path)
            return True
        except (OSError, TypeError, ValueError):
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return False

    def create(self, case):
        """Create a case and return the persisted record, or None on failure."""

        if not isinstance(case, dict):
            return None

        cases = self._read_cases()
        if cases is None:
            return None

        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            return None
        if case.get("tenant_id", "default") != self.tenant_id:
            return None
        if case_id in cases:
            return None

        updated_cases = dict(cases)
        updated_cases[case_id] = dict(case)
        return dict(case) if self._write_cases(updated_cases) else None

    def get(self, case_id):
        """Return one case, or None when it is missing or storage is unavailable."""

        cases = self._read_cases()
        if cases is None:
            return None

        case = cases.get(case_id)
        if not isinstance(case, dict):
            return None
        if case.get("tenant_id", "default") != self.tenant_id:
            return None
        return dict(case)

    def update(self, case_id, updates):
        """Apply field updates and return the persisted case, or None on failure."""

        if not isinstance(updates, dict):
            return None

        cases = self._read_cases()
        if cases is None or case_id not in cases:
            return None

        current_case = cases.get(case_id)
        if (
            not isinstance(current_case, dict)
            or current_case.get("tenant_id", "default") != self.tenant_id
        ):
            return None

        updated_case = dict(current_case)
        updated_case.update(updates)
        updated_cases = dict(cases)
        updated_cases[case_id] = updated_case
        return (
            dict(updated_case)
            if self._write_cases(updated_cases)
            else None
        )

    def find_by_fingerprint(self, fingerprint):
        """Return the first case matching a fingerprint, or None."""

        if not isinstance(fingerprint, str) or not fingerprint:
            return None

        cases = self._read_cases()
        if cases is None:
            return None

        for case in cases.values():
            if (
                isinstance(case, dict)
                and case.get("fingerprint") == fingerprint
                and case.get("tenant_id", "default") == self.tenant_id
            ):
                return dict(case)

        return None

    def list_cases(self):
        """Return only cases belonging to this storage tenant."""

        cases = self._read_cases()
        if cases is None:
            return []
        return [
            dict(case)
            for case in cases.values()
            if isinstance(case, dict)
            and case.get("tenant_id", "default") == self.tenant_id
        ]
