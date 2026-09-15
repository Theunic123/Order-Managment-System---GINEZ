import pandas as pd
import streamlit as st
import datetime
import calendar
import io
import os
import base64
import hashlib
from supabase import create_client, Client

# ==========================================
# CONFIGURACIÓN DE PÁGINA Y RUTAS
# ==========================================
st.set_page_config(
    page_title="Sistema Logístico - Grupo GINEZ",
    page_icon="📦",
    layout="wide",
)

st.logo("logo.jpg")

st.markdown("""
    <style>
        [data-testid="stLogo"] { height: 3.5rem !important; object-fit: contain !important; }
        button[kind="primary"] { background-color: #E2231A !important; border-color: #E2231A !important; color: white !important; }
        button[kind="primary"]:hover { background-color: #19255A !important; border-color: #19255A !important; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# CONEXIÓN A SUPABASE (NUBE PERSISTENTE)
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
            {"username": "ginezti", "password": hash_password(pass_admin), "rol": "Admin Master", "correo": "N/A"}
        ]
        supabase.table('usuarios').insert(usuarios_prueba).execute()

check_initial_admin()

# ==========================================
# 2. SISTEMA DE LOGIN
# ==========================================
def verificar_credenciales(username, password):
    hashed_pw = hash_password(password)
    res = supabase.table('usuarios').select('id, username, rol, correo').eq('username', username).eq('password', hashed_pw).execute()
    if len(res.data) > 0:
        user = res.data[0]
        return (user['id'], user['username'], user['rol'], user['correo'])
    return None

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
    if pd.isna(date_str) or not date_str: return None
    return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

roles_jefes = ["Admin Master", "Jefe de Área"]
st.session_state.is_admin = (st.session_state.rol == "Admin Master")
st.session_state.permiso_edicion = (st.session_state.rol in roles_jefes)
st.session_state.permiso_financiero = (st.session_state.rol in roles_jefes)

# ---------------------------------------------------------
# GUARDAR ARCHIVOS EN SUPABASE STORAGE (Actualizado para 4 archivos)
# ---------------------------------------------------------
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

    # 1. OC Interna (Con Precios)
    if archivo_pdf is not None:
        ruta_pdf = safe_upload(f"{base_path}/{folio}_interna.pdf", archivo_pdf.getvalue(), "application/pdf")
        
    # 2. Distribución Excel
    if es_oc and archivo_excel is not None:
        ext = archivo_excel.name.split('.')[-1]
        c_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx" else "application/vnd.ms-excel"
        ruta_excel = safe_upload(f"{base_path}/{prov_limpio}_{fecha_fmt}.{ext}", archivo_excel.getvalue(), c_type)
        
    # 3. OC Proveedor (Sin Precios)
    if es_oc and archivo_pdf_sp is not None:
        ruta_pdf_sp = safe_upload(f"{base_path}/{folio}_proveedor.pdf", archivo_pdf_sp.getvalue(), "application/pdf")
        
    # 4. Factura o Nota de Remisión
    if es_oc and archivo_factura is not None:
        ext = archivo_factura.name.split('.')[-1].lower()
        c_type = "application/pdf" if ext == "pdf" else f"image/{ext}"
        ruta_factura = safe_upload(f"{base_path}/{folio}_factura.{ext}", archivo_factura.getvalue(), c_type)
            
    return ruta_pdf, ruta_excel, ruta_pdf_sp, ruta_factura

# ==========================================
# MODALES DE INTERACCIÓN
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
            if es_oc:
                archivo_pdf_sp = st.file_uploader("📄 OC Proveedor (Sin precios)", type=["pdf"])
            else:
                archivo_pdf_sp = None
                
        with col_docs2:
            if es_oc: 
                archivo_excel = st.file_uploader("📊 Distribución (Excel)", type=["xlsx", "xls"])
                archivo_factura = st.file_uploader("🧾 Factura / Remisión Física (Recibo CEDIS)", type=["pdf", "jpg", "jpeg", "png"])
            else: 
                archivo_excel = None
                archivo_factura = None

        if st.form_submit_button("💾 Guardar", type="primary", use_container_width=True):
            if not folio.strip(): 
                st.error("⚠️ Folio obligatorio.")
            else:
                check_folio = supabase.table('movimientos').select('"Folio"').eq('"Folio"', folio).execute()
                if len(check_folio.data) > 0:
                    st.error("❌ El Folio ya existe en la base de datos.")
                else:
                    r_pdf, r_exc, r_pdf_sp, r_fac = guardar_archivos_fisicos(archivo_pdf, archivo_excel, archivo_pdf_sp, archivo_factura, folio, proveedor, destino, fecha, es_oc)
                    data_insert = {
                        "Folio": folio, "Proveedor": proveedor, "Fecha": fecha.strftime("%Y-%m-%d"),
                        "Estatus": estatus, "Entrega": entrega.strftime("%Y-%m-%d") if entrega else None,
                        "Recepcion": recepcion.strftime("%Y-%m-%d") if recepcion else None,
                        "Obs": obs, "Destino": destino, "Factura": factura, "Calidad": calidad,
                        "Monto": monto, "F_Pago": f_pago, "Est_Pago": est_pago, "Tipo_Doc": tipo_doc_bd,
                        "Ruta_PDF": r_pdf, "Ruta_Excel": r_exc, 
                        "Ruta_PDF_SinPrecios": r_pdf_sp, "Ruta_Factura": r_fac, 
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
                nuevo_monto, nueva_factura, nuevo_fpago, nuevo_estpago = 0.0, None, None, None

            st.markdown("#### 📂 Reemplazar / Añadir Documentos")
            c_doc1, c_doc2 = st.columns(2)
            with c_doc1: 
                nuevo_pdf = st.file_uploader("Sobrescribir OC Interna", type=["pdf"])
                if es_oc_edit:
                    nuevo_pdf_sp = st.file_uploader("Sobrescribir OC Proveedor", type=["pdf"])
                else: nuevo_pdf_sp = None
                
            with c_doc2: 
                if es_oc_edit:
                    nuevo_excel = st.file_uploader("Sobrescribir Distribución", type=["xlsx", "xls"]) 
                    nueva_factura_doc = st.file_uploader("Añadir / Sobrescribir Factura Física", type=["pdf", "jpg", "jpeg", "png"])
                else: 
                    nuevo_excel = None
                    nueva_factura_doc = None

            if st.form_submit_button("🔄 Actualizar", type="primary", use_container_width=True):
                ruta_pdf_bd = registro['Ruta_PDF']
                ruta_excel_bd = registro.get('Ruta_Excel', None)
                ruta_pdf_sp_bd = registro.get('Ruta_PDF_SinPrecios', None)
                ruta_fac_bd = registro.get('Ruta_Factura', None)

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
                registrar_bitacora(st.session_state.username, "ACTUALIZACIÓN", f"Se editó el folio {folio_editar}.")
                st.session_state.mensaje_exito = f"🔄 Movimiento {folio_editar} actualizado."
                st.rerun()

        # Botón de ELIMINACIÓN LÓGICA (Soft Delete)
        if st.button("🗑️ Enviar a Papelera (Ocultar)", type="primary", use_container_width=True):
            supabase.table('movimientos').update({"Activo": 0}).eq('"Folio"', folio_editar).execute()
            registrar_bitacora(st.session_state.username, "ELIMINACIÓN (Soft)", f"Se ocultó el folio {folio_editar}.")
            st.session_state.mensaje_exito = f"🗑️ Movimiento {folio_editar} movido a la papelera."
            st.rerun()

# ---------------------------------------------------------
# LECTOR DE ARCHIVOS DESDE URL DE LA NUBE (4 Archivos)
# ---------------------------------------------------------
@st.dialog("📄 Gestor de Documentos", width="large")
def modal_ver_documento(df_mov):
    folios_disponibles = df_mov['Folio'].tolist()
    if not folios_disponibles:
        st.info("No hay registros.")
        return
        
    folio_selec = st.selectbox("🔍 Selecciona el Folio:", [""] + folios_disponibles)
    if folio_selec:
        registro = df_mov[df_mov['Folio'] == folio_selec].iloc[0]
        ruta_pdf = registro.get('Ruta_PDF', None)
        ruta_excel = registro.get('Ruta_Excel', None)
        ruta_pdf_sp = registro.get('Ruta_PDF_SinPrecios', None)
        ruta_fac = registro.get('Ruta_Factura', None)
        
        st.markdown(f"### Documentos de: {folio_selec}")
        
        # Fila 1: OC Interna y Excel
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 📄 OC Interna (Con Precios)")
            if pd.notna(ruta_pdf) and str(ruta_pdf).startswith("http"):
                pdf_display = f'<iframe src="{ruta_pdf}" width="100%" height="300" type="application/pdf"></iframe>'
                st.markdown(pdf_display, unsafe_allow_html=True)
                st.markdown(f"**[📥 Descargar OC Interna]({ruta_pdf})**")
            else:
                st.warning("⚠️ Sin OC Interna adjunta.")

        with c2:
            st.markdown("#### 📊 Matriz de Distribución")
            if pd.notna(ruta_excel) and str(ruta_excel).startswith("http"):
                st.success("✅ Archivo de Excel ubicado exitosamente.")
                st.markdown(f"**[📥 Descargar Excel de Distribución]({ruta_excel})**")
            else:
                if registro['Tipo_Doc'] == 'TR': st.info("ℹ️ Los traspasos no llevan hoja de distribución.")
                else: st.warning("⚠️ Sin archivo Excel adjunto.")
                
        st.markdown("---")
        
        # Fila 2: OC Proveedor y Factura Física
        c3, c4 = st.columns(2)
        with c3:
            st.markdown("#### 📄 OC Proveedor (Sin Precios)")
            if pd.notna(ruta_pdf_sp) and str(ruta_pdf_sp).startswith("http"):
                pdf_display_sp = f'<iframe src="{ruta_pdf_sp}" width="100%" height="300" type="application/pdf"></iframe>'
                st.markdown(pdf_display_sp, unsafe_allow_html=True)
                st.markdown(f"**[📥 Descargar OC Proveedor]({ruta_pdf_sp})**")
            else:
                if registro['Tipo_Doc'] == 'TR': st.info("ℹ️ No aplica para traspasos.")
                else: st.warning("⚠️ Sin OC para Proveedor adjunta.")
                
        with c4:
            st.markdown("#### 🧾 Factura / Remisión de Llegada")
            if pd.notna(ruta_fac) and str(ruta_fac).startswith("http"):
                if "pdf" in str(ruta_fac).lower():
                    fac_display = f'<iframe src="{ruta_fac}" width="100%" height="300" type="application/pdf"></iframe>'
                    st.markdown(fac_display, unsafe_allow_html=True)
                else:
                    st.image(str(ruta_fac), use_container_width=True)
                st.markdown(f"**[📥 Descargar Factura / Remisión]({ruta_fac})**")
            else:
                if registro['Tipo_Doc'] == 'TR': st.info("ℹ️ No aplica para traspasos.")
                else: st.warning("⚠️ Sin evidencia de recepción adjunta.")

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

# Cargar BD de Supabase (Actualizada para incluir nuevas columnas)
res_movimientos = supabase.table('movimientos').select('*').eq('"Activo"', 1).execute()
df_movimientos = pd.DataFrame(res_movimientos.data)
if df_movimientos.empty:
    df_movimientos = pd.DataFrame(columns=[
        "id", "Folio", "Proveedor", "Fecha", "Estatus", "Entrega", "Recepcion", "Obs", "Destino", 
        "Factura", "Calidad", "Monto", "F_Pago", "Est_Pago", "Tipo_Doc", "Ruta_PDF", "Ruta_Excel", 
        "Ruta_PDF_SinPrecios", "Ruta_Factura", "Activo"
    ])

tabs_names = ["📊 Panel Principal", "📅 Calendario", "👤 Mi Perfil"]
if st.session_state.is_admin: tabs_names.append("⚙️ Panel Súper-Admin")

tabs = st.tabs(tabs_names)
tab_panel = tabs[0]
tab_calendario = tabs[1]
tab_perfil = tabs[2]
if st.session_state.is_admin: tab_admin = tabs[3]

# ------------------------------------------
# PESTAÑA 1: PANEL PRINCIPAL
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
        if st.button("📄 Ver Documentos", use_container_width=True): modal_ver_documento(df_movimientos)

    st.markdown("---")
    
    # ---------------------------------------------------------
    # MÉTRICAS Y LEYENDAS (Cálculo de últimos folios)
    # ---------------------------------------------------------
    df_oc = df_movimientos[df_movimientos['Tipo_Doc'] == 'OC']
    df_tr = df_movimientos[df_movimientos['Tipo_Doc'] == 'TR']
    
    pend_oc = len(df_oc[df_oc['Estatus'].isin(['Programado', 'Solicitado'])])
    pend_tr = len(df_tr[df_tr['Estatus'].isin(['Programado', 'Solicitado'])])
    
    # Lógica del folio mayor (Alfanumérico)
    ultimo_folio_oc = df_oc['Folio'].max() if not df_oc.empty else "Sin registros"
    # Lógica del último ingresado (Por orden cronológico de ID en la BD)
    ultimo_traspaso = df_tr.sort_values(by='id', ascending=False)['Folio'].iloc[0] if not df_tr.empty else "Sin registros"
    
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🕒 OC Pendientes", pend_oc)
    k2.metric("🔝 Último Folio OC", ultimo_folio_oc)
    k3.metric("🕒 Traspasos Pendientes", pend_tr)
    k4.metric("🔝 Último Traspaso", ultimo_traspaso)
    
    st.markdown("---")
    
    # BUSCADOR INTELIGENTE
    col_busq, col_vacia = st.columns([1, 2])
    with col_busq:
        busqueda = st.text_input("🔍 Buscar por Folio o Proveedor...")

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
        
        # Filtro de Búsqueda
        if busqueda:
            termino = busqueda.lower()
            df_movimientos = df_movimientos[
                df_movimientos['Folio'].str.lower().str.contains(termino) | 
                df_movimientos['Proveedor'].str.lower().str.contains(termino, na=False)
            ]
        
        col_oculta = ["Activo", "id", "Nivel_Urg", "Ruta_PDF", "Ruta_Excel", "Ruta_PDF_SinPrecios", "Ruta_Factura"]
        if not st.session_state.permiso_financiero: col_oculta.extend(["Monto", "Factura", "F_Pago", "Est_Pago", "Proveedor"])
            
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
                    prov_str = "Traspaso" if row['Tipo_Doc'] == 'TR' else (str(row['Proveedor']).strip() if pd.notna(row['Proveedor']) and str(row['Proveedor']).strip() != "" else "Sin Proveedor")
                    html_contenido += f"<div class='badge-pend'>🕒 {row['Folio']} ({row['Destino']})<br><span style='font-size: 0.85em; font-weight: normal;'>🏢 {prov_str}</span></div>"
                for _, row in recibidos.iterrows():
                    prov_str = "Traspaso" if row['Tipo_Doc'] == 'TR' else (str(row['Proveedor']).strip() if pd.notna(row['Proveedor']) and str(row['Proveedor']).strip() != "" else "Sin Proveedor")
                    html_contenido += f"<div class='badge-ok'>✅ {row['Folio']} ({row['Destino']})<br><span style='font-size: 0.85em; font-weight: normal;'>🏢 {prov_str}</span></div>"
                    
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
            datos_update = {"username": nuevo_user}
            if nueva_pass.strip() != "":
                datos_update["password"] = hash_password(nueva_pass)
                
            supabase.table('usuarios').update(datos_update).eq('id', st.session_state.user_id).execute()
            registrar_bitacora(st.session_state.username, "PERFIL", "Actualizó sus credenciales.")
            st.success("✅ Credenciales actualizadas. Por favor, cierra sesión e ingresa nuevamente.")

# ------------------------------------------
# PESTAÑA 4: PANEL SÚPER-ADMIN
# ------------------------------------------
if st.session_state.is_admin:
    with tab_admin:
        st.subheader("🛡️ Panel de Administración y Auditoría")
        
        c_adm1, c_adm2 = st.columns(2)
        with c_adm1:
            st.markdown("#### Gestión de Usuarios")
            with st.form("crear_usuario"):
                n_usr = st.text_input("Usuario")
                n_pwd = st.text_input("Contraseña temporal")
                n_rol = st.selectbox("Rol del Sistema", ["Jefe de Área", "Gerente de Sucursal"])
                n_correo = st.text_input("Correo Empresarial")
                if st.form_submit_button("Crear Usuario"):
                    supabase.table('usuarios').insert({"username": n_usr, "password": hash_password(n_pwd), "rol": n_rol, "correo": n_correo}).execute()
                    registrar_bitacora("Admin", "CREAR USUARIO", f"Usuario {n_usr} creado.")
                    st.success("Usuario creado con éxito.")
                    
            res_usr = supabase.table('usuarios').select('id, username, rol, correo').execute()
            df_usr = pd.DataFrame(res_usr.data)
            st.write("Usuarios Actuales")
            st.dataframe(df_usr, hide_index=True)

        with c_adm2:
            st.markdown("#### 📜 Bitácora de Trazabilidad")
            res_bit = supabase.table('bitacora').select('fecha, usuario, accion, detalle').order('id', desc=True).limit(50).execute()
            df_bitacora = pd.DataFrame(res_bit.data)
            st.dataframe(df_bitacora, height=400, hide_index=True)

            st.markdown("#### 🗑️ Papelera de Reciclaje (Registros Ocultos)")
            res_borrados = supabase.table('movimientos').select('Folio, Proveedor, Destino, Estatus').eq('"Activo"', 0).execute()
            df_borrados = pd.DataFrame(res_borrados.data)
            
            if not df_borrados.empty:
                st.dataframe(df_borrados, hide_index=True)
                restaurar_folio = st.selectbox("Selecciona un folio para restaurar", df_borrados['Folio'])
                if st.button("Restaurar Folio"):
                    supabase.table('movimientos').update({"Activo": 1}).eq('"Folio"', restaurar_folio).execute()
                    registrar_bitacora("Admin", "RESTAURACIÓN", f"Restauró el folio {restaurar_folio}.")
                    st.rerun()
            else:
                st.info("La papelera está vacía.")