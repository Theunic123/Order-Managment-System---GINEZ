import io
import unittest
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd

from data_access import leer_todos
from ventas_etl import ArchivoVentas, guardar_vista_previa, preparar_vista_previa, validar_archivo


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


class ConsultaEnMemoria:
    """Emula el contrato usado de PostgREST; bytea se almacena como bytes."""

    def __init__(self, base, tabla):
        self.base = base
        self.tabla = tabla
        self.filtros = []
        self.rango = None
        self.orden = None
        self.operacion = "select"
        self.payload = None

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

    def order(self, columna):
        self.orden = columna
        return self

    def range(self, inicio, final):
        self.rango = (inicio, final)
        return self

    def insert(self, payload):
        self.operacion, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.operacion, self.payload = "update", payload
        return self

    def _filtradas(self):
        filas = self.base.tablas[self.tabla]
        for metodo, columna, valor in self.filtros:
            if columna == "hash_archivo":
                self.base.consultas_hash += 1
                valor = bytes.fromhex(valor[2:])
            if metodo == "eq":
                filas = [fila for fila in filas if fila.get(columna) == valor]
            elif metodo == "gte":
                filas = [fila for fila in filas if fila.get(columna) >= valor]
            else:
                filas = [fila for fila in filas if fila.get(columna) <= valor]
        return filas

    def execute(self):
        if self.operacion == "insert":
            if self.tabla == "ventas_historicas":
                self.base.lotes_ventas += 1
                if self.base.fallar_lote == self.base.lotes_ventas:
                    raise RuntimeError("fallo simulado en lote de ventas")
            if self.tabla == "cargas_etl_rechazos":
                self.base.lotes_rechazos += 1
                if self.base.fallar_rechazo == self.base.lotes_rechazos:
                    raise RuntimeError("fallo simulado en lote de rechazos")
            entradas = self.payload if isinstance(self.payload, list) else [self.payload]
            guardadas = []
            for entrada in entradas:
                fila = dict(entrada)
                if self.tabla == "cargas_etl":
                    fila["hash_archivo"] = bytes.fromhex(fila["hash_archivo"][2:])
                fila["id"] = len(self.base.tablas[self.tabla]) + 1
                self.base.tablas[self.tabla].append(fila)
                guardadas.append(fila)
            return SimpleNamespace(data=guardadas)
        filas = self._filtradas()
        if self.operacion == "update":
            for fila in filas:
                fila.update(self.payload)
            return SimpleNamespace(data=filas)
        if self.orden:
            filas = sorted(filas, key=lambda fila: fila[self.orden])
        if self.rango:
            filas = filas[self.rango[0]:self.rango[1] + 1]
        return SimpleNamespace(data=filas)


