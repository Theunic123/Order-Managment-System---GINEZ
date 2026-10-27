"""Pruebas de regresion para nombres de columna en consultas PostgREST.

Se ejecutan sin Streamlit ni credenciales:
    python -m unittest discover -s tests -v
"""
import ast
from pathlib import Path
import unittest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class TestPostgrestIdentifiers(unittest.TestCase):
    def test_filters_use_column_names_without_extra_quotes(self):
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8-sig"))
        violations = []
        filter_methods = {
            "eq", "neq", "gt", "gte", "lt", "lte",
            "like", "ilike", "in_", "is_", "order",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in filter_methods or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                col = first.value
                if col.startswith('"') and col.endswith('"'):
                    violations.append(f"linea {node.lineno}: {node.func.attr}({col!r})")
        self.assertEqual([], violations, "No agregar comillas SQL a identificadores PostgREST")

    def test_movimientos_uses_activo_identifier(self):
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8-sig"))
        active_filter = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "eq"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "Activo"
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == 1
            for node in ast.walk(tree)
        )
        self.assertTrue(active_filter, "El filtro de movimientos debe usar Activo = 1")


if __name__ == "__main__":
    unittest.main()
