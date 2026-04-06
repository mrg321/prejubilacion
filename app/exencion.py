# exencion.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, List, Dict, Iterable, Tuple

import pandas as pd
from dateutil.relativedelta import relativedelta

from core import _append_key_values_to_sheet, _append_triplets_to_sheet, _days_between

# ============================================================
# 2) Antigüedad: días pre y post 12/02/2012 (45/33)
# ============================================================

def _segmentos_antiguedad(fecha_inicio: datetime, fecha_baja: datetime) -> Dict[str, float]:
    """
    Devuelve días de servicio PRE y POST 12/02/2012.
      - PRE: desde fecha_inicio hasta 12-02-2012 (inclusive conceptual)
      - POST: desde 13-02-2012 hasta fecha_baja (inclusive conceptual)
    Retorna en días naturales.
    """
    corte = datetime(2012, 2, 12)
    pre_days = 0
    post_days = 0
    if fecha_baja <= fecha_inicio:
        return {"dias_pre": 0.0, "dias_post": 0.0}

    # PRE: [fecha_inicio, min(corte, fecha_baja)]
    if fecha_inicio <= corte:
        fin_pre = min(corte, fecha_baja)
        pre_days = _days_between(fecha_inicio, fin_pre)

    # POST: [max(fecha_inicio, corte+1), fecha_baja]
    inicio_post = max(fecha_inicio, corte + timedelta(days=1))
    if fecha_baja >= inicio_post:
        post_days = _days_between(inicio_post, fecha_baja)

    return {"dias_pre": float(pre_days), "dias_post": float(post_days)}

def _cap_mensualidades(monto: float, salario_mensual: float, max_meses: int) -> float:
    """
    Aplica tope en mensualidades: min(monto, salario_mensual * max_meses)
    """
    return min(float(monto), float(salario_mensual) * max_meses)


# ============================================================
# 3) Indemnización legal (ET + DT 2012): 45 d/año (tope 42M) + 33 d/año hasta 24M
# ============================================================

def _indemnizacion_legal_exenta(
    *,
    salario_reg_anual_exencion: float,
    fecha_inicio_relacion: datetime,
    fecha_baja: datetime,
) -> Dict[str, float]:
    """
    Calcula la indemnización legal "máximo exento base" combinando:
      - 45 días/año hasta 12/02/2012 (tope 42 mensualidades)
      - Si el importe PRE < 24 mensualidades, añade POST 33 d/año hasta 24 mensualidades en total.
    Devuelve:
      - 'importe_pre_45' (ya con tope 42M)
      - 'importe_post_33' (utilizado)
      - 'importe_legal_conjunto'
      - 'salario_mensual', 'salario_diario'
    """
    salario_diario = float(salario_reg_anual_exencion) / 365.0
    salario_mensual = float(salario_reg_anual_exencion) / 12.0

    seg = _segmentos_antiguedad(fecha_inicio_relacion, fecha_baja)
    anios_pre = seg["dias_pre"] / 365.0
    anios_post = seg["dias_post"] / 365.0

    importe_pre_45 = salario_diario * (45.0 * anios_pre)
    importe_post_33 = salario_diario * (33.0 * anios_post)

    # Tope PRE 42 mensualidades
    importe_pre_45_cap = _cap_mensualidades(importe_pre_45, salario_mensual, 42)

    # Regla de 24 mensualidades conjunto (si PRE < 24M, completar con POST)
    tope_24 = salario_mensual * 24
    if importe_pre_45_cap >= tope_24:
        importe_legal = importe_pre_45_cap
        importe_post_33_utilizado = 0.0
    else:
        margen = tope_24 - importe_pre_45_cap
        importe_post_33_utilizado = min(importe_post_33, margen)
        importe_legal = importe_pre_45_cap + importe_post_33_utilizado

    return {
        "salario_mensual": salario_mensual,
        "salario_diario": salario_diario,
        "dias_pre": seg["dias_pre"],
        "dias_post": seg["dias_post"],
        "importe_pre_45": round(importe_pre_45_cap, 2),
        "importe_post_33": round(importe_post_33_utilizado, 2),
        "importe_legal_conjunto": round(importe_legal, 2),
    }

# ============================================================
# 4) Función principal: Exención + (si procede) Reducción 30% Renta Irregular (art. 18.2 LIRPF)
# ============================================================

