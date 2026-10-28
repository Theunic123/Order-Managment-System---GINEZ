import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from data_access import leer_todos
from ventas_etl import ArchivoVentas, preparar_vista_previa, validar_archivo


class ConsultaFalsa:
    def __init__(self, datos, llamadas, tabla):
        self.datos = datos
        self.llamadas = llamadas
        self.tabla = tabla
        self.filtros = []
        self.limite = None
        self.rango = None
        self.orden = None

    def select(self, columnas):
        return self

    def eq(self, columna, valor):
        self.filtros.append(("eq", columna, valor))
        return self

    def gte(self, columna, valor):
        self.filtros.append(("gte", columna, valor))
        return self

    def lte(self, columna, valor):
        self.filtros.append(("lte", columna, valor))
        return self

    def limit(self, cantidad):
        self.limite = cantidad
        return self

    def order(self, columna):
        self.orden = columna
        return self

    def range(self, inicio, final):
        self.rango = (inicio, final)
        return self

    def execute(self):
        filas = self.datos
        for metodo, columna, valor in self.filtros:
            if metodo == "eq":
                filas = [fila for fila in filas if fila.get(columna) == valor]
            elif metodo == "gte":
                filas = [fila for fila in filas if fila.get(columna) >= valor]
            else:
                filas = [fila for fila in filas if fila.get(columna) <= valor]
        if self.orden:
            filas = sorted(filas, key=lambda fila: fila[self.orden])
        if self.limite is not None:
            filas = filas[:self.limite]
        if self.rango:
            filas = filas[self.rango[0]:self.rango[1] + 1]
        self.llamadas.append((self.tabla, self.rango))
        return SimpleNamespace(data=filas)


class SupabaseFalso:
    def __init__(self, **tablas):
        self.tablas = tablas
        self.llamadas = []

    def table(self, nombre):
        return ConsultaFalsa(self.tablas.get(nombre, []), self.llamadas, nombre)


def archivo_de_prueba(filas, nombre="ventas.xlsx"):
    """Genera la estructura GeneralV esperada, sin usar datos de producción."""
    marco = pd.DataFrame(filas)
    salida = io.BytesIO()
    with pd.ExcelWriter(salida, engine="openpyxl") as escritor:
        marco.to_excel(escritor, sheet_name="GeneralV", startrow=5, index=False)
    return ArchivoVentas(nombre, salida.getvalue(), 1, "Mixquiahuala")


class VentasSicarTests(unittest.TestCase):
    def test_integridad_y_rechazos_sin_fecha_inventada(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
            {"Fecha": "fecha imposible", "Folio": "A2", "Unnamed: 4": "Producto [SKU2]", "Unnamed: 1": 1},
            {"Fecha": "27/10/2026", "Folio": "A3", "Unnamed: 4": "Sin código", "Unnamed: 1": 1},
            {"Fecha": "27/10/2026", "Folio": "A4", "Unnamed: 4": "Producto [SKU4]", "Unnamed: 1": 0},
        ])
        validas, rechazadas, leidas = validar_archivo(archivo)
        self.assertEqual((len(validas), len(rechazadas), leidas), (1, 3, 4))
        self.assertEqual(validas[0]["fecha"], "2026-10-27")
        self.assertEqual(validas[0]["sku"], "SKU1")
        self.assertIn("fecha inválida o ausente", rechazadas[0]["motivos"])
        self.assertIn("SKU ausente en la descripción", rechazadas[1]["motivos"])
        self.assertIn("cantidad inválida o no positiva", rechazadas[2]["motivos"])

    def test_duplicados_archivo_y_clave_existente(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
        ])
        base = SupabaseFalso(ventas_historicas=[])
        vista = preparar_vista_previa(base, [archivo])
        self.assertEqual((len(vista.validos), len(vista.duplicados)), (1, 1))
        existente = vista.validos.iloc[0].to_dict()
        existente["id"] = 1
        base = SupabaseFalso(ventas_historicas=[existente])
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertEqual(len(vista.duplicados), 2)
        base = SupabaseFalso(cargas_etl=[{
            "id": 1, "tipo_reporte": "VENTAS", "hash_archivo": "\\x" + archivo.hash_archivo,
            "estatus": "COMPLETADA",
        }])
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertEqual(len(vista.duplicados), 2)

    def test_conflicto_de_identidad_se_rechaza(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
        ])
        base = SupabaseFalso(ventas_historicas=[{
            "id": 1, "ubicacion_id": 1, "fecha": "2026-10-27", "folio": "A1",
            "sku": "SKU1", "cantidad": 3, "documento": None, "cliente": None,
            "desc_sicar": "Producto", "importe": None, "total": None,
        }])
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertEqual(len(vista.rechazados), 1)
        self.assertIn("misma identidad existente", vista.rechazados.iloc[0]["motivos"][0])

    def test_sucursal_obligatoria(self):
        archivo = archivo_de_prueba([{"Fecha": "27/10/2026", "Folio": "1", "Unnamed: 4": "[SKU]", "Unnamed: 1": 1}])
        with self.assertRaisesRegex(ValueError, "sucursal"):
            validar_archivo(ArchivoVentas(archivo.nombre, archivo.contenido, 0, ""))

    def test_paginacion_supera_mil(self):
        base = SupabaseFalso(ventas_historicas=[{"id": i} for i in range(1501)])
        filas = leer_todos(base, "ventas_historicas", "id")
        self.assertEqual(len(filas), 1501)
        self.assertEqual(base.llamadas, [
            ("ventas_historicas", (0, 999)), ("ventas_historicas", (1000, 1999)),
        ])


if __name__ == "__main__":
    unittest.main()
