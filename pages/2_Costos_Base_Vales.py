"""
pages/2_Costos_Base_Vales.py
Base Saldos + Vales vs Contabilidad D.
Lee Contabilidad D de session_state (cargada en app.py).
La lógica de matching (scoring, greedy, candidatos) no se modifica.
"""

import streamlit as st
import pandas as pd
from core.io_utils import read_table, to_excel_bytes, show_df, prepare_df_for_excel
from core.engines.majority import (
    prep_contabilidad_costos, prep_base_saldos, prep_vales,
    match_base_vs_cont, match_vales_vs_cont, resumen_dh_contabilidad,
)
from core.ledger import crear_ledger, filtrar_cont_disponible, registrar_matches_batch, get_resumen_ledger

st.set_page_config(page_title="Costos – Base Saldos & Vales", layout="wide")
st.title("💰 Módulo 2 — Costos · Base Saldos & Vales vs Contabilidad D")
st.caption("Match por mayoría de criterios (scoring) • Respeta ledger de Ingresos • Prioridades 2 y 3")

# ─── Verificar contabilidad global ───
if "cont_global" not in st.session_state:
    st.error("⚠️ Primero carga la **Contabilidad** en la página principal (inicio).")
    st.stop()

if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

cont_d_global = st.session_state["cont_d_global"]
st.info(
    f"📂 Contabilidad: **{st.session_state['cont_file_name']}** · "
    f"Movimientos D disponibles: **{len(cont_d_global):,}**"
)

# ─── Sidebar ───
with st.sidebar:
    st.header("📁 Archivos")
    base_file  = st.file_uploader("Base Saldos corregida", type=["xlsx","xls","xlsm","csv"])
    vales_file = st.file_uploader("Vales",                  type=["xlsx","xls","xlsm","csv"])
    st.divider()

    st.header("⚙️ Configuración")
    ndigits       = st.number_input("Redondeo importe", 0, 4, 2)
    proceso       = st.radio("Proceso a ejecutar",
                             ["Base Saldos vs Cont D", "Vales vs Cont D", "Ambos"], index=2)
    consumir_discr= st.checkbox("Discrepancias consumen ledger", value=False)
    respetar_led  = st.checkbox("Respetar ledger de Ingresos", value=True,
                                help="Excluye mov. D ya consumidos por Módulo 1.")
    st.divider()
    run = st.button("▶ Procesar", type="primary", use_container_width=True)

if not base_file and not vales_file:
    st.info("👈 Carga al menos un archivo (Base Saldos o Vales).")
    st.stop()

# ─── Control de re-proceso ───
sig = (
    base_file.name  if base_file  else "",
    vales_file.name if vales_file else "",
    ndigits, proceso, consumir_discr, respetar_led,
    st.session_state.get("cont_file_name",""),
)
if st.session_state.get("costos_sig") != sig:
    st.session_state["costos_result"] = None

if not run and st.session_state.get("costos_result") is None:
    st.info("Configura y presiona **▶ Procesar**.")
    st.stop()

