"""Validación y carga auditada de ventas SICAR sin modificar datos al previsualizar."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re

import pandas as pd

from data_access import leer_todos


COLUMNAS_VENTA = (
    "sucursal", "ubicacion_id", "fecha", "documento", "folio", "cliente",
    "sku", "desc_sicar", "cantidad", "importe", "total",
)
COLUMNAS_VALIDAS = (*COLUMNAS_VENTA, "archivo", "fila_origen", "hash_archivo", "datos_originales")
COLUMNAS_RECHAZADAS = ("archivo", "fila_origen", "hash_archivo", "motivos", "datos_originales")
COLUMNAS_DUPLICADAS = (*COLUMNAS_VALIDAS, "motivo")
MOTIVO_RECUPERADA = "línea ya guardada en carga parcial del mismo archivo"
MAPEO_SICAR = {
    "Unnamed: 1": "cantidad", "Column2": "cantidad",
    "Unnamed: 4": "desc_cruda", "Column5": "desc_cruda",
    "Unnamed: 19": "importe", "Column20": "importe",
    "Unnamed: 27": "total", "Column28": "total",
    "Fecha": "fecha", "Folio": "folio", "Cliente": "cliente",
    "Documento": "documento",
}


@dataclass(frozen=True)
class ArchivoVentas:
    nombre: str
    contenido: bytes
    ubicacion_id: int
    sucursal: str

    @property
    def hash_archivo(self):
        return hashlib.sha256(self.contenido).hexdigest()


@dataclass
class VistaPreviaVentas:
    archivos: list[ArchivoVentas]
    validos: pd.DataFrame
    rechazados: pd.DataFrame
    duplicados: pd.DataFrame
    filas_leidas: dict[str, int]
    bloqueos: list[str]


def _vacio(valor):
    return valor is None or (not isinstance(valor, (list, dict)) and pd.isna(valor)) or (
        isinstance(valor, str) and not valor.strip()
    )


def _texto(valor):
    if _vacio(valor):
        return None
    if isinstance(valor, (int, float)) and float(valor).is_integer():
        return str(int(valor))
    texto = str(valor).strip()
    return texto or None


def _fecha(valor):
    if _vacio(valor) or isinstance(valor, (int, float)):
        return None
    if isinstance(valor, (date, datetime, pd.Timestamp)):
        return valor.date().isoformat() if isinstance(valor, (datetime, pd.Timestamp)) else valor.isoformat()
    fecha = pd.to_datetime(str(valor).strip(), errors="coerce", dayfirst=True)
    return None if pd.isna(fecha) else fecha.date().isoformat()


def _decimal(valor):
    if _vacio(valor) or isinstance(valor, bool):
        return None
    texto = str(valor).strip().replace("$", "").replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+\.\d{1,2}", texto):
        texto = texto.replace(",", "")
    elif re.fullmatch(r"-?\d+,\d{1,2}", texto):
        texto = texto.replace(",", ".")
    elif not re.fullmatch(r"-?\d+(?:\.\d+)?", texto):
        return None
    try:
        numero = Decimal(texto)
    except InvalidOperation:
        return None
    return format(numero, "f") if numero.is_finite() else None


def _original(fila):
    datos = {}
    for clave, valor in fila.items():
        if _vacio(valor):
            datos[str(clave)] = None
        elif isinstance(valor, (date, datetime, pd.Timestamp)):
            datos[str(clave)] = valor.isoformat()
        else:
            datos[str(clave)] = str(valor)
    return datos


def validar_archivo(archivo):
    """Lee GeneralV y separa líneas de venta válidas de las rechazadas."""
    if not archivo.contenido or not archivo.nombre:
        raise ValueError("El archivo está vacío o no tiene nombre")
    if not isinstance(archivo.ubicacion_id, int) or archivo.ubicacion_id <= 0 or not archivo.sucursal.strip():
        raise ValueError("Selecciona una sucursal registrada en ubicaciones")
    try:
        bruto = pd.read_excel(io.BytesIO(archivo.contenido), sheet_name="GeneralV", skiprows=5, dtype=object)
    except Exception as exc:
        raise ValueError(f"No se pudo leer GeneralV de {archivo.nombre}") from exc
    bruto = bruto.rename(columns=MAPEO_SICAR)
    if bruto.columns.duplicated().any():
        raise ValueError(f"Columnas SICAR ambiguas en {archivo.nombre}")
    faltantes = {"fecha", "folio", "desc_cruda", "cantidad"} - set(bruto.columns)
    if faltantes:
        raise ValueError(f"Faltan columnas SICAR en {archivo.nombre}: {', '.join(sorted(faltantes))}")

    original = bruto.copy()
    for nombre in ("fecha", "folio", "cliente", "documento"):
        if nombre not in bruto:
            bruto[nombre] = None
        bruto[nombre] = bruto[nombre].replace(r"^\s*$", pd.NA, regex=True).ffill()

    validos, rechazados = [], []
    leidas = 0
    for posicion, (_, fila) in enumerate(bruto.iterrows(), start=7):
        if _vacio(fila.get("desc_cruda")) and _vacio(fila.get("cantidad")):
            continue
        leidas += 1
        datos_originales = _original(original.iloc[posicion - 7].to_dict())
        razones = []
        fecha = _fecha(fila.get("fecha"))
        folio = _texto(fila.get("folio"))
        descripcion = _texto(fila.get("desc_cruda"))
        coincidencia = re.search(r"\[([^\]]+)\]", descripcion or "")
        sku = _texto(coincidencia.group(1)) if coincidencia else None
        cantidad = _decimal(fila.get("cantidad"))
        importe = _decimal(fila.get("importe")) if not _vacio(fila.get("importe")) else None
        total = _decimal(fila.get("total")) if not _vacio(fila.get("total")) else None
        if not fecha:
            razones.append("fecha inválida o ausente")
        if not folio or folio.casefold() == "folio":
            razones.append("folio inválido o ausente")
        if not sku:
            razones.append("SKU ausente en la descripción")
        if cantidad is None or Decimal(cantidad) <= 0:
            razones.append("cantidad inválida o no positiva")
        if not _vacio(fila.get("importe")) and (importe is None or Decimal(importe) < 0):
            razones.append("importe inválido")
        if not _vacio(fila.get("total")) and (total is None or Decimal(total) < 0):
            razones.append("total inválido")
        origen = {
            "archivo": archivo.nombre, "fila_origen": posicion,
            "hash_archivo": archivo.hash_archivo, "datos_originales": datos_originales,
        }
        if razones:
            rechazados.append({**origen, "motivos": razones})
            continue
        validos.append({
            "sucursal": archivo.sucursal, "ubicacion_id": archivo.ubicacion_id,
            "fecha": fecha, "documento": _texto(fila.get("documento")),
            "folio": folio, "cliente": _texto(fila.get("cliente")),
            "sku": sku, "desc_sicar": re.sub(r"\[[^\]]+\]", "", descripcion, count=1).strip(),
            "cantidad": cantidad, "importe": importe, "total": total,
            **origen,
        })
    return validos, rechazados, leidas


def _clave(fila):
    return (int(fila["ubicacion_id"]), str(fila["fecha"]), str(fila["folio"]), str(fila["sku"]))


def _valor_comparable(valor):
    if _vacio(valor):
        return None
    try:
        return str(Decimal(str(valor)).normalize())
    except InvalidOperation:
        return str(valor).strip()


def _contenido(fila):
    return (
        *(_texto(fila.get(c)) for c in ("documento", "cliente", "desc_sicar")),
        *(_valor_comparable(fila.get(c)) for c in ("cantidad", "importe", "total")),
    )


def _cargas_del_archivo(supabase, hash_archivo):
    respuesta = (supabase.table("cargas_etl").select("id,estatus,ubicacion_id")
                 .eq("tipo_reporte", "VENTAS")
                 .eq("hash_archivo", "\\x" + hash_archivo).execute())
    return respuesta.data or []


def _hash_ya_cargado(supabase, hash_archivo):
    return any(carga["estatus"] == "COMPLETADA"
               for carga in _cargas_del_archivo(supabase, hash_archivo))


def _existentes(supabase, filas):
    encontrados = {}
    por_ubicacion = {}
    for fila in filas:
        por_ubicacion.setdefault(fila["ubicacion_id"], []).append(fila["fecha"])
    for ubicacion_id, fechas in por_ubicacion.items():
        registros = leer_todos(
            supabase, "ventas_historicas",
            "id,carga_id,ubicacion_id,fecha,folio,sku,documento,cliente,desc_sicar,cantidad,importe,total",
            filtros=(("eq", "ubicacion_id", ubicacion_id),
                     ("gte", "fecha", min(fechas)), ("lte", "fecha", max(fechas))),
        )
        encontrados.update((_clave(fila), fila) for fila in registros)
    return encontrados


def preparar_vista_previa(supabase, archivos):
    """No escribe en Supabase; distingue duplicados exactos y conflictos."""
    unicos = {}
    for archivo in archivos:
        anterior = unicos.get(archivo.hash_archivo)
        if anterior and anterior.ubicacion_id != archivo.ubicacion_id:
            raise ValueError("El mismo archivo fue asignado a dos sucursales")
        unicos.setdefault(archivo.hash_archivo, archivo)
    todas, rechazados, leidas = [], [], {}
    for archivo in unicos.values():
        validas, invalidas, cantidad = validar_archivo(archivo)
        todas.extend(validas)
        rechazados.extend(invalidas)
        leidas[archivo.hash_archivo] = leidas.get(archivo.hash_archivo, 0) + cantidad

    cargas = {h: _cargas_del_archivo(supabase, h) for h in unicos}
    for h, registros in cargas.items():
        if any(c.get("ubicacion_id") not in (None, unicos[h].ubicacion_id)
               for c in registros):
            raise ValueError("El archivo ya tiene otra sucursal en cargas_etl")
    cargados = {h for h, registros in cargas.items()
                if any(c["estatus"] == "COMPLETADA" for c in registros)}
    fallidas = {h: {c["id"] for c in registros if c["estatus"] == "FALLIDA"}
                for h, registros in cargas.items()}
    existentes = _existentes(supabase, todas) if todas else {}
    ocurrencias = {}
    for fila in todas:
        ocurrencias.setdefault(_clave(fila), set()).add(
            (fila["hash_archivo"], fila["fila_origen"])
        )
    ambiguas = {clave for clave, origenes in ocurrencias.items() if len(origenes) > 1}
    validos, duplicados, bloqueos = [], [], []
    for fila in todas:
        clave, contenido = _clave(fila), _contenido(fila)
        if fila["hash_archivo"] in cargados:
            duplicados.append({**fila, "motivo": "archivo ya completado"})
        elif clave in ambiguas:
            rechazados.append({k: fila[k] for k in COLUMNAS_RECHAZADAS if k != "motivos"} |
                               {"motivos": ["varias líneas con la misma identidad; requiere revisión antes de guardar"]})
            bloqueos.append(f"{fila['fecha']} / {fila['folio']} / {fila['sku']}")
        elif clave in existentes:
            existente = existentes[clave]
            if (existente.get("carga_id") in fallidas.get(fila["hash_archivo"], set())
                    and _contenido(existente) == contenido):
                duplicados.append({**fila, "motivo": MOTIVO_RECUPERADA})
            else:
                rechazados.append({k: fila[k] for k in COLUMNAS_RECHAZADAS if k != "motivos"} |
                                   {"motivos": ["identidad ya existente; posible línea legítima de otro archivo"]})
                bloqueos.append(f"{fila['fecha']} / {fila['folio']} / {fila['sku']}")
        else:
            validos.append(fila)
    return VistaPreviaVentas(
        list(unicos.values()), pd.DataFrame(validos, columns=COLUMNAS_VALIDAS),
        pd.DataFrame(rechazados, columns=COLUMNAS_RECHAZADAS),
        pd.DataFrame(duplicados, columns=COLUMNAS_DUPLICADAS), leidas,
        sorted(set(bloqueos)),
    )


def guardar_vista_previa(supabase, vista, usuario, tamano_lote=500):
    """Guarda filas nuevas o reanuda una carga fallida sin repetir sus filas."""
    if vista.bloqueos:
        raise ValueError("La vista previa contiene identidades ambiguas; revisa el archivo antes de guardar")
    if not 1 <= tamano_lote <= 1000:
        raise ValueError("tamano_lote debe estar entre 1 y 1000")
    filas = vista.validos.to_dict("records")
    recuperadas = vista.duplicados[
        vista.duplicados["motivo"] == MOTIVO_RECUPERADA
    ].to_dict("records")
    if not filas and not recuperadas:
        return 0
    hashes = {f["hash_archivo"] for f in filas + recuperadas}
    if any(_hash_ya_cargado(supabase, h) for h in hashes):
        raise ValueError("Un archivo ya se cargó; vuelve a generar la vista previa")
    existentes = _existentes(supabase, filas)
    if any(_clave(fila) in existentes for fila in filas):
        raise ValueError("Los datos cambiaron; vuelve a generar la vista previa")
    if recuperadas:
        existentes_previas = _existentes(supabase, recuperadas)
        for fila in recuperadas:
            actual = existentes_previas.get(_clave(fila))
            fallidas = {c["id"] for c in _cargas_del_archivo(supabase, fila["hash_archivo"])
                        if c["estatus"] == "FALLIDA"}
            if (not actual or actual.get("carga_id") not in fallidas
                    or _contenido(actual) != _contenido(fila)):
                raise ValueError("La carga parcial cambió; vuelve a generar la vista previa")

    insertadas = 0
    for archivo in vista.archivos:
        nuevas = [fila for fila in filas if fila["hash_archivo"] == archivo.hash_archivo]
        previas = [fila for fila in recuperadas if fila["hash_archivo"] == archivo.hash_archivo]
        if not nuevas and not previas:
            continue
        rechazadas = vista.rechazados[
            vista.rechazados["hash_archivo"] == archivo.hash_archivo
        ].to_dict("records")
        omitidas = len(vista.duplicados[
            vista.duplicados["hash_archivo"] == archivo.hash_archivo
        ])
        fechas = [fila["fecha"] for fila in nuevas + previas]
        encabezado = {
            "tipo_reporte": "VENTAS", "nombre_archivo": archivo.nombre,
            "hash_archivo": "\\x" + archivo.hash_archivo,
            "ubicacion_id": archivo.ubicacion_id,
            "fecha_desde": min(fechas), "fecha_hasta": max(fechas),
            "usuario_referencia": usuario,
            "filas_leidas": vista.filas_leidas[archivo.hash_archivo],
            "filas_validas": len(nuevas) + len(previas), "filas_rechazadas": len(rechazadas),
            "estatus": "EN_PROCESO",
            "detalle_error": f"Duplicados omitidos: {omitidas}" if omitidas else None,
        }
        fallidas = [c for c in _cargas_del_archivo(supabase, archivo.hash_archivo)
                    if c["estatus"] == "FALLIDA"
                    and c.get("ubicacion_id") in (None, archivo.ubicacion_id)]
        if len(fallidas) > 1:
            raise RuntimeError("Hay varias cargas fallidas del mismo archivo; requiere conciliación manual")
        if fallidas:
            carga_id = fallidas[0]["id"]
            supabase.table("cargas_etl").update({
                campo: valor for campo, valor in encabezado.items()
                if campo not in ("tipo_reporte", "nombre_archivo", "hash_archivo", "ubicacion_id")
            }).eq("id", carga_id).execute()
        else:
            respuesta = supabase.table("cargas_etl").insert(encabezado).execute()
            if not respuesta.data or "id" not in respuesta.data[0]:
                raise RuntimeError("No se recibió el identificador de la carga ETL")
            carga_id = respuesta.data[0]["id"]
        try:
            ventas = [
                {**{col: fila[col] for col in COLUMNAS_VENTA}, "carga_id": carga_id}
                for fila in nuevas
            ]
            for inicio in range(0, len(ventas), tamano_lote):
                supabase.table("ventas_historicas").insert(
                    ventas[inicio:inicio + tamano_lote]
                ).execute()
                insertadas += len(ventas[inicio:inicio + tamano_lote])
            rechazos_existentes = {
                fila["fila_origen"] for fila in leer_todos(
                    supabase, "cargas_etl_rechazos", "id,fila_origen",
                    filtros=(("eq", "carga_id", carga_id),),
                )
            }
            rechazos = [
                {"carga_id": carga_id, "fila_origen": fila["fila_origen"],
                 "motivos": fila["motivos"], "datos_originales": fila["datos_originales"]}
                for fila in rechazadas if fila["fila_origen"] not in rechazos_existentes
            ]
            for inicio in range(0, len(rechazos), tamano_lote):
                supabase.table("cargas_etl_rechazos").insert(
                    rechazos[inicio:inicio + tamano_lote]
                ).execute()
            supabase.table("cargas_etl").update({"estatus": "COMPLETADA"}).eq("id", carga_id).execute()
        except Exception as exc:
            detalle = f"{type(exc).__name__}: {getattr(exc, 'code', '')}"[:200]
            try:
                supabase.table("cargas_etl").update({
                    "estatus": "FALLIDA", "detalle_error": detalle,
                }).eq("id", carga_id).execute()
            except Exception:
                pass
            raise RuntimeError(
                "Carga interrumpida; algunas filas pueden haberse guardado. "
                "Genera otra vista previa antes de reintentar."
            ) from exc
    return insertadas
