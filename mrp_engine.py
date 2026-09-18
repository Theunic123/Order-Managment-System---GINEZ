import pandas as pd
import numpy as np
from scipy.stats import norm, gamma, skew, gaussian_kde
from xgboost import XGBRegressor
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor
from statsmodels.tsa.holtwinters import ExponentialSmoothing
import warnings

warnings.filterwarnings("ignore")

# =============================================================================
# FUNCIONES AUXILIARES (Syntetos-Boylan, Croston, FVA)
# =============================================================================
def clasificar_demanda(historial):
    demanda_positiva = historial[historial > 0]
    if len(demanda_positiva) == 0: return 'Obsolete/Zero'
    
    adi = len(historial) / len(demanda_positiva)
    cv2 = (np.std(demanda_positiva, ddof=1) / np.mean(demanda_positiva))**2 if len(demanda_positiva) > 1 else 0.0
    
    if adi < 1.32 and cv2 < 0.49: return 'Smooth'
    elif adi < 1.32 and cv2 >= 0.49: return 'Erratic'
    elif adi >= 1.32 and cv2 < 0.49: return 'Intermittent'
    else: return 'Lumpy'

def demanda_agregada_exacta(serie, t, x_tau):
    h_int = int(np.floor(x_tau))
    frac = x_tau - h_int
    if t + h_int + (1 if frac > 0 else 0) > len(serie): return np.nan
    demanda = np.sum(serie[t : t+h_int]) if h_int > 0 else 0
    if frac > 0: demanda += serie[t+h_int] * frac
    return demanda

def crear_features_direct(serie, x_tau, lags=3):
    X_data, y_data = [], []
    h_ceil = int(np.ceil(x_tau))
    for t in range(lags, len(serie) - h_ceil + 1):
        X_data.append([serie[t-1], serie[t-2], serie[t-3], np.mean(serie[t-3:t])])
        y_data.append(demanda_agregada_exacta(serie, t, x_tau))
    return np.array(X_data), np.array(y_data)

def intermittent_forecasts(train_s, h_tau, alpha=0.1, beta=0.1):
    z, p, p_tsb = (train_s[0] if train_s[0]>0 else 1), 1.0, 0.5
    q = 1
    for y in train_s:
        if y > 0:
            z = alpha * y + (1 - alpha) * z
            p = alpha * q + (1 - alpha) * p
            p_tsb = beta * 1 + (1 - beta) * p_tsb
            q = 1
        else:
            p_tsb = beta * 0 + (1 - beta) * p_tsb
            q += 1
            
    f_cros = (z / p) * h_tau if p > 0 else 0
    f_sba = (1 - alpha / 2) * (z / p) * h_tau if p > 0 else 0
    f_tsb = (p_tsb * z) * h_tau
    return f_cros, f_sba, f_tsb

