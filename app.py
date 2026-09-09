import sqlite3
import pandas as pd
import streamlit as st
import datetime
import calendar
import io
import os
import base64
import hashlib

# ==========================================
# CONFIGURACIÓN DE PÁGINA Y RUTAS
# ==========================================
st.set_page_config(
    page_title="Sistema Logístico - Grupo GINEZ",
    page_icon="📦",
    layout="wide",
)

# --- NUEVO: Logotipo de la Empresa y CSS ---
st.logo("logo.jpg")

st.markdown("""
    <style>
        /* Hacer el logo responsivo y más grande */
        [data-testid="stLogo"] {
            height: 3.5rem !important;
            object-fit: contain !important;
        }
        
        /* Forzar el color corporativo de Grupo GINEZ en los botones primarios */
        button[kind="primary"] {
            background-color: #E2231A !important;
            border-color: #E2231A !important;
            color: white !important;
        }
        button[kind="primary"]:hover {
            background-color: #19255A !important; /* Azul corporativo al pasar el mouse */
            border-color: #19255A !important;
        }
    </style>
""", unsafe_allow_html=True)
# -------------------------------------

DB_NAME = "ginez_logistica.db"
BASE_PEDIDOS = r"C:\Users\gvargas\OPERADORA GINEZ DE MEXICO\Joseline Lopez Garcia - Planeación de la demanda\PEDIDOS"
BASE_TRASPASOS = r"C:\Users\gvargas\OPERADORA GINEZ DE MEXICO\Joseline Lopez Garcia - Planeación de la demanda\TRASPASOS"

# ==========================================
# FUNCIONES DE SEGURIDAD Y HASHING
# ==========================================
def hash_password(password):
    """Cifra la contraseña usando SHA-256 para seguridad industrial."""
    return hashlib.sha256(password.encode()).hexdigest()

def registrar_bitacora(usuario, accion, detalle):
    """Registra cualquier movimiento crítico en la tabla de auditoría."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        fecha_hora = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO bitacora (fecha, usuario, accion, detalle) VALUES (?, ?, ?, ?)",
            (fecha_hora, usuario, accion, detalle)
        )
        conn.commit()

# ==========================================
# 1. INICIALIZACIÓN DE BASE DE DATOS
# ==========================================
def inicializar_bd():
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                rol TEXT NOT NULL,
                correo TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bitacora (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                usuario TEXT NOT NULL,
                accion TEXT NOT NULL,
                detalle TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS movimientos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                Folio TEXT UNIQUE NOT NULL,
                Proveedor TEXT,
                Fecha TEXT NOT NULL,
                Estatus TEXT NOT NULL,
                Entrega TEXT,
                Recepcion TEXT,
                Obs TEXT,
                Destino TEXT NOT NULL,
                Factura TEXT,
                Calidad TEXT,
                Monto REAL,
                F_Pago TEXT,
                Est_Pago TEXT,
                Tipo_Doc TEXT NOT NULL,
                Ruta_PDF TEXT,
                Ruta_Excel TEXT,
                Activo INTEGER DEFAULT 1
            )
        """)

        cursor.execute("SELECT COUNT(*) FROM usuarios")
        if cursor.fetchone()[0] == 0:
            usuarios_prueba = [
                ("ginezti", hash_password("jarciginez"), "Admin Master", "N/A"),
                ("jefe_cedis", hash_password("jefe123"), "Jefe de Área", "jefe@ginez.com"),
                ("gerente_suc", hash_password("gerente123"), "Gerente de Sucursal", "gerente@ginez.com"),
            ]
            cursor.executemany("INSERT INTO usuarios (username, password, rol, correo) VALUES (?, ?, ?, ?)", usuarios_prueba)

        conn.commit()

inicializar_bd()

# ==========================================
# 2. SISTEMA DE LOGIN
# ==========================================
def verificar_credenciales(username, password):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, rol, correo FROM usuarios WHERE username = ? AND password = ?", (username, hash_password(password)))
        return cursor.fetchone()

