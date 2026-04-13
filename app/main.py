
# main.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
import os
import json
import pandas as pd

from core import (RUTA_BASES_COTIZACION, RUTA_BASES_OK, RUTA_EXCEL_RESUMEN_JUBILACION,
                  RUTA_TXT_BASES, RUTA_PDF_BASES, EXCEL_SALIDA_PATH)
from openpyxl import load_workbook
from jubilacion import calcular_jubilacion_anticipada
from rentas import calcular_rentas_hasta_65
from exencion import calcular_exencion_fiscal
from estimador_pensiones import (
    proyectar_una_opcion,
    _pension_12_desde_mensual_14,
)
from simulacion import ParametrosSimulacion, ejecutar_simulacion  # simulación iterativa

# --- NUEVO: conversor desde TXT (Adobe) a CSV con 12 meses ---
from txt2bases_csv import txt_to_rows, write_csv, pdf_to_text

from pathlib import Path


# ========== NUEVO: Carga .env si existe (sin dependencias externas) ==========
def _load_env_if_exists():
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            # No sobrescribir variables ya presentes en el entorno
            if k and (k not in os.environ):
                os.environ[k] = v

_load_env_if_exists()


# ===================== Helpers de lectura tipada desde ENV ===================
def _get_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "t", "yes", "y", "si", "sí")

