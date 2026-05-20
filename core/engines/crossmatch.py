"""
core/engines/crossmatch.py
Engine de investigación post-match para registros NO_EXISTE_EN_CONTABILIDAD_D.
Ultra-rápido con merges vectorizados. NO consume del ledger, solo informa.
"""

from __future__ import annotations
import time
import pandas as pd
import streamlit as st
from core.normalizers import norm_for_key, norm_amount, norm_viaje
from core.ledger import filtrar_cont_disponible


# ─────────────────────────────────────────────
# Preparación
# ─────────────────────────────────────────────

def prep_base_no_existe(df: pd.DataFrame, ndigits: int = 2) -> pd.DataFrame:
    out = df.copy()
    out["idx_original"]  = range(len(out))
    out["poliza_norm"]   = out.get("FOLIO_CONTRARECIBO", out.get("poliza_norm", pd.Series())).fillna("").astype(str).str.strip().str.upper()
    out["viaje_norm"]    = norm_viaje(out.get("NUMERO_VIAJE", out.get("viaje_norm", pd.Series(dtype=str))))
    out["importe"]       = pd.to_numeric(out.get("Importe", out.get("importe", pd.Series())), errors="coerce").fillna(0).round(ndigits)
    out["concepto_norm"] = out.get("Concepto contabilidad", out.get("concepto_norm", pd.Series())).fillna("").astype(str).str.upper()
    out["es_diesel"]     = out["concepto_norm"].str.contains("DIESEL|CONSUMIBLES", na=False)
    return out


def prep_contabilidad_crossmatch(df: pd.DataFrame, ndigits: int = 2) -> dict[str, pd.DataFrame]:
    cont = df.copy()
    cont["poliza_norm"]   = cont["ClavePoliza"].fillna("").astype(str).str.strip().str.upper()
    cont["viaje_norm"]    = norm_viaje(cont["Referencia"])
    cont["importe"]       = pd.to_numeric(cont["Importe"], errors="coerce").fillna(0).round(ndigits)
    cont["concepto_norm"] = cont.get("ConceptoDetalle", pd.Series()).fillna("").astype(str).str.upper()
    cont["tipo_poliza"]   = cont["ClavePoliza"].fillna("").astype(str).str[:2]

    cont_d = cont[cont["TipoMovimiento"].str.upper() == "D"].copy()
    cont_h = cont[cont["TipoMovimiento"].str.upper() == "H"].copy()

    return {
        "cont_d":      cont_d,
        "cont_h":      cont_h,
        "cont_d_ca":   cont_d[cont_d["tipo_poliza"] == "CA"].copy(),
        "cont_d_pd":   cont_d[cont_d["tipo_poliza"] == "PD"].copy(),
        "cont_h_no_ca":cont_h[~cont_h["tipo_poliza"].isin(["CA"])].copy(),
    }


# ─────────────────────────────────────────────
# Engine principal
# ─────────────────────────────────────────────

def analizar_crossmatch(
    df_no_existe: pd.DataFrame,
    df_cont: pd.DataFrame,
    ledger: pd.DataFrame | None = None,
    ndigits: int = 2,
    bonif_min: float = 10.0,
    bonif_max: float = 20.0,
) -> pd.DataFrame:
    """
    Crossmatch vectorizado. NUNCA consume ledger.
    Si ledger está presente, excluye de cont los ya consumidos (informativo).
    """
    inicio = time.time()
    n = len(df_no_existe)
    st.info(f"🔍 Analizando {n:,} registros...")

    # Aplicar ledger si existe
    if ledger is not None and not ledger.empty:
        cont_disp = filtrar_cont_disponible(df_cont, ledger)
        excluidos = len(df_cont) - len(cont_disp)
        if excluidos:
            st.caption(f"ℹ️ {excluidos:,} mov. contables excluidos (ya consumidos por otros procesos)")
    else:
        cont_disp = df_cont.copy()

    # Preparar
    base = prep_base_no_existe(df_no_existe, ndigits)
    partes = prep_contabilidad_crossmatch(cont_disp, ndigits)

    # ── Cargos CA ──
    with st.spinner("Buscando cargos CA..."):
        base = _buscar_cargo_ca(base, partes["cont_d_ca"])

    # ── Cargos PD + bonificación diesel ──
    with st.spinner("Buscando cargos PD y bonificación diesel..."):
        base = _buscar_cargo_pd(base, partes["cont_d_pd"], bonif_min, bonif_max)

    # ── Abonos H ──
    with st.spinner("Buscando abonos H..."):
        base = _buscar_abono_h(base, partes["cont_h_no_ca"])

    # ── Clasificar ──
    base["TIPO_CASO"]   = base.apply(_clasificar, axis=1)
    base["DIAGNOSTICO"] = base.apply(_diagnostico, axis=1)

    st.success(f"✅ Completado en {time.time() - inicio:.1f}s")
    return base


# ─────────────────────────────────────────────
# Sub-búsquedas vectorizadas
# ─────────────────────────────────────────────

def _buscar_cargo_ca(base: pd.DataFrame, cont_ca: pd.DataFrame) -> pd.DataFrame:
    b = base[["idx_original", "poliza_norm", "importe"]].copy()
    c = cont_ca[["poliza_norm", "importe", "Unidad", "Referencia"]].copy()

    m = b.merge(c, on=["poliza_norm", "importe"], how="left", suffixes=("", "_ca"))
    m = m.groupby("idx_original").first().reset_index()

    base["tiene_cargo_ca"] = base["idx_original"].isin(m.loc[m["Unidad"].notna(), "idx_original"])
    base = base.merge(
        m[["idx_original", "Unidad", "Referencia"]].rename(
            columns={"Unidad": "ca_unidad", "Referencia": "ca_viaje"}
        ),
        on="idx_original", how="left",
    )
    return base


