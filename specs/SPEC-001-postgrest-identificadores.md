# SPEC-001 — Recuperar consultas de movimientos en PostgREST

**Estado:** Implementada en rama de corrección; pendiente de validación y despliegue.
**Tipo:** Bugfix acotado de código Python.
**Entorno:** Streamlit Community Cloud + Supabase Postgres 17.
**Fuente de verdad:** Tabla `public.movimientos` y código `app.py` en GitHub.

## Problema observable
Al iniciar sesión y entrar en el ERP, Streamlit falla en `app.py:435` con `postgrest.exceptions.APIError`. Log de PostgreSQL: `column movimientos.\\\"Activo\\\" does not exist`. La columna real se llama `Activo` (A mayúscula). Algunos argumentos a `.eq()` y `.select()` contienen comillas dobles incrustadas en el nombre de columna.

## Objetivo
Restaurar lectura de folios activos y compatibilidad del CRUD de folios con los nombres de columna existentes en PostgREST, sin alterar estructura ni datos.

## Cambios permitidos
- Reemplazar identificadores literales `'"Activo"'` por `'Activo'` en filtros.
- Reemplazar identificadores literales `'"Folio"'` por `'Folio'` en filtros y consultas `select`.
- Crear prueba de regresión estática para evitar reincidir.

## Restricciones
- No modificar tablas, políticas RLS, cuentas, contraseñas ni registros.
- No cambiar lógica de negocio o pantallas no relacionadas.
- No fusionar o desplegar automáticamente sin verificar en VS Code y Streamlit.

## Criterios de aceptación
1. `python -m unittest discover -s tests -v` termina sin fallos.
2. La app compila: `python -m py_compile app.py`.
3. Login con cuenta existente de prueba y Dashboard sin error `42703`.
4. En Operaciones, abrir lista de folios, buscar uno existente y validar su detalle sin crear, borrar o modificar datos reales durante la prueba.
5. En los logs de Supabase no aparece un nuevo error por columna `"Activo"` o `"Folio"` mal interpretada.
6. El diff no contiene cambios a archivos de BD, secretos ni reglas de acceso.

## Verificación de solo lectura realizada
Al investigar, el 27-10-2026, existían 33 movimientos, de los cuales 30 estaban activos. Estas cantidades solo son observaciones del momento, no deben codificarse como expectativas fijas.

## Reversión
Si aparecen regresiones tras desplegar: restaurar commit anterior de la rama de despliegue mediante `git revert` del merge; revisar logs de Streamlit y Supabase. No hay rollback de base de datos porque esta especificación no la modifica.

## Fuera de alcance / siguientes especificaciones
- Autorización de acceso con políticas RLS y mínimo privilegio.
- Recuperación del motor MRP y definición precisa de relación proveedor/departamento.
- Carga trazable e idempotente de ventas históricas.
- Versionado de dependencias, CI y migraciones de esquema.
