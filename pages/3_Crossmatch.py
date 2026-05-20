"""
pages/3_Crossmatch.py
Investigación de registros NO_EXISTE_EN_CONTABILIDAD_D.
Solo informativo: NO consume del ledger.
"""

import streamlit as st
import pandas as pd
from core.io_utils import read_table, to_excel_bytes, show_df, prepare_df_for_excel
from core.engines.crossmatch import analizar_crossmatch
from core.ledger import crear_ledger, get_resumen_ledger

st.set_page_config(page_title="Crossmatch – Investigación", layout="wide")
st.title("🔍 Crossmatch — Investigación de No Encontrados")
st.caption("Analiza registros NO_EXISTE_EN_CONTABILIDAD_D buscando CA, PD y H • Solo informativo, no consume ledger")

# ─── Ledger global ───
if "match_ledger" not in st.session_state:
    st.session_state.match_ledger = crear_ledger()

# ─── Sidebar ───
with st.sidebar:
    st.header("📁 Archivos")
    reporte_file = st.file_uploader(
        "Reporte Base Saldos clasificado",
        type=["xlsx","xls","xlsm","csv"],
        help="Archivo con columna ESTATUS_MATCH ya calculada por el proceso de Costos.",
    )
    cont_file = st.file_uploader("Contabilidad completa", type=["xlsx","xls","xlsm","csv"])
    st.divider()

    st.header("⚙️ Configuración")
    muestra       = st.checkbox("Modo prueba (1,000 registros)", value=False)
    bonif_min     = st.number_input("Bonif. diesel mín ($)", 0.0, 100.0, 10.0, step=1.0)
    bonif_max     = st.number_input("Bonif. diesel máx ($)", 0.0, 500.0, 20.0, step=1.0)
    usar_ledger   = st.checkbox("Excluir mov. ya consumidos (ledger)", value=True,
                                help="Informa qué mov. contables ya fueron bloqueados por Ingresos o Costos.")
    st.divider()
    run = st.button("🚀 Analizar", type="primary", use_container_width=True)

# ─── Ledger info ───
resumen_led = get_resumen_ledger(st.session_state.match_ledger)
st.sidebar.caption(
    f"🔒 Ledger actual: {resumen_led['consumidos']} bloqueados | "
    f"Procesos: {', '.join(resumen_led['procesos']) or 'ninguno'}"
)

if not reporte_file or not cont_file:
    st.info("👈 Carga ambos archivos para iniciar.")

    st.markdown("""
    ### Flujo esperado
    1. Ejecuta **Ingresos** (página 1) → genera ledger
    2. Ejecuta **Costos** (página 2) → enriquece ledger
    3. El archivo de reporte debe tener la columna `ESTATUS_MATCH`
    4. Esta página filtra los `NO_EXISTE_EN_CONTABILIDAD_D` y busca en CA, PD y H

    ### Velocidad esperada
    - 1K registros: ~10s
    - 5K registros: ~25s
    - 23K registros: ~60s
    """)
    st.stop()

if not run:
    st.info("Configura y presiona **Analizar**.")
    st.stop()

# ─── Carga ───
try:
    df_reporte = read_table(reporte_file)
    df_cont    = read_table(cont_file, preferred_sheet="ContabilidadSET_PLUS_datos")
except Exception as e:
    st.error(f"Error al leer archivos: {e}")
    st.stop()

# ─── Validar columna ESTATUS_MATCH ───
if "ESTATUS_MATCH" not in df_reporte.columns:
    st.error(
        "❌ El reporte no tiene la columna `ESTATUS_MATCH`. "
        "Primero ejecuta el proceso de **Costos** y descarga el resultado."
    )
    st.stop()

df_no_existe = df_reporte[df_reporte["ESTATUS_MATCH"] == "NO_EXISTE_EN_CONTABILIDAD_D"].copy()

if df_no_existe.empty:
    st.success("✅ No hay registros con ESTATUS_MATCH = NO_EXISTE_EN_CONTABILIDAD_D.")
    st.stop()

if muestra:
    df_no_existe = df_no_existe.head(1000)
    st.warning(f"⚠️ Modo prueba activo: procesando {len(df_no_existe):,} registros.")