def calcular_target_level(historial, x_tau):
    """Torneo FVA y Optimización de Inventario."""
    tipo_dem = clasificar_demanda(historial)
    if tipo_dem == 'Obsolete/Zero': 
        return 0.0, 0.0, 0.0, 'Demanda Nula', 0.0

    n, h_ceil = len(historial), int(np.ceil(x_tau))
    errores = {'Naive': [], 'SMA': [], 'Holt-Winters': [], 'XGBoost': [], 'RandomForest': [], 'ExtraTrees': [], 'Croston': [], 'SBA': [], 'TSB': []}
    
    in_sample_naive_mae = np.mean(np.abs(np.diff(historial))) if len(historial) > 1 else 1.0
    if in_sample_naive_mae == 0: in_sample_naive_mae = 1.0

    if n >= 4 + h_ceil:
        for t in range(4, n - h_ceil + 1):
            train_s = historial[:t]
            actual_y = demanda_agregada_exacta(historial, t, x_tau)
            if np.isnan(actual_y): continue
            
            errores['Naive'].append(actual_y - (train_s[-1] * x_tau))
            errores['SMA'].append(actual_y - (np.mean(train_s[-min(3, len(train_s)):]) * x_tau))
            
            if tipo_dem in ['Smooth', 'Erratic']:
                try: errores['Holt-Winters'].append(actual_y - max(0, (np.sum(ExponentialSmoothing(train_s, initialization_method="estimated").fit(optimized=True).forecast(h_ceil)) / h_ceil) * x_tau if h_ceil > 0 else 0))
                except: pass
                
                X_tr, y_tr = crear_features_direct(train_s, x_tau, 3)
                if len(X_tr) > 0 and len(train_s) >= 3:
                    X_p = np.array([[train_s[-1], train_s[-2], train_s[-3], np.mean(train_s[-3:])]])
                    errores['XGBoost'].append(actual_y - max(0, XGBRegressor(n_estimators=50, random_state=42).fit(X_tr, y_tr).predict(X_p)[0]))
                    errores['RandomForest'].append(actual_y - max(0, RandomForestRegressor(n_estimators=50, random_state=42).fit(X_tr, y_tr).predict(X_p)[0]))
            
            if tipo_dem in ['Intermittent', 'Lumpy']:
                fc, fsba, ftsb = intermittent_forecasts(train_s, x_tau)
                errores['Croston'].append(actual_y - fc)
                errores['SBA'].append(actual_y - fsba)
                errores['TSB'].append(actual_y - ftsb)

    fva_results = []
    for mod, err in errores.items():
        if len(err) > 0:
            bias = np.mean(err)
            mae = np.mean(np.abs(err))
            mase = mae / in_sample_naive_mae
            rmse = np.sqrt(np.mean(np.square(err)))
            fva_results.append({'Modelo': mod, 'Abs_Bias': abs(bias), 'Bias': bias, 'MASE': mase, 'RMSE': rmse, 'SigmaE': np.std(err, ddof=1) if len(err) > 1 else rmse})
    
    if not fva_results:
        fva_results.append({'Modelo': 'Heurística', 'Abs_Bias': 0, 'Bias': 0, 'MASE': 1, 'RMSE': np.std(historial)*np.sqrt(x_tau), 'SigmaE': np.std(historial)*np.sqrt(x_tau)})

    df_fva = pd.DataFrame(fva_results).sort_values(by=['Abs_Bias', 'MASE', 'RMSE'])
    ganador = df_fva.iloc[0]
    mejor_modelo, sigma_e = ganador['Modelo'], max(0.01, ganador['SigmaE'])

    ult = historial[-3:] if len(historial) >= 3 else historial
    if mejor_modelo == 'Naive': mu_x = historial[-1] * x_tau 
    elif mejor_modelo in ['SMA', 'Heurística']: mu_x = np.mean(ult) * x_tau
    elif mejor_modelo == 'Holt-Winters':
        try: mu_x = max(0, (np.sum(ExponentialSmoothing(historial, initialization_method="estimated").fit(optimized=True).forecast(h_ceil)) / h_ceil) * x_tau if h_ceil > 0 else 0)
        except: mu_x = np.mean(ult) * x_tau
    elif mejor_modelo in ['Croston', 'SBA', 'TSB']:
        fc, fsba, ftsb = intermittent_forecasts(historial, x_tau)
        mu_x = {'Croston': fc, 'SBA': fsba, 'TSB': ftsb}[mejor_modelo]
    else:
        X_all, y_all = crear_features_direct(historial, x_tau, 3)
        X_p = np.array([[ult[-1], ult[-2], ult[-3], np.mean(ult)]])
        mod_final = {'XGBoost': XGBRegressor, 'RandomForest': RandomForestRegressor, 'ExtraTrees': ExtraTreesRegressor}[mejor_modelo](n_estimators=50, random_state=42).fit(X_all, y_all)
        mu_x = max(0, mod_final.predict(X_p)[0])

    CSL = 0.95
    if tipo_dem in ['Intermittent', 'Lumpy'] and np.mean(historial == 0) > 0.6 and mu_x < 5:
        datos_riesgo = np.convolve(historial, np.ones(h_ceil, dtype=int), 'valid') if len(historial) >= h_ceil else historial * x_tau
        target_level = np.argmax(np.cumsum(gaussian_kde(datos_riesgo, bw_method='scott').evaluate(np.arange(0, int(np.max(datos_riesgo) * 2 + max(10, mu_x))))) >= CSL) if np.std(datos_riesgo) > 0 else np.max(datos_riesgo)
    elif skew(historial) > 1 or (mu_x - 2*sigma_e < 0):
        target_level = gamma.ppf(CSL, a=(max(mu_x, 0.01)**2)/(max(sigma_e, 0.01)**2), scale=(max(sigma_e, 0.01)**2)/max(mu_x, 0.01))
    else: 
        target_level = mu_x + norm.ppf(CSL) * sigma_e

    cv = (sigma_e / mu_x) if mu_x > 0 else 0
    return max(0, mu_x), sigma_e, max(0, target_level), mejor_modelo, cv