def _buscar_cargo_pd(base, cont_pd, bonif_min, bonif_max):
    cont_pd = cont_pd.copy()
    cont_pd["es_diesel_pd"]  = cont_pd["concepto_norm"].str.contains("DIESEL", na=False)
    cont_pd["es_anticipo_pd"]= cont_pd["concepto_norm"].str.contains("ANTICIPO", na=False)

    # Exacto: viaje + importe + diesel
    b_pd = base[["idx_original", "viaje_norm", "importe", "es_diesel"]].copy()
    c_pd = cont_pd[cont_pd["es_diesel_pd"]][["viaje_norm", "importe", "Unidad", "Referencia", "ClavePoliza"]].copy()

    m_exact = b_pd.merge(c_pd, on=["viaje_norm", "importe"], how="inner", suffixes=("", "_pd"))
    m_exact = m_exact[m_exact["es_diesel"]].groupby("idx_original").first().reset_index()

    # Bonificación: viaje + diff importe 10-20
    b_diesel = b_pd[b_pd["es_diesel"]].copy()
    c_diesel = cont_pd[cont_pd["es_diesel_pd"]][["viaje_norm", "importe", "Unidad", "Referencia", "ClavePoliza"]].copy()

    m_bonif = b_diesel.merge(c_diesel, on="viaje_norm", how="inner", suffixes=("_base", "_pd"))
    m_bonif["diff"] = m_bonif["importe_pd"] - m_bonif["importe_base"]
    m_bonif = m_bonif[(m_bonif["diff"] > bonif_min) & (m_bonif["diff"] < bonif_max)]
    m_bonif = m_bonif.groupby("idx_original").first().reset_index()

    base["tiene_pd_exacto"] = base["idx_original"].isin(m_exact["idx_original"])
    base["tiene_pd_bonif"]  = base["idx_original"].isin(m_bonif["idx_original"])

    base = base.merge(
        m_exact[["idx_original", "Unidad", "Referencia", "ClavePoliza"]].rename(
            columns={"Unidad": "pd_unidad", "Referencia": "pd_viaje", "ClavePoliza": "pd_poliza"}
        ), on="idx_original", how="left",
    )
    base = base.merge(
        m_bonif[["idx_original", "Unidad", "Referencia", "ClavePoliza", "diff"]].rename(
            columns={"Unidad": "pd_bonif_unidad", "Referencia": "pd_bonif_viaje",
                     "ClavePoliza": "pd_bonif_poliza", "diff": "bonif_diff"}
        ), on="idx_original", how="left",
    )
    return base


def _buscar_abono_h(base, cont_h):
    b = base[["idx_original", "viaje_norm", "importe"]].copy()
    c = cont_h[["viaje_norm", "importe", "Unidad", "Referencia", "ClavePoliza", "NombreCuentaContable"]].copy()

    m = b.merge(c, on=["viaje_norm", "importe"], how="left", suffixes=("", "_h"))
    m = m.groupby("idx_original").first().reset_index()

    base["tiene_abono_h"] = base["idx_original"].isin(m.loc[m["Unidad"].notna(), "idx_original"])
    base = base.merge(
        m[["idx_original", "Unidad", "Referencia", "ClavePoliza", "NombreCuentaContable"]].rename(
            columns={"Unidad": "h_unidad", "Referencia": "h_viaje",
                     "ClavePoliza": "h_poliza", "NombreCuentaContable": "h_owner"}
        ), on="idx_original", how="left",
    )
    return base


def _clasificar(row) -> str:
    if row.get("tiene_pd_bonif"):
        return "BONIFICACION_DIESEL"
    if row.get("tiene_cargo_ca") and row.get("tiene_abono_h"):
        return "COMPLETO_CA_H"
    if row.get("tiene_pd_exacto") and row.get("tiene_abono_h"):
        return "COMPLETO_PD_H"
    if row.get("tiene_cargo_ca"):
        return "SOLO_CARGO_CA"
    if row.get("tiene_pd_exacto") or row.get("tiene_pd_bonif"):
        return "SOLO_CARGO_PD"
    if row.get("tiene_abono_h"):
        return "SOLO_ABONO_H"
    return "NO_ENCONTRADO"


def _diagnostico(row) -> str:
    t = row.get("TIPO_CASO", "")
    if t == "BONIFICACION_DIESEL":
        return f"✅ PD {row.get('pd_bonif_poliza','')} bonif ${row.get('bonif_diff',0):.2f} | {row.get('pd_bonif_unidad','')}|{row.get('pd_bonif_viaje','')}"
    if t == "COMPLETO_CA_H":
        return f"✅ CA {row.get('ca_unidad','')}|{row.get('ca_viaje','')} | H {row.get('h_poliza','')} {row.get('h_unidad','')}|{row.get('h_viaje','')}"
    if t == "COMPLETO_PD_H":
        return f"✅ PD {row.get('pd_poliza','')} {row.get('pd_unidad','')}|{row.get('pd_viaje','')} | H {row.get('h_poliza','')}"
    if t == "SOLO_CARGO_CA":
        return f"⚠️ Solo CA: {row.get('ca_unidad','')}|{row.get('ca_viaje','')}"
    if t == "SOLO_CARGO_PD":
        return f"⚠️ Solo PD: {row.get('pd_bonif_poliza', row.get('pd_poliza',''))}"
    if t == "SOLO_ABONO_H":
        return f"🔄 Solo H: {row.get('h_poliza','')} {row.get('h_unidad','')}|{row.get('h_viaje','')}"
    return "❌ No encontrado en ninguna póliza"
