"""Lecturas completas de PostgREST, que limita las respuestas por página."""


def leer_todos(supabase, tabla, columnas="*", *, ordenar_por="id", filtros=(), tamano_pagina=1000):
    """Devuelve todas las filas en orden estable usando rangos inclusivos."""
    if not 1 <= tamano_pagina <= 1000:
        raise ValueError("tamano_pagina debe estar entre 1 y 1000")

    filas = []
    inicio = 0
    while True:
        consulta = supabase.table(tabla).select(columnas)
        for metodo, columna, valor in filtros:
            consulta = getattr(consulta, metodo)(columna, valor)
        respuesta = consulta.order(ordenar_por).range(
            inicio, inicio + tamano_pagina - 1
        ).execute()
        pagina = respuesta.data or []
        filas.extend(pagina)
        if len(pagina) < tamano_pagina:
            return filas
        inicio += len(pagina)
