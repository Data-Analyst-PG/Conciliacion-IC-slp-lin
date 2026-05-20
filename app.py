"""
app.py — SET Freight: Sistema de Conciliación Contable
Punto de entrada principal. Dashboard de estado y ledger global.
"""

import streamlit as st
import pandas as pd
from core.ledger import crear_ledger, get_resumen_ledger, detectar_conflictos
from core.io_utils import show_df, to_excel_bytes, prepare_df_for_excel

st.set_page_config(
    page_title="SET Conciliación",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Ledger global ───
if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

# ─── Header ───
st.title("📊 SET Freight — Conciliación Contable")
st.caption("Sistema de matching y conciliación financiera · Ingresos · Costos · Crossmatch")

st.markdown("""
Esta aplicación centraliza los procesos de conciliación financiera:

| Módulo | Descripción | Prioridad |
|--------|-------------|-----------|
| **1 · Ingresos STAR vs SAC** | Liquidaciones vs Contabilidad (match exacto) | 🥇 1 |
| **2 · Costos Base & Vales** | Base Saldos y Vales vs Contabilidad D (scoring) | 🥈 2–3 |
| **3 · Crossmatch** | Investigación de No Encontrados (CA/PD/H) | 🥉 4 |

> Navega por el menú lateral para ejecutar cada proceso.  
> El **Match Ledger** garantiza que ningún movimiento contable sea consumido dos veces.
""")

# ─── Estado del ledger ───
st.divider()
st.subheader("🔒 Estado del Match Ledger")

ledger = st.session_state.match_ledger
resumen = get_resumen_ledger(ledger)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Matches registrados",      resumen["total_matches"])
c2.metric("Mov. contables bloqueados", resumen["consumidos"])
c3.metric("Procesos ejecutados",       len(resumen["procesos"]))
c4.metric("⚠️ Conflictos detectados", resumen["conflictos"],
          delta_color="inverse" if resumen["conflictos"] > 0 else "off")

if resumen["procesos"]:
    st.caption(f"Procesos en ledger: {' · '.join(resumen['procesos'])}")

# Conflictos
if resumen["conflictos"] > 0:
    with st.expander("⚠️ Ver conflictos (mismo mov. contable consumido 2 veces)", expanded=True):
        st.error("Existen movimientos contables bloqueados por más de un proceso. Revisa el orden de ejecución.")
        conflictos_df = detectar_conflictos(ledger)
        show_df(conflictos_df)

# Ledger completo
if not ledger.empty:
    with st.expander("📋 Ver Match Ledger completo"):
        show_df(ledger.sort_values(["prioridad_match", "proceso"]))

    st.divider()
    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("🗑️ Resetear Ledger", type="secondary"):
            st.session_state.match_ledger = crear_ledger()
            st.success("Ledger reseteado.")
            st.rerun()
    with col_b:
        if st.button("💾 Exportar Ledger a Excel"):
            xlsx = to_excel_bytes({"MatchLedger": prepare_df_for_excel(ledger)})
            st.download_button(
                "⬇️ Descargar Match Ledger",
                data=xlsx,
                file_name="match_ledger.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
else:
    st.info("El ledger está vacío. Ejecuta los procesos desde el menú lateral para comenzar.")

# ─── Guía de uso ───
st.divider()
with st.expander("📖 Guía de uso"):
    st.markdown("""
    ### Orden recomendado de ejecución

    1. **Ingresos STAR vs SAC** — Ejecutar primero. Bloquea en ledger los movimientos `H` matcheados exactamente.
    2. **Costos Base & Vales** — Ejecutar después. Respeta lo bloqueado y trabaja sobre `D` disponibles.
    3. **Crossmatch** — Ejecutar último. Solo investiga, nunca bloquea registros.

    ### Tipos de match

    | Tipo | Consume ledger | Descripción |
    |------|---------------|-------------|
    | `MATCH_OK` | ✅ Siempre | Match exacto o 5/5 criterios |
    | `MATCH_CON_DISCREPANCIA` | ⚙️ Configurable | 3–4/5 criterios |
    | `CANDIDATO_DEBIL` | ❌ Nunca | Menos de 3 criterios |
    | `CROSSMATCH_EXPLORATORIO` | ❌ Nunca | Investigación CA/PD/H |
    | `SUGERENCIA_RELAJADA` | ❌ Nunca | Match relajado informativo |

    ### Reglas de negocio aplicadas

    - **Base Saldos vs Cont D**: póliza · unidad · viaje · concepto flexible · importe
    - **Vales vs Cont D**: vale · unidad · concepto · póliza/contrarecibo · importe
    - **Concepto flexible**: strip de sufijos `- ####`, equivalencias diesel/consumibles/anticipo
    - **Crossmatch**: busca CA (por póliza+importe) · PD (por viaje+importe+diesel) · H (por viaje+importe)
    - **Bonificación diesel**: diferencia de importe PD vs base dentro de rango configurable
    """)