# =============================================================================
# FUNCIÓN PRINCIPAL EXPORTABLE A STREAMLIT
# =============================================================================
def generar_sugerencia_pull(proveedor, l_prov, r_prov, sucursal, df_ventas, df_inventarios, df_catalogo):
    """
    Toma los datos puros desde Supabase, los convierte en series de tiempo,
    los pasa por el torneo FVA y devuelve un DataFrame listo para la UI de Streamlit.
    """
    # 1. Cruzar ventas con catálogo para filtrar solo artículos del proveedor seleccionado
    df_ventas = df_ventas.merge(df_catalogo[['sku', 'departamento', 'pkg']], on='sku', how='left')
    df_ventas_prov = df_ventas[df_ventas['departamento'].str.upper() == proveedor.upper()]
    
    if df_ventas_prov.empty:
        return pd.DataFrame() # No hay ventas de este proveedor
        
    # 2. Filtrar por sucursal (Si no es CEDIS consolidado)
    if sucursal != 'CEDIS':
        df_ventas_prov = df_ventas_prov[df_ventas_prov['sucursal'].str.upper() == sucursal.upper()]
        
    # 3. Crear Series de Tiempo (Agrupación Mensual)
    df_ventas_prov['fecha'] = pd.to_datetime(df_ventas_prov['fecha'])
    df_ventas_prov['PERIODO'] = df_ventas_prov['fecha'].dt.to_period('M')
    
    df_agrupado = df_ventas_prov.groupby(['sku', 'desc_sicar', 'PERIODO'])['cantidad'].sum().reset_index()
    
    if df_agrupado.empty:
        return pd.DataFrame()

    matriz_ts = df_agrupado.pivot(index=['sku', 'desc_sicar'], columns='PERIODO', values='cantidad').fillna(0)
    
    # 4. Obtener Stock Actual (El más reciente de la tabla inventario_historico)
    df_inv_suc = df_inventarios[df_inventarios['sucursal'].str.upper() == sucursal.upper()]
    # Nos quedamos con el último registro por SKU basado en el ID (El más nuevo)
    if not df_inv_suc.empty:
        df_inv_suc = df_inv_suc.sort_values(by='id').drop_duplicates(subset=['sku'], keep='last')
        dict_stock = dict(zip(df_inv_suc['sku'], df_inv_suc['existencias']))
    else:
        dict_stock = {}

    # 5. Ejecutar Motor FVA por cada SKU
    x_tau = l_prov + r_prov
    resultados = []
    
    for (sku, descripcion), row in matriz_ts.iterrows():
        historial = row.values
        # Solo calculamos si hay historia real (evitar procesar basura)
        if len(historial) < 3 or np.sum(historial) == 0:
            continue
            
        mu_x, sigma, target, modelo, cv = calcular_target_level(historial, x_tau)
        
        stock_actual = dict_stock.get(sku, 0)
        necesidad = max(0, target - stock_actual)
        
        resultados.append({
            "SKU": sku,
            "Descripción": descripcion,
            "Ventas (Media/Mes)": round(np.mean(historial), 1),
            "Stock Actual": int(stock_actual),
            "Mínimo / SS": int(max(0, target - mu_x)),
            "Máximo / Target": int(target),
            "Sugerencia Sistema": int(necesidad),
            "Desviación (σ)": round(sigma, 2),
            "CV": round(cv, 2)
        })
        
    df_resultado = pd.DataFrame(resultados)
    if not df_resultado.empty:
        df_resultado = df_resultado.sort_values(by="Sugerencia Sistema", ascending=False).reset_index(drop=True)
        
    return df_resultado

import io

