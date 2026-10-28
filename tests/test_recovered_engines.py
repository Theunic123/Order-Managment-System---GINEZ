"""Checks that the recovered ETL and MRP workflows remain callable offline."""

import io
import unittest

import pandas as pd

import etl_engine
import mrp_engine


class FakeBuilder:
    def upsert(self, rows):
        self.rows = rows
        return self

    def execute(self):
        return {"ok": True}


class FakeClient:
    def table(self, name):
        self.builder = FakeBuilder()
        return self.builder


class TestRecoveredEngines(unittest.TestCase):
    def test_forecast_returns_a_suggestion(self):
        sales = pd.DataFrame({
            "sku": ["A"] * 4,
            "desc_sicar": ["Articulo A"] * 4,
            "sucursal": ["CEDIS"] * 4,
            "fecha": ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"],
            "cantidad": [10, 12, 11, 13],
        })
        inventory = pd.DataFrame({
            "id": [1], "sku": ["A"], "sucursal": ["CEDIS"], "existencias": [5]
        })
        catalog = pd.DataFrame({
            "sku": ["A"], "departamento": ["PROV"], "pkg": [1]
        })
        result = mrp_engine.generar_sugerencia_pull(
            "PROV", 0.5, 0.5, "CEDIS", sales, inventory, catalog
        )
        self.assertEqual(result["SKU"].tolist(), ["A"])
        self.assertIn("Sugerencia Sistema", result.columns)

    def test_consolidation_generates_both_excel_sheets(self):
        orders = pd.DataFrame({"id": [1], "sucursal": ["CEDIS"]})
        details = pd.DataFrame({
            "pedido_id": [1], "sku": ["A"], "cantidad_pedida": [10]
        })
        inventory = pd.DataFrame({"sku": ["A"], "existencias": [5]})
        catalog = pd.DataFrame({
            "sku": ["A"], "descripcion": ["Articulo A"],
            "pkg": [1], "precio_compra": [2.5]
        })
        data = mrp_engine.consolidar_y_generar_excel(
            "PROV", orders, details, inventory, catalog
        )
        self.assertEqual(
            pd.ExcelFile(io.BytesIO(data)).sheet_names,
            ["PEDIDO CEDIS", "DISTRIBUCION"],
        )

    def test_etl_serializes_dates_for_upsert(self):
        client = FakeClient()
        frame = pd.DataFrame({
            "sku": ["A"], "fecha": [pd.Timestamp("2026-01-01")]
        })
        self.assertTrue(etl_engine.cargar_a_supabase(client, "dummy", frame))
        self.assertEqual(client.builder.rows[0]["sku"], "A")
        self.assertTrue(client.builder.rows[0]["fecha"].startswith("2026-01-01"))


if __name__ == "__main__":
    unittest.main()