# ─── Preparar contabilidad D (ya normalizada globalmente) ───
# prep_contabilidad_costos necesita las columnas clave normalizadas.
# Como cont_d_global ya tiene ROW_ID_CONT y TipoMovimiento normalizado,
# solo aplicamos el mapeo de columnas al esquema interno del engine.
if run or st.session_state.get("costos_result") is None:

    concept_map = st.session_state.get("concept_map", {})

    with st.spinner("Preparando Contabilidad D..."):
        try:
            cont_d, cont_colmap = prep_contabilidad_costos(
                cont_d_global, ndigits, concept_map, tipo_mov=None  # ya filtrado D
            )
            # Preservar ROW_ID_CONT original del global
            if "ROW_ID_CONT" not in cont_d.columns and "ROW_ID_CONT" in cont_d_global.columns:
                cont_d["ROW_ID_CONT"] = cont_d_global["ROW_ID_CONT"].values
        except Exception as e:
            st.error(f"Error preparando Contabilidad D: {e}")
            st.stop()

    # Aplicar ledger
    ledger = st.session_state.match_ledger
    if respetar_led and not ledger.empty:
        cont_d_disp = filtrar_cont_disponible(cont_d, ledger)
        excluidos   = len(cont_d) - len(cont_d_disp)
        if excluidos:
            st.info(f"ℹ️ {excluidos:,} movimientos D excluidos por ledger de Ingresos.")
    else:
        cont_d_disp = cont_d.copy()

    c1,c2,c3 = st.columns(3)
    c1.metric("Total movimientos D",      f"{len(cont_d):,}")
    c2.metric("Disponibles (no bloqueados)", f"{len(cont_d_disp):,}")
    c3.metric("Columna importe usada",    cont_colmap.get("importe_usado","—"))

    result_base  = {}
    result_vales = {}

    # ════════════════════════════
    # BASE SALDOS
    # ════════════════════════════
    if proceso in {"Base Saldos vs Cont D", "Ambos"} and base_file:
        st.divider()
        st.header("1️⃣ Base Saldos vs Contabilidad D")
        try:
            with st.spinner("Cargando Base Saldos..."):
                base_raw = read_table(base_file)
            base = prep_base_saldos(base_raw, ndigits, concept_map)

            with st.spinner(f"Ejecutando match Base Saldos ({len(base):,} filas)..."):
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

            result_base = {
                "base_clas":      base_clas,
                "cont_base_clas": cont_base_clas,
                "cand_base":      cand_base,
                "best_base":      best_base,
            }

            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Base filas",               f"{len(base):,}")
            c2.metric("✅ MATCH_OK",              int((base_clas["ESTATUS_MATCH"]=="MATCH_OK").sum()))
            c3.metric("⚠️ Con discrepancia",      int((base_clas["ESTATUS_MATCH"]=="MATCH_CON_DISCREPANCIA").sum()))
            c4.metric("❌ No existe en Cont D",   int((base_clas["ESTATUS_MATCH"]=="NO_EXISTE_EN_CONTABILIDAD_D").sum()))

            tb1,tb2,tb3,tb4 = st.tabs(["Base clasificada","Cont vs Base","Candidatos","Mejores matches"])
            with tb1: show_df(base_clas)
            with tb2: show_df(cont_base_clas)
            with tb3: show_df(cand_base)
            with tb4: show_df(best_base)

        except Exception as e:
            st.error(f"Error en Base Saldos: {e}")
            import traceback; st.exception(e)

    # ════════════════════════════
    # VALES
    # ════════════════════════════
    if proceso in {"Vales vs Cont D", "Ambos"} and vales_file:
        # Re-filtrar ledger actualizado (Base Saldos pudo haber consumido más)
        ledger_actual = st.session_state.match_ledger
        cont_d_disp_vales = filtrar_cont_disponible(cont_d, ledger_actual) if respetar_led else cont_d.copy()

        st.divider()
        st.header("2️⃣ Vales vs Contabilidad D")
        try:
            with st.spinner("Cargando Vales..."):
                vales_raw = read_table(vales_file)
            vales = prep_vales(vales_raw, ndigits, concept_map)

            with st.spinner(f"Ejecutando match Vales ({len(vales):,} filas)..."):
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

            # Resumen D/H sobre contabilidad completa
            cont_all_global = st.session_state["cont_global"]
            cont_all, _ = prep_contabilidad_costos(cont_all_global, ndigits, concept_map, tipo_mov=None)
            resumen_dh  = resumen_dh_contabilidad(cont_all)

            result_vales = {
                "vales_clas":      vales_clas,
                "cont_vales_clas": cont_vales_clas,
                "cand_vales":      cand_vales,
                "best_vales":      best_vales,
                "resumen_dh":      resumen_dh,
            }

            c1,c2,c3,c4 = st.columns(4)
            c1.metric("Vales totales",             f"{len(vales):,}")
            c2.metric("✅ MATCH_OK",               int((vales_clas["ESTATUS_MATCH"]=="MATCH_OK").sum()))
            c3.metric("⚠️ Con discrepancia",       int((vales_clas["ESTATUS_MATCH"]=="MATCH_CON_DISCREPANCIA").sum()))
            c4.metric("❌ No existe en Cont D",    int((vales_clas["ESTATUS_MATCH"]=="NO_EXISTE_EN_CONTABILIDAD_D").sum()))

            tv1,tv2,tv3,tv4,tv5 = st.tabs(["Vales clasificados","Cont vs Vales","Candidatos","Mejores matches","Resumen D/H"])
            with tv1: show_df(vales_clas)
            with tv2: show_df(cont_vales_clas)
            with tv3: show_df(cand_vales)
            with tv4: show_df(best_vales)
            with tv5: show_df(resumen_dh)

        except Exception as e:
            st.error(f"Error en Vales: {e}")
            import traceback; st.exception(e)

    st.session_state["costos_result"] = {"base": result_base, "vales": result_vales}
    st.session_state["costos_sig"]    = sig

# ─── Estado ledger ───
resumen_led = get_resumen_ledger(st.session_state.match_ledger)
st.sidebar.divider()
st.sidebar.caption(f"🔒 Ledger: {resumen_led['consumidos']} bloqueados")

# ─── Exportar ───
st.divider()
cr = st.session_state.get("costos_result", {})
sheets = {}
if cr.get("base"):
    b = cr["base"]
    sheets.update({
        "Base_clasificada": prepare_df_for_excel(b["base_clas"]),
        "Cont_vs_Base":     prepare_df_for_excel(b["cont_base_clas"]),
        "Candidatos_Base":  prepare_df_for_excel(b["cand_base"]),
        "Mejores_Base":     prepare_df_for_excel(b["best_base"]),
    })
if cr.get("vales"):
    v = cr["vales"]
    sheets.update({
        "Vales_clasificados": prepare_df_for_excel(v["vales_clas"]),
        "Cont_vs_Vales":      prepare_df_for_excel(v["cont_vales_clas"]),
        "Candidatos_Vales":   prepare_df_for_excel(v["cand_vales"]),
        "Mejores_Vales":      prepare_df_for_excel(v["best_vales"]),
        "Resumen_DH":         prepare_df_for_excel(v["resumen_dh"]),
    })

if sheets and st.button("Preparar Excel"):
    with st.spinner("Generando..."):
        xlsx = to_excel_bytes(sheets)
    st.download_button(
        "⬇️ Descargar reporte Costos", data=xlsx,
        file_name="reporte_costos_base_vales.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
