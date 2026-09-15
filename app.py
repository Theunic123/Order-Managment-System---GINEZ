import pandas as pd
import numpy as np
import streamlit as st
import datetime
import calendar
import io
import os
import base64
import hashlib
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
# FUNCIONES DE SEGURIDAD
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
# SISTEMA DE LOGIN (Con Sucursal)
# ==========================================
def verificar_credenciales(username, password):
    hashed_pw = hash_password(password)
    res = supabase.table('usuarios').select('*').eq('username', username).eq('password', hashed_pw).execute()
    if len(res.data) > 0:
        user = res.data[0]
        return user
    return None

if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

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
                st.session_state.correo = user_data['correo']
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
# FUNCIONES DE ARCHIVOS Y MODALES (OPERACIONES)
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

@st.dialog("📝 Registrar Nuevo Movimiento", width="large")
def modal_nuevo_registro():
    # (El código de tu modal de altas se mantiene idéntico)
    st.info("Formulario de Alta conservado para Operaciones.")
    # Por brevedad en la muestra visual, puedes expandir esto luego si lo necesitas,
    # pero como pediste ver cómo queda estructurado, aquí va el esqueleto que lo llama.
    if st.button("Cerrar"): st.rerun()

@st.dialog("📄 Gestor de Documentos", width="large")
def modal_ver_documento(df_mov):
    st.info("Visor de PDFs conservado.")
    if st.button("Cerrar"): st.rerun()

# ==========================================
# 🧠 SIMULADOR DEL MOTOR MRP (UI TEST)
# ==========================================
def simular_motor_mrp(proveedor, l_prov, r_prov, sucursal):
    """Genera datos falsos para probar que la tabla funcione. Luego conectaremos tu Python real aquí."""
    np.random.seed(42)
    datos = []
    for i in range(1, 21):
        sug = np.random.randint(0, 100)
        datos.append({
            "SKU": f"TRU-{1000+i}",
            "Descripción": f"Producto {proveedor} {i}",
            "Ventas (Últimos días)": np.random.randint(10, 150),
            "Stock Actual": np.random.randint(0, 50),
            "Mínimo / SS": np.random.randint(5, 20),
            "Máximo / Target": np.random.randint(50, 120),
            "Sugerencia Sistema": sug,
            "Desviación (σ)": round(np.random.uniform(1.5, 5.5), 2),
            "CV": round(np.random.uniform(0.1, 3.5), 2)
        })
    df = pd.DataFrame(datos)
    return df.sort_values(by="Sugerencia Sistema", ascending=False).reset_index(drop=True)

@st.dialog("⚡ Carga Rápida de Sugerencias")
def popup_cargar_sugerencia():
    st.markdown("### El algoritmo ha terminado de calcular.")
    st.write("¿Deseas precargar las cantidades sugeridas por el sistema en tu columna de pedido? (Podrás editarlas libremente después).")
    
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

# DEFINICIÓN DE PESTAÑAS
tabs_names = ["📊 Operaciones (Folios)", "📅 Calendario", "🧠 Planeación (Pull)", "⚙️ Carga Datos (ETL)", "👤 Mi Perfil"]
if st.session_state.is_admin: tabs_names.append("🛡️ Panel Súper-Admin")

tabs = st.tabs(tabs_names)
tab_operaciones = tabs[0]
tab_calendario = tabs[1]
tab_planeacion = tabs[2]
tab_etl = tabs[3]
tab_perfil = tabs[4]
if st.session_state.is_admin: tab_admin = tabs[5]

# Cargar BD Operaciones
res_movimientos = supabase.table('movimientos').select('*').eq('"Activo"', 1).execute()
df_movimientos = pd.DataFrame(res_movimientos.data)
if df_movimientos.empty:
    df_movimientos = pd.DataFrame(columns=["id", "Folio", "Proveedor", "Fecha", "Estatus", "Entrega", "Recepcion", "Obs", "Destino", "Tipo_Doc", "Activo"])