if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
    st.session_state.user_id = None
    st.session_state.username = ""
    st.session_state.rol = ""
    st.session_state.correo = ""

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
                st.session_state.user_id = user_data[0]
                st.session_state.username = user_data[1]
                st.session_state.rol = user_data[2]
                st.session_state.correo = user_data[3]
                registrar_bitacora(user_data[1], "LOGIN", "El usuario inició sesión exitosamente.")
                st.rerun()
            else:
                st.error("❌ Usuario o contraseña incorrectos.")
    st.stop()


# ==========================================
# FUNCIONES AUXILIARES Y PERMISOS
# ==========================================
def str_to_date(date_str):
    if pd.isna(date_str) or not date_str:
        return None
    return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

roles_jefes = ["Admin Master", "Jefe de Área"]
st.session_state.is_admin = (st.session_state.rol == "Admin Master")
st.session_state.permiso_edicion = (st.session_state.rol in roles_jefes)
st.session_state.permiso_financiero = (st.session_state.rol in roles_jefes)

def guardar_archivos_fisicos(archivo_pdf, archivo_excel, folio, proveedor, destino, fecha, es_oc):
    """Guarda archivos con lógica HÍBRIDA: OneDrive (Local) o Directorio Temporal (Nube)"""
    ruta_pdf, ruta_excel = None, None
    fecha_fmt = fecha.strftime("%Y%m%d") if fecha else datetime.date.today().strftime("%Y%m%d")
    
    # Detección de entorno: Si existe el disco C en Windows, usa OneDrive. Si no (Linux/Nube), usa temporal.
    if os.path.exists(r"C:\\"):
        base_p = BASE_PEDIDOS
        base_t = BASE_TRASPASOS
    else:
        base_p = "cloud_docs/pedidos"
        base_t = "cloud_docs/traspasos"

    if es_oc:
        prov_limpio = str(proveedor).strip().upper() if proveedor else "SIN_PROVEEDOR"
        directorio_meta = os.path.join(base_p, prov_limpio)
    else:
        dest_limpio = str(destino).strip().upper() if destino else "SIN_DESTINO"
        directorio_meta = os.path.join(base_t, dest_limpio)
        
    try:
        os.makedirs(directorio_meta, exist_ok=True)
    except Exception as e:
        st.error(f"Error al crear carpetas de almacenamiento: {e}")
        return None, None
        
    if archivo_pdf is not None:
        ruta_pdf = os.path.join(directorio_meta, f"{folio}.pdf")
        with open(ruta_pdf, "wb") as f: f.write(archivo_pdf.getbuffer())
    if es_oc and archivo_excel is not None:
        ruta_excel = os.path.join(directorio_meta, f"{prov_limpio}_{fecha_fmt}.xlsx")
        with open(ruta_excel, "wb") as f: f.write(archivo_excel.getbuffer())
            
    return ruta_pdf, ruta_excel


# ==========================================
# DEFINICIÓN DE VENTANAS EMERGENTES (MODALES)
# ==========================================
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
            obs = st.text_input("Observaciones")

        if es_oc:
            st.markdown("---")
            cf1, cf2, cf3, cf4 = st.columns(4)
            with cf1: monto = st.number_input("Monto ($)", min_value=0.0, step=100.0)
            with cf2: factura = st.text_input("UUID / Nota de Remisión")
            with cf3: f_pago = st.selectbox("Forma de Pago", ["Transferencia", "Efectivo", "Tarjeta", "Crédito", "N/A"])
            with cf4: est_pago = st.selectbox("Estatus de Pago", ["Pendiente", "Pagado", "Vencido"])
        else:
            monto, factura, f_pago, est_pago = None, None, None, None

        st.markdown("#### 📂 Anexar Documentos")
        col_docs1, col_docs2 = st.columns(2)
        with col_docs1: archivo_pdf = st.file_uploader("📄 Subir PDF", type=["pdf"])
        with col_docs2:
            if es_oc: archivo_excel = st.file_uploader("📊 Subir Distribución", type=["xlsx", "xls"])
            else: archivo_excel = None

        if st.form_submit_button("💾 Guardar", type="primary", use_container_width=True):
            if not folio.strip(): st.error("⚠️ Folio obligatorio.")
            else:
                try:
                    ruta_pdf_final, ruta_excel_final = guardar_archivos_fisicos(archivo_pdf, archivo_excel, folio, proveedor, destino, fecha, es_oc)
                    with sqlite3.connect(DB_NAME) as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO movimientos (
                                Folio, Proveedor, Fecha, Estatus, Entrega, Recepcion, 
                                Obs, Destino, Factura, Calidad, Monto, F_Pago, Est_Pago, Tipo_Doc, Ruta_PDF, Ruta_Excel
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (folio, proveedor, fecha.strftime("%Y-%m-%d"), estatus, entrega.strftime("%Y-%m-%d") if entrega else None, 
                              recepcion.strftime("%Y-%m-%d") if recepcion else None, obs, destino, factura, calidad, monto, f_pago, est_pago, tipo_doc_bd, ruta_pdf_final, ruta_excel_final))
                        conn.commit()
                    registrar_bitacora(st.session_state.username, "CREACIÓN", f"Se creó el folio {folio}.")
                    st.session_state.mensaje_exito = f"✅ Movimiento {folio} guardado."
                    st.rerun() 
                except sqlite3.IntegrityError: st.error("❌ El Folio ya existe.")