def _get_float(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except Exception:
        return default

def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v is not None else default
    except Exception:
        return default

def _get_date(name: str, default: datetime) -> datetime:
    v = os.getenv(name)
    try:
        return datetime.fromisoformat(v) if v else default
    except Exception:
        return default

def _get_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None else default


# =================== Parámetros de entrada (todos por ENV) ===================
FECHA_NACIMIENTO = _get_date("FECHA_NACIMIENTO", datetime(1970, 1, 1))
SALARIO_FIJO_ANUAL = _get_float("SALARIO_FIJO_ANUAL", 0.0)
BONUS_TARGET_ANUAL = _get_float("BONUS_TARGET_ANUAL", 0.0)
COMPLEMENTOS = _get_float("COMPLEMENTOS", 0.0)
INCENTIVOS_COMERCIALES = _get_float("INCENTIVOS_COMERCIALES", 0.0)
INCENTIVOS = _get_float("INCENTIVOS", 0.0)
RETRIBUCION_TIEMPO = _get_float("RETRIBUCION_TIEMPO", 0.0)
GRATIFICACION = _get_float("GRATIFICACION", 0.0)
OTROS_CONCEPTOS = _get_float("OTROS_CONCEPTOS", 0.0)
VALES_COMIDA = _get_float("VALES_COMIDA", 0.0)
BOLSA_P3 = _get_float("BOLSA_P3", 0.0)
APORTACION_PROMOTOR_PP = _get_float("APORTACION_PROMOTOR_PP", 0.0)
PRIMA_SEGURO_VIDA = _get_float("PRIMA_SEGURO_VIDA", 0.0)
POLIZA_SALUD = _get_float("POLIZA_SALUD", 0.0)

FECHA_INICIO_RELACION = _get_date("FECHA_INICIO_RELACION", datetime(2000, 1, 1))
FECHA_BAJA = _get_date("FECHA_BAJA", datetime(2026, 3, 1))
FECHA_JUBILACION_ANTICIPADA = _get_date("FECHA_JUBILACION_ANTICIPADA", datetime(2032, 1, 1))

NUM_HIJOS = _get_int("NUM_HIJOS", 0)
PCT_RENTA_HASTA_63 = _get_float("PCT_RENTA_HASTA_63", 0.0)
PCT_RENTA_HASTA_65 = _get_float("PCT_RENTA_HASTA_65", 0.0)
PCT_REVAL_DESDE_63 = _get_float("PCT_REVAL_DESDE_63", 0.0)

SEDE_FISCAL = _get_str("SEDE_FISCAL", "ESTATAL")  # "ESTATAL" o "AUTONOMICA"

MODALIDAD = _get_str("MODALIDAD", "ERE")  # "ERE" o "PSI"
APLICAR_BRECHA_GENERO = _get_bool("APLICAR_BRECHA_GENERO", False)

INCLUIR_PENDIENTE = _get_bool("INCLUIR_PENDIENTE", False)

# Salidas
EXPORT_EXCEL = _get_bool("EXPORT_EXCEL", True)

# Otros parámetros de cálculo
PCT_REVAL_CONVENIO = _get_float("PCT_REVAL_CONVENIO", 0.02)
SEXO = _get_str("SEXO", "HOMBRE")  # para proyección

# Linealidad 61-65
APLICAR_LINEALIDAD = _get_bool("APLICAR_LINEALIDAD", False)
EDAD_INICIO_LINEALIDAD = _get_int("EDAD_INICIO_LINEALIDAD", 61)  # edad a partir de la cual se aplica linealidad (p.ej. 61, 62, etc.)

RUTA_CSV_BASES = RUTA_BASES_COTIZACION  # usa la ruta por defecto de core

if __name__ == "__main__":
    # ==============
    # 0) PDF/TXT -> CSV (formato ; con 12 meses)
    # ==============
    df_bases_in = None
    try:
        df_bases_in = pd.read_csv(RUTA_BASES_OK, sep=";", encoding="utf-8-sig")
        print(f"[OK] Cargado CSV de bases desde {RUTA_BASES_OK} (filas: {len(df_bases_in)})")

    except FileNotFoundError:
        print(f"[AVISO] No encontré el CSV en {RUTA_BASES_OK}.")

        txt_origen = None

        # 1) Intentar usar TXT si ya existe
        if Path(RUTA_TXT_BASES).exists():
            txt_origen = RUTA_TXT_BASES
            print(f"[OK] Encontrado TXT origen: {RUTA_TXT_BASES}")

        # 2) Si no existe TXT, intentar generarlo desde PDF
        elif Path(RUTA_PDF_BASES).exists():
            print(f"[AVISO] No encontré el TXT {RUTA_TXT_BASES}. Intentando generarlo desde PDF: {RUTA_PDF_BASES}...")
            try:
                pdf_to_text(RUTA_PDF_BASES, RUTA_TXT_BASES)
                txt_origen = RUTA_TXT_BASES
                print(f"[OK] TXT generado desde PDF: {RUTA_TXT_BASES}")
            except Exception as e:
                print(f"[ERROR] No pude convertir el PDF a TXT: {e}")

        # 3) Si tenemos TXT, generar CSV
        if txt_origen is not None:
            try:
                rows = txt_to_rows(txt_origen, include_pending=INCLUIR_PENDIENTE)
                write_csv(rows, RUTA_CSV_BASES, encoding="utf-8-sig")
                print(f"[OK] Generado CSV de bases: {RUTA_CSV_BASES} (filas: {len(rows)})")

                try:
                    df_bases_in = pd.read_csv(RUTA_CSV_BASES, sep=";", encoding="utf-8-sig")
                    print(f"[OK] Cargado CSV de bases desde {RUTA_CSV_BASES} (filas: {len(df_bases_in)})")
                except Exception as e:
                    print(f"[ERROR] No pude cargar el CSV generado: {e}")

            except FileNotFoundError:
                print(f"[ERROR] No encontré el TXT origen: {txt_origen}")
            except Exception as e:
                print(f"[ERROR] No pude generar el CSV desde el TXT: {e}")

        else:
            print(
                f"[AVISO] No encontré ni el CSV ({RUTA_BASES_OK}), ni el TXT ({RUTA_TXT_BASES}), "
                f"ni el PDF ({RUTA_PDF_BASES})."
            )

    # 1) Cálculo de jubilación anticipada
    res_jub = calcular_jubilacion_anticipada(
        fecha_nacimiento=FECHA_NACIMIENTO,
        fecha_baja_ere_despido=FECHA_BAJA,
        fecha_jubilacion_anticipada=FECHA_JUBILACION_ANTICIPADA,
        modalidad=MODALIDAD,
        causa_involuntaria=True,
        aplicar_incremento_2=True,
        pct_reval_convenio=PCT_REVAL_CONVENIO,
        verbose=True,
        df_bases_in=df_bases_in,  # <-- Pasamos el DataFrame extraído del PDF
        export_libro_excel=EXPORT_EXCEL,
        export_libro_excel_path=RUTA_EXCEL_RESUMEN_JUBILACION,
        incluir_tablas_entrada_en_libro=True,
        activar_regla_prebaja_max=True,
        num_hijos=NUM_HIJOS,
        aplicar_brecha_genero=APLICAR_BRECHA_GENERO,
        sexo=SEXO,
    )
    print("Resumen jubilación:", {k: v for k, v in res_jub.items() if not isinstance(v, pd.DataFrame)})

    # 2) df_bases_mensuales para rentas (lo devuelve la función de jubilación)
    df_bases_mensuales = res_jub["df_bases_mensuales"]

    # 3) Pensión (14 pagas) para 63-65
    #pension_14_opc1 = res_jub.get("Pensión Bruta Mensual (opción 1)")
    pension_14_opc2 = res_jub.get("Pensión Bruta Mensual", 0.0)

    # 4) Cálculo de rentas hasta 65
    export_excel_path = EXCEL_SALIDA_PATH
    res_rentas = calcular_rentas_hasta_65(
        modalidad=MODALIDAD,
        fecha_nacimiento=FECHA_NACIMIENTO,
        fecha_baja=FECHA_BAJA,
        df_bases_mensuales=df_bases_mensuales,
        pension_bruta_mensual_14pagas=pension_14_opc2,
        fecha_jubilacion_anticipada=FECHA_JUBILACION_ANTICIPADA,
        fecha_jubilacion_ordinaria=res_jub.get("Fecha ordinaria"),  # de res_jub
        salario_fijo_anual=SALARIO_FIJO_ANUAL,
        bonus_target_anual=BONUS_TARGET_ANUAL,
        incentivos_comerciales=INCENTIVOS_COMERCIALES,
        incentivos=INCENTIVOS,
        complementos=COMPLEMENTOS,
        retribucion_tiempo=RETRIBUCION_TIEMPO,
        gratificacion=GRATIFICACION,
        otros_conceptos=OTROS_CONCEPTOS,
        pct_renta_hasta_63=PCT_RENTA_HASTA_63,
        pct_renta_hasta_65=PCT_RENTA_HASTA_65,
        pct_reval_desde_63=PCT_REVAL_DESDE_63,
        num_hijos=NUM_HIJOS,
        #aplicar_linealidad_61_65=True,  # NUEVO: activar linealidad 61-65
        aplicar_linealidad=APLICAR_LINEALIDAD,
        edad_inicio_linealidad=EDAD_INICIO_LINEALIDAD,
        export_excel=EXPORT_EXCEL,
        export_excel_path=export_excel_path,
        incluir_tablas_entrada_en_libro=True,
        verbose=True,
    )
    print("Resumen rentas:", {k: v for k, v in res_rentas.items() if not isinstance(v, pd.DataFrame)})

    # 5) Cálculo de exención fiscal
    df_detalle = res_rentas.get("df_detalle_rentas")
    _inputs_exencion = {
        "retrib_fijas_anual": SALARIO_FIJO_ANUAL,
        "devengos_circ_12m": (BOLSA_P3 + VALES_COMIDA + OTROS_CONCEPTOS +
                               RETRIBUCION_TIEMPO + COMPLEMENTOS),  # conceptos variables incluidos en devengos
        "incentivos_12m": (BONUS_TARGET_ANUAL + INCENTIVOS_COMERCIALES + INCENTIVOS + GRATIFICACION),
        "aportaciones_promotor_pp": APORTACION_PROMOTOR_PP,
        "prima_seguro_vida": PRIMA_SEGURO_VIDA,
        "poliza_salud": POLIZA_SALUD,
    }
    mes_idx = res_rentas.get("mes_idx")
    res_exencion = calcular_exencion_fiscal(
        **_inputs_exencion,
        modalidad=MODALIDAD,
        df_detalle_rentas=df_detalle,
        fecha_inicio_relacion=FECHA_INICIO_RELACION,
        fecha_baja=FECHA_BAJA,
        sede_fiscal=SEDE_FISCAL,
        verbose=True,
        # NUEVO: escribir Excel desde exencion.py
        export_excel=EXPORT_EXCEL,
        export_excel_path=export_excel_path,
    )
    print("Resumen exención fiscal:", {k: v for k, v in res_exencion.items() if not isinstance(v, pd.DataFrame)})

    # ----------------------------------------------------------------------
    # 8) Estimación de rentas por pensión hasta esperanza de vida
    # ----------------------------------------------------------------------
    fecha_ordinaria = res_jub.get("Fecha ordinaria")
    if isinstance(fecha_ordinaria, str):
        fecha_ordinaria = pd.to_datetime(fecha_ordinaria).to_pydatetime()
    if not isinstance(fecha_ordinaria, datetime):
        raise ValueError("No se ha encontrado la fecha de jubilación ordinaria en res_jub.")

    ultima_pension = res_rentas.get("Ultima_pension", 0.0)
    pension_63_65 = res_rentas.get("Total pensión 63-65 (12 pagas)", 0.0)
    # --- Proyección de pensiones (y escritura directa en Excel desde el módulo) ---
    p1 = proyectar_una_opcion(
        sexo=SEXO,
        fecha_jub_ordinaria=fecha_ordinaria,
        fecha_nacimiento=FECHA_NACIMIENTO,
        pension_opc1_mensual_12=float(ultima_pension or 0.0),
        export_excel=EXPORT_EXCEL,
        export_excel_path=export_excel_path,
        mes_idx=mes_idx,
        verbose=True,
        total_pension_63_65=float(pension_63_65 or 0.0),
    )
    print("Resumen proyección pensiones: ", {k: v for k, v in p1.__dict__.items() if k != "df_mensual"})

    # 9) SIMULACIÓN ITERATIVA DE FECHA_JUBILACION_ANTICIPADA (al final)
    params_sim = ParametrosSimulacion(
        fecha_nacimiento=FECHA_NACIMIENTO,
        fecha_baja_ere_despido=FECHA_BAJA,  # o datetime(2026, 3, 1)
        fecha_jub_anticipada_inicio=FECHA_JUBILACION_ANTICIPADA,  # punto de partida
        modalidad=MODALIDAD,
        causa_involuntaria=True,
        aplicar_incremento_2=True,
        pct_reval_convenio=PCT_REVAL_CONVENIO,
        #pension_max_mensual_2025=PENSION_MAX_MENS_2025,
        salario_fijo_anual=SALARIO_FIJO_ANUAL,
        bonus_target_anual=BONUS_TARGET_ANUAL,
        complementos=COMPLEMENTOS,
        incentivos_comerciales=INCENTIVOS_COMERCIALES,
        pct_renta_hasta_63=PCT_RENTA_HASTA_63,
        pct_renta_hasta_65=PCT_RENTA_HASTA_65,
        pct_reval_desde_63=PCT_REVAL_DESDE_63,
        num_hijos=NUM_HIJOS,
        bolsas_y_vales_12m=(BOLSA_P3 + VALES_COMIDA),
        aportacion_promotor_pp=APORTACION_PROMOTOR_PP,
        prima_seguro_vida=PRIMA_SEGURO_VIDA,
        poliza_salud=POLIZA_SALUD,
        fecha_inicio_relacion=FECHA_INICIO_RELACION,
        sede_fiscal=SEDE_FISCAL,
        # Guardamos el log de cada iteración en este Excel/hoja:
        excel_salida_path=export_excel_path,  # p.ej. "Resumen_Rentas.xlsx"
        excel_sheet_name="Simulacion",
        verbose=True,
        df_bases_in=df_bases_in,  # <-- DataFrame extraído del PDF para usarlo en cada iteración
    )
    ejecutar_simulacion(params_sim)
