"""
core/engines/exact.py
Engine de match exacto fila a fila para STAR vs SAC (Liquidaciones vs Contabilidad).
Usa merge con _seq para empatar duplicados 1:1.
Conserva toda la auditoría de v8 + clasificación fila por fila de v9.
"""

from __future__ import annotations
import pandas as pd
from core.normalizers import norm_text, norm_amount, build_seq


# ─────────────────────────────────────────────
# Preparación de archivos
# ─────────────────────────────────────────────

KEY_COLS = ["PR", "VIAJE", "UNIDAD", "TIPO_PAGO", "IMPORTE"]

LIQ_RENAME = {
    "Liquidacion": "PR",
    "Numero_Viaje": "VIAJE",
    "TipoPago": "TIPO_PAGO",
    "Monto": "IMPORTE",
    "Unidad": "UNIDAD",
    "Owner": "OWNER_LIQ",
    "Tipo_Concepto": "TIPO_CONCEPTO",
}

CONT_RENAME = {
    "Factura": "PR",
    "Referencia": "VIAJE",
    "TipoPago": "TIPO_PAGO",
    "Importe": "IMPORTE",
    "Unidad": "UNIDAD",
    "NombreCuentaContable": "OWNER_CONT",
    "TipoMovimiento": "TIPO_MOV",
}

CONCEPTOS_EXCLUIR = {"ADICIONAL CHARGES"}


def prep_liquidaciones(
    raw: pd.DataFrame,
    ndigits: int = 2,
    star_to_nombre: dict | None = None,
) -> pd.DataFrame:
    df = raw.rename(columns=LIQ_RENAME).copy()

    # Excluir conceptos que no se reflejan en contabilidad
    if "Concepto" in df.columns:
        df["Concepto"] = df["Concepto"].apply(norm_text)
        df = df[~df["Concepto"].isin(CONCEPTOS_EXCLUIR)].copy()

    for c in ["PR", "VIAJE", "TIPO_PAGO", "UNIDAD", "OWNER_LIQ", "TIPO_CONCEPTO"]:
        if c in df.columns:
            df[c] = df[c].apply(norm_text)

    df["IMPORTE"] = pd.to_numeric(
        df["IMPORTE"].apply(lambda x: norm_amount(x, ndigits)), errors="coerce"
    )
    df["OWNER_STD_LIQ"] = df["OWNER_LIQ"].map(star_to_nombre or {}).fillna("")
    return df


def prep_contabilidad_ingresos(
    raw: pd.DataFrame,
    ndigits: int = 2,
    sac_to_nombre: dict | None = None,
) -> pd.DataFrame:
    df = raw.rename(columns=CONT_RENAME).copy()

    for c in ["PR", "VIAJE", "TIPO_PAGO", "UNIDAD", "OWNER_CONT", "TIPO_MOV"]:
        if c in df.columns:
            df[c] = df[c].apply(norm_text)

    df["IMPORTE"] = pd.to_numeric(
        df["IMPORTE"].apply(lambda x: norm_amount(x, ndigits)), errors="coerce"
    )
    df["OWNER_STD_CONT"] = df["OWNER_CONT"].map(sac_to_nombre or {}).fillna("")
    return df


# ─────────────────────────────────────────────
# Match exacto
# ─────────────────────────────────────────────

