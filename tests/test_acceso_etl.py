import unittest

from acceso_etl import puede_acceder_etl


class AccesoEtlTests(unittest.TestCase):
    def test_solo_administrador_o_jefe_de_cedis(self):
        casos = [
            ("Admin Master", "CEDIS", True),
            ("Admin Master", "Querétaro", True),
            ("Jefe de Área", "CEDIS", True),
            ("Jefe de Área", "Querétaro", False),
            ("Gerente de Sucursal", "CEDIS", False),
            ("Sin Rol", "CEDIS", False),
            (None, "CEDIS", False),
        ]
        for rol, sucursal, permitido in casos:
            with self.subTest(rol=rol, sucursal=sucursal):
                self.assertEqual(puede_acceder_etl(rol, sucursal), permitido)


if __name__ == "__main__":
    unittest.main()
