from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "repair_false_response_sync_20260922.py"


class RepairResponseSyncImportPathTests(unittest.TestCase):
    def test_repo_root_is_added_before_app_import(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)

        app_import_line = None
        sys_path_insert_line = None

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.db":
                app_import_line = node.lineno
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "insert"
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "path"
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "sys"
                ):
                    sys_path_insert_line = node.lineno

        self.assertIsNotNone(app_import_line)
        self.assertIsNotNone(sys_path_insert_line)
        self.assertLess(sys_path_insert_line, app_import_line)
        self.assertIn(
            'ROOT = Path(__file__).resolve().parents[1]',
            source,
        )


if __name__ == "__main__":
    unittest.main()