def calcular_exencion_fiscal(
    *,
    # 1) Salario regulador a EF: o bien agregado o por componentes
    salario_reg_exencion_anual: Optional[float] = None,
    retrib_fijas_anual: float = 0.0,
    devengos_circ_12m: float = 0.0,
    incentivos_12m: float = 0.0,
    aportaciones_promotor_pp: float = 0.0,
    prima_seguro_vida: float = 0.0,
    poliza_salud: float = 0.0,
    # 2) Tabla Detalle_Rentas (salida unificada de calcular_rentas_hasta_65)
    df_detalle_rentas: pd.DataFrame,
    # 3) Antigüedad a EF
    fecha_inicio_relacion: datetime,
    fecha_baja: datetime,
    # 4) Sede fiscal
    sede_fiscal: str = "ESTATAL",  # "ESTATAL", "BIZKAIA", "GIPUZKOA", "ALAVA", "NAVARRA", ...
    # Parámetros / personalización
    limite_exencion_absoluto: float = 180_000.0,  # tope general
    columnas_indemnizatorias: Optional[List[str]] = None,
    incluir_compensacion_sepe_en_indemnizatoria: bool = True,
    verbose: bool = True,
    # 5) NUEVO: escritura en Excel
    export_excel: bool = False,
    export_excel_path: Optional[str] = None,
    modalidad: str = "ERE",  # "ERE" o "PSI" (para aplicar o no la función de exención fiscal)
) -> Dict[str, object]:
    """
    Calcula:
      1) Importe exento (indemnización legal sujeta a exención) con tope absoluto (general 180.000 €; Navarra 125.000 €).
      2) Si, una vez agotada la exención, el cociente (antigüedad / nº ejercicios de cobro) > 2, aplica reducción del 30%
         como "renta irregular" (art. 18.2 LIRPF) sobre la parte indemnizatoria que tributa, con tope de **base reducible**
         de 300.000 € por año fiscal.

    Además, si export_excel=True y export_excel_path se informa:
      - Reemplaza la hoja 'Detalle_Rentas' con el df actualizado.
      - Apende secciones:
         * Entrada -> 'Exencion_Fiscal_Inputs'  (2 columnas)
         * Salida  -> 'Exencion_Fiscal_Resultados' (3 columnas)
         * Valores usados -> 'Exencion_Fiscal_Valores_Usados' (3 columnas)
    """

    # -------------------- Salario regulador a EF --------------------
    if salario_reg_exencion_anual is None:
        salario_reg_exencion_anual = (
            float(retrib_fijas_anual)
            + float(devengos_circ_12m)
            + float(incentivos_12m)
            + float(aportaciones_promotor_pp)
            + float(prima_seguro_vida)
            + float(poliza_salud)
        )
    salario_reg_exencion_anual = float(salario_reg_exencion_anual)

    # --- NUEVO: resolver modalidad ---
    modalidad_up = (modalidad or "ERE").strip().upper()
    if modalidad_up not in {"ERE", "PSI"}:
        raise ValueError("modalidad debe ser 'ERE' o 'PSI'.")
    is_PSI = (modalidad_up == "PSI")

    # -------------------- Indemnización legal (ET + DT 2012) --------------------
    desg = _indemnizacion_legal_exenta(
        salario_reg_anual_exencion=salario_reg_exencion_anual,
        fecha_inicio_relacion=fecha_inicio_relacion,
        fecha_baja=fecha_baja,
    )
    importe_legal = float(desg["importe_legal_conjunto"])

    # -------------------- Tope absoluto exención por sede fiscal --------------------
    sede_upper = str(sede_fiscal or "").strip().upper()
    limite_por_sede = float(limite_exencion_absoluto)
    if sede_upper in {"NAVARRA", "NAF", "CFN"}:
        limite_por_sede = 125_000.0

    # --- CAMBIO: en PSI NO hay exención fiscal de ningún tipo
    if is_PSI:
        importe_exento = 0.0
    else:
        importe_exento = min(importe_legal, limite_por_sede)

    # -------------------- Preparación del detalle --------------------
    # Lee la hoja 'Detalle_Rentas' del Excel generado
    #df_detalle_rentas = pd.read_excel(export_excel_path, sheet_name='Detalle_Rentas', engine='openpyxl')

    df = df_detalle_rentas.copy()
    if "fecha_mes" in df.columns:
        df["fecha_mes"] = pd.to_datetime(df["fecha_mes"])
        df = df.sort_values("fecha_mes").reset_index(drop=True)

    # Columnas indemnizatorias por defecto
    if columnas_indemnizatorias is None:
        columnas_indemnizatorias = [
            "complemento_empresa_63",
            "renta_indemn_63",
            "renta_indemn_65",
        ]

    # --- CAMBIO: en PSI no añadimos comp_paro_ss_empresa a la base indemnizatoria
    incluir_comp_sepe = (bool(incluir_compensacion_sepe_en_indemnizatoria) and not is_PSI)
    if incluir_comp_sepe:
        columnas_indemnizatorias.append("comp_paro_ss_empresa")


    for col in columnas_indemnizatorias + ["prestacion_mes", "pension_12"]:
        if col not in df.columns:
            df[col] = 0.0

    # -------------------- Cociente antigüedad/ejercicios fiscales --------------------
    total_dias_servicio = float(desg.get("dias_pre", 0.0) + desg.get("dias_post", 0.0))
    anios_servicio = total_dias_servicio / 365.0

    if "fecha_mes" in df.columns:
        df["__year"] = pd.to_datetime(df["fecha_mes"]).dt.year
    else:
        df["__year"] = 1900

    # Usar exclusivamente las columnas indemnizatorias ya definidas arriba:
    # columnas_indemnizatorias (incluye 'comp_paro_ss_empresa' solo si el flag lo añade)
    indemn_cols = columnas_indemnizatorias

    tiene_indemn = (df[indemn_cols].sum(axis=1) > 0) if indemn_cols else pd.Series([False] * len(df))
    ejercicios_cobro = int(df.loc[tiene_indemn, "__year"].nunique()) if len(df) else 0

    cociente_antiguedad_sobre_ejercicios = (anios_servicio / ejercicios_cobro) if ejercicios_cobro > 0 else 0.0

    aplica_reduccion_irregular = cociente_antiguedad_sobre_ejercicios > 2.0

    # --- CAMBIO: en PSI no hay reducción por rentas irregulares
    if is_PSI:
        aplica_reduccion_irregular = False

    # -------------------- NUEVO LIMITE DINAMICO 30% (según renta indemnizatoria total) --------------------
    # Se calcula una única vez, a partir del total indemnizatorio, y se aplica como tope anual de base reducible.
    total_indemnizatorio = float(df[indemn_cols].sum().sum()) if indemn_cols else 0.0
    if total_indemnizatorio <= 700_000.0:
        tope_base_reducible_anual = 300_000.0
    elif total_indemnizatorio < 1_000_000.0:
        tope_base_reducible_anual = 300_000.0 - (total_indemnizatorio - 700_000.0)
    else:
        tope_base_reducible_anual = 0.0
    # Seguridad: no permitir negativo
    tope_base_reducible_anual = max(0.0, float(tope_base_reducible_anual))

    
    # -------------------- Asignación cronológica: Exención + Reducción 30% --------------------
    exencion_restante = importe_exento
    exencion_aplicada = []   # SOLO exención legal (no suma la reducción irregular)
    tributa_mes = []
    reduccion_irregular_mes_list = []

    base_reducible_consumida_por_ano: Dict[int, float] = {}

    for _, row in df.iterrows():
        year = int(row["__year"]) if "__year" in row else 1900
        base_reducida_en_ano = base_reducible_consumida_por_ano.get(year, 0.0)

        indemn_mes = float(sum(row.get(c, 0.0) for c in columnas_indemnizatorias))
        no_exentos_basicos = float(row.get("prestacion_mes", 0.0)) + float(row.get("pension_12", 0.0))

        # 1) Exención legal
        exento_mes = min(indemn_mes, exencion_restante)
        exencion_restante = max(0.0, exencion_restante - exento_mes)

        # 2) Parte indemnizatoria que tributa
        indemn_tributa_mes = indemn_mes - exento_mes

        # 3) Reducción irregular 30% si aplica (con tope base anual dinámico)
        reduccion_irregular_mes = 0.0
        if aplica_reduccion_irregular and indemn_tributa_mes > 0.0:
            espacio_base_anual = max(0.0, tope_base_reducible_anual - base_reducida_en_ano)
            base_reducible_mes = min(indemn_tributa_mes, espacio_base_anual)
            reduccion_irregular_mes = round(0.30 * base_reducible_mes, 2)
            base_reducible_consumida_por_ano[year] = base_reducida_en_ano + base_reducible_mes

        total_tributa = (indemn_tributa_mes - reduccion_irregular_mes) + no_exentos_basicos

        reduccion_irregular_mes_list.append(reduccion_irregular_mes)
        exencion_aplicada.append(round(exento_mes, 2))     # SOLO exención legal
        tributa_mes.append(round(total_tributa, 2))

    # -------------------- Escritura de columnas y métricas --------------------
    df["reduccion_irregular_mes"] = reduccion_irregular_mes_list
    df["exencion_aplicada_mes"] = exencion_aplicada
    df["irpf_tributa_mes"] = tributa_mes

    num_meses_exencion = int(
        ((df["exencion_aplicada_mes"] > df["reduccion_irregular_mes"]) &
         (df[[c for c in columnas_indemnizatorias]].sum(axis=1) > 0)).sum()
    )
    importe_reduccion_irregular_total = float(round(df["reduccion_irregular_mes"].sum(), 2))

    # -------------------- Notas --------------------
    notas = (
        "Cálculo de indemnización exenta conforme a ET y Reforma 2012 (45 días/año hasta 12/02/2012 con tope 42 "
        "mensualidades; si inferior a 24 mensualidades, se añade 33 días/año desde 13/02/2012 hasta 24 mensualidades). "
        "Exención fiscal hasta el límite legal obligatorio y tope absoluto general de 180.000 € (art. 7.e) LIRPF). "
        "La exención no aplica a SEPE ni a Pensión. Además, si el cociente antigüedad/ejercicios fiscales es > 2, "
        "se aplica la reducción del 30% (art. 18.2 LIRPF) con tope anual de 300.000 € sobre la base reducible."
    )

    if verbose:
        print(f"[EXENCIÓN] Salario reg. anual (EF): {salario_reg_exencion_anual:,.2f} €")
        print(f"[EXENCIÓN] Indemnización legal conjunta: {importe_legal:,.2f} €")
        print(f"[EXENCIÓN] Tope absoluto aplicado por sede: {limite_por_sede:,.2f} € (sede={sede_upper})")
        print(f"[EXENCIÓN] IMPORTE EXENTO: {importe_exento:,.2f} € | Meses con exención: {num_meses_exencion}")
        print(
            f"[IRREGULAR] anios_servicio={anios_servicio:.6f}, ejercicios_cobro={ejercicios_cobro}, "
            f"cociente={cociente_antiguedad_sobre_ejercicios:.6f}, aplica={aplica_reduccion_irregular}"
        )
        if aplica_reduccion_irregular:
            print(f"[IRREGULAR] Reducción total aplicada (30%): {importe_reduccion_irregular_total:,.2f} €")

    # -------------------- Escritura en Excel desde aquí (opcional) --------------------
    if export_excel:
        if not export_excel_path:
            raise ValueError("export_excel=True requiere export_excel_path con la ruta del Excel.")

        # 1) Reemplazar Detalle_Rentas
        with pd.ExcelWriter(export_excel_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name="Detalle_Rentas", index=False)

        # 2) Entrada -> Exencion_Fiscal_Inputs (2 columnas)
        entrada_pairs = [
            ("modalidad", modalidad_up),
            ("retrib_fijas_anual", retrib_fijas_anual),
            ("devengos_circ_12m",  devengos_circ_12m),
            ("incentivos_12m",     incentivos_12m),
            ("aportaciones_promotor_pp", aportaciones_promotor_pp),
            ("prima_seguro_vida",  prima_seguro_vida),
            ("poliza_salud",       poliza_salud),
            ("fecha_inicio_relacion", str(fecha_inicio_relacion.date())),
            ("fecha_baja",           str(fecha_baja.date())),
            ("sede_fiscal",          sede_fiscal),
        ]
        _append_key_values_to_sheet(
            export_excel_path, "Entrada", "Exencion_Fiscal_Inputs", entrada_pairs
        )

        # 3) Salida -> Exencion_Fiscal_Resultados (3 columnas)
        salida_triplets = [
            ("Hacienda", "importe_exento", importe_exento),
            ("Hacienda", "num_meses_exencion", num_meses_exencion),
            ("Hacienda", "importe_reduccion_irregular_total", importe_reduccion_irregular_total),
        ]
        _append_triplets_to_sheet(
            export_excel_path, "Salida", "Exencion_Fiscal_Resultados", salida_triplets
        )

        # 4) Valores usados -> Exencion_Fiscal_Valores_Usados (3 columnas)
        valores_usados_triplets = [
            ("Exencion_Fiscal", "salario_reg_exencion_anual", round(salario_reg_exencion_anual, 2)),
            ("Exencion_Fiscal", "total indemnizatorio", round(total_indemnizatorio, 2)),
            ("Exencion_Fiscal", "anios_servicio",             round(anios_servicio, 6)),
            ("Exencion_Fiscal", "ejercicios_cobro",           ejercicios_cobro),
            ("Exencion_Fiscal", "cociente_antiguedad_sobre_ejercicios", round(cociente_antiguedad_sobre_ejercicios, 6)),
            ("Exencion_Fiscal", "aplica_reduccion_irregular", bool(aplica_reduccion_irregular)),
        ]
        _append_triplets_to_sheet(
            export_excel_path, "Valores usados", "Exencion_Fiscal_Valores_Usados", valores_usados_triplets
        )

    # -------------------- Retorno --------------------
    return {
        "importe_exento": round(importe_exento, 2),
        "num_meses_exencion": int(num_meses_exencion),
        "importe_reduccion_irregular_total": importe_reduccion_irregular_total,
        "detalle_rentas_con_tributacion": df,
        "desglose_indemnizacion_legal": desg,
        "valores_usados": {
            "salario_reg_exencion_anual": round(salario_reg_exencion_anual, 2),
            "anios_servicio": round(anios_servicio, 6),
            "ejercicios_cobro": ejercicios_cobro,
            "cociente_antiguedad_sobre_ejercicios": round(cociente_antiguedad_sobre_ejercicios, 6),
            "aplica_reduccion_irregular": bool(aplica_reduccion_irregular),
        },
        "notas_legales": notas,
    }