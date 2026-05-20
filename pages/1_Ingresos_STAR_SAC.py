"""
pages/1_Ingresos_STAR_SAC.py
Confronta Liquidaciones (STAR) vs Contabilidad H.
Lee Contabilidad de session_state (cargada en app.py).
"""

import streamlit as st
import pandas as pd
from core.io_utils import read_table, to_excel_bytes, show_df, load_catalogo_operadores, prepare_df_for_excel
from core.engines.exact import (
    prep_liquidaciones, prep_contabilidad_ingresos,
    run_exact_match, KEY_COLS,
)
from core.ledger import crear_ledger, registrar_match, get_resumen_ledger

st.set_page_config(page_title="Ingresos STAR vs SAC", layout="wide")
st.title("📊 Módulo 1 — Ingresos · STAR vs SAC")
st.caption("Confronta Liquidaciones vs Contabilidad H • Match exacto fila por fila • Prioridad 1 del ledger")

# ─── Verificar contabilidad global ───
if "cont_global" not in st.session_state:
    st.error("⚠️ Primero carga la **Contabilidad** en la página principal (inicio).")
    st.stop()

if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

# ─── Info contabilidad disponible ───
cont_h_global = st.session_state["cont_h_global"]
st.info(
    f"📂 Contabilidad: **{st.session_state['cont_file_name']}** · "
    f"Movimientos H disponibles: **{len(cont_h_global):,}**"
)

# ─── Sidebar ───
with st.sidebar:
    st.header("📁 Archivos")
    liq_file      = st.file_uploader("Liquidaciones (STAR)", type=["xlsx","xls","xlsm","csv"])
    catalogo_file = st.file_uploader("Catálogo operadores (opt)", type=["xlsx","xls","xlsm","csv"])
    st.divider()

    st.header("⚙️ Configuración")
    ndigits        = st.number_input("Redondeo importe", 0, 4, 2)
    liq_tipo       = st.selectbox("Tipo_Concepto Liquidaciones", ["E","I","e","i"], index=0,
                                   help="El campo se normaliza a mayúscula automáticamente.")
    cont_tipo      = st.selectbox("TipoMovimiento Contabilidad", ["H","D"], index=0)
    usar_catalogo  = st.checkbox("Aplicar filtro catálogo", value=True)
    enable_relaxed = st.checkbox("Sugerencia match relajado", value=True)
    consumir_discr = st.checkbox("Discrepancias consumen ledger", value=False,
                                  help="Si activo, MATCH_CON_DISCREPANCIA bloquea el registro.")
    st.divider()
    run = st.button("▶ Procesar", type="primary", use_container_width=True)

if not liq_file:
    st.info("👈 Carga el archivo de Liquidaciones para continuar.")
    st.stop()

# ─── Control de re-proceso ───
sig = (
    liq_file.name,
    catalogo_file.name if catalogo_file else "",
    ndigits, liq_tipo, cont_tipo, usar_catalogo, enable_relaxed, consumir_discr,
    st.session_state.get("cont_file_name",""),
)
if st.session_state.get("ingresos_sig") != sig:
    st.session_state["ingresos_result"] = None

if not run and st.session_state.get("ingresos_result") is None:
    st.info("Configura y presiona **▶ Procesar**.")
    st.stop()

