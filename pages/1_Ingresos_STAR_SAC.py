"""
pages/1_Ingresos_STAR_SAC.py
Confronta Liquidaciones (STAR) vs Contabilidad (SAC).
Match exacto fila a fila con auditoría completa.
Registra matches en el ledger global de la sesión.
"""

import streamlit as st
import pandas as pd
from core.io_utils import read_table, to_excel_bytes, show_df, load_catalogo_operadores, prepare_df_for_excel
from core.engines.exact import (
    prep_liquidaciones, prep_contabilidad_ingresos,
    run_exact_match, KEY_COLS,
)
from core.ledger import crear_ledger, registrar_matches_batch, get_resumen_ledger

st.set_page_config(page_title="Ingresos STAR vs SAC", layout="wide")
st.title("📊 Ingresos — STAR vs SAC")
st.caption("Confronta Liquidaciones vs Contabilidad • Match exacto fila por fila • Prioridad 1 del ledger")

# ─── Ledger global ───
if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

# ─── Sidebar ───
with st.sidebar:
    st.header("📁 Archivos")
    liq_file     = st.file_uploader("Liquidaciones (STAR)",     type=["xlsx","xls","xlsm","csv"])
    cont_file    = st.file_uploader("Contabilidad (SAC)",        type=["xlsx","xls","xlsm","csv"])
    catalogo_file= st.file_uploader("Catálogo operadores (opt)", type=["xlsx","xls","xlsm","csv"])
    st.divider()

    st.header("⚙️ Configuración")
    ndigits         = st.number_input("Redondeo importe", 0, 4, 2)
    liq_tipo        = st.selectbox("Tipo_Concepto Liquidaciones", ["E","I"], index=0)
    cont_tipo       = st.selectbox("TipoMovimiento Contabilidad", ["H","D"], index=0)
    usar_catalogo   = st.checkbox("Aplicar filtro catálogo", value=True)
    enable_relaxed  = st.checkbox("Sugerencia match relajado", value=True)
    consumir_discr  = st.checkbox("Discrepancias consumen ledger", value=False,
                                  help="Si está activo, MATCH_CON_DISCREPANCIA bloquea el registro en contabilidad.")
    st.divider()
    run = st.button("▶ Procesar", type="primary", use_container_width=True)

if not liq_file or not cont_file:
    st.info("👈 Carga ambos archivos para iniciar.")
    st.stop()

if not run:
    st.info("Configura y presiona **Procesar**.")
    st.stop()

# ─── Carga ───
try:
    liq_raw  = read_table(liq_file,  preferred_sheet="LiquidacionesSET_PLUS_datos")
    cont_raw = read_table(cont_file, preferred_sheet="ContabilidadSET_PLUS_datos")
except Exception as e:
    st.error(f"Error al leer archivos: {e}")
    st.stop()

# ─── Catálogo ───
star_to_nombre: dict = {}
sac_to_nombre:  dict = {}
tipos_sel: list = []

if catalogo_file:
    try:
        cat = load_catalogo_operadores(catalogo_file)
        tipos_disp = sorted([t for t in cat["TIPO"].dropna().unique() if t])
        tipos_sel  = st.sidebar.multiselect(
            "Tipos catálogo", tipos_disp,
            default=["OWNER"] if "OWNER" in tipos_disp else tipos_disp,
        )
        if tipos_sel:
            cat = cat[cat["TIPO"].isin(tipos_sel)]
        star_to_nombre = dict(cat.loc[cat["USUARIO_STAR"] != "", ["USUARIO_STAR","NOMBRE"]].values)
        sac_to_nombre  = dict(cat.loc[cat["USUARIO_SAC"]  != "", ["USUARIO_SAC", "NOMBRE"]].values)
    except Exception as e:
        st.error(f"Error en catálogo: {e}")
        st.stop()

# ─── Normalizar ───
liq  = prep_liquidaciones(liq_raw,  ndigits, star_to_nombre)
cont = prep_contabilidad_ingresos(cont_raw, ndigits, sac_to_nombre)

