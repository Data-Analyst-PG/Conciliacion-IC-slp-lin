"""
pages/2_Costos_Base_Vales.py
Base Saldos vs Contabilidad D + Vales vs Contabilidad D.
Usa ledger para excluir movimientos ya consumidos por Ingresos.
"""

import streamlit as st
import pandas as pd
from core.io_utils import read_table, to_excel_bytes, show_df, load_concept_map, prepare_df_for_excel
from core.engines.majority import (
    prep_contabilidad_costos, prep_base_saldos, prep_vales,
    match_base_vs_cont, match_vales_vs_cont, resumen_dh_contabilidad,
)
from core.ledger import crear_ledger, filtrar_cont_disponible, registrar_matches_batch, get_resumen_ledger

st.set_page_config(page_title="Costos – Base Saldos & Vales", layout="wide")
st.title("💰 Costos — Base Saldos & Vales vs Contabilidad D")
st.caption("Match por mayoría de criterios (scoring) • Respeta ledger de Ingresos • Prioridades 2 y 3")

# ─── Ledger global ───
if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

# ─── Sidebar ───
with st.sidebar:
    st.header("📁 Archivos")
    cont_file    = st.file_uploader("Contabilidad",               type=["xlsx","xls","xlsm","csv"])
    base_file    = st.file_uploader("Base Saldos corregida",       type=["xlsx","xls","xlsm","csv"])
    vales_file   = st.file_uploader("Vales",                       type=["xlsx","xls","xlsm","csv"])
    concept_file = st.file_uploader("Catálogo conceptos (opt)",    type=["xlsx","xls","xlsm","csv"])
    st.divider()

    st.header("⚙️ Configuración")
    ndigits       = st.number_input("Redondeo importe", 0, 4, 2)
    proceso       = st.radio("Proceso a ejecutar", ["Base Saldos vs Cont D", "Vales vs Cont D", "Ambos"], index=2)
    consumir_discr= st.checkbox("Discrepancias consumen ledger", value=False)
    respetar_led  = st.checkbox("Respetar ledger de Ingresos", value=True,
                                help="Excluye de Contabilidad los registros ya consumidos por el proceso de Ingresos.")
    st.divider()
    run = st.button("▶ Procesar", type="primary", use_container_width=True)

if not cont_file:
    st.info("👈 Carga al menos el archivo de Contabilidad.")
    st.stop()

if not run:
    st.info("Configura y presiona **Procesar**.")
    st.stop()

# ─── Carga ───
concept_map = load_concept_map(concept_file)

try:
    cont_raw = read_table(cont_file, preferred_sheet="ContabilidadSET_PLUS_datos")
    cont_d, cont_colmap = prep_contabilidad_costos(cont_raw, ndigits, concept_map, tipo_mov="D")
    cont_all, _         = prep_contabilidad_costos(cont_raw, ndigits, concept_map, tipo_mov=None)
except Exception as e:
    st.error(f"Error preparando Contabilidad: {e}")
    st.stop()

# ─── Aplicar ledger ───
ledger = st.session_state.match_ledger
if respetar_led and not ledger.empty:
    cont_d_disp = filtrar_cont_disponible(cont_d, ledger)
    excluidos   = len(cont_d) - len(cont_d_disp)
    if excluidos:
        st.info(f"ℹ️ {excluidos:,} movimientos D excluidos por ledger de Ingresos.")
else:
    cont_d_disp = cont_d.copy()

# ─── Info contabilidad ───
st.subheader("Contabilidad D disponible")
c1,c2,c3 = st.columns(3)
c1.metric("Total movimientos D", f"{len(cont_d):,}")
c2.metric("Disponibles (no consumidos)", f"{len(cont_d_disp):,}")
c3.metric("Columna importe usada", cont_colmap.get("importe_usado",""))

result_sheets: dict[str, pd.DataFrame] = {
    "Contabilidad_D":      cont_d,
    "Contabilidad_D_Disp": cont_d_disp,
    "Cont_todos_movs":     cont_all,
}

