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