class SupabaseEnMemoria:
    def __init__(self):
        self.tablas = {nombre: [] for nombre in (
            "ventas_historicas", "cargas_etl", "cargas_etl_rechazos"
        )}
        self.fallar_lote = None
        self.fallar_rechazo = None
        self.lotes_ventas = 0
        self.lotes_rechazos = 0
        self.consultas_hash = 0

    def table(self, nombre):
        return ConsultaEnMemoria(self, nombre)


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

    def test_lineas_repetidas_en_folio_bloquean_guardado(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
        ])
        base = SupabaseFalso(ventas_historicas=[])
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertEqual(len(vista.rechazados), 2)
        self.assertEqual(len(vista.bloqueos), 1)
        with self.assertRaisesRegex(ValueError, "identidades ambiguas"):
            guardar_vista_previa(base, vista, "usuario")

    def test_archivo_completado_es_duplicado(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
        ])
        base = SupabaseFalso(cargas_etl=[{
            "id": 1, "tipo_reporte": "VENTAS", "hash_archivo": "\\x" + archivo.hash_archivo,
            "estatus": "COMPLETADA",
        }])
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertEqual(len(vista.duplicados), 1)
        self.assertFalse(vista.bloqueos)

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
        self.assertIn("identidad ya existente", vista.rechazados.iloc[0]["motivos"][0])
        self.assertTrue(vista.bloqueos)

    def test_misma_identidad_en_dos_archivos_no_se_descarta(self):
        fila = {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2}
        primero = archivo_de_prueba([fila], "semana_1.xlsx")
        segundo = archivo_de_prueba([fila, {**fila, "Folio": "A2"}], "semana_2.xlsx")
        vista = preparar_vista_previa(SupabaseFalso(), [primero, segundo])
        self.assertEqual(len(vista.rechazados), 2)
        self.assertEqual(len(vista.validos), 1)
        self.assertEqual(len(vista.bloqueos), 1)

    def test_identidad_existente_igual_de_otro_archivo_es_ambigua(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
        ])
        base = SupabaseFalso(ventas_historicas=[{
            "id": 1, "carga_id": 42, "ubicacion_id": 1, "fecha": "2026-10-27",
            "folio": "A1", "sku": "SKU1", "cantidad": "2", "documento": None,
            "cliente": None, "desc_sicar": "Producto", "importe": None, "total": None,
        }])
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertTrue(vista.duplicados.empty)
        self.assertEqual(len(vista.bloqueos), 1)

    def test_sucursal_obligatoria(self):
        archivo = archivo_de_prueba([{"Fecha": "27/10/2026", "Folio": "1", "Unnamed: 4": "[SKU]", "Unnamed: 1": 1}])
        with self.assertRaisesRegex(ValueError, "sucursal"):
            validar_archivo(ArchivoVentas(archivo.nombre, archivo.contenido, 0, ""))

    def test_cantidades_y_totales_coinciden_con_archivo_sintetico(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Uno [SKU1]",
             "Unnamed: 1": 2, "Unnamed: 19": "1,234.50", "Unnamed: 27": "1,234.50"},
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Dos [SKU2]",
             "Unnamed: 1": 3, "Unnamed: 19": "10.25", "Unnamed: 27": "10.25"},
        ])
        validas, rechazadas, _ = validar_archivo(archivo)
        self.assertFalse(rechazadas)
        self.assertEqual(sum(Decimal(f["cantidad"]) for f in validas), Decimal("5"))
        self.assertEqual(sum(Decimal(f["total"]) for f in validas), Decimal("1244.75"))

    def test_documento_proviene_del_encabezado_no_de_la_unidad(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Documento": "Ticket",
             "Total   ": 100, "Unnamed: 1": None, "Unnamed: 4": None},
            {"Fecha": None, "Folio": None, "Documento": "PZA",
             "Unnamed: 1": 2, "Unnamed: 4": "Producto [SKU1]", "Unnamed: 27": 100},
        ])
        validas, rechazadas, _ = validar_archivo(archivo)
        self.assertEqual(len(validas), 1)
        self.assertFalse(rechazadas)
        self.assertEqual(validas[0]["documento"], "Ticket")

    def test_detalle_suma_total_del_folio_y_rechaza_pie(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Documento": "Ticket",
             "Total   ": 30},
            {"Documento": "PZA", "Unnamed: 1": 1, "Unnamed: 4": "Uno [SKU1]",
             "Unnamed: 27": 10},
            {"Documento": "PZA", "Unnamed: 1": 2, "Unnamed: 4": "Dos [SKU2]",
             "Unnamed: 27": 20},
            {"Documento": "Página 1", "Unnamed: 1": "Total", "Unnamed: 4": "Resumen"},
        ])
        validas, rechazadas, _ = validar_archivo(archivo)
        self.assertEqual(len(validas), 2)
        self.assertEqual(len(rechazadas), 1)
        self.assertEqual(sum(Decimal(f["total"]) for f in validas), Decimal("30"))

    def test_paginacion_supera_mil(self):
        base = SupabaseFalso(ventas_historicas=[{"id": i} for i in range(1501)])
        filas = leer_todos(base, "ventas_historicas", "id")
        self.assertEqual(len(filas), 1501)
        self.assertEqual(base.llamadas, [
            ("ventas_historicas", (0, 999)), ("ventas_historicas", (1000, 1999)),
        ])

    def test_hash_bytea_y_recuperacion_de_carga_parcial_aislada(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
            {"Fecha": "27/10/2026", "Folio": "A2", "Unnamed: 4": "Producto [SKU2]", "Unnamed: 1": 3},
        ])
        base = SupabaseEnMemoria()
        vista = preparar_vista_previa(base, [archivo])
        self.assertEqual(len(vista.validos), 2)
        base.fallar_lote = 2
        with self.assertRaisesRegex(RuntimeError, "Carga interrumpida"):
            guardar_vista_previa(base, vista, "usuario", tamano_lote=1)
        self.assertEqual(len(base.tablas["ventas_historicas"]), 1)
        self.assertEqual(base.tablas["cargas_etl"][0]["estatus"], "FALLIDA")
        self.assertEqual(base.tablas["cargas_etl"][0]["hash_archivo"],
                         bytes.fromhex(archivo.hash_archivo))

        base.fallar_lote = None
        vista = preparar_vista_previa(base, [archivo])
        self.assertEqual((len(vista.validos), len(vista.duplicados)), (1, 1))
        self.assertFalse(vista.bloqueos)
        self.assertEqual(guardar_vista_previa(base, vista, "usuario", tamano_lote=1), 1)
        self.assertEqual(len(base.tablas["ventas_historicas"]), 2)
        self.assertEqual(len(base.tablas["cargas_etl"]), 1)
        self.assertEqual({fila["carga_id"] for fila in base.tablas["ventas_historicas"]}, {1})
        self.assertEqual(base.tablas["cargas_etl"][-1]["estatus"], "COMPLETADA")
        self.assertGreater(base.consultas_hash, 0)
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertEqual(len(vista.duplicados), 2)

    def test_reanuda_auditoria_si_falla_guardar_rechazos(self):
        archivo = archivo_de_prueba([
            {"Fecha": "27/10/2026", "Folio": "A1", "Unnamed: 4": "Producto [SKU1]", "Unnamed: 1": 2},
            {"Fecha": "fecha inválida", "Folio": "A2", "Unnamed: 4": "Producto [SKU2]", "Unnamed: 1": 1},
        ])
        base = SupabaseEnMemoria()
        vista = preparar_vista_previa(base, [archivo])
        base.fallar_rechazo = 1
        with self.assertRaisesRegex(RuntimeError, "Carga interrumpida"):
            guardar_vista_previa(base, vista, "usuario")
        self.assertEqual(len(base.tablas["ventas_historicas"]), 1)
        self.assertEqual(base.tablas["cargas_etl"][0]["estatus"], "FALLIDA")

        base.fallar_rechazo = None
        vista = preparar_vista_previa(base, [archivo])
        self.assertTrue(vista.validos.empty)
        self.assertEqual(len(vista.duplicados), 1)
        self.assertEqual(guardar_vista_previa(base, vista, "usuario"), 0)
        self.assertEqual(len(base.tablas["ventas_historicas"]), 1)
        self.assertEqual(len(base.tablas["cargas_etl_rechazos"]), 1)
        self.assertEqual(len(base.tablas["cargas_etl"]), 1)
        self.assertEqual(base.tablas["cargas_etl"][0]["estatus"], "COMPLETADA")


if __name__ == "__main__":
    unittest.main()
