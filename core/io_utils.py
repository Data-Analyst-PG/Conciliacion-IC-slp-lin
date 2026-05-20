"""
core/io_utils.py
Lectura de archivos, exportación a Excel y helpers de UI comunes.
"""

from __future__ import annotations
from pathlib import Path
from io import BytesIO

import pandas as pd
import streamlit as st


# ─────────────────────────────────────────────
# Lectura
# ─────────────────────────────────────────────

def read_table(
    file_obj,
    preferred_sheet: str | None = None,
    usecols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Lee xlsx / xlsm / xls / csv.
    Intenta la hoja preferred_sheet primero; si falla, lee la primera.
    """
    suffix = Path(file_obj.name).suffix.lower()
    raw = file_obj.getvalue()

    if suffix == ".csv":
        return pd.read_csv(BytesIO(raw), usecols=usecols, low_memory=False)

    if suffix in {".xlsx", ".xlsm", ".xls"}:
        engine = "xlrd" if suffix == ".xls" else "openpyxl"
        if preferred_sheet:
            try:
                return pd.read_excel(
                    BytesIO(raw), sheet_name=preferred_sheet,
                    usecols=usecols, engine=engine,
                )
            except Exception:
                pass
        return pd.read_excel(BytesIO(raw), usecols=usecols, engine=engine)

    raise ValueError(f"Formato no soportado: {suffix}")


@st.cache_data(show_spinner=False)
def read_table_cached(file_bytes: bytes, file_name: str, preferred_sheet: str | None = None) -> pd.DataFrame:
    """Versión cacheada de read_table. Usa los bytes + nombre como clave de cache."""

    class _FakeFile:
        def __init__(self, name, data):
            self.name = name
            self._data = data
        def getvalue(self):
            return self._data

    return read_table(_FakeFile(file_name, file_bytes), preferred_sheet=preferred_sheet)


# ─────────────────────────────────────────────
# Catálogo de operadores
# ─────────────────────────────────────────────

def load_catalogo_operadores(file_obj) -> pd.DataFrame:
    """
    Lee catálogo con columnas: NOMBRE, USUARIO_STAR, USUARIO_SAC, TIPO.
    Normaliza nombres de columnas flexiblemente.
    """
    from core.normalizers import norm_text, norm_for_key

    df = read_table(file_obj)
    df.columns = df.columns.astype(str).str.strip().str.upper()

    rename_map = {
        "NOMBRE": "NOMBRE",
        "USUARIO STAR (SUGERIDO)": "USUARIO_STAR",
        "USUARIO STAR": "USUARIO_STAR",
        "USUARIO SAC (SUGERIDO)": "USUARIO_SAC",
        "USUARIO SAC": "USUARIO_SAC",
        "TIPO": "TIPO",
    }
    df = df.rename(columns=rename_map)

    required = ["NOMBRE", "USUARIO_STAR", "USUARIO_SAC", "TIPO"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Al catálogo le faltan columnas: {missing}")

    for col in required:
        df[col] = df[col].apply(norm_text)

    return df[required].copy()


def load_concept_map(file_obj) -> dict[str, str]:
    """
    Lee catálogo de conceptos con columnas concepto_origen / concepto_canonico.
    Devuelve dict {norm_key: norm_key}.
    """
    from core.normalizers import norm_for_key

    if file_obj is None:
        return {}

    df = read_table(file_obj)
    src_candidates = ["concepto_origen", "concepto", "ingles", "english", "source"]
    dst_candidates = ["concepto_canonico", "canonico", "espanol", "spanish", "target"]

    src = _find_col(df, src_candidates)
    dst = _find_col(df, dst_candidates)

    if not src or not dst:
        st.warning(
            "El catálogo de conceptos necesita columnas tipo "
            "'concepto_origen' y 'concepto_canonico'. Se ignoró el catálogo."
        )
        return {}

    return {
        norm_for_key(a): norm_for_key(b)
        for a, b in zip(df[src], df[dst])
        if norm_for_key(a) and norm_for_key(b)
    }


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    from core.normalizers import norm_for_key
    normalized = {norm_for_key(c): c for c in df.columns}
    for c in candidates:
        if norm_for_key(c) in normalized:
            return normalized[norm_for_key(c)]
    return None


# ─────────────────────────────────────────────
# Exportación Excel
# ─────────────────────────────────────────────

def ensure_unique_columns(df: pd.DataFrame) -> pd.DataFrame:
    if not df.columns.duplicated().any():
        return df
    seen: dict[str, int] = {}
    new_cols = []
    for c in df.columns:
        if c not in seen:
            seen[c] = 0
            new_cols.append(c)
        else:
            seen[c] += 1
            new_cols.append(f"{c}_{seen[c]}")
    out = df.copy()
    out.columns = new_cols
    return out


def prepare_df_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    out = ensure_unique_columns(df)
    out.columns = [str(c)[:250] for c in out.columns]
    for col in out.columns:
        try:
            dtype = str(out[col].dtype)
            if dtype == "category":
                out[col] = out[col].astype("string").fillna("")
            elif "string" in dtype:
                out[col] = out[col].fillna("")
        except Exception:
            pass
    return out


def to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Genera un Excel multi-hoja en memoria."""
    bio = BytesIO()
    with pd.ExcelWriter(
        bio,
        engine="xlsxwriter",
        engine_kwargs={"options": {"constant_memory": True}},
    ) as writer:
        for name, df in sheets.items():
            prepare_df_for_excel(df).to_excel(
                writer, sheet_name=name[:31], index=False
            )
    bio.seek(0)
    return bio.getvalue()


# ─────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────

def show_df(df: pd.DataFrame, height: int = 500, max_rows: int = 5000):
    """Muestra DataFrame con límite de filas y aviso."""
    if df.empty:
        st.dataframe(df, use_container_width=True, height=height)
        return
    if len(df) > max_rows:
        st.caption(
            f"Mostrando {max_rows:,} de {len(df):,} filas. "
            "El Excel descargable incluye todo."
        )
        st.dataframe(df.head(max_rows), use_container_width=True, height=height)
    else:
        st.dataframe(df, use_container_width=True, height=height)


def metric_row(metrics: list[tuple[str, str | int | float]]):
    """Renderiza una fila de métricas dinámicamente."""
    cols = st.columns(len(metrics))
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)