def consolidar_y_generar_excel(proveedor, df_pedidos, df_detalles, df_inv_cedis, df_catalogo):
    """
    Toma los pedidos de las sucursales, descuenta el stock de CEDIS,
    calcula la necesidad de compra global y genera la Matriz de Cross-Docking.
    Retorna un archivo Excel en memoria listo para descargar.
    """
    # 1. Preparar datos y catálogos
    df_detalles = df_detalles.merge(df_catalogo[['sku', 'descripcion', 'pkg', 'precio_compra']], on='sku', how='left')
    df_detalles['pkg'] = df_detalles['pkg'].fillna(1).astype(int)
    df_detalles['precio_compra'] = df_detalles['precio_compra'].fillna(0.0)

    # 2. Agrupar la necesidad total de la red (Suma de lo que piden todas las sucursales)
    df_base = df_detalles.groupby(['sku', 'descripcion', 'pkg', 'precio_compra'])['cantidad_pedida'].sum().reset_index()
    df_base.rename(columns={'cantidad_pedida': 'NECESIDAD_PURA_RED'}, inplace=True)

    # 3. Cruzar con el inventario actual del CEDIS
    dict_stock_cedis = dict(zip(df_inv_cedis['sku'], df_inv_cedis['existencias']))
    df_base['STOCK_CEDIS'] = df_base['sku'].map(dict_stock_cedis).fillna(0)

    # 4. Calcular Compra Sugerida (Lo que pide la red menos lo que ya tenemos en CEDIS)
    df_base['COMPRA_SUGERIDA'] = np.maximum(0, df_base['NECESIDAD_PURA_RED'] - df_base['STOCK_CEDIS'])
    
    # Redondear a empaques (PKG)
    df_base['PIEZAS'] = np.ceil(df_base['COMPRA_SUGERIDA'] / df_base['pkg']) * df_base['pkg']
    df_base['PAQUETES'] = (df_base['PIEZAS'] / df_base['pkg']).astype(int)
    df_base['IMPORTE'] = df_base['PIEZAS'] * df_base['precio_compra']

    # Hoja 1: Pedido Global
    df_pedido = df_base[['sku', 'descripcion', 'pkg', 'PIEZAS', 'PAQUETES', 'precio_compra', 'IMPORTE', 'NECESIDAD_PURA_RED', 'STOCK_CEDIS']].rename(columns={'sku': 'CLAVE'}).sort_values(by='IMPORTE', ascending=False)

    # 5. MATRIZ DE DISTRIBUCIÓN (Cross-Docking con Fair-Share)
    df_detalles = df_detalles.merge(df_pedidos[['id', 'sucursal']], left_on='pedido_id', right_on='id', how='left')
    df_detalles['COMPRA_EN_CAMINO'] = df_detalles['sku'].map(dict(zip(df_base['sku'], df_base['PIEZAS']))).fillna(0)
    df_detalles['STOCK_CEDIS'] = df_detalles['sku'].map(dict_stock_cedis).fillna(0)
    
    # Total de piezas disponibles para repartir (Lo que hay + lo que llegará)
    df_detalles['INV_TOTAL_REPARTIR'] = df_detalles['STOCK_CEDIS'] + df_detalles['COMPRA_EN_CAMINO']

    # Fair Share (Reparto equitativo proporcional)
    total_need_por_sku = df_detalles.groupby('sku')['cantidad_pedida'].transform('sum')
    df_detalles['ALLOCATION_RAW'] = np.where(total_need_por_sku > 0, (df_detalles['cantidad_pedida'] / total_need_por_sku) * df_detalles['INV_TOTAL_REPARTIR'], 0)
    df_detalles['ENVIO_FINAL'] = np.minimum(df_detalles['ALLOCATION_RAW'], df_detalles['cantidad_pedida'])
    
    # Redondear envíos al PKG más cercano hacia abajo
    df_detalles['ENVIO_FINAL'] = np.floor(df_detalles['ENVIO_FINAL'] / df_detalles['pkg']) * df_detalles['pkg']

    # Hoja 2: Matriz Dinámica Pivotada
    df_pivot = df_detalles.pivot_table(index=['sku', 'descripcion'], columns='sucursal', values='ENVIO_FINAL', fill_value=0).reset_index()
    df_pivot.rename(columns={'sku': 'CLAVE', 'descripcion': 'DESCRIPCION'}, inplace=True)
    
    # 6. Construir el archivo Excel en la memoria RAM
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df_pedido.to_excel(writer, sheet_name='PEDIDO CEDIS', index=False)
        df_pivot.to_excel(writer, sheet_name='DISTRIBUCION', index=False)
        
        # Formatos Bonitos (Enterprise Grade)
        wb = writer.book
        f_mon = wb.add_format({'num_format': '$#,##0.00'})
        f_ent = wb.add_format({'num_format': '#,##0'})
        
        ws_p = writer.sheets['PEDIDO CEDIS']
        ws_p.set_column('A:A', 15)
        ws_p.set_column('B:B', 45)
        ws_p.set_column('C:E', 12, f_ent)
        ws_p.set_column('F:G', 14, f_mon)

        ws_d = writer.sheets['DISTRIBUCION']
        ws_d.set_column('A:A', 15)
        ws_d.set_column('B:B', 45)

    return output.getvalue()