def run_exact_match(
    liq_f: pd.DataFrame,
    cont_f: pd.DataFrame,
    enable_relaxed: bool = True,
) -> dict:
    """
    Ejecuta el match exacto + auditoría completa.
    Devuelve dict con todos los DataFrames resultado.
    """
    liq_f = liq_f.reset_index(drop=True)
    cont_f = cont_f.reset_index(drop=True)
    liq_f["ROW_ID_LIQ"] = liq_f.index + 1
    cont_f["ROW_ID_CONT"] = cont_f.index + 1

    merge_keys = KEY_COLS + ["_seq"]
    liq_k = build_seq(liq_f, KEY_COLS)
    cont_k = build_seq(cont_f, KEY_COLS)

    m = liq_k.merge(
        cont_k,
        how="outer",
        on=merge_keys,
        suffixes=("_LIQ", "_CONT"),
        indicator=True,
    )

    matched   = m[m["_merge"] == "both"].copy()
    only_liq  = m[m["_merge"] == "left_only"].copy()
    only_cont = m[m["_merge"] == "right_only"].copy()

    # Clasificar matcheados
    matched["OWNER_MATCH"] = (
        matched["OWNER_LIQ"].astype("string").fillna("") ==
        matched["OWNER_CONT"].astype("string").fillna("")
    )
    matched["ESTATUS_MATCH"] = matched["OWNER_MATCH"].map(
        {True: "MATCH_OK", False: "MATCH_CON_DISCREPANCIA"}
    )
    matched["OBSERVACION"] = matched["OWNER_MATCH"].map({
        True:  "Coincide llave exacta y owner.",
        False: "Coincide llave exacta, pero owner es distinto.",
    })

    only_liq["ESTATUS_MATCH"]  = "NO_EXISTE_EN_CONTABILIDAD"
    only_liq["OBSERVACION"]    = "Sin contraparte exacta en Contabilidad."
    only_cont["ESTATUS_MATCH"] = "NO_EXISTE_EN_LIQUIDACIONES"
    only_cont["OBSERVACION"]   = "Sin contraparte exacta en Liquidaciones."

    # Propagar estatus a archivos originales
    liq_clasificado  = _propagar_liq(liq_f, matched, only_liq)
    cont_clasificado = _propagar_cont(cont_f, matched, only_cont)

    # PR banderas
    liq_pr_set  = set(liq_f["PR"].dropna().astype(str))
    cont_pr_set = set(cont_f["PR"].dropna().astype(str))
    liq_clasificado["PR_EXISTE_EN_CONT"]  = liq_clasificado["PR"].astype(str).isin(cont_pr_set)
    cont_clasificado["PR_EXISTE_EN_LIQ"]  = cont_clasificado["PR"].astype(str).isin(liq_pr_set)

    # Totales por PR
    liq_totales = liq_f.groupby("PR", dropna=False).agg(
        REG_LIQ=("PR", "size"), IMPORTE_TOTAL_LIQ=("IMPORTE", "sum")
    ).reset_index()
    cont_totales = cont_f.groupby("PR", dropna=False).agg(
        REG_CONT=("PR", "size"), IMPORTE_TOTAL_CONT=("IMPORTE", "sum")
    ).reset_index()

    liq_clasificado  = liq_clasificado.merge(cont_totales, on="PR", how="left")
    cont_clasificado = cont_clasificado.merge(liq_totales, on="PR", how="left")

    # Match relajado
    if enable_relaxed:
        liq_clasificado, cont_clasificado = _add_relaxed_match(
            liq_clasificado, cont_clasificado, only_liq, only_cont
        )
    else:
        liq_clasificado["MATCH_RELAXED"] = False
        cont_clasificado["MATCH_RELAXED"] = False

    # MOTIVO_PROBABLE
    liq_clasificado["MOTIVO_PROBABLE"]  = liq_clasificado.apply(_motivo_liq, axis=1)
    cont_clasificado["MOTIVO_PROBABLE"] = cont_clasificado.apply(_motivo_cont, axis=1)

    # Duplicados
    dup_liq  = _get_duplicados(liq_f,  KEY_COLS)
    dup_cont = _get_duplicados(cont_f, KEY_COLS)

    # Conciliación por PR
    control_pr = _control_pr(liq_f, cont_f)

    return {
        "liq_clasificado":   liq_clasificado,
        "cont_clasificado":  cont_clasificado,
        "matched":           matched,
        "only_liq":          only_liq,
        "only_cont":         only_cont,
        "dup_liq":           dup_liq["detalle"],
        "dup_liq_resumen":   dup_liq["resumen"],
        "dup_cont":          dup_cont["detalle"],
        "dup_cont_resumen":  dup_cont["resumen"],
        "control_pr":        control_pr,
    }


# ─────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────

def _propagar_liq(liq_f, matched, only_liq):
    from_matched = matched[["ROW_ID_LIQ", "ROW_ID_CONT", "ESTATUS_MATCH", "OBSERVACION",
                             "OWNER_CONT", "OWNER_STD_CONT"]].copy()
    from_only = only_liq[["ROW_ID_LIQ", "ESTATUS_MATCH", "OBSERVACION"]].copy()
    from_only["ROW_ID_CONT"] = pd.NA
    from_only["OWNER_CONT"] = ""
    from_only["OWNER_STD_CONT"] = ""
    status = pd.concat([from_matched, from_only], ignore_index=True)
    return liq_f.merge(status, on="ROW_ID_LIQ", how="left")


def _propagar_cont(cont_f, matched, only_cont):
    from_matched = matched[["ROW_ID_CONT", "ROW_ID_LIQ", "ESTATUS_MATCH", "OBSERVACION",
                             "OWNER_LIQ", "OWNER_STD_LIQ"]].copy()
    from_only = only_cont[["ROW_ID_CONT", "ESTATUS_MATCH", "OBSERVACION"]].copy()
    from_only["ROW_ID_LIQ"] = pd.NA
    from_only["OWNER_LIQ"] = ""
    from_only["OWNER_STD_LIQ"] = ""
    status = pd.concat([from_matched, from_only], ignore_index=True)
    return cont_f.merge(status, on="ROW_ID_CONT", how="left")