liq_f  = liq[liq["TIPO_CONCEPTO"] == liq_tipo].copy()
cont_f = cont[cont["TIPO_MOV"]     == cont_tipo].copy()

filtro_cat = catalogo_file is not None and usar_catalogo and bool(tipos_sel)
if filtro_cat:
    liq_f  = liq_f[liq_f["OWNER_STD_LIQ"]  != ""].copy()
    cont_f = cont_f[cont_f["OWNER_STD_CONT"] != ""].copy()

# ─── Resumen carga ───
st.subheader("Resumen de carga")
c1,c2,c3,c4 = st.columns(4)
c1.metric("Liquidaciones original", f"{len(liq):,}")
c2.metric("Liquidaciones filtrado", f"{len(liq_f):,}")
c3.metric("Contabilidad original",  f"{len(cont):,}")
c4.metric("Contabilidad filtrado",  f"{len(cont_f):,}")

# ─── Match ───
with st.spinner("Ejecutando match exacto..."):
    resultados = run_exact_match(liq_f, cont_f, enable_relaxed=enable_relaxed)

liq_clas  = resultados["liq_clasificado"]
cont_clas = resultados["cont_clasificado"]

# ─── Registrar en ledger ───
best_matched = resultados["matched"]
if not best_matched.empty:
    # MATCH_OK
    ok_rows = best_matched[best_matched["ESTATUS_MATCH"] == "MATCH_OK"]
    if not ok_rows.empty:
        for _, row in ok_rows.iterrows():
            from core.ledger import registrar_match
            st.session_state.match_ledger = registrar_match(
                st.session_state.match_ledger,
                proceso="STAR_SAC", source_module="1_Ingresos_STAR_SAC",
                source_table="Liquidaciones",
                source_row_id=int(row.get("ROW_ID_LIQ", 0)),
                cont_row_id=int(row.get("ROW_ID_CONT", 0)),
                cont_poliza=str(row.get("PR", "")),
                cont_tipo_mov=cont_tipo,
                cont_importe=float(row.get("IMPORTE", 0)),
                estatus_match="MATCH_OK", score=5,
                criterios="PR,VIAJE,UNIDAD,TIPO_PAGO,IMPORTE",
                puede_consumir=True,
            )
    # MATCH_CON_DISCREPANCIA (configurable)
    if consumir_discr:
        disc_rows = best_matched[best_matched["ESTATUS_MATCH"] == "MATCH_CON_DISCREPANCIA"]
        for _, row in disc_rows.iterrows():
            from core.ledger import registrar_match
            st.session_state.match_ledger = registrar_match(
                st.session_state.match_ledger,
                proceso="STAR_SAC", source_module="1_Ingresos_STAR_SAC",
                source_table="Liquidaciones",
                source_row_id=int(row.get("ROW_ID_LIQ", 0)),
                cont_row_id=int(row.get("ROW_ID_CONT", 0)),
                cont_poliza=str(row.get("PR", "")),
                cont_tipo_mov=cont_tipo,
                cont_importe=float(row.get("IMPORTE", 0)),
                estatus_match="MATCH_CON_DISCREPANCIA", score=5,
                criterios="PR,VIAJE,UNIDAD,TIPO_PAGO,IMPORTE",
                puede_consumir=True,
            )

# ─── Métricas resultado ───
st.divider()
st.subheader("Resultado del match")
c1,c2,c3,c4 = st.columns(4)
n_ok   = int((liq_clas["ESTATUS_MATCH"] == "MATCH_OK").sum())
n_disc = int((liq_clas["ESTATUS_MATCH"] == "MATCH_CON_DISCREPANCIA").sum())
n_no   = int((liq_clas["ESTATUS_MATCH"] == "NO_EXISTE_EN_CONTABILIDAD").sum())
c1.metric("✅ MATCH_OK",                n_ok)
c2.metric("⚠️ MATCH_CON_DISCREPANCIA",  n_disc)
c3.metric("❌ No existe en Contabilidad",n_no)
c4.metric("Control total Liq",          f"{len(liq_clas):,}")