# ------------------------------------------
# PESTAÑA 1: OPERACIONES (Mantenida intacta)
# ------------------------------------------
with tab_operaciones:
    c_btn1, c_btn2, c_btn3, c_btn4 = st.columns([1, 1, 1, 1])
    with c_btn1:
        if st.session_state.permiso_edicion: 
            if st.button("➕ Nuevo Registro", use_container_width=True): modal_nuevo_registro()
    with c_btn3:
        if st.button("📄 Ver Documentos", use_container_width=True): modal_ver_documento(df_movimientos)
        
    st.markdown("---")
    st.info("Aquí sigue viviendo la tabla de tus Órdenes de Compra y Traspasos con su buscador inteligente.")

# ------------------------------------------
# PESTAÑA 2: CALENDARIO LOGÍSTICO
# ------------------------------------------
with tab_calendario:
    st.markdown("### 📅 Programación y Recepción de Mercancía")
    st.info("Calendario conservado.")

# ------------------------------------------
# PESTAÑA 3: PLANEACIÓN DE LA DEMANDA (NUEVA)
# ------------------------------------------
with tab_planeacion:
    st.subheader(f"🛒 Generación de Pedido - {st.session_state.sucursal}")
    st.markdown("Configure los parámetros. El motor calculará la demanda sugerida (FVA).")

    with st.form("form_parametros_pull"):
        c1, c2, c3 = st.columns(3)
        with c1:
            prov_input = st.selectbox("▶ Proveedor a solicitar:", ["TRUPER", "CLOROX", "DETERGENTES"])
        with c2:
            l_prov = st.number_input("▶ Lead Time (meses)", min_value=0.1, value=0.5, step=0.1)
        with c3:
            r_prov = st.number_input("▶ Periodo de Revisión (meses)", min_value=0.1, value=1.0, step=0.1)
            
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
        
        column_config = {
            "CANTIDAD A PEDIR": st.column_config.NumberColumn(
                "✍️ CANTIDAD A PEDIR", help="Haz doble clic para editar", min_value=0, step=1, required=True
            )
        }
        
        df_editado = st.data_editor(
            st.session_state.df_pedido_actual,
            column_config=column_config,
            disabled=["SKU", "Descripción", "Ventas (Últimos días)", "Stock Actual", "Mínimo / SS", "Máximo / Target", "Sugerencia Sistema", "Desviación (σ)", "CV"],
            hide_index=True, use_container_width=True, height=500
        )
        
        if st.button("💾 Enviar Pedido a CEDIS", type="primary"):
            pedido_final = df_editado[df_editado['CANTIDAD A PEDIR'] > 0]
            if not pedido_final.empty:
                st.success(f"✅ ¡Pedido enviado a CEDIS! ({len(pedido_final)} SKUs solicitados).")
                st.session_state.mostrar_grid = False
            else:
                st.warning("⚠️ No capturaste ninguna cantidad mayor a cero.")

# ------------------------------------------
# PESTAÑA 4: CARGA DE DATOS (NUEVA)
# ------------------------------------------
with tab_etl:
    if st.session_state.sucursal == 'CEDIS' or st.session_state.is_admin:
        st.subheader("⚙️ Procesamiento de Datos (ETL)")
        st.markdown("Sube los archivos crudos de SICAR. El sistema los limpiará automáticamente.")
        c_etl1, c_etl2 = st.columns(2)
        with c_etl1:
            st.markdown("#### 1. Inventarios Diarios")
            st.file_uploader("Existencias (Sucursales y CEDIS)", accept_multiple_files=True)
            if st.button("Procesar Inventarios"): st.info("ETL en construcción...")
        with c_etl2:
            st.markdown("#### 2. Ventas Históricas")
            st.file_uploader("Tickets Semanales", accept_multiple_files=True)
            if st.button("Procesar Ventas"): st.info("ETL en construcción...")
    else:
        st.warning("No tienes permisos para inyectar bases de datos globales.")

# ------------------------------------------
# PESTAÑAS 5 y 6: PERFIL Y ADMIN
# ------------------------------------------
with tab_perfil:
    st.subheader("Mi Perfil")
if st.session_state.is_admin:
    with tab_admin:
        st.subheader("Panel de Súper Administrador")