@st.dialog("⚙️ Editar o Eliminar Registro", width="large")
def modal_editar_registro(df_mov):
    folios_disponibles = df_mov['Folio'].tolist()
    if not folios_disponibles:
        st.info("No hay registros en la base de datos.")
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
                idx_estatus = opciones_estatus.index(registro['Estatus']) if registro['Estatus'] in opciones_estatus else 0
                nuevo_estatus = st.selectbox("Estatus", opciones_estatus, index=idx_estatus)
                nueva_entrega = st.date_input("Fecha estimada de Entrega", value=str_to_date(registro['Entrega']))
                nueva_recepcion = st.date_input("Fecha real en Recepción", value=str_to_date(registro['Recepcion']))
            with c3:
                opciones_destino = ["CEDIS", "Actopan", "Mixquiahuala", "Pachuca", "Querétaro", "Oaxaca", "Veracruz"]
                idx_destino = opciones_destino.index(registro['Destino']) if registro['Destino'] in opciones_destino else 0
                nuevo_destino = st.selectbox("Destino", opciones_destino, index=idx_destino)
                opciones_calidad = ["N/A", "Completo", "Parcial", "Dañado"]
                idx_calidad = opciones_calidad.index(registro['Calidad']) if registro['Calidad'] in opciones_calidad else 0
                nueva_calidad = st.selectbox("Calidad en Recepción", opciones_calidad, index=idx_calidad)
                nueva_obs = st.text_input("Observaciones", value=registro['Obs'] if pd.notna(registro['Obs']) else "")

            if es_oc_edit:
                st.markdown("---")
                cf1, cf2, cf3, cf4 = st.columns(4)
                with cf1: nuevo_monto = st.number_input("Monto ($)", min_value=0.0, step=100.0, value=float(registro['Monto']) if pd.notna(registro['Monto']) else 0.0)
                with cf2: nueva_factura = st.text_input("UUID / Nota de Remisión", value=registro['Factura'] if pd.notna(registro['Factura']) else "")
                with cf3:
                    opc_pago = ["Transferencia", "Efectivo", "Tarjeta", "Crédito", "N/A"]
                    idx_pago = opc_pago.index(registro['F_Pago']) if registro['F_Pago'] in opc_pago else 0
                    nuevo_fpago = st.selectbox("Forma de Pago", opc_pago, index=idx_pago)
                with cf4:
                    opc_est_pago = ["Pendiente", "Pagado", "Vencido"]
                    idx_est_pago = opc_est_pago.index(registro['Est_Pago']) if registro['Est_Pago'] in opc_est_pago else 0
                    nuevo_estpago = st.selectbox("Estatus de Pago", opc_est_pago, index=idx_est_pago)
            else:
                nuevo_monto, nueva_factura, nuevo_fpago, nuevo_estpago = None, None, None, None

            st.markdown("#### 📂 Reemplazar Documentos")
            c_doc1, c_doc2 = st.columns(2)
            with c_doc1: nuevo_pdf = st.file_uploader("Sobrescribir PDF", type=["pdf"])
            with c_doc2: nuevo_excel = st.file_uploader("Sobrescribir Excel", type=["xlsx", "xls"]) if es_oc_edit else None

            if st.form_submit_button("🔄 Actualizar", type="primary", use_container_width=True):
                ruta_pdf_bd = registro['Ruta_PDF']
                ruta_excel_bd = registro.get('Ruta_Excel', None)

                if nuevo_pdf is not None or nuevo_excel is not None:
                    n_pdf, n_excel = guardar_archivos_fisicos(nuevo_pdf, nuevo_excel, folio_editar, nuevo_proveedor, nuevo_destino, nueva_fecha, es_oc_edit)
                    if nuevo_pdf is not None: ruta_pdf_bd = n_pdf
                    if nuevo_excel is not None: ruta_excel_bd = n_excel

                with sqlite3.connect(DB_NAME) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE movimientos SET 
                            Proveedor=?, Fecha=?, Estatus=?, Entrega=?, Recepcion=?, Obs=?, Destino=?, 
                            Factura=?, Calidad=?, Monto=?, F_Pago=?, Est_Pago=?, Ruta_PDF=?, Ruta_Excel=?
                        WHERE Folio=?
                    """, (nuevo_proveedor, nueva_fecha.strftime("%Y-%m-%d") if nueva_fecha else None, 
                          nuevo_estatus, nueva_entrega.strftime("%Y-%m-%d") if nueva_entrega else None, 
                          nueva_recepcion.strftime("%Y-%m-%d") if nueva_recepcion else None, nueva_obs, 
                          nuevo_destino, nueva_factura, nueva_calidad, nuevo_monto, nuevo_fpago, nuevo_estpago, 
                          ruta_pdf_bd, ruta_excel_bd, folio_editar))
                    conn.commit()
                registrar_bitacora(st.session_state.username, "ACTUALIZACIÓN", f"Se editó el folio {folio_editar}.")
                st.session_state.mensaje_exito = f"🔄 Movimiento {folio_editar} actualizado."
                st.rerun()

        # Botón de ELIMINACIÓN LÓGICA (Soft Delete)
        if st.button("🗑️ Enviar a Papelera (Ocultar)", type="primary", use_container_width=True):
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE movimientos SET Activo = 0 WHERE Folio = ?", (folio_editar,))
                conn.commit()
            registrar_bitacora(st.session_state.username, "ELIMINACIÓN (Soft)", f"Se ocultó el folio {folio_editar}.")
            st.session_state.mensaje_exito = f"🗑️ Movimiento {folio_editar} movido a la papelera."
            st.rerun()

@st.dialog("📄 Gestor de Documentos", width="large")
def modal_ver_documento(df_mov):
    folios_disponibles = df_mov['Folio'].tolist()
    if not folios_disponibles:
        st.info("No hay registros.")
        return
        
    folio_selec = st.selectbox("🔍 Selecciona el Folio para ver/descargar sus documentos:", [""] + folios_disponibles)
    
    if folio_selec:
        registro = df_mov[df_mov['Folio'] == folio_selec].iloc[0]
        ruta_pdf = registro.get('Ruta_PDF', None)
        ruta_excel = registro.get('Ruta_Excel', None)
        
        st.markdown(f"### Documentos de: {folio_selec}")
        c1, c2 = st.columns(2)
        
        with c1:
            st.markdown("#### 📄 Documento Principal (PDF)")
            if pd.notna(ruta_pdf) and os.path.exists(str(ruta_pdf)):
                with open(ruta_pdf, "rb") as f:
                    base64_pdf = base64.b64encode(f.read()).decode('utf-8')
                pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="400" type="application/pdf"></iframe>'
                st.markdown(pdf_display, unsafe_allow_html=True)
                
                with open(ruta_pdf, "rb") as f:
                    st.download_button(label="📥 Descargar PDF", data=f, file_name=f"{folio_selec}.pdf", mime="application/pdf", type="primary", use_container_width=True)
            else:
                st.warning("⚠️ El PDF no se encontró en el servidor de la nube.")

        with c2:
            st.markdown("#### 📊 Matriz de Distribución")
            if pd.notna(ruta_excel) and os.path.exists(str(ruta_excel)):
                st.success("✅ Archivo de Excel ubicado exitosamente.")
                with open(ruta_excel, "rb") as f:
                    st.download_button(label="📥 Descargar Excel", data=f, file_name=os.path.basename(str(ruta_excel)), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary", use_container_width=True)
            else:
                if registro['Tipo_Doc'] == 'TR':
                    st.info("ℹ️ Los traspasos (TR) no llevan hoja de distribución anexa.")
                else:
                    st.warning("⚠️ El archivo Excel no se encontró en el servidor.")


# ==========================================
# ESTRUCTURA PRINCIPAL E INTERFAZ
# ==========================================
with st.sidebar:
    st.write(f"👤 **Usuario:** {st.session_state.username}")
    st.write(f"🛡️ **Rol:** {st.session_state.rol}")
    if st.button("Cerrar Sesión", use_container_width=True):
        st.session_state.clear()
        st.rerun()

st.title("📦 Panel de Control Logístico")

if "mensaje_exito" in st.session_state:
    st.success(st.session_state.mensaje_exito)
    del st.session_state.mensaje_exito

# Cargar BD - Solo registros Activos (Soft Delete)
with sqlite3.connect(DB_NAME) as conn:
    df_movimientos = pd.read_sql("SELECT * FROM movimientos WHERE Activo = 1", conn)

# Preparar pestañas según el Rol
tabs_names = ["📊 Panel Principal", "📅 Calendario", "👤 Mi Perfil"]
if st.session_state.is_admin:
    tabs_names.append("⚙️ Panel Súper-Admin")

tabs = st.tabs(tabs_names)
tab_panel = tabs[0]
tab_calendario = tabs[1]
tab_perfil = tabs[2]
if st.session_state.is_admin:
    tab_admin = tabs[3]

# ------------------------------------------
# PESTAÑA 1: PANEL PRINCIPAL (SEMÁFORO)
# ------------------------------------------
with tab_panel:
    c_btn1, c_btn2, c_btn3, c_btn4 = st.columns([1, 1, 1, 1])
    with c_btn1:
        if st.session_state.permiso_edicion:
            if st.button("➕ Nuevo Registro", use_container_width=True): modal_nuevo_registro()
    with c_btn2:
        if st.session_state.permiso_edicion:
            if st.button("✏️ Editar / Eliminar", use_container_width=True): modal_editar_registro(df_movimientos)
    with c_btn3:
        # Botón de documentos visible para todos los roles
        if st.button("📄 Ver Documentos", use_container_width=True): modal_ver_documento(df_movimientos)

    st.markdown("---")
    
    # KPIs Rápidos
    k1, k2, k3 = st.columns(3)
    pend_oc = len(df_movimientos[(df_movimientos['Tipo_Doc'] == 'OC') & (df_movimientos['Estatus'].isin(['Programado', 'Solicitado']))])
    pend_tr = len(df_movimientos[(df_movimientos['Tipo_Doc'] == 'TR') & (df_movimientos['Estatus'].isin(['Programado', 'Solicitado']))])
    
    k1.metric("🕒 OC Pendientes", pend_oc)
    k2.metric("🕒 Traspasos Pendientes", pend_tr)
    if st.session_state.permiso_financiero:
        monto_p = df_movimientos[(df_movimientos['Tipo_Doc'] == 'OC') & (df_movimientos['Est_Pago'] == 'Pendiente')]['Monto'].sum()
        k3.metric("💰 Monto Pendiente de Pago", f"${monto_p:,.2f}")
    
    st.markdown("---")

    # Lógica de Semáforo de Urgencia y Ordenamiento
    hoy_str = datetime.date.today()
    def asignar_semaforo(row):
        if row['Estatus'] == 'Recibido': return 3, '🟢 Recibido'
        if pd.notna(row['Entrega']):
            f_entrega = datetime.datetime.strptime(row['Entrega'], "%Y-%m-%d").date()
            dias = (f_entrega - hoy_str).days
            if dias < 0: return 1, '🔴 Vencido'
            if dias <= 3: return 2, '🟡 Próximo'
        return 4, '⚪ A tiempo / Sin Fecha'

    if not df_movimientos.empty:
        df_movimientos[['Nivel_Urg', 'Alerta']] = df_movimientos.apply(asignar_semaforo, axis=1, result_type='expand')
        # Ordenar: Primero los urgentes (1=Rojo), luego por fecha de entrega
        df_movimientos = df_movimientos.sort_values(by=['Nivel_Urg', 'Entrega'])
        
        # Filtros de visualización
        col_oculta = ["Activo", "id", "Nivel_Urg", "Ruta_PDF", "Ruta_Excel"]
        if not st.session_state.permiso_financiero:
            col_oculta.extend(["Monto", "Factura", "F_Pago", "Est_Pago", "Proveedor"])
            
        cols_visibles = ['Alerta'] + [c for c in df_movimientos.columns if c not in col_oculta and c != 'Alerta']
        df_visual = df_movimientos[cols_visibles].rename(columns={'Factura': 'UUID / Remisión'})
        
        st.dataframe(df_visual, width='stretch', hide_index=True)


# ------------------------------------------
# PESTAÑA 2: CALENDARIO LOGÍSTICO
# ------------------------------------------
with tab_calendario:
    st.markdown("### 📅 Programación y Recepción de Mercancía")
    hoy = datetime.date.today()
    col_mes, col_anio, _ = st.columns([1, 1, 3])
    with col_mes:
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        mes_sel = st.selectbox("Mes", range(1, 13), index=hoy.month - 1, format_func=lambda x: meses[x-1])
    with col_anio:
        anio_sel = st.selectbox("Año", range(hoy.year - 1, hoy.year + 2), index=1)
        
    st.markdown("""
        <style>
        .cal-header { text-align: center; font-weight: bold; padding: 10px 0; border-bottom: 2px solid rgba(128,128,128,0.3); background-color: rgba(128,128,128,0.1); }
        .cal-cell { border: 1px solid rgba(128,128,128,0.2); min-height: 120px; padding: 5px; margin-bottom: 10px; border-radius: 4px; display: flex; flex-direction: column;}
        .cal-today { border: 2px solid #E2231A; background-color: rgba(226, 35, 26, 0.05); }
        .cal-date { font-weight: bold; font-size: 1.1em; margin-bottom: 8px; opacity: 0.7; text-align: right;}
        .badge-pend { background-color: #fff3cd; color: #856404; border: 1px solid #ffeeba; border-radius: 3px; padding: 3px 5px; font-size: 0.75em; margin-bottom: 3px; font-weight: 600;}
        .badge-ok { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; border-radius: 3px; padding: 3px 5px; font-size: 0.75em; margin-bottom: 3px; font-weight: 600; text-decoration: line-through opacity 0.5;}
        </style>
    """, unsafe_allow_html=True)

    dias_semana = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    cols_header = st.columns(7)
    for i, col in enumerate(cols_header):
        col.markdown(f"<div class='cal-header'>{dias_semana[i]}</div>", unsafe_allow_html=True)
    
    cal_matrix = calendar.monthcalendar(anio_sel, mes_sel)
    
    for semana in cal_matrix:
        cols_dias = st.columns(7)
        for i, dia in enumerate(semana):
            if dia == 0:
                cols_dias[i].markdown("<div class='cal-cell' style='background-color: transparent; border: none;'></div>", unsafe_allow_html=True)
            else:
                fecha_iteracion = datetime.date(anio_sel, mes_sel, dia)
                str_fecha = fecha_iteracion.strftime("%Y-%m-%d")
                es_hoy = (fecha_iteracion == hoy)
                
                clase_celda = "cal-cell cal-today" if es_hoy else "cal-cell"
                html_contenido = f"<div class='{clase_celda}'><div class='cal-date'>{dia}</div>"
                
                pendientes = df_movimientos[(df_movimientos['Entrega'] == str_fecha) & (df_movimientos['Estatus'].isin(['Programado', 'Solicitado']))]
                recibidos = df_movimientos[(df_movimientos['Recepcion'] == str_fecha) & (df_movimientos['Estatus'] == 'Recibido')]
                
                for _, row in pendientes.iterrows():
                    html_contenido += f"<div class='badge-pend'>🕒 {row['Folio']} ({row['Destino']})</div>"
                for _, row in recibidos.iterrows():
                    html_contenido += f"<div class='badge-ok'>✅ {row['Folio']} ({row['Destino']})</div>"
                    
                html_contenido += "</div>"
                cols_dias[i].markdown(html_contenido, unsafe_allow_html=True)


# ------------------------------------------
# PESTAÑA 3: MI PERFIL (Cambio de Credenciales)
# ------------------------------------------
with tab_perfil:
    st.subheader("Configuración de Mi Cuenta")
    with st.form("form_perfil"):
        nuevo_user = st.text_input("Cambiar Nombre de Usuario", value=st.session_state.username)
        nueva_pass = st.text_input("Nueva Contraseña", type="password", help="Déjalo en blanco si no deseas cambiarla")
        
        if st.form_submit_button("Guardar Cambios", type="primary"):
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.cursor()
                if nueva_pass.strip() != "":
                    cursor.execute("UPDATE usuarios SET username=?, password=? WHERE id=?", (nuevo_user, hash_password(nueva_pass), st.session_state.user_id))
                else:
                    cursor.execute("UPDATE usuarios SET username=? WHERE id=?", (nuevo_user, st.session_state.user_id))
                conn.commit()
            registrar_bitacora(st.session_state.username, "PERFIL", "Actualizó sus credenciales.")
            st.success("✅ Credenciales actualizadas. Por favor, cierra sesión e ingresa nuevamente.")


# ------------------------------------------
# PESTAÑA 4: PANEL SÚPER-ADMIN (Solo para ginezti)
# ------------------------------------------
if st.session_state.is_admin:
    with tab_admin:
        st.subheader("🛡️ Panel de Administración y Auditoría")
        
        c_adm1, c_adm2 = st.columns(2)
        with c_adm1:
            st.markdown("#### Gestión de Usuarios")
            with st.form("crear_usuario"):
                st.write("Crear nuevo perfil")
                n_usr = st.text_input("Usuario")
                n_pwd = st.text_input("Contraseña temporal")
                n_rol = st.selectbox("Rol del Sistema", ["Jefe de Área", "Gerente de Sucursal"])
                n_correo = st.text_input("Correo Empresarial")
                if st.form_submit_button("Crear Usuario"):
                    with sqlite3.connect(DB_NAME) as conn:
                        cursor = conn.cursor()
                        cursor.execute("INSERT INTO usuarios (username, password, rol, correo) VALUES (?, ?, ?, ?)", (n_usr, hash_password(n_pwd), n_rol, n_correo))
                        conn.commit()
                    registrar_bitacora("Admin", "CREAR USUARIO", f"Usuario {n_usr} creado.")
                    st.success("Usuario creado con éxito.")
                    
            with sqlite3.connect(DB_NAME) as conn:
                df_usr = pd.read_sql("SELECT id, username, rol, correo FROM usuarios", conn)
            st.write("Usuarios Actuales (Contraseñas cifradas e invisibles)")
            st.dataframe(df_usr, hide_index=True)

        with c_adm2:
            st.markdown("#### 📜 Bitácora de Trazabilidad")
            with sqlite3.connect(DB_NAME) as conn:
                df_bitacora = pd.read_sql("SELECT fecha, usuario, accion, detalle FROM bitacora ORDER BY id DESC LIMIT 50", conn)
            st.dataframe(df_bitacora, height=400, hide_index=True)

            st.markdown("#### 🗑️ Papelera de Reciclaje (Registros Ocultos)")
            with sqlite3.connect(DB_NAME) as conn:
                df_borrados = pd.read_sql("SELECT Folio, Proveedor, Destino, Estatus FROM movimientos WHERE Activo = 0", conn)
            if not df_borrados.empty:
                st.dataframe(df_borrados, hide_index=True)
                restaurar_folio = st.selectbox("Selecciona un folio para restaurar", df_borrados['Folio'])
                if st.button("Restaurar Folio"):
                    with sqlite3.connect(DB_NAME) as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE movimientos SET Activo = 1 WHERE Folio = ?", (restaurar_folio,))
                        conn.commit()
                    registrar_bitacora("Admin", "RESTAURACIÓN", f"Restauró el folio {restaurar_folio}.")
                    st.rerun()
            else:
                st.info("La papelera está vacía.")