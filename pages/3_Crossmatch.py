"""
pages/3_Crossmatch.py
Investigación de registros NO_EXISTE_EN_CONTABILIDAD_D.
Lee Contabilidad completa de session_state. Solo informativo, no consume ledger.
"""

import streamlit as st
import pandas as pd
from core.io_utils import read_table, to_excel_bytes, show_df, prepare_df_for_excel
from core.engines.crossmatch import analizar_crossmatch
from core.ledger import crear_ledger, get_resumen_ledger

st.set_page_config(page_title="Crossmatch – Investigación", layout="wide")
st.title("🔍 Módulo 3 — Crossmatch · Investigación de No Encontrados")
st.caption("Analiza NO_EXISTE_EN_CONTABILIDAD_D buscando CA, PD y H • Solo informativo, no consume ledger")

# ─── Verificar contabilidad global ───
if "cont_global" not in st.session_state:
    st.error("⚠️ Primero carga la **Contabilidad** en la página principal (inicio).")
    st.stop()

if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

cont_global = st.session_state["cont_global"]
st.info(
    f"📂 Contabilidad: **{st.session_state['cont_file_name']}** · "
    f"Total movimientos: **{len(cont_global):,}**"
)

# ─── Sidebar ───
with st.sidebar:
    st.header("📁 Archivos")
    reporte_file = st.file_uploader(
        "Reporte Base Saldos clasificado (con columna ESTATUS_MATCH)",
        type=["xlsx","xls","xlsm","csv"],
        help="Descargado del Módulo 2 — hoja Base_clasificada.",
    )
    st.divider()

    st.header("⚙️ Configuración")
    muestra    = st.checkbox("Modo prueba (1,000 registros)", value=False)
    bonif_min  = st.number_input("Bonif. diesel mín ($)", 0.0, 100.0, 10.0, step=1.0)
    bonif_max  = st.number_input("Bonif. diesel máx ($)", 0.0, 500.0, 20.0, step=1.0)
    usar_ledger= st.checkbox("Excluir mov. ya consumidos (ledger)", value=True,
                             help="Informa qué mov. contables ya fueron bloqueados por módulos anteriores.")
    st.divider()
    run = st.button("🚀 Analizar", type="primary", use_container_width=True)

# ─── Estado ledger en sidebar ───
resumen_led = get_resumen_ledger(st.session_state.match_ledger)
st.sidebar.divider()
st.sidebar.caption(
    f"🔒 Ledger: {resumen_led['consumidos']} bloqueados · "
    f"Procesos: {', '.join(resumen_led['procesos']) or 'ninguno'}"
)

if not reporte_file:
    st.info("👈 Carga el reporte de Base Saldos clasificado para continuar.")
    st.markdown("""
    ### Flujo esperado
    1. **Módulo 1 (Ingresos)** → genera resultados y actualiza ledger
    2. **Módulo 2 (Costos)** → genera resultados, descarga el Excel
    3. **Este módulo** → sube la hoja `Base_clasificada` del Excel de Costos
    4. Filtra los `NO_EXISTE_EN_CONTABILIDAD_D` y busca en pólizas CA, PD y H
    """)
    st.stop()

# ─── Control de re-proceso ───
sig = (
    reporte_file.name,
    muestra, bonif_min, bonif_max, usar_ledger,
    st.session_state.get("cont_file_name",""),
    resumen_led["consumidos"],
)
if st.session_state.get("crossmatch_sig") != sig:
    st.session_state["crossmatch_result"] = None

if not run and st.session_state.get("crossmatch_result") is None:
    st.info("Configura y presiona **🚀 Analizar**.")
    st.stop()

# ─── Procesar ───
if run or st.session_state.get("crossmatch_result") is None:

    with st.spinner("Cargando reporte..."):
        try:
            df_reporte = read_table(reporte_file)
        except Exception as e:
            st.error(f"Error leyendo reporte: {e}")
            st.stop()

    if "ESTATUS_MATCH" not in df_reporte.columns:
        st.error(
            "❌ El reporte no tiene la columna `ESTATUS_MATCH`. "
            "Asegúrate de subir la hoja **Base_clasificada** del Excel generado por el Módulo 2."
        )
        st.stop()

    df_no_existe = df_reporte[df_reporte["ESTATUS_MATCH"] == "NO_EXISTE_EN_CONTABILIDAD_D"].copy()

    if df_no_existe.empty:
        st.success("✅ No hay registros con ESTATUS_MATCH = NO_EXISTE_EN_CONTABILIDAD_D.")
        st.stop()

    if muestra:
        df_no_existe = df_no_existe.head(1000)
        st.warning(f"⚠️ Modo prueba: {len(df_no_existe):,} registros.")

    st.write(f"📊 Registros a investigar: **{len(df_no_existe):,}**")

    # Usar contabilidad del session_state (ya cargada, no se vuelve a leer)
    ledger = st.session_state.match_ledger if usar_ledger else crear_ledger()

    resultado = analizar_crossmatch(
        df_no_existe,
        cont_global,           # ← del session_state, no de un file_uploader
        ledger=ledger,
        bonif_min=bonif_min,
        bonif_max=bonif_max,
    )

    st.session_state["crossmatch_result"] = resultado
    st.session_state["crossmatch_sig"]    = sig

