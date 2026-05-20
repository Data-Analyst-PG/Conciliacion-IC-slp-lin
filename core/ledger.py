"""
core/ledger.py
MatchLedger: registro central de matches para evitar doble consumo de Contabilidad.
Se persiste en st.session_state durante la sesión de trabajo.
"""

from __future__ import annotations
from datetime import datetime
import pandas as pd

# ─────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────

LEDGER_COLS = [
    "match_id",
    "proceso",
    "source_module",
    "source_table",
    "source_row_id",
    "cont_row_id",
    "cont_poliza",
    "cont_tipo_mov",
    "cont_importe",
    "estatus_match",
    "score",
    "criterios_coincidentes",
    "fecha_proceso",
    "prioridad_match",
    "puede_consumir",
]

# Prioridad de cada proceso (menor = mayor prioridad)
PROCESO_PRIORIDAD = {
    "STAR_SAC":    1,
    "BASE_SALDOS": 2,
    "VALES":       3,
    "CROSSMATCH":  4,
    "SUGERENCIA":  5,
}

# Reglas de consumo por estatus
REGLAS_CONSUMO: dict[str, bool | None] = {
    "MATCH_OK":                True,
    "MATCH_CON_DISCREPANCIA":  False,   # configurable
    "CANDIDATO_DEBIL":         False,
    "SUGERENCIA_RELAJADA":     False,
    "CROSSMATCH_EXPLORATORIO": False,
}


# ─────────────────────────────────────────────
# Crear / resetear ledger
# ─────────────────────────────────────────────

def crear_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=LEDGER_COLS)


# ─────────────────────────────────────────────
# Registrar un match individual
# ─────────────────────────────────────────────

def registrar_match(
    ledger: pd.DataFrame,
    proceso: str,
    source_module: str,
    source_table: str,
    source_row_id: int | str,
    cont_row_id: int,
    cont_poliza: str = "",
    cont_tipo_mov: str = "",
    cont_importe: float = 0.0,
    estatus_match: str = "MATCH_OK",
    score: int = 0,
    criterios: str = "",
    puede_consumir: bool = True,
) -> pd.DataFrame:
    nueva = {
        "match_id":             f"{proceso}_{source_row_id}_{cont_row_id}",
        "proceso":              proceso,
        "source_module":        source_module,
        "source_table":         source_table,
        "source_row_id":        source_row_id,
        "cont_row_id":          cont_row_id,
        "cont_poliza":          cont_poliza,
        "cont_tipo_mov":        cont_tipo_mov,
        "cont_importe":         cont_importe,
        "estatus_match":        estatus_match,
        "score":                score,
        "criterios_coincidentes": criterios,
        "fecha_proceso":        datetime.now().isoformat(),
        "prioridad_match":      PROCESO_PRIORIDAD.get(proceso, 9),
        "puede_consumir":       puede_consumir,
    }
    return pd.concat([ledger, pd.DataFrame([nueva])], ignore_index=True)


# ─────────────────────────────────────────────
# Registro masivo desde un DataFrame de matches
# ─────────────────────────────────────────────