# ─── Tabs resultado ───
st.divider()
t1,t2,t3,t4,t5,t6 = st.tabs([
    f"✅ Match OK ({n_ok})",
    f"⚠️ Discrepancias ({n_disc})",
    f"❌ No en Cont ({n_no})",
    "📋 Liq. clasificadas",
    "📋 Cont. clasificada",
    "📊 Control PR",
])

cols_base = ["ROW_ID_LIQ","PR","VIAJE","UNIDAD","TIPO_PAGO","IMPORTE","OWNER_LIQ","OWNER_STD_LIQ",
             "ESTATUS_MATCH","MOTIVO_PROBABLE","ROW_ID_CONT","OWNER_CONT","OWNER_STD_CONT","PR_EXISTE_EN_CONT","MATCH_RELAXED"]

with t1:
    df_ok = liq_clas[liq_clas["ESTATUS_MATCH"] == "MATCH_OK"]
    show_df(df_ok[[c for c in cols_base if c in df_ok.columns]])

with t2:
    df_disc = liq_clas[liq_clas["ESTATUS_MATCH"] == "MATCH_CON_DISCREPANCIA"]
    show_df(df_disc[[c for c in cols_base if c in df_disc.columns]])

with t3:
    df_no = liq_clas[liq_clas["ESTATUS_MATCH"] == "NO_EXISTE_EN_CONTABILIDAD"]
    show_df(df_no[[c for c in cols_base if c in df_no.columns]])

with t4:
    show_df(liq_clas[[c for c in cols_base if c in liq_clas.columns]])

with t5:
    cols_cont = ["ROW_ID_CONT","PR","VIAJE","UNIDAD","TIPO_PAGO","IMPORTE","OWNER_CONT","OWNER_STD_CONT",
                 "ESTATUS_MATCH","MOTIVO_PROBABLE","ROW_ID_LIQ","OWNER_LIQ","OWNER_STD_LIQ","PR_EXISTE_EN_LIQ","MATCH_RELAXED"]
    show_df(cont_clas[[c for c in cols_cont if c in cont_clas.columns]])

with t6:
    show_df(resultados["control_pr"])

# ─── Duplicados ───
st.divider()
with st.expander("🔁 Duplicados detectados"):
    c1,c2 = st.columns(2)
    c1.metric("Duplicados Liquidaciones", len(resultados["dup_liq"]))
    c2.metric("Duplicados Contabilidad",  len(resultados["dup_cont"]))
    td1,td2,td3,td4 = st.tabs(["Dup Liq Detalle","Dup Liq Resumen","Dup Cont Detalle","Dup Cont Resumen"])
    with td1: show_df(resultados["dup_liq"])
    with td2: show_df(resultados["dup_liq_resumen"])
    with td3: show_df(resultados["dup_cont"])
    with td4: show_df(resultados["dup_cont_resumen"])

# ─── Estado ledger ───
resumen_led = get_resumen_ledger(st.session_state.match_ledger)
st.sidebar.divider()
st.sidebar.caption(f"🔒 Ledger: {resumen_led['consumidos']} mov. contables bloqueados")

# ─── Exportar ───
st.divider()
sheets = {
    "Liq_clasificadas":       liq_clas,
    "Cont_clasificada":       cont_clas,
    "Dup_Liq_Detalle":        resultados["dup_liq"],
    "Dup_Liq_Resumen":        resultados["dup_liq_resumen"],
    "Dup_Cont_Detalle":       resultados["dup_cont"],
    "Dup_Cont_Resumen":       resultados["dup_cont_resumen"],
    "Control_PR":             resultados["control_pr"],
}
if st.button("Preparar Excel"):
    with st.spinner("Generando..."):
        xlsx = to_excel_bytes({k: prepare_df_for_excel(v) for k, v in sheets.items()})
    st.download_button(
        "⬇️ Descargar reporte STAR vs SAC",
        data=xlsx,
        file_name="reporte_ingresos_star_sac.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