# ════════════════════════════════════════
# BASE SALDOS
# ════════════════════════════════════════
if proceso in {"Base Saldos vs Cont D", "Ambos"}:
    st.divider()
    st.header("1️⃣ Base Saldos vs Contabilidad D")

    if base_file is None:
        st.warning("⚠️ Falta el archivo de Base Saldos.")
    else:
        try:
            base_raw  = read_table(base_file)
            base      = prep_base_saldos(base_raw, ndigits, concept_map)

            with st.spinner("Ejecutando match Base Saldos..."):
                base_clas, cont_base_clas, cand_base, best_base = match_base_vs_cont(base, cont_d_disp)

            # Registrar en ledger
            if not best_base.empty:
                for estatus, consumir in [("MATCH_OK", True), ("MATCH_CON_DISCREPANCIA", consumir_discr)]:
                    rows = best_base[best_base["ESTATUS_MATCH"] == estatus]
                    if not rows.empty:
                        st.session_state.match_ledger = registrar_matches_batch(
                            st.session_state.match_ledger, rows,
                            proceso="BASE_SALDOS",
                            source_module="2_Costos_Base_Vales",
                            source_table="Base_Saldos",
                            left_id_col="ROW_ID_BASE",
                            cont_id_col="ROW_ID_CONT",
                            cont_df=cont_d_disp,
                            puede_consumir=consumir,
                        )

            # Métricas
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Base filas",                f"{len(base):,}")
            c2.metric("✅ MATCH_OK",               int((base_clas["ESTATUS_MATCH"]=="MATCH_OK").sum()))
            c3.metric("⚠️ CON_DISCREPANCIA",       int((base_clas["ESTATUS_MATCH"]=="MATCH_CON_DISCREPANCIA").sum()))
            c4.metric("❌ No existe en Cont D",    int((base_clas["ESTATUS_MATCH"]=="NO_EXISTE_EN_CONTABILIDAD_D").sum()))

            # Tabs
            tb1,tb2,tb3,tb4 = st.tabs(["Base clasificada","Contabilidad vs Base","Candidatos técnicos","Mejores matches"])
            with tb1: show_df(base_clas)
            with tb2: show_df(cont_base_clas)
            with tb3: show_df(cand_base)
            with tb4: show_df(best_base)

            result_sheets.update({
                "Base_clasificada":    base_clas,
                "Cont_vs_Base":        cont_base_clas,
                "Candidatos_Base":     cand_base,
                "Mejores_Base":        best_base,
            })

        except Exception as e:
            st.error(f"Error en Base Saldos: {e}")

# ════════════════════════════════════════
# VALES
# ════════════════════════════════════════
# Actualizar cont_d_disp con lo que se consumió en Base Saldos
if proceso in {"Vales vs Cont D", "Ambos"}:
    # Re-filtrar ledger actualizado
    ledger_actual = st.session_state.match_ledger
    if respetar_led:
        cont_d_disp_vales = filtrar_cont_disponible(cont_d, ledger_actual)
    else:
        cont_d_disp_vales = cont_d.copy()

    st.divider()
    st.header("2️⃣ Vales vs Contabilidad D")

    if vales_file is None:
        st.warning("⚠️ Falta el archivo de Vales.")
    else:
        try:
            vales_raw  = read_table(vales_file)
            vales      = prep_vales(vales_raw, ndigits, concept_map)

            with st.spinner("Ejecutando match Vales..."):
                vales_clas, cont_vales_clas, cand_vales, best_vales = match_vales_vs_cont(vales, cont_d_disp_vales)

            # Registrar en ledger
            if not best_vales.empty:
                for estatus, consumir in [("MATCH_OK", True), ("MATCH_CON_DISCREPANCIA", consumir_discr)]:
                    rows = best_vales[best_vales["ESTATUS_MATCH"] == estatus]
                    if not rows.empty:
                        st.session_state.match_ledger = registrar_matches_batch(
                            st.session_state.match_ledger, rows,
                            proceso="VALES",
                            source_module="2_Costos_Base_Vales",
                            source_table="Vales",
                            left_id_col="ROW_ID_VALE",
                            cont_id_col="ROW_ID_CONT",
                            cont_df=cont_d_disp_vales,
                            puede_consumir=consumir,
                        )

            resumen_dh = resumen_dh_contabilidad(cont_all)

            # Métricas
            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Vales totales",             f"{len(vales):,}")
            c2.metric("✅ MATCH_OK",               int((vales_clas["ESTATUS_MATCH"]=="MATCH_OK").sum()))
            c3.metric("⚠️ CON_DISCREPANCIA",       int((vales_clas["ESTATUS_MATCH"]=="MATCH_CON_DISCREPANCIA").sum()))
            c4.metric("❌ No existe en Cont D",    int((vales_clas["ESTATUS_MATCH"]=="NO_EXISTE_EN_CONTABILIDAD_D").sum()))

            # Tabs
            tv1,tv2,tv3,tv4,tv5 = st.tabs(["Vales clasificados","Contabilidad vs Vales","Candidatos técnicos","Mejores matches","Resumen D/H"])
            with tv1: show_df(vales_clas)
            with tv2: show_df(cont_vales_clas)
            with tv3: show_df(cand_vales)
            with tv4: show_df(best_vales)
            with tv5: show_df(resumen_dh)

            result_sheets.update({
                "Vales_clasificados":  vales_clas,
                "Cont_vs_Vales":       cont_vales_clas,
                "Candidatos_Vales":    cand_vales,
                "Mejores_Vales":       best_vales,
                "Resumen_DH":          resumen_dh,
            })

        except Exception as e:
            st.error(f"Error en Vales: {e}")

# ─── Estado ledger ───
resumen_led = get_resumen_ledger(st.session_state.match_ledger)
st.sidebar.divider()
st.sidebar.caption(f"🔒 Ledger: {resumen_led['consumidos']} mov. contables bloqueados")

# ─── Exportar ───
st.divider()
if st.button("Preparar Excel"):
    with st.spinner("Generando..."):
        xlsx = to_excel_bytes({k: prepare_df_for_excel(v) for k, v in result_sheets.items()})
    st.download_button(
        "⬇️ Descargar reporte Costos",
        data=xlsx,
        file_name="reporte_costos_base_vales.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