# ─── Mostrar resultados ───
resultado = st.session_state["crossmatch_result"]
total     = len(resultado)

resumen_df = resultado["TIPO_CASO"].value_counts().reset_index()
resumen_df.columns = ["Tipo", "Cantidad"]
resumen_df["% del total"] = (resumen_df["Cantidad"] / total * 100).round(2)

st.divider()
c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("Total analizados",   f"{total:,}")
c2.metric("✅ Bonif. Diesel",   int((resultado["TIPO_CASO"]=="BONIFICACION_DIESEL").sum()))
c3.metric("✅ Completos",        int(resultado["TIPO_CASO"].str.contains("COMPLETO",na=False).sum()))
c4.metric("⚠️ Solo Cargo",      int(resultado["TIPO_CASO"].str.contains("SOLO_CARGO",na=False).sum()))
c5.metric("❌ No encontrado",    int((resultado["TIPO_CASO"]=="NO_ENCONTRADO").sum()))

st.subheader("Distribución por tipo de caso")
st.dataframe(resumen_df, hide_index=True, use_container_width=True)

st.divider()
tab_bonif, tab_comp, tab_solo, tab_no, tab_todo = st.tabs([
    "💰 Bonif. Diesel", "✅ Completos", "⚠️ Solo Cargo/Abono", "❌ No Encontrado", "📋 Todo",
])

with tab_bonif:
    df_b = resultado[resultado["TIPO_CASO"]=="BONIFICACION_DIESEL"]
    st.caption(f"{len(df_b):,} registros"); show_df(df_b)
with tab_comp:
    df_c = resultado[resultado["TIPO_CASO"].str.contains("COMPLETO",na=False)]
    st.caption(f"{len(df_c):,} registros"); show_df(df_c)
with tab_solo:
    df_s = resultado[resultado["TIPO_CASO"].str.contains("SOLO",na=False)]
    st.caption(f"{len(df_s):,} registros"); show_df(df_s)
with tab_no:
    df_n = resultado[resultado["TIPO_CASO"]=="NO_ENCONTRADO"]
    st.caption(f"{len(df_n):,} registros"); show_df(df_n)
with tab_todo:
    cols = ["DIAGNOSTICO","TIPO_CASO","FOLIO_CONTRARECIBO","NUMERO_VIAJE","Importe",
            "Concepto contabilidad","ca_unidad","ca_viaje","pd_poliza","pd_unidad",
            "h_poliza","h_unidad","h_owner","bonif_diff"]
    show_df(resultado[[c for c in cols if c in resultado.columns]])

# ─── Exportar ───
st.divider()
if st.button("Preparar Excel"):
    with st.spinner("Generando..."):
        xlsx = to_excel_bytes({
            "Completo":      prepare_df_for_excel(resultado),
            "Bonif_Diesel":  prepare_df_for_excel(resultado[resultado["TIPO_CASO"]=="BONIFICACION_DIESEL"]),
            "Completos":     prepare_df_for_excel(resultado[resultado["TIPO_CASO"].str.contains("COMPLETO",na=False)]),
            "Solo_Cargo":    prepare_df_for_excel(resultado[resultado["TIPO_CASO"].str.contains("SOLO_CARGO",na=False)]),
            "Solo_Abono":    prepare_df_for_excel(resultado[resultado["TIPO_CASO"]=="SOLO_ABONO_H"]),
            "No_Encontrado": prepare_df_for_excel(resultado[resultado["TIPO_CASO"]=="NO_ENCONTRADO"]),
            "Resumen":       prepare_df_for_excel(resumen_df),
        })
    st.download_button(
        "⬇️ Descargar reporte Crossmatch", data=xlsx,
        file_name="reporte_crossmatch.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