st.write(f"📊 **Registros a investigar:** {len(df_no_existe):,} | **Contabilidad total:** {len(df_cont):,}")

# ─── Analizar ───
ledger = st.session_state.match_ledger if usar_ledger else crear_ledger()

resultado = analizar_crossmatch(
    df_no_existe,
    df_cont,
    ledger=ledger,
    bonif_min=bonif_min,
    bonif_max=bonif_max,
)

# ─── Métricas ───
st.divider()
total = len(resultado)
resumen = resultado["TIPO_CASO"].value_counts().reset_index()
resumen.columns = ["Tipo", "Cantidad"]
resumen["% del total"] = (resumen["Cantidad"] / total * 100).round(2)

c1,c2,c3,c4,c5 = st.columns(5)
c1.metric("Total analizados", f"{total:,}")
c2.metric("✅ Bonif. Diesel",  int((resultado["TIPO_CASO"]=="BONIFICACION_DIESEL").sum()))
c3.metric("✅ Completos",       int(resultado["TIPO_CASO"].str.contains("COMPLETO",na=False).sum()))
c4.metric("⚠️ Solo Cargo",     int(resultado["TIPO_CASO"].str.contains("SOLO_CARGO",na=False).sum()))
c5.metric("❌ No encontrado",   int((resultado["TIPO_CASO"]=="NO_ENCONTRADO").sum()))

# ─── Distribución ───
st.subheader("Distribución por tipo de caso")
st.dataframe(resumen, hide_index=True, use_container_width=True)

# ─── Tabs detalle ───
st.divider()
tab_bonif, tab_comp, tab_solo, tab_no_enc, tab_todo = st.tabs([
    "💰 Bonif. Diesel",
    "✅ Completos",
    "⚠️ Solo Cargo/Abono",
    "❌ No Encontrado",
    "📋 Todo",
])

with tab_bonif:
    df_b = resultado[resultado["TIPO_CASO"] == "BONIFICACION_DIESEL"]
    st.caption(f"{len(df_b):,} registros")
    show_df(df_b)

with tab_comp:
    df_c = resultado[resultado["TIPO_CASO"].str.contains("COMPLETO", na=False)]
    st.caption(f"{len(df_c):,} registros")
    show_df(df_c)

with tab_solo:
    df_s = resultado[resultado["TIPO_CASO"].str.contains("SOLO", na=False)]
    st.caption(f"{len(df_s):,} registros")
    show_df(df_s)

with tab_no_enc:
    df_n = resultado[resultado["TIPO_CASO"] == "NO_ENCONTRADO"]
    st.caption(f"{len(df_n):,} registros")
    show_df(df_n)

with tab_todo:
    cols_show = ["DIAGNOSTICO","TIPO_CASO","FOLIO_CONTRARECIBO","NUMERO_VIAJE","Importe",
                 "Concepto contabilidad","ca_unidad","ca_viaje","pd_poliza","pd_unidad",
                 "h_poliza","h_unidad","h_owner","bonif_diff"]
    show_df(resultado[[c for c in cols_show if c in resultado.columns]])

# ─── Exportar ───
st.divider()
def generar_excel(df: pd.DataFrame) -> bytes:
    sheets = {
        "Completo":        df,
        "Bonif_Diesel":    df[df["TIPO_CASO"] == "BONIFICACION_DIESEL"],
        "Completos":       df[df["TIPO_CASO"].str.contains("COMPLETO", na=False)],
        "Solo_Cargo":      df[df["TIPO_CASO"].str.contains("SOLO_CARGO", na=False)],
        "Solo_Abono":      df[df["TIPO_CASO"] == "SOLO_ABONO_H"],
        "No_Encontrado":   df[df["TIPO_CASO"] == "NO_ENCONTRADO"],
        "Resumen":         resumen,
    }
    return to_excel_bytes({k: prepare_df_for_excel(v) for k, v in sheets.items()})

if st.button("Preparar Excel"):
    with st.spinner("Generando..."):
        xlsx = generar_excel(resultado)
    st.download_button(
        "⬇️ Descargar reporte Crossmatch",
        data=xlsx,
        file_name="reporte_crossmatch.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
