import pandas as pd
import numpy as np
import streamlit as st
import datetime
import calendar
import io
import os
import base64
import hashlib
import etl_engine
from supabase import create_client, Client

# --- LIBRERÍAS MATEMÁTICAS ---
from scipy.stats import norm, gamma, skew, gaussian_kde
from xgboost import XGBRegressor
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor
from statsmodels.tsa.holtwinters import ExponentialSmoothing
import warnings
warnings.filterwarnings("ignore")

# ==========================================
# CONFIGURACIÓN DE PÁGINA
# ==========================================
st.set_page_config(page_title="Sistema Logístico - Grupo GINEZ", page_icon="📦", layout="wide")
st.logo("logo.jpg")

st.markdown("""
    <style>
        [data-testid="stLogo"] { height: 3.5rem !important; object-fit: contain !important; }
        button[kind="primary"] { background-color: #E2231A !important; border-color: #E2231A !important; color: white !important; }
        button[kind="primary"]:hover { background-color: #19255A !important; border-color: #19255A !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# CONEXIÓN A SUPABASE
# ==========================================
@st.cache_resource
def init_connection():
    url: str = st.secrets["SUPABASE_URL"]
    key: str = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_connection()

# ==========================================
# FUNCIONES DE SEGURIDAD Y HASHING
# ==========================================
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def registrar_bitacora(usuario, accion, detalle):
    fecha_hora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    supabase.table('bitacora').insert({"fecha": fecha_hora, "usuario": usuario, "accion": accion, "detalle": detalle}).execute()

def check_initial_admin():
    res = supabase.table('usuarios').select('id', count='exact').execute()
    if res.count == 0:
        pass_admin = st.secrets.get("ADMIN_PASS", "clave_local_123")
        usuarios_prueba = [
            {"username": "ginezti", "password": hash_password(pass_admin), "rol": "Admin Master", "correo": "N/A", "sucursal_asignada": "CEDIS"}
        ]
        supabase.table('usuarios').insert(usuarios_prueba).execute()

check_initial_admin()

# ==========================================
# SISTEMA DE LOGIN (Seguro contra recargas)
# ==========================================
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
if "sucursal" not in st.session_state:
    st.session_state.sucursal = "CEDIS"
if "username" not in st.session_state:
    st.session_state.username = "Usuario"
if "rol" not in st.session_state:
    st.session_state.rol = "Sin Rol"

def verificar_credenciales(username, password):
    hashed_pw = hash_password(password)
    res = supabase.table('usuarios').select('*').eq('username', username).eq('password', hashed_pw).execute()
    if len(res.data) > 0:
        return res.data[0]
    return None

if not st.session_state.autenticado:
    st.title("🔐 Grupo GINEZ - Control Logístico")
    with st.form("login_form"):
        st.subheader("Inicio de Sesión")
        usuario_input = st.text_input("Usuario")
        password_input = st.text_input("Contraseña", type="password")
        submit_login = st.form_submit_button("Ingresar")
        
        if submit_login:
            user_data = verificar_credenciales(usuario_input, password_input)
            if user_data:
                st.session_state.autenticado = True
                st.session_state.user_id = user_data['id']
                st.session_state.username = user_data['username']
                st.session_state.rol = user_data['rol']
                st.session_state.correo = user_data.get('correo', 'N/A')
                st.session_state.sucursal = user_data.get('sucursal_asignada', 'CEDIS')
                registrar_bitacora(user_data['username'], "LOGIN", "El usuario inició sesión exitosamente.")
                st.rerun()
            else:
                st.error("❌ Usuario o contraseña incorrectos.")
    st.stop()

def str_to_date(date_str):
    if pd.isna(date_str) or not date_str: return None
    return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

roles_jefes = ["Admin Master", "Jefe de Área"]
st.session_state.is_admin = (st.session_state.rol == "Admin Master")
st.session_state.permiso_edicion = (st.session_state.rol in roles_jefes)
st.session_state.permiso_financiero = (st.session_state.rol in roles_jefes)

# ==========================================
# FUNCIONES DE ARCHIVOS (OPERACIONES)
# ==========================================
def guardar_archivos_fisicos(archivo_pdf, archivo_excel, archivo_pdf_sp, archivo_factura, folio, proveedor, destino, fecha, es_oc):
    ruta_pdf, ruta_excel, ruta_pdf_sp, ruta_factura = None, None, None, None
    fecha_fmt = fecha.strftime("%Y%m%d") if fecha else datetime.date.today().strftime("%Y%m%d")
    bucket = "documentos" 

    if es_oc:
        prov_limpio = str(proveedor).strip().upper().replace(" ", "_") if proveedor else "SIN_PROVEEDOR"
        base_path = f"pedidos/{prov_limpio}"
    else:
        dest_limpio = str(destino).strip().upper().replace(" ", "_") if destino else "SIN_DESTINO"
        base_path = f"traspasos/{dest_limpio}"
        
    def safe_upload(path, f_bytes, c_type):
        try: supabase.storage.from_(bucket).remove([path])
        except: pass
        supabase.storage.from_(bucket).upload(path, f_bytes, file_options={"content-type": c_type})
        return supabase.storage.from_(bucket).get_public_url(path)

    if archivo_pdf is not None: ruta_pdf = safe_upload(f"{base_path}/{folio}_interna.pdf", archivo_pdf.getvalue(), "application/pdf")
    if es_oc and archivo_excel is not None:
        ext = archivo_excel.name.split('.')[-1]
        c_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx" else "application/vnd.ms-excel"
        ruta_excel = safe_upload(f"{base_path}/{prov_limpio}_{fecha_fmt}.{ext}", archivo_excel.getvalue(), c_type)
    if es_oc and archivo_pdf_sp is not None: ruta_pdf_sp = safe_upload(f"{base_path}/{folio}_proveedor.pdf", archivo_pdf_sp.getvalue(), "application/pdf")
    if es_oc and archivo_factura is not None:
        ext = archivo_factura.name.split('.')[-1].lower()
        c_type = "application/pdf" if ext == "pdf" else f"image/{ext}"
        ruta_factura = safe_upload(f"{base_path}/{folio}_factura.{ext}", archivo_factura.getvalue(), c_type)
            
    return ruta_pdf, ruta_excel, ruta_pdf_sp, ruta_factura

# ==========================================
# MODALES
# ==========================================
@st.dialog("🏢 Crear Nuevo Proveedor")
def modal_crear_proveedor():
    st.write("Agrega un nuevo proveedor a la base de datos.")
    nombre_prov = st.text_input("Razón Social / Nombre del Proveedor")
    logo_prov = st.file_uploader("Logo del Proveedor (Opcional)", type=["png", "jpg", "jpeg"])
    
    if st.button("💾 Guardar Proveedor", type="primary", use_container_width=True):
        if not nombre_prov.strip():
            st.error("El nombre del proveedor es obligatorio.")
        else:
            logo_url = None
            if logo_prov:
                path = f"logos/{nombre_prov.strip().upper().replace(' ', '_')}_{logo_prov.name}"
                supabase.storage.from_("documentos").upload(path, logo_prov.getvalue(), file_options={"content-type": f"image/{logo_prov.name.split('.')[-1]}"})
                logo_url = supabase.storage.from_("documentos").get_public_url(path)
            
            supabase.table('proveedores').insert({
                "nombre": nombre_prov.strip().upper(),
                "logo_url": logo_url
            }).execute()
            
            registrar_bitacora(st.session_state.username, "CREAR PROVEEDOR", f"Creó el proveedor {nombre_prov}.")
            st.success("✅ Proveedor creado exitosamente.")
            st.rerun()

@st.dialog("📝 Registrar Nuevo Movimiento", width="large")
def modal_nuevo_registro():
    opcion_doc = st.radio("Tipo", ["Orden de Compra (OC)", "Traspaso Interno (TR)"], horizontal=True)
    es_oc = "OC" in opcion_doc
    tipo_doc_bd = "OC" if es_oc else "TR"

    with st.form("form_captura", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            folio = st.text_input("Folio (Ej. 26-0026 o B-0002)")
            proveedor = st.text_input("Proveedor", disabled=not es_oc)
            fecha = st.date_input("Fecha de alta", datetime.date.today())
        with c2:
            estatus = st.selectbox("Estatus", ["Programado", "Solicitado", "Recibido", "Cancelado"])
            entrega = st.date_input("Fecha estimada de Entrega", value=None)
            recepcion = st.date_input("Fecha real en Recepción", value=None)
        with c3:
            destino = st.selectbox("Destino", ["CEDIS", "Actopan", "Mixquiahuala", "Pachuca", "Querétaro", "Oaxaca", "Veracruz"])
            calidad = st.selectbox("Calidad en Recepción", ["N/A", "Completo", "Parcial", "Dañado"])
            obs = st.text_input("Observaciones (Ej. Faltantes en descarga)")

        if es_oc:
            st.markdown("---")
            cf1, cf2, cf3, cf4 = st.columns(4)
            with cf1: monto = st.number_input("Monto ($)", min_value=0.0, step=100.0)
            with cf2: factura = st.text_input("UUID / Nota de Remisión")
            with cf3: f_pago = st.selectbox("Forma de Pago", ["Transferencia", "Efectivo", "Tarjeta", "Crédito", "N/A"])
            with cf4: est_pago = st.selectbox("Estatus de Pago", ["Pendiente", "Pagado", "Vencido"])
        else:
            monto, factura, f_pago, est_pago = 0.0, None, None, None

        st.markdown("#### 📂 Anexar Documentos")
        col_docs1, col_docs2 = st.columns(2)
        
        with col_docs1: 
            archivo_pdf = st.file_uploader("📄 OC Interna (Con precios)", type=["pdf"])
            if es_oc: archivo_pdf_sp = st.file_uploader("📄 OC Proveedor (Sin precios)", type=["pdf"])
            else: archivo_pdf_sp = None
                
        with col_docs2:
            if es_oc: 
                archivo_excel = st.file_uploader("📊 Distribución (Excel)", type=["xlsx", "xls"])
                archivo_factura = st.file_uploader("🧾 Factura / Remisión Física", type=["pdf", "jpg", "jpeg", "png"])
            else: 
                archivo_excel = None
                archivo_factura = None

        if st.form_submit_button("💾 Guardar", type="primary", use_container_width=True):
            if not folio.strip(): 
                st.error("⚠️ Folio obligatorio.")
            else:
                check_folio = supabase.table('movimientos').select('"Folio"').eq('"Folio"', folio).execute()
                if len(check_folio.data) > 0:
                    st.error("❌ El Folio ya existe.")
                else:
                    r_pdf, r_exc, r_pdf_sp, r_fac = guardar_archivos_fisicos(archivo_pdf, archivo_excel, archivo_pdf_sp, archivo_factura, folio, proveedor, destino, fecha, es_oc)
                    data_insert = {
                        "Folio": folio, "Proveedor": proveedor, "Fecha": fecha.strftime("%Y-%m-%d"),
                        "Estatus": estatus, "Entrega": entrega.strftime("%Y-%m-%d") if entrega else None,
                        "Recepcion": recepcion.strftime("%Y-%m-%d") if recepcion else None,
                        "Obs": obs, "Destino": destino, "Factura": factura, "Calidad": calidad,
                        "Monto": monto, "F_Pago": f_pago, "Est_Pago": est_pago, "Tipo_Doc": tipo_doc_bd,
                        "Ruta_PDF": r_pdf, "Ruta_Excel": r_exc, "Ruta_PDF_SinPrecios": r_pdf_sp, "Ruta_Factura": r_fac, 
                        "Activo": 1
                    }
                    supabase.table('movimientos').insert(data_insert).execute()
                    registrar_bitacora(st.session_state.username, "CREACIÓN", f"Se creó el folio {folio}.")
                    st.session_state.mensaje_exito = f"✅ Movimiento {folio} guardado."
                    st.rerun() 

@st.dialog("⚙️ Editar o Eliminar Registro", width="large")
def modal_editar_registro(df_mov):
    folios_disponibles = df_mov['Folio'].tolist()
    if not folios_disponibles:
        st.info("No hay registros activos.")
        return
    folio_editar = st.selectbox("🔍 Selecciona el Folio:", [""] + folios_disponibles)
    if folio_editar:
        registro = df_mov[df_mov['Folio'] == folio_editar].iloc[0]
        es_oc_edit = registro['Tipo_Doc'] == 'OC'
        with st.form("form_editar"):
            c1, c2, c3 = st.columns(3)
            with c1:
                nuevo_proveedor = st.text_input("Proveedor", value=registro['Proveedor'] if pd.notna(registro['Proveedor']) else "", disabled=not es_oc_edit)
                nueva_fecha = st.date_input("Fecha de alta", value=str_to_date(registro['Fecha']))
            with c2:
                opciones_estatus = ["Programado", "Solicitado", "Recibido", "Cancelado"]
                nuevo_estatus = st.selectbox("Estatus", opciones_estatus, index=opciones_estatus.index(registro['Estatus']) if registro['Estatus'] in opciones_estatus else 0)
                nueva_entrega = st.date_input("Fecha estimada de Entrega", value=str_to_date(registro['Entrega']))
                nueva_recepcion = st.date_input("Fecha real en Recepción", value=str_to_date(registro['Recepcion']))
            with c3:
                opciones_destino = ["CEDIS", "Actopan", "Mixquiahuala", "Pachuca", "Querétaro", "Oaxaca", "Veracruz"]
                nuevo_destino = st.selectbox("Destino", opciones_destino, index=opciones_destino.index(registro['Destino']) if registro['Destino'] in opciones_destino else 0)
                opciones_calidad = ["N/A", "Completo", "Parcial", "Dañado"]
                nueva_calidad = st.selectbox("Calidad en Recepción", opciones_calidad, index=opciones_calidad.index(registro['Calidad']) if registro['Calidad'] in opciones_calidad else 0)
                nueva_obs = st.text_input("Observaciones", value=registro['Obs'] if pd.notna(registro['Obs']) else "")

            if es_oc_edit:
                st.markdown("---")
                cf1, cf2, cf3, cf4 = st.columns(4)
                with cf1: nuevo_monto = st.number_input("Monto ($)", min_value=0.0, step=100.0, value=float(registro['Monto']) if pd.notna(registro['Monto']) else 0.0)
                with cf2: nueva_factura = st.text_input("UUID / Nota de Remisión", value=registro['Factura'] if pd.notna(registro['Factura']) else "")
                with cf3:
                    opc_pago = ["Transferencia", "Efectivo", "Tarjeta", "Crédito", "N/A"]
                    nuevo_fpago = st.selectbox("Forma de Pago", opc_pago, index=opc_pago.index(registro['F_Pago']) if registro['F_Pago'] in opc_pago else 0)
                with cf4:
                    opc_est_pago = ["Pendiente", "Pagado", "Vencido"]
                    nuevo_estpago = st.selectbox("Estatus de Pago", opc_est_pago, index=opc_est_pago.index(registro['Est_Pago']) if registro['Est_Pago'] in opc_est_pago else 0)
            else:
                nuevo_monto, nueva_factura, nuevo_fpago, nuevo_estpago = 0.0, None, None, None

            st.markdown("#### 📂 Reemplazar Documentos")
            c_doc1, c_doc2 = st.columns(2)
            with c_doc1: 
                nuevo_pdf = st.file_uploader("Sobrescribir OC Interna", type=["pdf"])
                if es_oc_edit: nuevo_pdf_sp = st.file_uploader("Sobrescribir OC Proveedor", type=["pdf"])
                else: nuevo_pdf_sp = None
            with c_doc2: 
                if es_oc_edit:
                    nuevo_excel = st.file_uploader("Sobrescribir Distribución", type=["xlsx", "xls"]) 
                    nueva_factura_doc = st.file_uploader("Añadir / Sobrescribir Factura Física", type=["pdf", "jpg", "jpeg", "png"])
                else: 
                    nuevo_excel, nueva_factura_doc = None, None

            if st.form_submit_button("🔄 Actualizar", type="primary", use_container_width=True):
                ruta_pdf_bd, ruta_excel_bd, ruta_pdf_sp_bd, ruta_fac_bd = registro['Ruta_PDF'], registro.get('Ruta_Excel'), registro.get('Ruta_PDF_SinPrecios'), registro.get('Ruta_Factura')
                if nuevo_pdf or nuevo_excel or nuevo_pdf_sp or nueva_factura_doc:
                    n_pdf, n_exc, n_pdf_sp, n_fac = guardar_archivos_fisicos(nuevo_pdf, nuevo_excel, nuevo_pdf_sp, nueva_factura_doc, folio_editar, nuevo_proveedor, nuevo_destino, nueva_fecha, es_oc_edit)
                    if nuevo_pdf: ruta_pdf_bd = n_pdf
                    if nuevo_excel: ruta_excel_bd = n_exc
                    if nuevo_pdf_sp: ruta_pdf_sp_bd = n_pdf_sp
                    if nueva_factura_doc: ruta_fac_bd = n_fac

                data_update = {
                    "Proveedor": nuevo_proveedor, "Fecha": nueva_fecha.strftime("%Y-%m-%d") if nueva_fecha else None,
                    "Estatus": nuevo_estatus, "Entrega": nueva_entrega.strftime("%Y-%m-%d") if nueva_entrega else None,
                    "Recepcion": nueva_recepcion.strftime("%Y-%m-%d") if nueva_recepcion else None,
                    "Obs": nueva_obs, "Destino": nuevo_destino, "Factura": nueva_factura,
                    "Calidad": nueva_calidad, "Monto": nuevo_monto, "F_Pago": nuevo_fpago,
                    "Est_Pago": nuevo_estpago, "Ruta_PDF": ruta_pdf_bd, "Ruta_Excel": ruta_excel_bd,
                    "Ruta_PDF_SinPrecios": ruta_pdf_sp_bd, "Ruta_Factura": ruta_fac_bd
                }
                supabase.table('movimientos').update(data_update).eq('"Folio"', folio_editar).execute()
                registrar_bitacora(st.session_state.username, "ACTUALIZACIÓN", f"Editó folio {folio_editar}.")
                st.session_state.mensaje_exito = f"🔄 Movimiento {folio_editar} actualizado."
                st.rerun()

        if st.button("🗑️ Enviar a Papelera", type="primary", use_container_width=True):
            supabase.table('movimientos').update({"Activo": 0}).eq('"Folio"', folio_editar).execute()
            st.session_state.mensaje_exito = f"🗑️ Movimiento {folio_editar} a papelera."
            st.rerun()

@st.dialog("📄 Gestor de Documentos", width="large")
def modal_ver_documento(df_mov):
    folios_disponibles = df_mov['Folio'].tolist()
    if not folios_disponibles:
        st.info("No hay registros.")
        return
    folio_selec = st.selectbox("🔍 Selecciona el Folio:", [""] + folios_disponibles)
    if folio_selec:
        registro = df_mov[df_mov['Folio'] == folio_selec].iloc[0]
        ruta_pdf, ruta_excel = registro.get('Ruta_PDF'), registro.get('Ruta_Excel')
        ruta_pdf_sp, ruta_fac = registro.get('Ruta_PDF_SinPrecios'), registro.get('Ruta_Factura')
        
        st.markdown(f"### Documentos de: {folio_selec}")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 📄 OC Interna (Con Precios)")
            if pd.notna(ruta_pdf) and str(ruta_pdf).startswith("http"):
                st.markdown(f'<iframe src="{ruta_pdf}" width="100%" height="300"></iframe>', unsafe_allow_html=True)
                st.markdown(f"**[📥 Descargar OC Interna]({ruta_pdf})**")
            else: st.warning("⚠️ Sin OC Interna.")

        with c2:
            st.markdown("#### 📊 Matriz de Distribución")
            if pd.notna(ruta_excel) and str(ruta_excel).startswith("http"): st.markdown(f"**[📥 Descargar Excel]({ruta_excel})**")
            else: st.warning("⚠️ Sin archivo Excel.")
                
        st.markdown("---")
        c3, c4 = st.columns(2)
        with c3:
            st.markdown("#### 📄 OC Proveedor (Sin Precios)")
            if pd.notna(ruta_pdf_sp) and str(ruta_pdf_sp).startswith("http"):
                st.markdown(f'<iframe src="{ruta_pdf_sp}" width="100%" height="300"></iframe>', unsafe_allow_html=True)
                st.markdown(f"**[📥 Descargar OC Proveedor]({ruta_pdf_sp})**")
            else: st.warning("⚠️ Sin OC para Proveedor.")
                
        with c4:
            st.markdown("#### 🧾 Factura / Remisión")
            if pd.notna(ruta_fac) and str(ruta_fac).startswith("http"):
                if "pdf" in str(ruta_fac).lower(): st.markdown(f'<iframe src="{ruta_fac}" width="100%" height="300"></iframe>', unsafe_allow_html=True)
                else: st.image(str(ruta_fac), use_container_width=True)
                st.markdown(f"**[📥 Descargar Factura]({ruta_fac})**")
            else: st.warning("⚠️ Sin evidencia adjunta.")

# ==========================================
# SIMULADOR DEL MOTOR MRP (UI TEST)
# ==========================================
def simular_motor_mrp(proveedor, l_prov, r_prov, sucursal):
    np.random.seed(42)
    datos = []
    for i in range(1, 21):
        sug = np.random.randint(0, 100)
        datos.append({
            "SKU": f"TRU-{1000+i}", "Descripción": f"Producto {proveedor} {i}",
            "Ventas (Últimos días)": np.random.randint(10, 150), "Stock Actual": np.random.randint(0, 50),
            "Mínimo / SS": np.random.randint(5, 20), "Máximo / Target": np.random.randint(50, 120),
            "Sugerencia Sistema": sug, "Desviación (σ)": round(np.random.uniform(1.5, 5.5), 2),
            "CV": round(np.random.uniform(0.1, 3.5), 2)
        })
    df = pd.DataFrame(datos)
    return df.sort_values(by="Sugerencia Sistema", ascending=False).reset_index(drop=True)

@st.dialog("⚡ Carga Rápida de Sugerencias")
def popup_cargar_sugerencia():
    st.markdown("### El algoritmo ha terminado.")
    st.write("¿Deseas precargar las cantidades sugeridas por el sistema en tu columna de pedido?")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ Sí, cargar sugerencia", use_container_width=True):
            st.session_state.df_pedido_actual['CANTIDAD A PEDIR'] = st.session_state.df_pedido_actual['Sugerencia Sistema']
            st.session_state.mostrar_grid = True
            st.rerun()
    with c2:
        if st.button("❌ No, dejar en blanco", use_container_width=True):
            st.session_state.df_pedido_actual['CANTIDAD A PEDIR'] = 0
            st.session_state.mostrar_grid = True
            st.rerun()

# ==========================================
# ESTRUCTURA PRINCIPAL E INTERFAZ
# ==========================================
with st.sidebar:
    st.write(f"👤 **Usuario:** {st.session_state.username}")
    st.write(f"🏢 **Sucursal:** {st.session_state.sucursal}")
    st.write(f"🛡️ **Rol:** {st.session_state.rol}")
    if st.button("Cerrar Sesión", use_container_width=True):
        st.session_state.clear()
        st.rerun()

st.title("📦 Sistema Integral GINEZ")
if "mensaje_exito" in st.session_state:
    st.success(st.session_state.mensaje_exito)
    del st.session_state.mensaje_exito

# PESTAÑAS
tabs_names = ["📊 Operaciones (Folios)", "📅 Calendario", "🧠 Planeación (Pull)", "⚙️ Carga Datos (ETL)", "🪢 Reglas MDM", "👤 Mi Perfil"]
if st.session_state.is_admin: tabs_names.append("🛡️ Panel Súper-Admin")

tabs = st.tabs(tabs_names)
tab_operaciones = tabs[0]
tab_calendario = tabs[1]
tab_planeacion = tabs[2]
tab_etl = tabs[3]
tab_mdm = tabs[4]
tab_perfil = tabs[5]
if st.session_state.is_admin: tab_admin = tabs[6]

# Cargar BD Operaciones
res_movimientos = supabase.table('movimientos').select('*').eq('"Activo"', 1).execute()
df_movimientos = pd.DataFrame(res_movimientos.data)
if df_movimientos.empty:
    df_movimientos = pd.DataFrame(columns=["id", "Folio", "Proveedor", "Fecha", "Estatus", "Entrega", "Recepcion", "Obs", "Destino", "Factura", "Calidad", "Monto", "F_Pago", "Est_Pago", "Tipo_Doc", "Ruta_PDF", "Ruta_Excel", "Ruta_PDF_SinPrecios", "Ruta_Factura", "Activo"])

# ------------------------------------------
# PESTAÑA 1: OPERACIONES
# ------------------------------------------
with tab_operaciones:
    c_btn1, c_btn2, c_btn3, c_btn4 = st.columns(4)
    with c_btn1:
        if st.session_state.permiso_edicion: 
            if st.button("➕ Nuevo Registro", use_container_width=True): modal_nuevo_registro()
    with c_btn2:
        if st.session_state.permiso_edicion:
            if st.button("✏️ Editar / Eliminar", use_container_width=True): modal_editar_registro(df_movimientos)
    with c_btn3:
        if st.button("📄 Ver Documentos", use_container_width=True): modal_ver_documento(df_movimientos)

    st.markdown("---")
    df_oc = df_movimientos[df_movimientos['Tipo_Doc'] == 'OC']
    df_tr = df_movimientos[df_movimientos['Tipo_Doc'] == 'TR']
    
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🕒 OC Pendientes", len(df_oc[df_oc['Estatus'].isin(['Programado', 'Solicitado'])]))
    k2.metric("🔝 Último Folio OC", df_oc['Folio'].max() if not df_oc.empty else "Sin registros")
    k3.metric("🕒 Traspasos Pendientes", len(df_tr[df_tr['Estatus'].isin(['Programado', 'Solicitado'])]))
    k4.metric("🔝 Último Traspaso", df_tr.sort_values(by='id', ascending=False)['Folio'].iloc[0] if not df_tr.empty else "Sin registros")
    
    st.markdown("---")
    col_busq, _ = st.columns([1, 2])
    with col_busq: busqueda = st.text_input("🔍 Buscar por Folio o Proveedor...")

    hoy_str = datetime.date.today()
    def asignar_semaforo(row):
        if row['Estatus'] == 'Recibido': return 3, '🟢 Recibido'
        if pd.notna(row['Entrega']):
            f_entrega = datetime.datetime.strptime(str(row['Entrega']), "%Y-%m-%d").date()
            dias = (f_entrega - hoy_str).days
            if dias < 0: return 1, '🔴 Vencido'
            if dias <= 3: return 2, '🟡 Próximo'
        return 4, '⚪ A tiempo / Sin Fecha'

    if not df_movimientos.empty:
        df_movimientos[['Nivel_Urg', 'Alerta']] = df_movimientos.apply(asignar_semaforo, axis=1, result_type='expand')
        df_movimientos = df_movimientos.sort_values(by=['Nivel_Urg', 'Entrega'])
        
        if busqueda:
            termino = busqueda.lower()
            df_movimientos = df_movimientos[df_movimientos['Folio'].str.lower().str.contains(termino) | df_movimientos['Proveedor'].str.lower().str.contains(termino, na=False)]
        
        col_oculta = ["Activo", "id", "Nivel_Urg", "Ruta_PDF", "Ruta_Excel", "Ruta_PDF_SinPrecios", "Ruta_Factura"]
        if not st.session_state.permiso_financiero: col_oculta.extend(["Monto", "Factura", "F_Pago", "Est_Pago", "Proveedor"])
        cols_visibles = ['Alerta'] + [c for c in df_movimientos.columns if c not in col_oculta and c != 'Alerta']
        st.dataframe(df_movimientos[cols_visibles].rename(columns={'Factura': 'UUID / Remisión'}), width='stretch', hide_index=True)

# ------------------------------------------
# PESTAÑA 2: CALENDARIO
# ------------------------------------------
with tab_calendario:
    st.markdown("### 📅 Programación y Recepción")
    hoy = datetime.date.today()
    col_mes, col_anio, _ = st.columns([1, 1, 3])
    with col_mes:
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        mes_sel = st.selectbox("Mes", range(1, 13), index=hoy.month - 1, format_func=lambda x: meses[x-1])
    with col_anio:
        anio_sel = st.selectbox("Año", range(hoy.year - 1, hoy.year + 2), index=1)
        
    st.markdown("""<style>.cal-header { text-align: center; font-weight: bold; padding: 10px 0; border-bottom: 2px solid rgba(128,128,128,0.3); background-color: rgba(128,128,128,0.1); } .cal-cell { border: 1px solid rgba(128,128,128,0.2); min-height: 120px; padding: 5px; margin-bottom: 10px; border-radius: 4px; display: flex; flex-direction: column;} .cal-today { border: 2px solid #E2231A; background-color: rgba(226, 35, 26, 0.05); } .cal-date { font-weight: bold; font-size: 1.1em; margin-bottom: 8px; opacity: 0.7; text-align: right;} .badge-pend { background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; border-radius: 3px; padding: 3px 5px; font-size: 0.75em; margin-bottom: 3px; font-weight: 600;} .badge-ok { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; border-radius: 3px; padding: 3px 5px; font-size: 0.75em; margin-bottom: 3px; font-weight: 600; text-decoration: line-through opacity 0.5;}</style>""", unsafe_allow_html=True)
    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    cols_header = st.columns(7)
    for i, col in enumerate(cols_header): col.markdown(f"<div class='cal-header'>{dias_semana[i]}</div>", unsafe_allow_html=True)
    
    cal_matrix = calendar.monthcalendar(anio_sel, mes_sel)
    for semana in cal_matrix:
        cols_dias = st.columns(7)
        for i, dia in enumerate(semana):
            if dia == 0:
                cols_dias[i].markdown("<div class='cal-cell' style='background-color: transparent; border: none;'></div>", unsafe_allow_html=True)
            else:
                str_fecha = datetime.date(anio_sel, mes_sel, dia).strftime("%Y-%m-%d")
                clase_celda = "cal-cell cal-today" if datetime.date(anio_sel, mes_sel, dia) == hoy else "cal-cell"
                html_contenido = f"<div class='{clase_celda}'><div class='cal-date'>{dia}</div>"
                
                for _, row in df_movimientos[(df_movimientos['Entrega'] == str_fecha) & (df_movimientos['Estatus'].isin(['Programado', 'Solicitado']))].iterrows():
                    prov_str = "Traspaso" if row['Tipo_Doc'] == 'TR' else (str(row['Proveedor']).strip() if pd.notna(row['Proveedor']) else "Sin Proveedor")
                    html_contenido += f"<div class='badge-pend'>🕒 {row['Folio']} ({row['Destino']})<br><span style='font-size: 0.85em; font-weight: normal;'>🏢 {prov_str}</span></div>"
                for _, row in df_movimientos[(df_movimientos['Recepcion'] == str_fecha) & (df_movimientos['Estatus'] == 'Recibido')].iterrows():
                    prov_str = "Traspaso" if row['Tipo_Doc'] == 'TR' else (str(row['Proveedor']).strip() if pd.notna(row['Proveedor']) else "Sin Proveedor")
                    html_contenido += f"<div class='badge-ok'>✅ {row['Folio']} ({row['Destino']})<br><span style='font-size: 0.85em; font-weight: normal;'>🏢 {prov_str}</span></div>"
                    
                cols_dias[i].markdown(html_contenido + "</div>", unsafe_allow_html=True)

# ------------------------------------------
# PESTAÑA 3: PLANEACIÓN DE LA DEMANDA
# ------------------------------------------
with tab_planeacion:
    col_titulo, col_boton = st.columns([3, 1])
    with col_titulo:
        st.subheader(f"🛒 Generación de Pedido - {st.session_state.sucursal}")
    with col_boton:
        if st.session_state.permiso_edicion:
            if st.button("➕ Crear Proveedor", use_container_width=True):
                modal_crear_proveedor()

    res_provs = supabase.table('proveedores').select('nombre').order('nombre').execute()
    lista_proveedores = [p['nombre'] for p in res_provs.data] if res_provs.data else ["SIN PROVEEDORES"]

    with st.form("form_parametros_pull"):
        c1, c2, c3 = st.columns(3)
        with c1: prov_input = st.selectbox("▶ Proveedor a solicitar:", lista_proveedores)
        with c2: l_prov = st.number_input("▶ Lead Time (meses)", min_value=0.1, value=0.5, step=0.1)
        with c3: r_prov = st.number_input("▶ Periodo de Revisión (meses)", min_value=0.1, value=1.0, step=0.1)
        calcular = st.form_submit_button("🚀 Calcular Demanda", type="primary")

    if calcular:
        with st.spinner("Analizando volatilidad y entrenando modelos..."):
            df_resultado = simular_motor_mrp(prov_input, l_prov, r_prov, st.session_state.sucursal)
            df_resultado['CANTIDAD A PEDIR'] = 0
            st.session_state.df_pedido_actual = df_resultado
            st.session_state.prov_actual = prov_input
            st.session_state.mostrar_grid = False
        popup_cargar_sugerencia()

    if st.session_state.get("mostrar_grid", False):
        st.markdown(f"### 📋 Hoja de Pedido: {st.session_state.prov_actual}")
        column_config = {"CANTIDAD A PEDIR": st.column_config.NumberColumn("✍️ CANTIDAD A PEDIR", help="Haz doble clic para editar", min_value=0, step=1, required=True)}
        df_editado = st.data_editor(st.session_state.df_pedido_actual, column_config=column_config, disabled=["SKU", "Descripción", "Ventas (Últimos días)", "Stock Actual", "Mínimo / SS", "Máximo / Target", "Sugerencia Sistema", "Desviación (σ)", "CV"], hide_index=True, use_container_width=True, height=500)
        
        if st.button("💾 Enviar Pedido a CEDIS", type="primary"):
            pedido_final = df_editado[df_editado['CANTIDAD A PEDIR'] > 0]
            if not pedido_final.empty:
                st.success(f"✅ ¡Pedido enviado a CEDIS! ({len(pedido_final)} SKUs).")
                st.session_state.mostrar_grid = False
            else: st.warning("⚠️ No capturaste ninguna cantidad mayor a cero.")

# ------------------------------------------
# PESTAÑA 4: CARGA DE DATOS (ETL)
# ------------------------------------------
with tab_etl:
    if st.session_state.sucursal == 'CEDIS' or st.session_state.is_admin:
        st.subheader("⚙️ Procesamiento de Datos (ETL)")
        st.markdown("El motor de limpieza procesará los Excels en la memoria RAM y los enviará normalizados a la Base de Datos.")
        
        c_etl1, c_etl2 = st.columns(2)
        
        with c_etl1:
            st.markdown("#### 1. Catálogo Maestro SICAR")
            cat_file = st.file_uploader("Catálogo de SICAR (.xls)", type=["xls", "xlsx"])
            
            if st.button("Procesar Catálogo", type="primary"):
                if cat_file:
                    with st.spinner("Limpiando catálogo..."):
                        df_cat = etl_engine.limpiar_catalogo_mdm(cat_file)
                        if not df_cat.empty:
                            exito = etl_engine.cargar_a_supabase(supabase, 'catalogo_maestro', df_cat)
                            if exito: st.success(f"✅ Catálogo actualizado: {len(df_cat)} artículos.")
                            else: st.error("❌ Error de conexión con Supabase.")
                        else: st.warning("⚠️ El archivo no tiene la estructura esperada.")
                else:
                    st.warning("⚠️ Debes subir el archivo del Catálogo.")
            
            st.markdown("#### 3. Inventarios Diarios")
            inv_files = st.file_uploader("Existencias (Sucursales y CEDIS)", accept_multiple_files=True, type=["xls", "xlsx"])
            
            if st.button("Procesar Inventarios", type="primary"):
                if inv_files:
                    with st.spinner("Consolidando existencias de la red..."):
                        df_inv = etl_engine.limpiar_inventarios(inv_files)
                        if not df_inv.empty:
                            exito = etl_engine.cargar_a_supabase(supabase, 'inventario_historico', df_inv)
                            if exito: st.success(f"✅ Inventario histórico actualizado: {len(df_inv)} registros guardados.")
                            else: st.error("❌ Error al subir inventarios.")
                else:
                    st.warning("⚠️ Sube al menos un archivo de inventario.")
            
        with c_etl2:
            st.markdown("#### 2. Paquetes / Lista de Materiales (BOM)")
            pkg_files = st.file_uploader("Catálogos de Paquetes", accept_multiple_files=True, type=["xls", "xlsx"])
            
            if st.button("Procesar Paquetes", type="primary"):
                if pkg_files:
                    with st.spinner("Extrayendo relaciones de componentes..."):
                        df_pkg = etl_engine.limpiar_paquetes_bom(pkg_files)
                        if not df_pkg.empty:
                            supabase.table('bom_paquetes').delete().neq("id", 0).execute() 
                            exito = etl_engine.cargar_a_supabase(supabase, 'bom_paquetes', df_pkg)
                            if exito: st.success(f"✅ BOM actualizado: {len(df_pkg)} dependencias extraídas.")
                            else: st.error("❌ Error al subir paquetes.")
                else:
                    st.warning("⚠️ Sube al menos un archivo de paquetes.")
            
            st.markdown("#### 4. Ventas Históricas")
            ventas_files = st.file_uploader("Tickets Semanales", accept_multiple_files=True, type=["xls", "xlsx"])
            
            if st.button("Procesar Ventas", type="primary"):
                if ventas_files:
                    with st.spinner("Estructurando historial de demanda..."):
                        df_ventas = etl_engine.limpiar_ventas(ventas_files)
                        if not df_ventas.empty:
                            exito = etl_engine.cargar_a_supabase(supabase, 'ventas_historicas', df_ventas)
                            if exito: st.success(f"✅ Demanda transaccional guardada: {len(df_ventas)} tickets procesados.")
                            else: st.error("❌ Error al subir las ventas.")
                else:
                    st.warning("⚠️ Sube al menos un archivo de ventas.")
    else: 
        st.warning("No tienes permisos para inyectar bases de datos globales.")

# ------------------------------------------
# PESTAÑA 5: REGLAS MDM (Diccionario y PKG)
# ------------------------------------------
with tab_mdm:
    if st.session_state.permiso_edicion:
        st.subheader("🪢 Gestión de Datos Maestros (MDM)")
        st.markdown("Corrige registros huérfanos y asigna reglas de empaque (PKG) sin usar Excel.")
        
        mdm_tab1, mdm_tab2 = st.tabs(["🚨 Corrección de Huérfanos", "📦 Asignación de PKG y Departamentos"])
        
        with mdm_tab1:
            st.info("El sistema detecta artículos vendidos que no existen en tu catálogo maestro.")
            v_res = supabase.table('ventas_historicas').select('sku, desc_sicar').execute()
            c_res = supabase.table('catalogo_maestro').select('sku').execute()
            d_res = supabase.table('diccionario_mdm').select('sku_viejo').execute()
            
            if v_res.data:
                df_v = pd.DataFrame(v_res.data).drop_duplicates(subset=['sku'])
                df_c = pd.DataFrame(c_res.data) if c_res.data else pd.DataFrame(columns=['sku'])
                df_d = pd.DataFrame(d_res.data) if d_res.data else pd.DataFrame(columns=['sku_viejo'])
                
                if not df_c.empty: df_v = df_v[~df_v['sku'].isin(df_c['sku'])]
                if not df_d.empty: df_v = df_v[~df_v['sku'].isin(df_d['sku_viejo'])]
                
                if not df_v.empty:
                    df_v['SKU_CORRECTO'] = ""
                    st.warning(f"Se detectaron {len(df_v)} códigos transaccionales huérfanos.")
                    
                    edit_huerfanos = st.data_editor(
                        df_v[['sku', 'desc_sicar', 'SKU_CORRECTO']].rename(columns={'sku': 'SKU_SICAR', 'desc_sicar': 'DESCRIPCIÓN VENDIDA'}),
                        hide_index=True, use_container_width=True
                    )
                    
                    if st.button("💾 Guardar Correcciones"):
                        corregidos = edit_huerfanos[edit_huerfanos['SKU_CORRECTO'].astype(str).str.strip() != ""]
                        if not corregidos.empty:
                            datos_insert = [{"sku_viejo": row['SKU_SICAR'], "sku_nuevo": row['SKU_CORRECTO']} for _, row in corregidos.iterrows()]
                            supabase.table('diccionario_mdm').upsert(datos_insert).execute()
                            st.success(f"✅ {len(corregidos)} registros agregados al diccionario MDM. Ya no serán huérfanos.")
                            st.rerun()
                else:
                    st.success("✅ Excelente. No hay registros huérfanos en tu historial de ventas.")
            else:
                st.write("Sube tu historial de ventas primero en la pestaña ETL.")

        with mdm_tab2:
            st.info("Busca, filtra y edita el Case Pack (PKG) o Departamento de tus artículos.")
            cat_res = supabase.table('catalogo_maestro').select('*').execute()
            
            if cat_res.data:
                df_catalogo = pd.DataFrame(cat_res.data)
                
                busq_cat = st.text_input("🔍 Buscar por SKU o Descripción:")
                if busq_cat:
                    termino = busq_cat.lower()
                    df_catalogo = df_catalogo[df_catalogo['sku'].str.lower().str.contains(termino) | df_catalogo['descripcion'].str.lower().str.contains(termino)]
                
                col_config = {
                    "pkg": st.column_config.NumberColumn("📦 PKG (Caja Master)", min_value=1, required=True),
                    "departamento": st.column_config.TextColumn("🏢 Departamento")
                }
                
                edit_cat = st.data_editor(
                    df_catalogo[['sku', 'descripcion', 'departamento', 'pkg']],
                    column_config=col_config,
                    disabled=["sku", "descripcion"],
                    hide_index=True, use_container_width=True
                )
                
                if st.button("💾 Actualizar Catálogo"):
                    cambios = []
                    for i in range(len(edit_cat)):
                        if (edit_cat.loc[i, 'pkg'] != df_catalogo.loc[i, 'pkg']) or (edit_cat.loc[i, 'departamento'] != df_catalogo.loc[i, 'departamento']):
                            cambios.append({
                                "sku": edit_cat.loc[i, 'sku'],
                                "departamento": edit_cat.loc[i, 'departamento'],
                                "pkg": int(edit_cat.loc[i, 'pkg'])
                            })
                    if cambios:
                        supabase.table('catalogo_maestro').upsert(cambios).execute()
                        st.success(f"✅ {len(cambios)} artículos actualizados exitosamente.")
                        st.rerun()
                    else:
                        st.info("No se detectaron cambios.")
            else:
                st.write("Sube el catálogo de SICAR en la pestaña ETL para empezar.")
    else:
        st.warning("No tienes permisos para editar los datos maestros.")

# ------------------------------------------
# PESTAÑA 6: PERFIL
# ------------------------------------------
with tab_perfil:
    st.subheader("Mi Perfil")
    with st.form("form_perfil"):
        nuevo_user = st.text_input("Cambiar Usuario", value=st.session_state.username)
        nueva_pass = st.text_input("Nueva Contraseña", type="password")
        if st.form_submit_button("Guardar Cambios", type="primary"):
            datos_update = {"username": nuevo_user}
            if nueva_pass.strip() != "": datos_update["password"] = hash_password(nueva_pass)
            supabase.table('usuarios').update(datos_update).eq('id', st.session_state.user_id).execute()
            st.success("✅ Credenciales actualizadas.")

# ------------------------------------------
# PESTAÑA 7: ADMIN (EDICIÓN DE USUARIOS)
# ------------------------------------------
if st.session_state.is_admin:
    with tab_admin:
        st.subheader("Panel de Súper Administrador")
        
        res_usr = supabase.table('usuarios').select('id, username, rol, sucursal_asignada, correo').execute()
        df_usr = pd.DataFrame(res_usr.data)
        
        c_admin1, c_admin2 = st.columns([2, 1])
        with c_admin1:
            st.markdown("#### Lista de Usuarios")
            st.dataframe(df_usr[['username', 'rol', 'sucursal_asignada', 'correo']], hide_index=True, use_container_width=True)
        
        with c_admin2:
            st.markdown("#### Asignar Rol y Sucursal")
            if not df_usr.empty:
                usr_sel = st.selectbox("Selecciona un usuario a editar:", df_usr['username'].tolist())
                usr_data = df_usr[df_usr['username'] == usr_sel].iloc[0]
                
                with st.form("form_editar_usuario"):
                    lista_roles = ["Admin Master", "Jefe de Área", "Gerente de Sucursal", "Operador"]
                    lista_sucs = ["CEDIS", "Actopan", "Mixquiahuala", "Pachuca", "Querétaro", "Oaxaca", "Veracruz"]
                    
                    idx_rol = lista_roles.index(usr_data['rol']) if usr_data['rol'] in lista_roles else 1
                    idx_suc = lista_sucs.index(usr_data['sucursal_asignada']) if usr_data['sucursal_asignada'] in lista_sucs else 0
                    
                    n_rol = st.selectbox("Nivel de Acceso", lista_roles, index=idx_rol)
                    n_suc = st.selectbox("Sucursal Asignada (Gafete)", lista_sucs, index=idx_suc)
                    
                    if st.form_submit_button("💾 Guardar Cambios", type="primary"):
                        supabase.table('usuarios').update({
                            "rol": n_rol,
                            "sucursal_asignada": n_suc
                        }).eq("id", str(usr_data['id'])).execute()
                        
                        st.success(f"✅ Los permisos de '{usr_sel}' han sido actualizados.")
                        st.rerun()