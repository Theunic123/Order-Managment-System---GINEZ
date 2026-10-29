"""Pruebas HTTP contra PostgreSQL + PostgREST efímeros de GitHub Actions."""

import os
import unittest
from decimal import Decimal

from postgrest import SyncPostgrestClient

from test_spec003_ventas import archivo_de_prueba
from ventas_etl import guardar_vista_previa, preparar_vista_previa


class _TablaConFalla:
    def __init__(self, tabla, cliente):
        self.tabla = tabla
        self.cliente = cliente

    def insert(self, filas):
        self.cliente.lotes += 1
        if self.cliente.lotes == 2:
            raise RuntimeError("interrupción sintética entre lotes HTTP")
        return self.tabla.insert(filas)

    def __getattr__(self, nombre):
        return getattr(self.tabla, nombre)


class _ClienteConFalla:
    def __init__(self, cliente):
        self.cliente = cliente
        self.lotes = 0

    def table(self, nombre):
        tabla = self.cliente.table(nombre)
        return _TablaConFalla(tabla, self) if nombre == "ventas_historicas" else tabla


@unittest.skipUnless(os.getenv("SPEC003_POSTGREST_URL"), "requiere PostgREST aislado")
class PostgrestRealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = SyncPostgrestClient(os.environ["SPEC003_POSTGREST_URL"])

    def test_bytea_carga_completa_y_segundo_intento(self):
        archivo = archivo_de_prueba([
            {"Fecha": "20/10/2026", "Folio": "CI-A", "Unnamed: 4": "Uno [CI-SKU-A]",
             "Unnamed: 1": 2, "Unnamed: 19": 10, "Unnamed: 27": 20},
            {"Fecha": "20/10/2026", "Folio": "CI-B", "Unnamed: 4": "Dos [CI-SKU-B]",
             "Unnamed: 1": 3, "Unnamed: 19": 10, "Unnamed: 27": 30},
            {"Fecha": "fecha inválida", "Folio": "CI-X", "Unnamed: 4": "Sin SKU",
             "Unnamed: 1": 1},
        ], "ci_carga_completa.xlsx")
        vista = preparar_vista_previa(self.db, [archivo])
        self.assertEqual((len(vista.validos), len(vista.rechazados)), (2, 1))
        self.assertFalse(vista.bloqueos)
        self.assertEqual(guardar_vista_previa(self.db, vista, "ci-sintetico"), 2)

        cargas = (self.db.table("cargas_etl")
                  .select("id,hash_archivo,estatus,filas_validas,filas_rechazadas")
                  .eq("hash_archivo", "\\x" + archivo.hash_archivo).execute().data)
        self.assertEqual(len(cargas), 1)
        self.assertEqual(cargas[0]["hash_archivo"], "\\x" + archivo.hash_archivo)
        self.assertEqual(cargas[0]["estatus"], "COMPLETADA")
        self.assertEqual((cargas[0]["filas_validas"], cargas[0]["filas_rechazadas"]), (2, 1))
        carga_id = cargas[0]["id"]
        ventas = self.db.table("ventas_historicas").select("folio,cantidad,total,carga_id").eq("carga_id", carga_id).execute().data
        self.assertEqual(len(ventas), 2)
        self.assertEqual(sum(Decimal(str(f["cantidad"])) for f in ventas), Decimal("5"))
        self.assertEqual(sum(Decimal(str(f["total"])) for f in ventas), Decimal("50"))
        rechazos = self.db.table("cargas_etl_rechazos").select("id").eq("carga_id", carga_id).execute().data
        self.assertEqual(len(rechazos), 1)

        repetida = preparar_vista_previa(self.db, [archivo])
        self.assertTrue(repetida.validos.empty)
        self.assertEqual(len(repetida.duplicados), 2)
        self.assertEqual(guardar_vista_previa(self.db, repetida, "ci-sintetico"), 0)
        self.assertEqual(len(self.db.table("cargas_etl").select("id").eq("hash_archivo", "\\x" + archivo.hash_archivo).execute().data), 1)
        self.assertEqual(len(self.db.table("ventas_historicas").select("id").eq("carga_id", carga_id).execute().data), 2)

    def test_fallo_entre_lotes_y_recuperacion(self):
        archivo = archivo_de_prueba([
            {"Fecha": "21/10/2026", "Folio": "CI-P1", "Unnamed: 4": "Uno [CI-SKU-P1]", "Unnamed: 1": 1},
            {"Fecha": "21/10/2026", "Folio": "CI-P2", "Unnamed: 4": "Dos [CI-SKU-P2]", "Unnamed: 1": 2},
            {"Fecha": "21/10/2026", "Folio": "CI-P3", "Unnamed: 4": "Tres [CI-SKU-P3]", "Unnamed: 1": 3},
        ], "ci_carga_parcial.xlsx")
        previa = preparar_vista_previa(self.db, [archivo])
        with self.assertRaisesRegex(RuntimeError, "Carga interrumpida"):
            guardar_vista_previa(_ClienteConFalla(self.db), previa, "ci-sintetico", tamano_lote=1)

        cargas = self.db.table("cargas_etl").select("id,estatus").eq("hash_archivo", "\\x" + archivo.hash_archivo).execute().data
        self.assertEqual(len(cargas), 1)
        self.assertEqual(cargas[0]["estatus"], "FALLIDA")
        carga_id = cargas[0]["id"]
        self.assertEqual(len(self.db.table("ventas_historicas").select("id").eq("carga_id", carga_id).execute().data), 1)

        reintento = preparar_vista_previa(self.db, [archivo])
        self.assertEqual((len(reintento.validos), len(reintento.duplicados)), (2, 1))
        self.assertFalse(reintento.bloqueos)
        self.assertEqual(guardar_vista_previa(self.db, reintento, "ci-sintetico", tamano_lote=1), 2)
        cargas_finales = self.db.table("cargas_etl").select("id,estatus,filas_validas").eq("hash_archivo", "\\x" + archivo.hash_archivo).execute().data
        self.assertEqual(len(cargas_finales), 1)
        self.assertEqual(cargas_finales[0]["id"], carga_id)
        self.assertEqual((cargas_finales[0]["estatus"], cargas_finales[0]["filas_validas"]), ("COMPLETADA", 3))
        ventas = self.db.table("ventas_historicas").select("folio,carga_id").eq("carga_id", carga_id).execute().data
        self.assertEqual(len(ventas), 3)
        self.assertEqual({fila["folio"] for fila in ventas}, {"CI-P1", "CI-P2", "CI-P3"})


if __name__ == "__main__":
    unittest.main()
