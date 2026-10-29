"""Regla de acceso al módulo de cargas globales."""


def puede_acceder_etl(rol, sucursal):
    return rol == "Admin Master" or (rol == "Jefe de Área" and sucursal == "CEDIS")