def _add_relaxed_match(liq_clas, cont_clas, only_liq, only_cont):
    relaxed_key_cols = ["PR", "UNIDAD", "TIPO_PAGO", "IMPORTE"]

    def make_key(df, cols):
        return df[cols].fillna("").astype(str).agg("||".join, axis=1)

    liq_no = only_liq[["ROW_ID_LIQ"] + relaxed_key_cols].copy()
    cont_no = only_cont[["ROW_ID_CONT"] + relaxed_key_cols].copy()

    if liq_no.empty or cont_no.empty:
        liq_clas["MATCH_RELAXED"] = liq_clas.get("MATCH_RELAXED", False)
        cont_clas["MATCH_RELAXED"] = cont_clas.get("MATCH_RELAXED", False)
        return liq_clas, cont_clas

    liq_no["REL_KEY"]  = make_key(liq_no,  relaxed_key_cols)
    cont_no["REL_KEY"] = make_key(cont_no, relaxed_key_cols)

    liq_no["MATCH_RELAXED"]  = liq_no["REL_KEY"].isin(set(cont_no["REL_KEY"]))
    cont_no["MATCH_RELAXED"] = cont_no["REL_KEY"].isin(set(liq_no["REL_KEY"]))

    liq_clas  = liq_clas.merge(liq_no[["ROW_ID_LIQ", "MATCH_RELAXED"]],  on="ROW_ID_LIQ",  how="left")
    cont_clas = cont_clas.merge(cont_no[["ROW_ID_CONT", "MATCH_RELAXED"]], on="ROW_ID_CONT", how="left")

    liq_clas["MATCH_RELAXED"]  = liq_clas["MATCH_RELAXED"].fillna(False)
    cont_clas["MATCH_RELAXED"] = cont_clas["MATCH_RELAXED"].fillna(False)
    return liq_clas, cont_clas


def _motivo_liq(row):
    if row["ESTATUS_MATCH"] == "MATCH_OK":
        return "Match exacto correcto"
    if row["ESTATUS_MATCH"] == "MATCH_CON_DISCREPANCIA":
        return "Owner distinto"
    if bool(row.get("PR_EXISTE_EN_CONT")) and bool(row.get("MATCH_RELAXED")):
        return "Existe PR en Contabilidad y hay candidato relajado; revisar VIAJE o duplicados"
    if bool(row.get("PR_EXISTE_EN_CONT")):
        return "Existe PR en Contabilidad, pero no hubo match exacto"
    return "PR no encontrado en Contabilidad filtrada"


def _motivo_cont(row):
    if row["ESTATUS_MATCH"] == "MATCH_OK":
        return "Match exacto correcto"
    if row["ESTATUS_MATCH"] == "MATCH_CON_DISCREPANCIA":
        return "Owner distinto"
    if bool(row.get("PR_EXISTE_EN_LIQ")) and bool(row.get("MATCH_RELAXED")):
        return "Existe PR en Liquidaciones y hay candidato relajado; revisar VIAJE o duplicados"
    if bool(row.get("PR_EXISTE_EN_LIQ")):
        return "Existe PR en Liquidaciones, pero no hubo match exacto"
    return "PR no encontrado en Liquidaciones filtrada"


def _get_duplicados(df: pd.DataFrame, key_cols: list[str]) -> dict:
    detalle = df[df.duplicated(subset=key_cols, keep=False)].copy()
    resumen = (
        df.groupby(key_cols, dropna=False, observed=True)
        .size()
        .reset_index(name="REPETICIONES")
    )
    resumen = resumen[resumen["REPETICIONES"] > 1].copy()
    return {"detalle": detalle, "resumen": resumen}


def _control_pr(liq_f: pd.DataFrame, cont_f: pd.DataFrame) -> pd.DataFrame:
    liq_pr = liq_f.groupby("PR", dropna=False).agg(
        REG_LIQ=("PR", "size"), IMPORTE_LIQ=("IMPORTE", "sum")
    ).reset_index()
    cont_pr = cont_f.groupby("PR", dropna=False).agg(
        REG_CONT=("PR", "size"), IMPORTE_CONT=("IMPORTE", "sum")
    ).reset_index()

    cp = liq_pr.merge(cont_pr, on="PR", how="outer").fillna(0)
    cp["REG_LIQ"]     = cp["REG_LIQ"].astype(int)
    cp["REG_CONT"]    = cp["REG_CONT"].astype(int)
    cp["DIF_REG"]     = cp["REG_LIQ"] - cp["REG_CONT"]
    cp["DIF_IMPORTE"] = cp["IMPORTE_LIQ"] - cp["IMPORTE_CONT"]

    def clasifica(row):
        ok_reg  = row["REG_LIQ"] == row["REG_CONT"]
        ok_imp  = abs(row["DIF_IMPORTE"]) < 0.005
        if ok_reg and ok_imp:
            return "OK"
        if ok_imp:
            return "MISMO IMPORTE / DIF REGISTROS"
        if ok_reg:
            return "MISMO NUM REG / DIF IMPORTE"
        return "REVISAR"

    cp["ESTATUS"] = cp.apply(clasifica, axis=1)
    return cp.sort_values(["ESTATUS", "PR"])
