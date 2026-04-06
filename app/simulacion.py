# -*- coding: utf-8 -*-
"""
simulacion.py

Ejecuta iteraciones mensuales variando `fecha_jubilacion_anticipada` hasta que
`calcular_jubilacion_anticipada` (jubilacion.py) lance un error de validación.

En cada iteración ejecuta en secuencia:
 1) jubilacion.calcular_jubilacion_anticipada
 2) rentas.calcular_rentas_hasta_65
 3) exencion.calcular_exencion_fiscal
 4) estimador_pensiones.proyectar_una_opcion

- Ninguna función intermedia escribe en Excel (export_* = False).
- simulacion.py registra las **Entradas** y **Salidas** (no-DataFrame) de cada
  paso en una pestaña del Excel indicada al simulador.

Basado en la estructura de main.py del proyecto.
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import pandas as pd
from dateutil.relativedelta import relativedelta

# Imports de módulos internos (deben existir en tu entorno)
from jubilacion import calcular_jubilacion_anticipada
from rentas import calcular_rentas_hasta_65
from exencion import calcular_exencion_fiscal
from estimador_pensiones import proyectar_una_opcion
from core import _append_rows_to_excel, _log_kv_rows

# -------------------------- Dataclass de entradas -------------------------- #
@dataclass
class ParametrosSimulacion:
    # Entradas fijas del caso (ajusta según tu contexto)
    fecha_nacimiento: datetime
    fecha_baja_ere_despido: datetime
    fecha_jub_anticipada_inicio: datetime
    modalidad: str
    causa_involuntaria: bool = True
    aplicar_incremento_2: bool = True
    pct_reval_convenio: float = 0.02
    pension_max_mensual_2025: float = 3267.60

    # Rentas
    salario_fijo_anual: float = 0.0
    bonus_target_anual: float = 0.0
    complementos: float = 0.0
    incentivos_comerciales: float = 0.0
    incentivos: float = 0.0
    retribucion_tiempo: float = 0.0
    gratificacion: float = 0.0
    otros_conceptos: float = 0.0
    pct_renta_hasta_63: float = 0.68
    pct_renta_hasta_65: float = 0.38
    pct_reval_desde_63: float = 0.01
    num_hijos: int = 0

    # Exención – componentes del salario regulador a EF
    bolsas_y_vales_12m: float = 0.0  # p. ej., BOLSA_P3 + VALES_COMIDA
    aportacion_promotor_pp: float = 0.0
    prima_seguro_vida: float = 0.0
    poliza_salud: float = 0.0
    fecha_inicio_relacion: Optional[datetime] = None
    sede_fiscal: str = "ESTATAL"

    # Persistencia y logging
    excel_salida_path: str = "Simulacion.xlsx"
    excel_sheet_name: str = "Simulacion"
    verbose: bool = True

    # Parámetro adicional para pasar el DataFrame de bases mensuales desde main.py a cada iteración de la simulación
    df_bases_in: pd.DataFrame = None  # DataFrame extraído del PDF para usar en cada iteración

# ------------------------------- Simulación ------------------------------- #

def ejecutar_simulacion(params: ParametrosSimulacion) -> None:
    """
    Itera mes a mes la `fecha_jub_anticipada` a partir de `fecha_jub_anticipada_inicio`
    hasta que `calcular_jubilacion_anticipada` lance una excepción de **validación**.

    En cada iteración, ejecuta jubilación -> rentas -> exención -> pensiones
    sin escritura a Excel en las funciones, y registra entradas/salidas (no-DF)
    en la hoja indicada por `excel_sheet_name` del fichero `excel_salida_path`.
    """
    iter_idx = 0
    fecha_cursor = params.fecha_jub_anticipada_inicio

    # DECLARACIÓN DE LA LISTA DE RESULTADOS
    resultados_simulacion: List[Dict[str, Any]] = []

    while True:
        iter_idx += 1
        if ParametrosSimulacion.verbose:
            print(f"--- Iteración {iter_idx}: fecha_jub_anticipada = {fecha_cursor.strftime('%Y-%m-%d')} ---")
            print("Iniciando cálculos...")
            
        # --------------------------- JUBILACIÓN --------------------------- #
        jub_inputs = {
            "fecha_nacimiento": params.fecha_nacimiento,
            "fecha_baja_ere_despido": params.fecha_baja_ere_despido,
            "fecha_jubilacion_anticipada": fecha_cursor,
            "modalidad": params.modalidad,
            "causa_involuntaria": params.causa_involuntaria,
            "aplicar_incremento_2": params.aplicar_incremento_2,
            "pct_reval_convenio": params.pct_reval_convenio,
            #"pension_max_mensual_2025": params.pension_max_mensual_2025,
            "verbose": False,
            "export_libro_excel": False,
            "export_libro_excel_path": None,
            "incluir_tablas_entrada_en_libro": False,
            # parámetros opcionales en main.py relacionados con regla prebaja
            "activar_regla_prebaja_max": True,
            "df_bases_in": params.df_bases_in,
        }

        if params.verbose:
            #print("Jubilación anticipada - Entradas:", jub_inputs)
            pass

        _append_rows_to_excel(
            params.excel_salida_path,
            params.excel_sheet_name,
            _log_kv_rows(iter_idx, fecha_cursor, "jubilacion_input", jub_inputs),
        )

        if params.verbose:
            print("Ejecutando calcular_jubilacion_anticipada...")

        try:
            res_jub = calcular_jubilacion_anticipada(**jub_inputs)
        except ValueError as e:
            _append_rows_to_excel(
                params.excel_salida_path,
                params.excel_sheet_name,
                [
                    {
                        "iter": iter_idx,
                        "fecha_jubilacion_anticipada": fecha_cursor.strftime("%Y-%m-%d"),
                        "paso": "jubilacion_error",
                        "clave": "exception",
                        "valor": str(e),
                    }
                ],
            )
            break

        jub_out_rows = _log_kv_rows(
            iter_idx,
            fecha_cursor,
            "jubilacion_output",
            {k: v for k, v in res_jub.items() if not isinstance(v, (pd.DataFrame, pd.Series))},
        )
        _append_rows_to_excel(params.excel_salida_path, params.excel_sheet_name, jub_out_rows)

        if params.verbose:
            print("Jubilación anticipada - Salidas:", {k: v for k, v in res_jub.items() if not isinstance(v, pd.DataFrame)})

        df_bases_mensuales = res_jub.get("df_bases_mensuales")
        if df_bases_mensuales is None:
            raise RuntimeError("res_jub no contiene 'df_bases_mensuales'.")

        #pension_14_opc1 = res_jub.get("Pensión Bruta Mensual (opción 1)")
        pension_14_opc2 = res_jub.get("Pensión Bruta Mensual (opción 2)")

        fecha_ordinaria = res_jub.get("Fecha ordinaria")

        # ----------------------------- RENTAS ----------------------------- #
        rentas_inputs = {
            "fecha_nacimiento": params.fecha_nacimiento,
            "fecha_baja": params.fecha_baja_ere_despido,
            "modalidad": params.modalidad,
            "df_bases_mensuales": df_bases_mensuales,
            "pension_bruta_mensual_14pagas": pension_14_opc2,
            # NUEVO:
            "fecha_jubilacion_anticipada": fecha_cursor,
            "fecha_jubilacion_ordinaria": fecha_ordinaria,   # de res_jub
            "salario_fijo_anual": params.salario_fijo_anual,
            "bonus_target_anual": params.bonus_target_anual,
            "incentivos_comerciales": params.incentivos_comerciales,
            "incentivos": params.incentivos,
            "retribucion_tiempo": params.retribucion_tiempo,
            "gratificacion": params.gratificacion,
            "otros_conceptos": params.otros_conceptos,
            "complementos": params.complementos,
            "pct_renta_hasta_63": params.pct_renta_hasta_63,
            "pct_renta_hasta_65": params.pct_renta_hasta_65,
            "pct_reval_desde_63": params.pct_reval_desde_63,
            "num_hijos": params.num_hijos,
            "export_excel": False,
            "export_excel_path": None,
            "incluir_tablas_entrada_en_libro": False,
            "verbose": False,
        }
        _append_rows_to_excel(
            params.excel_salida_path,
            params.excel_sheet_name,
            _log_kv_rows(iter_idx, fecha_cursor, "rentas_input", rentas_inputs),
        )

        if params.verbose:
            print("Ejecutando calcular_rentas_hasta_65...")
            print("Rentas - Entradas:", rentas_inputs)

        res_rentas = calcular_rentas_hasta_65(**rentas_inputs)
        rentas_out_rows = _log_kv_rows(
            iter_idx,
            fecha_cursor,
            "rentas_output",
            {k: v for k, v in res_rentas.items() if not isinstance(v, (pd.DataFrame, pd.Series))},
        )
        _append_rows_to_excel(params.excel_salida_path, params.excel_sheet_name, rentas_out_rows)

        if params.verbose:
            print("Rentas - Salidas:", {k: v for k, v in res_rentas.items() if not isinstance(v, pd.DataFrame)})

        df_detalle = res_rentas.get("df_detalle_rentas")
        if df_detalle is None:
            raise RuntimeError("res_rentas no contiene 'df_detalle_rentas'.")
        mes_idx = res_rentas.get("mes_idx")
        ultima_pension = res_rentas.get("Ultima_pension", 0.0)

        # ---------------------------- EXENCIÓN ---------------------------- #

        exen_inputs = {
            "modalidad": params.modalidad,
            "retrib_fijas_anual": params.salario_fijo_anual,
            "devengos_circ_12m": (params.bolsas_y_vales_12m + params.retribucion_tiempo + params.otros_conceptos + 
                                  params.complementos),
            "incentivos_12m": (params.bonus_target_anual + params.incentivos_comerciales + 
                               params.gratificacion + params.incentivos),
            "aportaciones_promotor_pp": params.aportacion_promotor_pp,
            "prima_seguro_vida": params.prima_seguro_vida,
            "poliza_salud": params.poliza_salud,
            "df_detalle_rentas": df_detalle,
            "fecha_inicio_relacion": params.fecha_inicio_relacion,
            "fecha_baja": params.fecha_baja_ere_despido,
            "sede_fiscal": params.sede_fiscal,
            "verbose": False,
            "export_excel": False,
            "export_excel_path": None,
        }
        _append_rows_to_excel(
            params.excel_salida_path,
            params.excel_sheet_name,
            _log_kv_rows(iter_idx, fecha_cursor, "exencion_input", exen_inputs),
        )

        if params.verbose:
            print("Ejecutando calcular_exencion_fiscal...")
            #print("Exención fiscal - Entradas:", exen_inputs)

        res_exen = calcular_exencion_fiscal(**exen_inputs)
        exen_out_rows = _log_kv_rows(
            iter_idx,
            fecha_cursor,
            "exencion_output",
            {k: v for k, v in res_exen.items() if not isinstance(v, (pd.DataFrame, pd.Series))},
        )
        _append_rows_to_excel(params.excel_salida_path, params.excel_sheet_name, exen_out_rows)
        if params.verbose:
            print("Exención fiscal - Salidas:", {k: v for k, v in res_exen.items() if not isinstance(v, pd.DataFrame)})

        # ----------------------------- PENSIONES -------------------------- #
        fecha_ordinaria = res_jub.get("Fecha ordinaria")
        if isinstance(fecha_ordinaria, str):
            try:
                fecha_ordinaria = pd.to_datetime(fecha_ordinaria).to_pydatetime()
            except Exception:
                pass
        if not isinstance(fecha_ordinaria, datetime):
            raise RuntimeError("No se ha encontrado la fecha de jubilación ordinaria en res_jub.")

        pens_inputs = {
            "sexo": "HOMBRE",  # ajustable
            "fecha_jub_ordinaria": fecha_ordinaria,
            "fecha_nacimiento": params.fecha_nacimiento,
            "pension_opc1_mensual_12": float(ultima_pension or 0.0),
            "export_excel": False,
            "export_excel_path": None,
            "mes_idx": mes_idx,
            "verbose": False,
            "total_pension_63_65": float(res_rentas.get("Total pensión 63-65 (12 pagas)", 0.0) or 0.0),
        }
        _append_rows_to_excel(
            params.excel_salida_path,
            params.excel_sheet_name,
            _log_kv_rows(iter_idx, fecha_cursor, "pensiones_input", pens_inputs),
        )

        if params.verbose:
            print("Ejecutando proyectar_una_opcion...")
            print("Proyección pensiones - Entradas:", pens_inputs)

        p1 = proyectar_una_opcion(**pens_inputs)
        p1_dict = getattr(p1, "__dict__", {})
        pen_out = {k: v for k, v in p1_dict.items() if k != "df_mensual"}
        _append_rows_to_excel(
            params.excel_salida_path,
            params.excel_sheet_name,
            _log_kv_rows(iter_idx, fecha_cursor, "pensiones_output", pen_out),
        )

        if params.verbose:
            print("Proyección pensiones - Salidas:", {k: v for k, v in pen_out.items()})

        # Almacenamos los resultados de esta iteración en una lista para análisis posterior
        resultados_simulacion.append({
            "fecha_jub": fecha_cursor,
            "meses_adelanto": res_jub.get("Meses Adelanto"),
            "ultima_pension": ultima_pension,
            "total_63_65": res_rentas.get("Total pensión 63-65 (12 pagas)", 0.0),
            "total_65_adelante": p1.total_pension_65_enadelante,
            "pension_total_acumulada": p1.total_pension, # Este es el valor a maximizar
            "coef_reductor": res_jub.get("Porcentaje de reducción"),
            "esperanza_vida": p1.esperanza_vida_anios,
        })

        # ------------------------ Avanzar un mes ------------------------- #
        fecha_cursor = fecha_cursor + relativedelta(months=1)

    # Al salir del bucle, llamamos a la función de análisis
    analizar_y_reportar_optimo(resultados_simulacion, params)

def analizar_y_reportar_optimo(resultados: List[Dict[str, Any]], params: ParametrosSimulacion) -> None:
    """
    Encuentra la iteración con la pensión total máxima y genera el informe justificativo.
    """
    if not resultados:
        return

    # 1. Encontrar el máximo basado en 'pension_total_acumulada'
    optimo = max(resultados, key=lambda x: x["pension_total_acumulada"])

    # 2. Construcción del texto explicativo
    informe = f"""
    --- INFORME DE OPTIMIZACIÓN DE JUBILACIÓN ---
    
    Se ha identificado que el valor máximo de beneficio acumulado se alcanza en la siguiente configuración:
    
    - Fecha de Jubilación Óptima: {optimo['fecha_jub'].strftime('%d/%m/%Y')}
    - Meses de Adelanto sobre la edad ordinaria: {optimo['meses_adelanto']} meses.
    - Pensión Bruta Mensual (última): {optimo['ultima_pension']:.2f} €
    - Total Percibido (63-65 años): {optimo['total_63_65']:.2f} €
    - Total Proyectado (65+ hasta esperanza vida): {optimo['total_65_adelante']:.2f} €
    - PENSION TOTAL ACUMULADA: {optimo['pension_total_acumulada']:.2f} €
    - Porcentaje de Reducción Aplicado: {optimo['coef_reductor']:.2f}%
    - Esperanza de Vida Asumida: {optimo['esperanza_vida']} años
    
    JUSTIFICACIÓN DEL RESULTADO:
    El valor obtenido representa el punto de equilibrio matemático donde el incremento de la base reguladora 
    y la disminución de los coeficientes reductores por retrasar la jubilación compensan la pérdida de 
    meses de cobro inmediato. 
    
    AVISO SOBRE EL CONVENIO ESPECIAL:
    Es fundamental advertir que este cálculo se basa en la cuantía de la pensión. Sin embargo, los meses 
    que se retrase la jubilación anticipada para alcanzar esta cifra conllevan la pérdida del pago del 
    Convenio Especial de la Seguridad Social por parte de la Empresa. El pago de dicho convenio durante 
    esos meses de retraso es un desembolso extra que el trabajador debe asumir, por lo que muy posiblemente,
    NO compense el incremento de la pensión neta a largo plazo, dependiendo de la esperanza de vida real y
    de la rentabilidad financiera de ese capital invertido.
    --------------------------------------------
    """
    
    print(informe)
    
    # Opcional: Registrar el informe en el Excel
    _append_rows_to_excel(
        params.excel_salida_path,
        "Resumen_Optimo",
        [{"clave": "Informe_Final", "valor": informe}]
    )