def registrar_matches_batch(
    ledger: pd.DataFrame,
    best_matches: pd.DataFrame,
    proceso: str,
    source_module: str,
    source_table: str,
    left_id_col: str,
    cont_id_col: str,
    cont_df: pd.DataFrame,
    puede_consumir: bool = True,
) -> pd.DataFrame:
    """
    Registra en bloque los matches confirmados de un engine.
    best_matches debe tener al mínimo: left_id_col, cont_id_col, TOTAL_COINCIDENCIAS, ESTATUS_MATCH.
    cont_df debe tener: ROW_ID_CONT, POLIZA_KEY, TIPO_MOV, IMPORTE_KEY.
    """
    if best_matches.empty:
        return ledger

    cont_info_cols = ["ROW_ID_CONT"]
    for c in ["POLIZA_KEY", "TIPO_MOV", "IMPORTE_KEY"]:
        if c in cont_df.columns:
            cont_info_cols.append(c)

    cont_info = cont_df[cont_info_cols].copy()

    score_col = "TOTAL_COINCIDENCIAS" if "TOTAL_COINCIDENCIAS" in best_matches.columns else None
    estatus_col = "ESTATUS_MATCH" if "ESTATUS_MATCH" in best_matches.columns else None

    merged = best_matches[[left_id_col, cont_id_col]].copy()
    if score_col:
        merged[score_col] = best_matches[score_col]
    if estatus_col:
        merged[estatus_col] = best_matches[estatus_col]

    merged = merged.merge(
        cont_info,
        left_on=cont_id_col,
        right_on="ROW_ID_CONT",
        how="left",
    )

    ts = datetime.now().isoformat()
    prioridad = PROCESO_PRIORIDAD.get(proceso, 9)

    new_rows = []
    for _, row in merged.iterrows():
        lid = row[left_id_col]
        rid = row[cont_id_col]
        new_rows.append({
            "match_id":               f"{proceso}_{lid}_{rid}",
            "proceso":                proceso,
            "source_module":          source_module,
            "source_table":           source_table,
            "source_row_id":          lid,
            "cont_row_id":            rid,
            "cont_poliza":            row.get("POLIZA_KEY", ""),
            "cont_tipo_mov":          row.get("TIPO_MOV", ""),
            "cont_importe":           row.get("IMPORTE_KEY", 0),
            "estatus_match":          row.get("ESTATUS_MATCH", ""),
            "score":                  row.get("TOTAL_COINCIDENCIAS", 0),
            "criterios_coincidentes": "",
            "fecha_proceso":          ts,
            "prioridad_match":        prioridad,
            "puede_consumir":         puede_consumir,
        })

    if not new_rows:
        return ledger

    return pd.concat([ledger, pd.DataFrame(new_rows)], ignore_index=True)


# ─────────────────────────────────────────────
# Consultar ledger
# ─────────────────────────────────────────────

def get_cont_consumidos(ledger: pd.DataFrame) -> set[int]:
    """Devuelve el set de ROW_ID_CONT ya bloqueados (puede_consumir=True)."""
    if ledger.empty:
        return set()
    return set(ledger.loc[ledger["puede_consumir"] == True, "cont_row_id"])


def filtrar_cont_disponible(cont: pd.DataFrame, ledger: pd.DataFrame) -> pd.DataFrame:
    """Excluye de cont los registros ya consumidos según el ledger."""
    consumidos = get_cont_consumidos(ledger)
    if not consumidos:
        return cont.copy()
    return cont[~cont["ROW_ID_CONT"].isin(consumidos)].copy()


def get_resumen_ledger(ledger: pd.DataFrame) -> dict:
    """Estadísticas rápidas del ledger para mostrar en UI."""
    if ledger.empty:
        return {
            "total_matches": 0,
            "consumidos": 0,
            "procesos": [],
            "conflictos": 0,
        }

    consumidos_mask = ledger["puede_consumir"] == True
    cont_ids_consumidos = ledger.loc[consumidos_mask, "cont_row_id"]
    conflictos = int((cont_ids_consumidos.value_counts() > 1).sum())

    return {
        "total_matches":  len(ledger),
        "consumidos":     int(consumidos_mask.sum()),
        "procesos":       ledger["proceso"].unique().tolist(),
        "conflictos":     conflictos,
    }


def detectar_conflictos(ledger: pd.DataFrame) -> pd.DataFrame:
    """
    Devuelve filas del ledger donde el mismo cont_row_id
    fue consumido por más de un proceso.
    """
    if ledger.empty:
        return pd.DataFrame(columns=LEDGER_COLS)

    consumidos = ledger[ledger["puede_consumir"] == True]
    dup_ids = consumidos["cont_row_id"].value_counts()
    dup_ids = dup_ids[dup_ids > 1].index

    return consumidos[consumidos["cont_row_id"].isin(dup_ids)].copy()
