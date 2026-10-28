# SPEC-003 — Ventas históricas SICAR

## Estado y alcance

Implementación en borrador. La vista previa y las pruebas usan solo lecturas o datos sintéticos. No se ha ejecutado ninguna carga real. Operaciones, Folios, estructura de Supabase y políticas RLS quedan fuera del alcance.

## Esquema verificado el 28 de octubre de 2026

- `ventas_historicas`: `id` entero; `sucursal`, `fecha`, `documento`, `folio`, `cliente`, `sku`, `desc_sicar`, `cantidad`, `importe`, `total`, `ubicacion_id` y `carga_id`. `ubicacion_id` referencia `ubicaciones.id`; `carga_id` referencia `cargas_etl.id`. Existe el índice único parcial `ventas_v1_identidad_uidx` sobre `(ubicacion_id, fecha, folio, sku)` cuando los cuatro campos tienen valor.
- `cargas_etl`: `id`, `tipo_reporte`, `nombre_archivo`, `hash_archivo` (`bytea`, SHA-256 de 32 bytes), `ubicacion_id`, fechas de cobertura, conteos, usuario, estado y detalle de error. El hash tiene índice, pero no restricción única.
- `cargas_etl_rechazos`: `carga_id`, `fila_origen`, `motivos` (`text[]`) y `datos_originales` (`jsonb`).
- `ubicaciones`: catálogo de sucursales y CEDIS. La selección de sucursal es obligatoria; solo se ofrecen ubicaciones con `tipo = SUCURSAL`.

Las tres tablas de ventas y auditoría estaban vacías durante la revisión de solo lectura. No se hicieron cambios en datos ni esquema.

## Contrato de importación

1. Leer la hoja `GeneralV` desde la fila 6 (encabezados después de cinco filas). Se admiten las columnas `Unnamed: 1/4/19/27` o `Column2/5/20/28` para cantidad, descripción, importe y total, además de `Fecha`, `Folio`, `Cliente` y `Documento`.
2. Propagar fecha, folio y cliente dentro del formato tabular de SICAR. `Documento` se toma solo del encabezado del folio: en las líneas de detalle esa columna contiene la unidad (`PZA`, `CAJA`, etc.). Una fecha ausente sin valor anterior o una fecha inválida causa rechazo. Nunca se sustituye por la fecha actual.
3. Validar fecha de calendario, folio no vacío, SKU no vacío entre corchetes, cantidad numérica positiva e importes numéricos no negativos cuando existan. Los SKU que aún no estén en el catálogo maestro permanecen válidos para que MDM pueda conciliarlos.
4. Comparar el SHA-256 del archivo con cargas completadas y la clave `(ubicacion_id, fecha, folio, sku)` contra ventas existentes y entre archivos seleccionados. Mostrar por separado filas válidas, rechazadas y duplicadas. Varias líneas con la misma clave en un archivo o entre archivos distintos quedan señaladas como ambiguas y bloquean todo el guardado: pueden ser ventas legítimas y el esquema actual no puede representarlas individualmente. Una identidad existente de otro archivo también bloquea el guardado, incluso si sus importes coinciden.
5. Guardar solo después de una vista previa explícita sin bloqueos. Revalidar hash y claves antes del primer insert. Registrar la carga y los rechazos asociados; insertar ventas en lotes con `ubicacion_id` y `carga_id`. Al reintentar un archivo cuya carga quedó `FALLIDA`, las filas idénticas ya guardadas con ese `carga_id` se reconocen, se reutiliza el mismo registro de carga y solo se insertan las ventas y los rechazos pendientes. El índice único es la última defensa ante cargas concurrentes.
6. Paginar lecturas de ventas de Dashboard y MRP en páginas de hasta 1,000 filas, con orden estable. La detección de duplicados también pagina las ventas del rango de fechas de cada sucursal.

## Límites y recuperación

- No hay una transacción única entre llamadas PostgREST. Si falla un lote después de insertar filas, la carga queda `FALLIDA` y puede haber ventas ya guardadas. Las pruebas locales con fallas inducidas en ventas y rechazos confirman recuperación sin duplicar filas ni auditoría. Antes de reintentar se debe generar una vista previa nueva. Si quedan varias cargas fallidas del mismo archivo o el estado cambia durante la recuperación, se requiere conciliación manual.
- El hash no es único en la base. Dos sesiones simultáneas pueden crear dos encabezados de carga; la restricción única de ventas impedirá duplicar filas, pero una carga puede terminar como fallida.
- La [documentación oficial de SICAR](https://manual.sicar.mx/Ventas/) indica que leer repetidamente un código puede aumentar la cantidad de un artículo, pero no demuestra que `GeneralV` nunca emita dos líneas con el mismo SKU y folio. Un archivo auténtico es necesario para comprobarlo y decidir si se requiere un identificador de línea o una consolidación explícita de cantidades e importes.
- La escritura de `bytea` por PostgREST no se ha probado en un Supabase aislado. La prueba local emula almacenamiento binario y consultas por hash; no prueba el servidor. El formato hexadecimal `\x` usado por el código coincide con la [documentación de PostgreSQL para `bytea`](https://www.postgresql.org/docs/current/datatype-binary.html).
- Los totales de cantidad e importe pasan una prueba con un Excel sintético y una conciliación de solo lectura con el archivo real descrito abajo. Este flujo no actualiza ventas anteriores.

## Validación de un archivo real de SICAR

Se revisó un archivo `GeneralV` de Querétaro en modo de solo lectura, sin copiarlo al repositorio. El parser reconoció las columnas esperadas, separó las líneas de venta de un pie de página, y las sumas de cantidad e importe coincidieron con el reporte. Los totales de detalle coincidieron con el total del encabezado para cada folio. En este archivo no hubo identidades `(ubicacion_id, fecha, folio, sku)` repetidas; esto no demuestra que otros reportes nunca las tengan. La revisión detectó y corrigió el mapeo de `Documento` descrito arriba. No se publican filas ni importes del archivo.

## Criterios para la primera carga

Confirmar con negocio la conciliación de solo lectura y el significado de `importe` y `total` por renglón. Si otros archivos presentan identidades repetidas, resolverlas sin perder ventas; si hace falta, preparar una migración de esquema separada. Validar escritura y lectura de `bytea` y la recuperación en un proyecto o rama de Supabase de ensayo, verificar permisos y auditoría, y autorizar una ventana de carga y plan de conciliación. No ejecutar la carga real como parte de este PR.