# ─── Procesar ───
if run or st.session_state.get("ingresos_result") is None:

    # Catálogo operadores
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
            sac_to_nombre  = dict(cat.loc[cat["USUARIO_SAC"]  != "", ["USUARIO_SAC","NOMBRE"]].values)
        except Exception as e:
            st.error(f"Error en catálogo: {e}")
            st.stop()

    # Cargar y normalizar Liquidaciones
    with st.spinner("Cargando Liquidaciones..."):
        try:
            liq_raw = read_table(liq_file, preferred_sheet="LiquidacionesSET_PLUS_datos")
        except Exception as e:
            st.error(f"Error leyendo Liquidaciones: {e}")
            st.stop()

    liq  = prep_liquidaciones(liq_raw, ndigits, star_to_nombre)
    # Normalizar tipo para comparar (puede venir minúscula)
    liq["TIPO_CONCEPTO"] = liq["TIPO_CONCEPTO"].str.upper().str.strip()
    liq_f = liq[liq["TIPO_CONCEPTO"] == liq_tipo.upper()].copy()

    # Obtener contabilidad H del estado global (ya normalizada, ya tiene ROW_ID_CONT)
    cont_h = cont_h_global.copy()
    # prep_contabilidad_ingresos ya fue aplicado al normalizar en app.py,
    # solo necesitamos renombrar columnas al esquema interno del engine
    cont_f = prep_contabilidad_ingresos(cont_h, ndigits, sac_to_nombre)
    # Filtrar por tipo de movimiento seleccionado
    if "TIPO_MOV" in cont_f.columns:
        cont_f = cont_f[cont_f["TIPO_MOV"] == cont_tipo].copy()

    # Aplicar filtro catálogo
    filtro_cat = catalogo_file is not None and usar_catalogo and bool(tipos_sel)
    if filtro_cat:
        liq_f  = liq_f[liq_f["OWNER_STD_LIQ"]   != ""].copy()
        cont_f = cont_f[cont_f["OWNER_STD_CONT"] != ""].copy()

    # Resumen de carga
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Liquidaciones total",    f"{len(liq):,}")
    c2.metric("Liquidaciones filtrado", f"{len(liq_f):,}")
    c3.metric("Contabilidad H total",   f"{len(cont_h):,}")
    c4.metric("Contabilidad H filtrado",f"{len(cont_f):,}")

    if liq_f.empty:
        st.error(
            f"No hay registros con Tipo_Concepto = '{liq_tipo.upper()}'. "
            f"Valores encontrados: {liq['TIPO_CONCEPTO'].unique().tolist()}"
        )
        st.stop()

    # Match
    with st.spinner(f"Ejecutando match exacto ({len(liq_f):,} × {len(cont_f):,})..."):
        resultados = run_exact_match(liq_f, cont_f, enable_relaxed=enable_relaxed)

    # Registrar en ledger
    matched = resultados["matched"]
    if not matched.empty:
        for _, row in matched[matched["ESTATUS_MATCH"] == "MATCH_OK"].iterrows():
            st.session_state.match_ledger = registrar_match(
                st.session_state.match_ledger,
                proceso="STAR_SAC", source_module="1_Ingresos",
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
        if consumir_discr:
            for _, row in matched[matched["ESTATUS_MATCH"] == "MATCH_CON_DISCREPANCIA"].iterrows():
                st.session_state.match_ledger = registrar_match(
                    st.session_state.match_ledger,
                    proceso="STAR_SAC", source_module="1_Ingresos",
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

    st.session_state["ingresos_result"] = resultados
    st.session_state["ingresos_sig"]    = sig

# ─── Mostrar resultados (siempre, desde session_state) ───
resultados  = st.session_state["ingresos_result"]
liq_clas    = resultados["liq_clasificado"]
cont_clas   = resultados["cont_clasificado"]

n_ok   = int((liq_clas["ESTATUS_MATCH"] == "MATCH_OK").sum())
n_disc = int((liq_clas["ESTATUS_MATCH"] == "MATCH_CON_DISCREPANCIA").sum())
n_no   = int((liq_clas["ESTATUS_MATCH"] == "NO_EXISTE_EN_CONTABILIDAD").sum())

st.divider()
st.subheader("Resultado del match")
c1,c2,c3,c4 = st.columns(4)
c1.metric("✅ MATCH_OK",                 n_ok)
c2.metric("⚠️ Con discrepancia",         n_disc)
c3.metric("❌ No existe en Contabilidad", n_no)
c4.metric("Total Liquidaciones",         f"{len(liq_clas):,}")

st.divider()
cols_liq  = ["ROW_ID_LIQ","PR","VIAJE","UNIDAD","TIPO_PAGO","IMPORTE","OWNER_LIQ","OWNER_STD_LIQ",
             "ESTATUS_MATCH","MOTIVO_PROBABLE","ROW_ID_CONT","OWNER_CONT","OWNER_STD_CONT",
             "PR_EXISTE_EN_CONT","MATCH_RELAXED"]
cols_cont = ["ROW_ID_CONT","PR","VIAJE","UNIDAD","TIPO_PAGO","IMPORTE","OWNER_CONT","OWNER_STD_CONT",
             "ESTATUS_MATCH","MOTIVO_PROBABLE","ROW_ID_LIQ","OWNER_LIQ","OWNER_STD_LIQ",
             "PR_EXISTE_EN_LIQ","MATCH_RELAXED"]

t1,t2,t3,t4,t5,t6 = st.tabs([
    f"✅ Match OK ({n_ok})",
    f"⚠️ Discrepancias ({n_disc})",
    f"❌ No en Cont ({n_no})",
    "📋 Liq. completas",
    "📋 Cont. clasificada",
    "📊 Control PR",
])
with t1: show_df(liq_clas[liq_clas["ESTATUS_MATCH"]=="MATCH_OK"][[c for c in cols_liq if c in liq_clas.columns]])
with t2: show_df(liq_clas[liq_clas["ESTATUS_MATCH"]=="MATCH_CON_DISCREPANCIA"][[c for c in cols_liq if c in liq_clas.columns]])
with t3: show_df(liq_clas[liq_clas["ESTATUS_MATCH"]=="NO_EXISTE_EN_CONTABILIDAD"][[c for c in cols_liq if c in liq_clas.columns]])
with t4: show_df(liq_clas[[c for c in cols_liq if c in liq_clas.columns]])
with t5: show_df(cont_clas[[c for c in cols_cont if c in cont_clas.columns]])
with t6: show_df(resultados["control_pr"])

st.divider()
with st.expander("🔁 Duplicados"):
    td1,td2,td3,td4 = st.tabs(["Dup Liq","Dup Liq Resumen","Dup Cont","Dup Cont Resumen"])
    with td1: show_df(resultados["dup_liq"])
    with td2: show_df(resultados["dup_liq_resumen"])
    with td3: show_df(resultados["dup_cont"])
    with td4: show_df(resultados["dup_cont_resumen"])

# Ledger estado
resumen_led = get_resumen_ledger(st.session_state.match_ledger)
st.sidebar.divider()
st.sidebar.caption(f"🔒 Ledger: {resumen_led['consumidos']} bloqueados")

# Exportar
st.divider()
if st.button("Preparar Excel"):
    with st.spinner("Generando..."):
        xlsx = to_excel_bytes({
            "Liq_clasificadas":   prepare_df_for_excel(liq_clas),
            "Cont_clasificada":   prepare_df_for_excel(cont_clas),
            "Dup_Liq":            prepare_df_for_excel(resultados["dup_liq"]),
            "Dup_Liq_Resumen":    prepare_df_for_excel(resultados["dup_liq_resumen"]),
            "Dup_Cont":           prepare_df_for_excel(resultados["dup_cont"]),
            "Dup_Cont_Resumen":   prepare_df_for_excel(resultados["dup_cont_resumen"]),
            "Control_PR":         prepare_df_for_excel(resultados["control_pr"]),
        })
    st.download_button(
        "⬇️ Descargar reporte STAR vs SAC", data=xlsx,
        file_name="reporte_ingresos_star_sac.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
