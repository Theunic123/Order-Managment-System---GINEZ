import pandas as pd
import numpy as np
import re
import math
import streamlit as st # Inyectamos Streamlit para mostrar errores reales
import json

def cargar_a_supabase(supabase, tabla, df, batch_size=500):
    """Convierte el DataFrame a JSON estricto para evitar choques de compatibilidad con httpx/PostgREST."""
    if df.empty: return False
    
    try:
        # EL TRUCO MAGISTRAL: Pandas convierte todo a texto JSON perfecto y neutro. 
        # Automáticamente vuelve los NaNs, NaTs e Infinitos en "null" aceptados por bases de datos.
        json_str = df.to_json(orient='records', date_format='iso')
        datos_puros = json.loads(json_str)
        
        # Subimos la información en bloques
        for i in range(0, len(datos_puros), batch_size):
            bloque = datos_puros[i:i+batch_size]
            supabase.table(tabla).upsert(bloque).execute()
        return True
    except Exception as e:
        import streamlit as st
        st.error(f"🚨 Falla en la Base de Datos ({tabla}). Detalle: {str(e)}")
        return False
    
def limpiar_catalogo_mdm(archivo_catalogo):
    """Fase 1: Extrae Catálogo de SICAR puro."""
    try:
        df_cat = pd.read_excel(archivo_catalogo, sheet_name=0)
        df_cat.columns = df_cat.columns.astype(str).str.strip().str.lower().str.replace(' ', '_')
        
        if 'clave' in df_cat.columns: df_cat = df_cat.rename(columns={'clave': 'sku'})
        
        df_cat = df_cat.dropna(subset=['sku']).drop_duplicates(subset=['sku'])
        if 'descripcion' in df_cat.columns: 
            df_cat['descripcion'] = df_cat['descripcion'].astype(str).str.strip().str.upper()
        
        if 'departamento' not in df_cat.columns: df_cat['departamento'] = "SIN CLASIFICAR"
        if 'categoria' not in df_cat.columns: df_cat['categoria'] = "SIN CLASIFICAR"
        if 'precio_compra' not in df_cat.columns: df_cat['precio_compra'] = 0.0
        
        df_cat['pkg'] = 1 

        columnas_finales = ['sku', 'descripcion', 'departamento', 'categoria', 'pkg', 'precio_compra']
        return df_cat[[c for c in columnas_finales if c in df_cat.columns]]
    except Exception as e:
        print(f"Error procesando catálogo: {e}")
        return pd.DataFrame()

def limpiar_paquetes_bom(archivos_pkg):
    """Fase 1.5: Extrae BOM y rellena vacíos de la estructura SICAR."""
    all_dfs = []
    for archivo in archivos_pkg:
        try:
            df = pd.read_excel(archivo, skiprows=4, header=None)
            if df.shape[1] < 5: continue
            
            df = df.iloc[:, [0, 1, 4]]
            df.columns = ['id_paquete', 'sku_componente', 'cantidad_texto']
            df = df.iloc[1:].reset_index(drop=True)
            df['id_paquete'] = df['id_paquete'].ffill()
            df = df.dropna(subset=['sku_componente'])
            df['cantidad_componente'] = df['cantidad_texto'].astype(str).str.extract(r'(\d+(\.\d+)?)')[0]
            df['cantidad_componente'] = pd.to_numeric(df['cantidad_componente'], errors='coerce').fillna(1.0)
            all_dfs.append(df[['id_paquete', 'sku_componente', 'cantidad_componente']])
        except Exception:
            continue
            
    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()

