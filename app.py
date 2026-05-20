"""
app.py — SET Freight: Sistema de Conciliación Contable
Carga centralizada de Contabilidad + estado global del ledger.
"""

import streamlit as st
from core.ledger import crear_ledger, get_resumen_ledger, detectar_conflictos
from core.io_utils import show_df, to_excel_bytes, prepare_df_for_excel
from core.normalizers import norm_text

st.set_page_config(
    page_title="SET Conciliación",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Ledger global (inicializar si no existe) ───
if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

# ════════════════════════════════════════════════
# CARGA CENTRALIZADA DE CONTABILIDAD
# ════════════════════════════════════════════════
st.title("📊 SET Freight — Conciliación Contable")
st.caption("Carga aquí los archivos globales antes de usar los módulos")

st.subheader("📁 Paso 1 — Archivos Globales")

col_up1, col_up2 = st.columns(2)

with col_up1:
    cont_file = st.file_uploader(
        "Contabilidad SET PLUS *(compartida entre todos los módulos)*",
        type=["xlsx", "xls", "xlsm", "csv"],
        key="cont_file_global",
        help="Se carga una sola vez y queda disponible para Ingresos, Costos y Crossmatch.",
    )

with col_up2:
    concept_file = st.file_uploader(
        "Catálogo de conceptos *(opcional)*",
        type=["xlsx", "xls", "xlsm", "csv"],
        key="concept_file_global",
        help="Columnas: concepto_origen / concepto_canonico",
    )

# ── Cargar contabilidad solo si cambió el archivo ──
if cont_file:
    if st.session_state.get("cont_file_name") != cont_file.name:
        with st.spinner(f"Cargando {cont_file.name} ({cont_file.size/1e6:.1f} MB)..."):
            try:
                from core.io_utils import read_table_cached
                cont_raw = read_table_cached(
                    cont_file.getvalue(),
                    cont_file.name,
                    preferred_sheet="ContabilidadSET_PLUS_datos",
                )

                # Normalizar tipo movimiento (puede venir en minúscula)
                col_mov = next(
                    (c for c in cont_raw.columns
                     if norm_text(c) in {"TIPOMOVIMIENTO", "TIPO MOVIMIENTO", "MOVIMIENTO"}),
                    None,
                )
                if col_mov is None:
                    st.error("No encontré columna TipoMovimiento en Contabilidad.")
                    st.stop()

                cont_raw[col_mov] = cont_raw[col_mov].apply(norm_text)

                # ROW_ID_CONT único y definitivo para toda la sesión
                cont_raw = cont_raw.reset_index(drop=True)
                cont_raw["ROW_ID_CONT"] = cont_raw.index + 1

                st.session_state["cont_global"]    = cont_raw
                st.session_state["cont_h_global"]  = cont_raw[cont_raw[col_mov] == "H"].copy()
                st.session_state["cont_d_global"]  = cont_raw[cont_raw[col_mov] == "D"].copy()
                st.session_state["cont_file_name"] = cont_file.name
                # Reset ledger al cambiar contabilidad (ROW_IDs cambian)
                st.session_state["match_ledger"]   = crear_ledger()
                st.success(f"✅ Contabilidad lista: {len(cont_raw):,} filas")

            except Exception as e:
                st.error(f"Error cargando Contabilidad: {e}")
                st.stop()

# ── Cargar catálogo de conceptos ──
if concept_file:
    if st.session_state.get("concept_file_name") != concept_file.name:
        try:
            from core.io_utils import load_concept_map
            cmap = load_concept_map(concept_file)
            st.session_state["concept_map"]       = cmap
            st.session_state["concept_file_name"] = concept_file.name
            st.success(f"✅ Catálogo conceptos: {len(cmap)} reglas")
        except Exception as e:
            st.warning(f"Catálogo de conceptos ignorado: {e}")
else:
    if "concept_map" not in st.session_state:
        st.session_state["concept_map"] = {}

# ════════════════════════════════════════════════
# ESTADO DEL SISTEMA
# ════════════════════════════════════════════════
st.divider()
st.subheader("📊 Paso 2 — Estado del Sistema")

cont_ok = "cont_global" in st.session_state

if cont_ok:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Archivo cargado", st.session_state["cont_file_name"])
    c2.metric("Total movimientos", f"{len(st.session_state['cont_global']):,}")
    c3.metric("Movimientos H (Ingresos)", f"{len(st.session_state['cont_h_global']):,}")
    c4.metric("Movimientos D (Costos)",   f"{len(st.session_state['cont_d_global']):,}")
else:
    st.warning("⚠️ Carga la Contabilidad arriba para habilitar los módulos.")

# ── Estado del ledger ──
st.divider()
st.subheader("🔒 Match Ledger Global")

ledger  = st.session_state.match_ledger
resumen = get_resumen_ledger(ledger)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Matches registrados",       resumen["total_matches"])
m2.metric("Mov. contables bloqueados", resumen["consumidos"])
m3.metric("Procesos ejecutados",       len(resumen["procesos"]))
m4.metric(
    "⚠️ Conflictos",
    resumen["conflictos"],
    delta_color="inverse" if resumen["conflictos"] > 0 else "off",
)

if resumen["procesos"]:
    st.caption(f"Procesos: {' · '.join(resumen['procesos'])}")

if resumen["conflictos"] > 0:
    with st.expander("⚠️ Conflictos detectados", expanded=True):
        st.error("Un movimiento contable fue consumido por más de un proceso.")
        show_df(detectar_conflictos(ledger))

if not ledger.empty:
    with st.expander("📋 Ver Match Ledger completo"):
        show_df(ledger.sort_values(["prioridad_match", "proceso"]))

# ── Acciones ──
st.divider()
col_a, col_b, col_c = st.columns(3)

with col_a:
    if st.button("🗑️ Resetear Ledger", type="secondary"):
        st.session_state.match_ledger = crear_ledger()
        st.success("Ledger reseteado.")
        st.rerun()

with col_b:
    if st.button("🗑️ Resetear TODO (incluyendo Contabilidad)"):
        keys_to_clear = [k for k in st.session_state if k not in {"cont_file_global", "concept_file_global"}]
        for k in keys_to_clear:
            del st.session_state[k]
        st.success("Sesión reseteada.")
        st.rerun()

with col_c:
    if not ledger.empty:
        if st.button("💾 Exportar Ledger"):
            xlsx = to_excel_bytes({"MatchLedger": prepare_df_for_excel(ledger)})
            st.download_button(
                "⬇️ Descargar Match Ledger",
                data=xlsx,
                file_name="match_ledger.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

# ════════════════════════════════════════════════
# GUÍA DE USO
# ════════════════════════════════════════════════
st.divider()
with st.expander("📖 Guía de uso"):
    st.markdown("""
    ### Orden de trabajo

    1. **Aquí (app.py)** → Carga **Contabilidad** una sola vez. Queda en memoria para todos los módulos.
    2. **Módulo 1 · Ingresos** → Sube solo Liquidaciones. Usa Contabilidad H del paso 1.
    3. **Módulo 2 · Costos** → Sube Base Saldos + Vales. Usa Contabilidad D del paso 1.
    4. **Módulo 3 · Crossmatch** → Sube el reporte de Costos. Investiga No Encontrados.

    ### Reglas del Match Ledger

    | Estatus | Consume ledger | Descripción |
    |---------|---------------|-------------|
    | `MATCH_OK` | ✅ Siempre | Match exacto o 5/5 criterios |
    | `MATCH_CON_DISCREPANCIA` | ⚙️ Configurable | 3–4/5 criterios |
    | `CANDIDATO_DEBIL` | ❌ Nunca | Menos de 3 criterios |
    | `CROSSMATCH_EXPLORATORIO` | ❌ Nunca | Solo investigación |

    ### Garantía anti-doble match
    - Contabilidad se carga **una sola vez** con `ROW_ID_CONT` únicos
    - Cada módulo **filtra** los ROW_ID_CONT ya consumidos antes de buscar candidatos
    - El ledger detecta automáticamente si un mismo movimiento fue consumido dos veces
    """)
