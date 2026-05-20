"""
core/normalizers.py
Funciones de normalización reutilizables para todos los módulos.
"""

import re
import unicodedata
import pandas as pd


# ─────────────────────────────────────────────
# Texto
# ─────────────────────────────────────────────

def norm_text(x: object) -> str:
    """Normaliza a string limpio, sin acentos, uppercase."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    s = str(x).strip().upper()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


def norm_for_key(x: object) -> str:
    """Normaliza para usar como clave de merge: solo alfanumérico + espacios."""
    s = norm_text(x)
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_amount(x: object, ndigits: int = 2) -> float:
    """Convierte a float redondeado. Acepta strings con comas/signo $."""
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return float("nan")
        if isinstance(x, str):
            x = x.replace(",", "").replace("$", "").strip()
        return round(float(x), ndigits)
    except Exception:
        return float("nan")


# ─────────────────────────────────────────────
# Conceptos
# ─────────────────────────────────────────────

def strip_concept_suffix(x: object) -> str:
    """
    Quita sufijos tipo ' - 20170908' o ' - ABC123' sin destruir el concepto base.
    Permite matching flexible de CXP (Diesel/Consumibles) - #### vs CXP (Diesel/Consumibles)
    """
    s = norm_text(x)
    s = re.sub(r"\s+-\s+\d+.*$", "", s)
    s = re.sub(r"\s+-\s+[A-Z0-9]+.*$", "", s)
    return s.strip()


def canonical_concept(x: object, concept_map: dict | None = None) -> str:
    """
    Mapea a concepto canónico.
    Primero aplica strip de sufijos, luego reglas base, luego catálogo.
    """
    s = strip_concept_suffix(x)
    k = norm_for_key(s)

    if concept_map and k in concept_map:
        return concept_map[k]

    rules = [
        (r"\bPERSONAL LOAN\b|\bLOAN\b|\bPRESTAMO\b", "LOAN PERSONAL LOAN"),
        (r"\bDIESEL\b|\bCONSUMIBLES\b",               "CXP DIESEL CONSUMIBLES"),
        (r"\bANTICIPO\b|\bADVANCE\b",                  "CXP ANTICIPO"),
        (r"\bFLETE\b|\bFREIGHT\b",                     "FLETE FREIGHT"),
        (r"\bCOMISION\b|\bCOMMISSION\b",               "COMISION"),
        (r"\bDEDUCIBLE\b|\bDEDUCTIBLE\b",             "DEDUCIBLE"),
    ]
    for pattern, value in rules:
        if re.search(pattern, k):
            return value
    return k


# ─────────────────────────────────────────────
# Normalización de viaje (crossmatch)
# ─────────────────────────────────────────────

def norm_viaje(serie: pd.Series) -> pd.Series:
    """Elimina / y - de número de viaje para comparar."""
    return (
        serie.fillna("")
        .astype(str)
        .str.replace("/", "", regex=False)
        .str.replace("-", "", regex=False)
        .str.strip()
        .str.upper()
    )


# ─────────────────────────────────────────────
# Helpers de columnas
# ─────────────────────────────────────────────

def resolve_col(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    """Busca la primera columna candidata en el DataFrame (flexible, sin case)."""
    normalized = {norm_for_key(c): c for c in df.columns}
    for c in candidates:
        key = norm_for_key(c)
        if key in normalized:
            return normalized[key]
    if required:
        raise ValueError(
            f"No encontré ninguna columna de: {candidates}. "
            f"Columnas disponibles: {list(df.columns)}"
        )
    return None


def resolve_all_cols(df: pd.DataFrame, candidates: list[str]) -> list[str]:
    """Devuelve todas las columnas que coincidan con los candidatos (para duplicados como Importe.1)."""
    wanted = {norm_for_key(c) for c in candidates}
    return [
        col for col in df.columns
        if norm_for_key(re.sub(r"\.\d+$", "", str(col))) in wanted
    ]


def choose_cont_importe_col(cont_raw: pd.DataFrame) -> str:
    """
    En Contabilidad puede haber dos columnas Importe (encabezado y movimiento).
    Preferimos la última detectada (normalmente Importe.1 = importe del movimiento).
    """
    cols = resolve_all_cols(cont_raw, ["Importe", "Monto", "Total"])
    if not cols:
        raise ValueError("No encontré columna de importe en Contabilidad.")
    return cols[-1]


def build_seq(df: pd.DataFrame, key_cols: list[str], seq_col: str = "_seq") -> pd.DataFrame:
    """Agrega consecutivo por grupo de llave, para empatar duplicados 1:1."""
    out = df.copy()
    out[seq_col] = out.groupby(key_cols, dropna=False).cumcount() + 1
    return out
