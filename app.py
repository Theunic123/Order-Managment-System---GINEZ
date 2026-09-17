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