def limpiar_ventas(archivos_ventas):
    """Fase 3: Extrae Tickets de SICAR blindado contra nulos."""
    all_dataframes = []
    for archivo in archivos_ventas:
        try:
            nombre = archivo.name.upper()
            sucursal = nombre.split('_')[1] if '_' in nombre else 'DESC'
            
            df = pd.read_excel(archivo, sheet_name='GeneralV', skiprows=5)
            column_mapping = {
                'Unnamed: 1': 'cantidad', 'Unnamed: 4': 'desc_cruda',
                'Unnamed: 19': 'importe', 'Unnamed: 27': 'total',
                'Column2': 'cantidad', 'Column5': 'desc_cruda',
                'Column20': 'importe', 'Column28': 'total',
                'Fecha': 'fecha', 'Folio': 'folio', 'Cliente': 'cliente', 'Documento': 'documento'
            }
            df = df.rename(columns=column_mapping)
            
            cols_to_fill = ['fecha', 'folio', 'cliente']
            exist_cols = [c for c in cols_to_fill if c in df.columns]
            df[exist_cols] = df[exist_cols].ffill()
            
            if 'cantidad' in df.columns: df = df.dropna(subset=['cantidad'])
            else: continue
                
            if 'desc_cruda' in df.columns:
                df['sku'] = df['desc_cruda'].str.extract(r'\[(.*?)\]')[0].fillna('SIN_SKU')
                df['desc_sicar'] = df['desc_cruda'].str.replace(r'\[.*?\]', '', regex=True).str.strip()
            
            df['sucursal'] = sucursal
            
            # --- BLINDAJE EXTRA CONTRA RESTRICCIONES DE POSTGRESQL ---
            if 'folio' in df.columns: df['folio'] = df['folio'].fillna("SIN_FOLIO").astype(str)
            if 'documento' in df.columns: df['documento'] = df['documento'].fillna("TICKET").astype(str)
            if 'cliente' in df.columns: df['cliente'] = df['cliente'].fillna("PUBLICO GENERAL").astype(str)
            
            for col in ['cantidad', 'importe', 'total']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.replace(r'[^\d.-]', '', regex=True)
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

            cols_finales = ['sucursal', 'fecha', 'documento', 'folio', 'cliente', 'sku', 'desc_sicar', 'cantidad', 'importe', 'total']
            all_dataframes.append(df[[c for c in cols_finales if c in df.columns]])
        except Exception as e:
            continue
            
    if all_dataframes:
        df_master = pd.concat(all_dataframes, ignore_index=True)
        if 'fecha' in df_master.columns: 
            df_master['fecha'] = pd.to_datetime(df_master['fecha'], errors='coerce').dt.strftime('%Y-%m-%d')
            # Forzar fecha segura para no chocar con SQL DATE NOT NULL
            df_master['fecha'] = df_master['fecha'].fillna(pd.Timestamp.today().strftime('%Y-%m-%d'))
        return df_master
    return pd.DataFrame()

def limpiar_inventarios(archivos_inv):
    """Fase 2: Consolida existencias dinámicamente."""
    all_dfs = []
    for archivo in archivos_inv:
        try:
            nombre = archivo.name.upper()
            partes = nombre.replace('.XLSX', '').replace('.XLS', '').split('_')
            sucursal = partes[0].strip()
            
            df = pd.read_excel(archivo, header=None)
            df = df.iloc[:, [0, 3, 8, 9, 10]]
            df.columns = ["sku", "descripcion", "precio_u", "existencias", "total"]
            
            df = df.dropna(subset=['sku', 'existencias'])
            mascara_basura = df['sku'].astype(str).str.contains("Reporte de Inventario|Clave", case=False, na=False)
            df = df[~mascara_basura].copy()
            
            for col in ['existencias', 'precio_u', 'total']:
                df[col] = df[col].astype(str).str.replace(r'[^\d.-]', '', regex=True)
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
            
            df['sucursal'] = sucursal
            df['fecha'] = pd.Timestamp.today().strftime('%Y-%m-%d') 
            
            all_dfs.append(df[['sucursal', 'fecha', 'sku', 'descripcion', 'existencias', 'precio_u', 'total']])
        except Exception:
            continue
            
    if all_dfs:
        return pd.concat(all_dfs, ignore_index=True)
    return pd.DataFrame()
