# rentas.py
# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import calendar
from dateutil.relativedelta import relativedelta
from datetime import datetime
from dateutil.relativedelta import relativedelta
import calendar
from core import (
    _validate_inputs_rentas, _read_table,
    _month_start, _add_months, _month_end, _nearest_month_start,
    _get_base_mensual_para_mes,
    PENSION_REVAL_IPC_OBJ, PCT_COMP_SEPE_SS_EMPRESA, RUTA_PRESTACION_MINMAX, 
    RUTA_DURACION_PRESTACION, PCT_CESS_EMPRESA, COEF_REDUCTOR_CESS, PCT_MEI_2023, 
    MEI_START)

def calcular_rentas_hasta_65(
    *,
    # Fechas y datos comunes
    fecha_nacimiento: datetime,
    fecha_baja: datetime,
    df_bases_mensuales: pd.DataFrame,  # ['fecha','base'] al primer día de mes
    pension_bruta_mensual_14pagas: float = None,

    # --- : parametrización de modalidad / overrides ---
    modalidad: str = "ERE",  # "ERE" | "PSI"

    # --- : Fechas de jubilación para tramos y validaciones ---
    fecha_jubilacion_anticipada: datetime = None,
    fecha_jubilacion_ordinaria: datetime = None,

    # Salario regulador (12 pagas, anual)
    salario_fijo_anual: float = 0.0,
    bonus_target_anual: float = 0.0,
    incentivos_comerciales: float = 0.0,
    incentivos: float = 0.0,
    complementos: float = 0.0,
    retribucion_tiempo: float = 0.0,
    gratificacion: float = 0.0,
    otros_conceptos: float = 0.0,
    
    # Parámetros de renta
    pct_renta_hasta_63: float = 0.0,
    pct_renta_hasta_65: float = 0.0,
    pct_reval_desde_63: float = 0.0,

    # --- NUEVO: Linealidad de rentas ---
    aplicar_linealidad: bool = False,
    edad_inicio_linealidad: int | None = None,

    # Situación familiar
    num_hijos: int = 0,

    # Override opcional de días cotizados
    dias_cotizados_previos: int = None,

    # Salidas/Excel
    export_excel: bool = True,
    export_excel_path: str = "Resumen_Rentas_hasta_65.xlsx",
    incluir_tablas_entrada_en_libro: bool = True,
    verbose: bool = True,
):
    """
    (1.1) Prestación contributiva por meses: 70% meses 1-6, 60% resto (mín./máx. por hijos),
          duración según tramos (últ. 6 años). Se añade 'comp_paro_ss_empresa' = 4,70% base SEPE (prorrateo).
    (1.2) Posprestación hasta 63: renta empresa = % * salario regulador mensual (prorrateo en mes 63 SIN el día).
    (1.3) 63–65: pensión (14->12) revalorizada 1-Ene + IPC objetivo + renta empresa con salario regulador FIJO a 63.
          Mes 63: incluir el DÍA del cumpleaños en 63–65. Mes 65: renta 1..(día-1); pensión completa.

    --- NUEVO ---
    • Aportación de la empresa al CESS:
        CESS_mes = base_cotizacion_mes * (0.2830 * 0.94) + base_cotizacion_mes * (0.0090 si fecha >= 2023-01-01)
      – 'Aportación_CESS' se añade por mes en Detalle_Rentas y en el resumen (pagador EMPRESA).
      – La base se toma de df_bases_mensuales (histórico + proyecciones que aporte jubilación.py).

    • Reglas por tramos (resumen):
      A) Si la jubilación ordinaria = 65:
         A.1) Si la anticipada = 63 -> mantener cálculos actuales (no CESS en 63–65).
         A.2) Si la anticipada > 63:
             - 63 -> anticipada: renta + pensión + CESS (empresa).
             - anticipada -> 65: renta + pensión, CESS = 0.

      B) Si la jubilación ordinaria > 65:
         Definimos umbral_24 = (ordinaria - 24 meses).
         B.1) Si anticipada > umbral_24:
             - 63 -> umbral_24: renta, sin pensión, CESS SÍ (empresa).
             - umbral_24 -> anticipada: renta, sin pensión, CESS = 0.
             - anticipada -> 65: renta + pensión, CESS = 0.
             - 65 -> ordinaria: solo pensión, CESS = 0 (se incorpora en el detalle como tramo adicional).
         B.2) Si anticipada = umbral_24: igual al caso B.1 pero sin el subtramo (umbral_24 -> anticipada).
         B.3) Si anticipada < umbral_24: lanzar ValueError.
      C) No puede ocurrir ordinaria < 65 -> ValueError.
      D) No puede ocurrir anticipada < 63 -> ValueError.

    NOTA: Para mantener compatibilidad, si no se informan 'fecha_jubilacion_anticipada' o 'fecha_jubilacion_ordinaria',
          se asume ordinaria = 65 y anticipada = 63 (comportamiento legacy). En ese caso,
          se mantienen los cálculos previos sin CESS en 63–65.
    """

    # --- Validación básica existente ---
    _validate_inputs_rentas(
        fecha_nacimiento=fecha_nacimiento,
        fecha_baja=fecha_baja,
        salario_fijo_anual=salario_fijo_anual,
        bonus_target_anual=bonus_target_anual,
        complementos=complementos,
        pct_renta_hasta_63=pct_renta_hasta_63,
        pct_renta_hasta_65=pct_renta_hasta_65,
        pct_reval_desde_63=pct_reval_desde_63,
        num_hijos=num_hijos
    )


    # --- NUEVO: resolución de modalidad / overrides ---
    if modalidad.upper() not in {"ERE", "PSI"}:
        raise ValueError("modalidad debe ser 'ERE' o 'PSI'.")

    # Valores por defecto según modalidad
    _defaults = {
        "ERE":  dict(tiene_SEPE=True,  exencion_180k=True,  exencion_irreg=True),
        "PSI":  dict(tiene_SEPE=False, exencion_180k=False, exencion_irreg=False),
    }[modalidad.upper()]

    tiene_SEPE = _defaults["tiene_SEPE"]
    aplica_exencion_despido_180k = _defaults["exencion_180k"]
    aplica_exencion_rentas_irregulares = _defaults["exencion_irreg"]

    if verbose:
        print(f"[0.1] Modalidad: {modalidad} | SEPE: {'Sí' if tiene_SEPE else 'No'} | "
            f"Exención 180k: {'Sí' if aplica_exencion_despido_180k else 'No'} | "
            f"Exención rentas irreg.: {'Sí' if aplica_exencion_rentas_irregulares else 'No'}")

    if verbose:
        print("\n[0] RENTAS HASTA 65 - Preparación")

    dfm = df_bases_mensuales.copy()
    dfm['fecha'] = pd.to_datetime(dfm['fecha']).dt.to_period('M').dt.to_timestamp()

    salario_regulador_anual = (
        float(salario_fijo_anual) + float(bonus_target_anual) +
        float(incentivos_comerciales) + float(complementos) +
        float(incentivos) + float(retribucion_tiempo) + 
        float(gratificacion) + float(otros_conceptos)
    )
    salario_regulador_mensual = salario_regulador_anual / 12.0
    
    fecha_63 = fecha_nacimiento + relativedelta(years=63)
    fecha_65 = fecha_nacimiento + relativedelta(years=65)

	# --- Cálculo anticipado para linealidad (salario revalorizado a 63 y % lineal) ---
	# Se calcula aquí para que esté disponible en prestación y posprestación
    anios_hasta_63 = max(0, relativedelta(_month_start(fecha_63), _month_start(fecha_baja)).years)
    salario_regulador_reval_a_63_anual = salario_regulador_anual * ((1.0 + pct_reval_desde_63) ** anios_hasta_63)
    salario_regulador_reval_a_63_mensual = salario_regulador_reval_a_63_anual / 12.0

    # Validación y cómputo del tramo de linealidad (opcional)
    _aplica_lineal = bool(aplicar_linealidad)
    _edad_inicio_linealidad = edad_inicio_linealidad
    pct_lineal_hasta_63 = None
    pct_lineal_hasta_65 = None
    inicio_linealidad_mes = None
    meses_lineal_hasta_63 = 0
    meses_lineal_desde_63 = 0

    if _aplica_lineal:
        if _edad_inicio_linealidad is None:
            raise ValueError("Debe informar 'edad_inicio_linealidad' cuando 'aplicar_linealidad' es True.")
        # Regla: X no > 63 ni < edad en baja
        edad_baja = relativedelta(fecha_baja, fecha_nacimiento).years
        if not (edad_baja <= _edad_inicio_linealidad <= 63):
            raise ValueError("edad_inicio_linealidad (X) debe estar entre la edad en la baja y 63 años.")
        # La linealidad arranca el MES SIGUIENTE al cumpleaños de X
        fecha_X = fecha_nacimiento + relativedelta(years=_edad_inicio_linealidad)
        inicio_linealidad_mes = _add_months(_month_start(fecha_X), 1)

        # Helper: contar meses (inclusive)
        def _count_months_inclusive(mstart, mend):
            if mstart is None or mend is None:
                return 0
            if _month_start(mstart) > _month_start(mend):
                return 0
            return (mend.year - mstart.year) * 12 + (mend.month - mstart.month) + 1

        # m1: meses en linealidad ANTES de 63 (desde inicio_linealidad_mes hasta mes anterior a 63)
        #m1_end = _add_months(_month_start(fecha_63), -1)
        # m1: meses en linealidad ANTES de 63 (desde inicio_linealidad_mes hasta mes de 63)
        m1_end = _add_months(_month_start(fecha_63), 0)

        meses_lineal_hasta_63 = _count_months_inclusive(inicio_linealidad_mes, m1_end)
        if inicio_linealidad_mes and inicio_linealidad_mes > m1_end:
            meses_lineal_hasta_63 = 0

        # m2: meses en linealidad DESDE 63 hasta el fin (normalmente 65), ambos inclusive
        #inicio_m2 = max(inicio_linealidad_mes, _month_start(fecha_63)) if inicio_linealidad_mes else None

        # m2: meses en linealidad DESDE mes siguiente a 63 hasta el fin (normalmente 65)
        inicio_m2 = max(inicio_linealidad_mes, _month_start(_add_months(_month_start(fecha_63), 1))) if inicio_linealidad_mes else None

        meses_lineal_desde_63 = _count_months_inclusive(inicio_m2, _month_start(fecha_65))

        total_meses_lineal = meses_lineal_hasta_63 + meses_lineal_desde_63
        if total_meses_lineal > 0:
            num_lineal = (
                pct_renta_hasta_63 * meses_lineal_hasta_63 * salario_regulador_mensual +
                pct_renta_hasta_65 * meses_lineal_desde_63 * salario_regulador_reval_a_63_mensual
            )
            den_h63 = total_meses_lineal * salario_regulador_mensual
            den_h65 = total_meses_lineal * salario_regulador_reval_a_63_mensual
            pct_lineal_hasta_63 = num_lineal / den_h63 if den_h63 else None
            pct_lineal_hasta_65 = num_lineal / den_h65 if den_h65 else None
        else:
            # Si el tramo de linealidad no intersecta el periodo de rentas, desactivar
            _aplica_lineal = False
            pct_lineal_hasta_63 = None
            pct_lineal_hasta_65 = None
            inicio_linealidad_mes = None

    # --- Normalización de fechas de jubilación (compatibilidad) ---
    if fecha_jubilacion_anticipada is None:
        fecha_jubilacion_anticipada = fecha_63  # legacy: anticipada=63
    if fecha_jubilacion_ordinaria is None:
        fecha_jubilacion_ordinaria = fecha_65  # legacy: ordinaria=65

    # Truncamos a inicio de mes para comparativas coherentes con el resto del módulo
    f63 = _month_start(fecha_63)
    f65 = _month_start(fecha_65)
    fant = _month_start(fecha_jubilacion_anticipada)
    ford = _month_start(fecha_jubilacion_ordinaria)
    f_umbral24 = _add_months(ford, -24)

    # --- Validaciones de sentido por reglas de negocio ---
    if ford < f65:
        raise ValueError("La edad ordinaria de jubilación no puede ser inferior a 65 años.")
    if fant < f63:
        raise ValueError("La jubilación anticipada no puede ser inferior a 63 años.")
    if ford > f65 and fant < f_umbral24:
        raise ValueError("La jubilación anticipada no puede ser anterior a (jubilación ordinaria - 24 meses).")

    # --- [1] Tablas auxiliares (igual que antes) ---
    #RUTA_PRESTACION_MINMAX: str = "prestacion_contributiva_espana.txt"
    #RUTA_DURACION_PRESTACION: str = "duracion_prestacion_contributiva.txt"
       
    df_minmax = pd.DataFrame(); df_dur = pd.DataFrame()
    if tiene_SEPE:
        if verbose:
            print("[1] Leyendo tablas auxiliares de PRESTACIÓN (Min/Max y Duración)")
        #RUTA_PRESTACION_MINMAX: str = "prestacion_contributiva_espana.txt"
        #RUTA_DURACION_PRESTACION: str = "duracion_prestacion_contributiva.txt"
        df_minmax = _read_table(RUTA_PRESTACION_MINMAX)
        df_dur = _read_table(RUTA_DURACION_PRESTACION)
        df_minmax.columns = [c.strip() for c in df_minmax.columns]
        df_dur.columns = [c.strip() for c in df_dur.columns]
    else:
        if verbose:
            print("[1] (PSI) Sin tablas de prestación: SEPE desactivado")


    # --- [2] Inicio prestación / base reguladora (sólo si hay SEPE) ---
    if tiene_SEPE:
        if verbose:
            print("[2] Ajustando inicio de prestación y base reguladora (media 6 meses)")
        inicio_prest = _nearest_month_start(fecha_baja)


        # 1. Identificar los 6 meses anteriores a la fecha de baja
        meses_anteriores = [fecha_baja - relativedelta(months=i) for i in range(1, 7)]

        suma_bases_6_meses = 0.0
        total_dias_reales = 0

        for mes_dt in meses_anteriores:
            # Obtener la base de cotización de ese mes desde el DataFrame de entrada
            # Usamos la función auxiliar _get_base_mensual_para_mes que ya existe en el script
            base_mes = _get_base_mensual_para_mes(df_bases_mensuales, mes_dt)
            
            # Calcular días reales del mes (ej: Septiembre=30, Octubre=31, Febrero=28...)
            # calendar.monthrange devuelve (dia_semana_inicio, numero_dias)
            dias_en_mes = calendar.monthrange(mes_dt.year, mes_dt.month)[1]
            
            suma_bases_6_meses += base_mes
            total_dias_reales += dias_en_mes

        # 2. Calcular base diaria según tu fórmula: (Suma Bases) / (Suma Días Reales)
        base_paro_diaria = suma_bases_6_meses / total_dias_reales

        # 3. Calcular la base mensual estándar para el paro: base diaria * 365 / 12 (media anualizada)
        base_paro_mensual = base_paro_diaria * 365 / 12

    else:
        inicio_prest = None
        base_paro_mensual = 0.0

    # --- [3] Días cotizados previos y duración (sólo si hay SEPE) ---
    if tiene_SEPE:
        # --- [3] Días cotizados previos y duración (igual) ---
        if dias_cotizados_previos is None:
            ventana_ini = _add_months(_month_start(inicio_prest), -72)
            df6 = dfm[(dfm['fecha'] >= ventana_ini) & (dfm['fecha'] < _month_start(inicio_prest))].copy()
            meses_cotizados_6a = int((df6['base'] > 0).sum())
            dias_cotizados_previos = meses_cotizados_6a * 30

        dur_cols = list(df_dur.columns)
        col_min = [c for c in dur_cols if 'min' in c.lower()][0]
        col_max = [c for c in dur_cols if 'max' in c.lower()][0]
        col_prest= [c for c in dur_cols if 'prest' in c.lower()][0]
        df_dur[col_max] = pd.to_numeric(df_dur[col_max], errors='coerce')
        df_dur[col_min] = pd.to_numeric(df_dur[col_min], errors='coerce')
        df_dur[col_prest]= pd.to_numeric(df_dur[col_prest], errors='coerce')

        fila = None
        for _, r in df_dur.iterrows():
            mn = int(r[col_min]) if not pd.isna(r[col_min]) else None
            mx = int(r[col_max]) if not pd.isna(r[col_max]) else None
            if (mn is None or dias_cotizados_previos >= mn) and (mx is None or dias_cotizados_previos <= mx):
                fila = r; break
        if fila is None:
            fila = df_dur.iloc[-1]
        dias_prestacion = int(fila[col_prest])        
    else:
        dias_prestacion = 0

    # --- [4] Mínimos y máximos mensuales por número de hijos ---
    if verbose:
        print("[4] Mínimos y máximos mensuales por número de hijos")

    # Si NO hay SEPE (p.ej. PSI), no aplicamos mínimos/máximos de prestación.
    # Dejamos min_mensual y max_mensual a 0 para que no capen nada más adelante.
    if not tiene_SEPE:
        df_min = pd.DataFrame()
        df_max = pd.DataFrame()
        min_mensual = 0.0
        max_mensual = 0.0
    else:
        df_min = df_minmax[df_minmax['Tipo'].str.strip().str.lower() == 'minima'].copy()
        df_max = df_minmax[df_minmax['Tipo'].str.strip().str.lower() == 'maxima'].copy()

        def _valor_min_mensual(num_hijos: int) -> float:
            if num_hijos > 0:
                fila = df_min[df_min['Situacion'].str.contains('hijo', case=False) &
                            df_min['Situacion'].str.contains('con', case=False)]
            else:
                fila = df_min[df_min['Situacion'].str.contains('sin', case=False)]
            if fila.empty:
                raise ValueError("No se encontraron valores mínimos en la tabla de prestación (minima).")
            return float(fila['Importe'].iloc[0])

        def _valor_max_mensual(num_hijos: int) -> float:
            if num_hijos <= 0:
                fila = df_max[df_max['Situacion'].str.contains('sin', case=False)]
            elif num_hijos == 1:
                fila = df_max[df_max['Situacion'].str.contains('1', case=False)]
            else:
                fila = df_max[df_max['Situacion'].str.contains('2', case=False)]
            if fila.empty:
                raise ValueError("No se encontraron valores máximos en la tabla de prestación (maxima).")
            return float(fila['Importe'].iloc[0])

        min_mensual = _valor_min_mensual(num_hijos)
        max_mensual = _valor_max_mensual(num_hijos)

    # --- helper CESS por mes ---
    def _cess_por_mes(dt: datetime) -> float:
        base_mes = _get_base_mensual_para_mes(dfm, dt)
        mei = PCT_MEI_2023 if _month_start(dt) >= _month_start(MEI_START) else 0.0
        return round(base_mes * (PCT_CESS_EMPRESA * COEF_REDUCTOR_CESS + mei), 2)
    
    # --- helper CESS prorrateado (0..1)
    def _cess_prorrateado(dt: datetime, factor: float) -> float:
        factor = max(0.0, min(1.0, float(factor)))
        return round(_cess_por_mes(dt) * factor, 2)

    # --- [5] Prestación por MESES (+ comp. SS empresa 4,70%) ---
    detalle_paro = []
    mes_idx = 1
    if tiene_SEPE:
        if verbose:
            print("[5] Construyendo calendario de PRESTACIÓN (+comp. SS 4,70%)")
        if verbose:
            print("[5] Construyendo calendario de prestación POR MESES (70%/60%) + compensación SS (4,70%)")
        #detalle_paro = []
        remaining_days = dias_prestacion
        #mes_idx = 1
        cursor = _month_start(inicio_prest)

        while remaining_days > 0:
            dias_mes_cubiertos = min(30, remaining_days)
            pct = 0.70 if mes_idx <= 6 else 0.60  # Porcentaje según mes de prestación
            bruto_mes = pct * base_paro_mensual * (dias_mes_cubiertos / 30.0)
            min_cap_mes = min_mensual * (dias_mes_cubiertos / 30.0)
            max_cap_mes = max_mensual * (dias_mes_cubiertos / 30.0)
            prestacion_mes = min(max(bruto_mes, min_cap_mes), max_cap_mes)

            # Cálculo de la renta indemnizatoria objetivo hasta 63 años (con linealidad opcional)
            renta_indemn_obj_63 = (
                pct_lineal_hasta_63 * salario_regulador_mensual
            ) if (_aplica_lineal and _month_start(cursor) >= _month_start(inicio_linealidad_mes)) else (
                pct_renta_hasta_63 * salario_regulador_mensual
            )

            complemento_empresa = max(0.0, renta_indemn_obj_63 - prestacion_mes)

            # Compensación SS empresa (SEPE) 4,70%
            comp_paro_ss_empresa = PCT_COMP_SEPE_SS_EMPRESA * base_paro_mensual * (dias_mes_cubiertos / 30.0)

            # --- Aportación CESS hasta 63 ---
            aportacion_cess = 0.0  #durante la prestación no hay aportación al CESS

            detalle_paro.append({
                'Mes': cursor.strftime("%Y-%m"),
                'fecha_mes': cursor,
                'mes_idx': mes_idx,
                'dias_cubiertos': dias_mes_cubiertos,
                'porcentaje': pct,
                'base_paro_mensual': round(base_paro_mensual, 2),
                'prestacion_mes': round(prestacion_mes, 2),
                'renta_indemn_obj_63': round(renta_indemn_obj_63, 2),
                'complemento_empresa_63': round(complemento_empresa, 2),
                'comp_paro_ss_empresa': round(comp_paro_ss_empresa, 2),
                'Aportación_CESS': round(aportacion_cess, 2),
                'total_mes_63': round(prestacion_mes + complemento_empresa, 2)
            })
            remaining_days -= dias_mes_cubiertos
            cursor = _add_months(cursor, 1)
            mes_idx += 1

        df_detalle_paro = pd.DataFrame(detalle_paro)

    else:
        if verbose:
            print("[5] (PSI) Sin prestación contributiva del SEPE")
    
    df_detalle_paro = pd.DataFrame(detalle_paro)

    # --- [6] POSPRESTACIÓN hasta 63 (igual + CESS) ---
    if verbose:
        print("[6] Posprestación hasta 63 (mes 63 prorrateado: 1..(día-1))")

    if tiene_SEPE and inicio_prest is not None:
        fin_prest_absoluto = _add_months(_month_start(inicio_prest), (len(df_detalle_paro) if not df_detalle_paro.empty else 0))
        inicio_posprest = _month_start(fin_prest_absoluto)
    else:
        # PSI: posprestación arranca desde la baja
        fin_prest_absoluto = _month_start(fecha_baja)
        inicio_posprest = _month_start(fecha_baja)

    fin_posprest_63 = f63

    detalle_posprest = []
    # Mes 63: prorrateo desde el día 63 hasta fin de mes, tanto en renta como en CESS. El mes se incluye completo si 
    # el día 63 es el primer día del mes.
    if inicio_posprest <= fin_posprest_63:
        cur = inicio_posprest
        while cur <= fin_posprest_63:
            dias_mes = calendar.monthrange(cur.year, cur.month)[1]
            # % aplicable este mes: lineal si aplica y el mes >= inicio_linealidad; si no, % hasta 63
            pct_mes = pct_lineal_hasta_63 if (_aplica_lineal and inicio_linealidad_mes and _month_start(cur) >= 
                                    _month_start(inicio_linealidad_mes)) else pct_renta_hasta_63
            renta_base_mes = pct_mes * salario_regulador_mensual
            if cur.year == fecha_63.year and cur.month == fecha_63.month:
                dias_prorr = max(0, fecha_63.day - 1)
                factor = (dias_prorr / dias_mes) if dias_mes else 0.0
                renta_mes = renta_base_mes * factor
                # PRORRATEO CESS EN EL ÚLTIMO MES HASTA 63
                aportacion_cess = _cess_prorrateado(cur, factor)
            else:
                renta_mes = renta_base_mes
                # Mes anterior a 63 -> CESS 100%
                aportacion_cess = _cess_por_mes(cur)
        
            detalle_posprest.append({
                'Mes': cur.strftime("%Y-%m"),
                'fecha_mes': cur,
                'renta_indemn_63': round(renta_mes, 2),
                'Aportación_CESS': round(aportacion_cess, 2),
                'total_mes_63': round(renta_mes, 2),
                'mes_idx': mes_idx,
            })
            cur = _add_months(cur, 1)
            mes_idx += 1

    df_detalle_posprest_63 = pd.DataFrame(detalle_posprest)

    # --- [7] 63–65 (pensión 12 reval 1-Ene + IPC, salario fijo a 63, prorrateos) ---
    if verbose:
        print("[7] 63–65 con prorrateos (mes 63 incluye el día; mes 65 renta 1..(día-1), pensión completa)")

    # Salario regulador mensual FIJO 63–65 (revalorizado solo hasta cumplir 63)
    anios_hasta_63 = max(0, relativedelta(_month_start(fecha_63), _month_start(fecha_baja)).years)
    salario_regulador_reval_a_63_anual = salario_regulador_anual * ((1.0 + pct_reval_desde_63) ** anios_hasta_63)
    salario_regulador_reval_a_63_mensual = salario_regulador_reval_a_63_anual / 12.0

    base_pension_12 = (pension_bruta_mensual_14pagas * (14.0 / 12.0)) if pension_bruta_mensual_14pagas else 0.0

    def _pension_12_reval_for_month(mref: datetime) -> float:
        n = max(0, mref.year - fecha_63.year)
        return base_pension_12 * ((1.0 + PENSION_REVAL_IPC_OBJ) ** n)

    detalle_63_65 = []
    cur = f63
    fin_63_65 = f65  # incluye mes 65
    while cur <= fin_63_65:
        dias_mes = calendar.monthrange(cur.year, cur.month)[1]

        # % renta este mes: lineal si aplica y el mes >= inicio_linealidad; si no, % hasta 65 
        # (sobre salario revalorizado a 63)
        renta_mes_full = ( (pct_lineal_hasta_65 if (_aplica_lineal and inicio_linealidad_mes and _month_start(cur) >= _month_start(inicio_linealidad_mes)) 
                            else pct_renta_hasta_65) * salario_regulador_reval_a_63_mensual )

        pension_12_full = _pension_12_reval_for_month(cur)

        # --- REGLA GLOBAL: la pensión sólo se cobra después de la jubilación anticipada ---
        # Calculamos primero la renta como hasta ahora, y luego imponemos la regla sobre la pensión
        # (con prorrateo específico si el mes coincide con la anticipada)

        # 1) Renta: prorrateos existentes por mes 63 y 65 (igual que en tu código)
        if cur.year == fecha_63.year and cur.month == fecha_63.month:
            # Mes 63: renta desde el día 63 hasta fin de mes
            dias_prorr = max(0, dias_mes - (fecha_63.day - 1))
            factor_renta = (dias_prorr / dias_mes) if dias_mes else 0.0
            renta_mes = renta_mes_full * factor_renta
            mes_idx -= 1  # no incrementar en este mes prorrateado
        elif cur.year == fecha_65.year and cur.month == fecha_65.month:
            # Mes 65: renta 1..(día-1)
            dias_prorr = max(0, fecha_65.day - 1)
            factor_renta = (dias_prorr / dias_mes) if dias_mes else 0.0
            renta_mes = renta_mes_full * factor_renta
        else:
            renta_mes = renta_mes_full

        # 2) Pensión: aplicar "sólo tras anticipada" + prorrateo en el mes de la anticipada
        if _month_start(cur) < fant:
            # Antes de la anticipada: pensión = 0
            pension_mes = 0.0
        elif (cur.year == fecha_jubilacion_anticipada.year) and (cur.month == fecha_jubilacion_anticipada.month):
            # Mes de la anticipada: pensión desde el día de la anticipada hasta fin de mes
            dias_prorr_p = max(0, dias_mes - (fecha_jubilacion_anticipada.day - 1))
            factor_pension = (dias_prorr_p / dias_mes) if dias_mes else 0.0
            pension_mes = pension_12_full * factor_pension
        else:
            # Mes posterior a la anticipada: pensión completa del mes (12 pagas homog.)
            pension_mes = pension_12_full

        # --- Política por tramos (aporta CESS / pensión sí/no)
        aportacion_cess = 0.0

        if ford == f65:
            # Caso A) ordinaria = 65
            if fant == f63:
                # A.1) anticipada = 63 -> mantener cálculos (sin CESS adicional en 63–65)
                pass
            elif fant > f63:
                # A.2) anticipada > 63
                # Corrección: el mes en que se cumplen 63 ya está contemplado en el tramo anterior, 
                # por lo que la condición se ajusta a > f63 (no >=)
                if cur > f63 and cur < fant:
                    # 63 -> anticipada: renta + pensión + CESS SÍ
                    aportacion_cess = _cess_por_mes(cur)
                else:
                    # anticipada -> 65: renta + pensión; CESS = 0
                    pass
        else:
            # Caso B) ordinaria > 65
            if cur < f_umbral24:
                # 63 -> (ordinaria - 24): renta; SIN pensión; CESS SÍ
                pension_mes = 0.0
                if cur > f63:  # El mes en que se cumplen 63 ya está contemplado en el tramo anterior, 
                               # por lo que se añade esta condición para no aplicar CESS en ese mes
                    aportacion_cess = _cess_por_mes(cur)
            elif cur >= f_umbral24 and cur < fant:
                # (ordinaria - 24) -> anticipada: renta; SIN pensión; CESS = 0
                pension_mes = 0.0
            elif cur >= fant and cur < f65:
                # anticipada -> 65: renta + pensión; CESS = 0
                pass
            elif cur >= f65:
                # El tramo 65 -> ordinaria se añadirá aparte (df_65_ord), aquí solo cerramos 63–65
                pass

        total_mes = renta_mes + pension_mes
        detalle_63_65.append({
            'Mes': cur.strftime("%Y-%m"),
            'fecha_mes': cur,
            'pension_12': round(pension_mes, 2),
            'renta_indemn_65': round(renta_mes, 2),
            'Aportación_CESS': round(aportacion_cess, 2),
            'total_mes_63_65': round(total_mes, 2),
            'mes_idx': mes_idx
        })
        cur = _add_months(cur, 1)
        mes_idx += 1  # Incrementar normalmente

    df_detalle_63_65 = pd.DataFrame(detalle_63_65)

    # --- Tramo 65 -> ordinaria (solo pensión) si ordinaria > 65 ---
    detalle_65_ord = []
    if ford > f65:
        cur = _add_months(f65, 1)  # mes siguiente al 65
        while cur < ford:
            pension_mes = _pension_12_reval_for_month(cur)
            detalle_65_ord.append({
                'Mes': cur.strftime("%Y-%m"),
                'fecha_mes': cur,
                'pension_12': round(pension_mes, 2),
                'renta_indemn_65': 0.0,
                'Aportación_CESS': 0.0,
                'total_mes_63_65': round(pension_mes, 2),  # solo pensión
                'mes_idx': mes_idx
            })
            cur = _add_months(cur, 1)
            mes_idx += 1
    df_65_ord = pd.DataFrame(detalle_65_ord)

    # --- [7.9] UNIFICACIÓN EN UNA SOLA TABLA (Detalle_Rentas) ---
    def _ensure_col(df, col, val=0.0):
        if col not in df.columns:
            df[col] = val
        return df

    # Prestación
    df_prest = df_detalle_paro.copy()
    if not df_prest.empty:
        df_prest['tramo'] = 'Prestación'
        for c in ['prestacion_mes','renta_indemn_obj_63','complemento_empresa_63',
                  'comp_paro_ss_empresa','renta_indemn_63','renta_indemn_65',
                  'pension_12','Aportación_CESS']:
            df_prest = _ensure_col(df_prest, c, 0.0)
        df_prest['total'] = df_prest['prestacion_mes'] + df_prest['complemento_empresa_63']
    else:
        df_prest = pd.DataFrame(columns=[
            'tramo','Mes','fecha_mes','prestacion_mes','renta_indemn_obj_63','complemento_empresa_63',
            'comp_paro_ss_empresa','renta_indemn_63','renta_indemn_65','pension_12','Aportación_CESS','total'
        ])

    # Posprestación
    df_pos = df_detalle_posprest_63.copy()
    if not df_pos.empty:
        df_pos['tramo'] = 'Posprestación hasta 63'
        df_pos = _ensure_col(df_pos, 'prestacion_mes', 0.0)

        # --- aplicar linealidad a 'renta_indemn_obj_63' en posprestación (por mes) ---
        if not df_pos.empty and 'fecha_mes' in df_pos.columns:
            def _renta_obj_63_for_row(dt: datetime) -> float:
                # mismo criterio de % que en el cálculo de 'renta_indemn_63'
                usa_lineal = (_aplica_lineal and inicio_linealidad_mes 
                            and _month_start(dt) >= _month_start(inicio_linealidad_mes))
                pct_mes = (pct_lineal_hasta_63 if usa_lineal else pct_renta_hasta_63)
                return round(pct_mes * salario_regulador_mensual, 2)

            df_pos['renta_indemn_obj_63'] = df_pos['fecha_mes'].apply(_renta_obj_63_for_row)
        else:
            # fallback (caso sin fechas): mantener el valor constante como hasta ahora
            salario_regulador_anual_aux = (float(salario_fijo_anual) + float(bonus_target_anual) + 
                                        float(incentivos_comerciales) + float(complementos) +
                                        float(incentivos) + float(retribucion_tiempo) + float(otros_conceptos))
            renta_indemn_obj_63_val = pct_renta_hasta_63 * (salario_regulador_anual_aux / 12.0)
            df_pos = _ensure_col(df_pos, 'renta_indemn_obj_63', round(renta_indemn_obj_63_val, 2))

        df_pos = _ensure_col(df_pos, 'complemento_empresa_63', 0.0)
        df_pos = _ensure_col(df_pos, 'comp_paro_ss_empresa', 0.0)
        df_pos = _ensure_col(df_pos, 'renta_indemn_63', df_pos['total_mes_63'] if 'total_mes_63' in df_pos.columns else 0.0)
        df_pos = _ensure_col(df_pos, 'renta_indemn_65', 0.0)
        df_pos = _ensure_col(df_pos, 'pension_12', 0.0)
        df_pos = _ensure_col(df_pos, 'Aportación_CESS', df_pos['Aportación_CESS'] if 'Aportación_CESS' in df_pos.columns else 0.0)
        df_pos['total'] = df_pos['renta_indemn_63']
    else:
        df_pos = pd.DataFrame(columns=[
            'tramo','Mes','fecha_mes','prestacion_mes','renta_indemn_obj_63','complemento_empresa_63',
            'comp_paro_ss_empresa','renta_indemn_63','renta_indemn_65','pension_12','Aportación_CESS','total'
        ])

    # 63-65 y (si procede) 65->Ordinaria
    df_6365 = pd.concat([df_detalle_63_65, df_65_ord], ignore_index=True) if not df_65_ord.empty else df_detalle_63_65.copy()
    if not df_6365.empty:
        # etiquetar tramo: se separa 63-65 y 65->Ordinaria para claridad
        df_6365['tramo'] = np.where(df_6365['fecha_mes'] < f65, '63-65', 
                              np.where(df_6365['fecha_mes'] == f65, '63-65',
                                np.where(df_6365['fecha_mes'] > f65, '65->Ordinaria', '63-65')))
        for c in ['prestacion_mes','renta_indemn_obj_63','complemento_empresa_63','comp_paro_ss_empresa','renta_indemn_63','Aportación_CESS']:
            df_6365 = _ensure_col(df_6365, c, 0.0)
        df_6365 = _ensure_col(df_6365, 'renta_indemn_65', df_6365['renta_indemn_65'] if 'renta_indemn_65' in df_6365.columns else 0.0)
        df_6365 = _ensure_col(df_6365, 'pension_12', df_6365['pension_12'] if 'pension_12' in df_6365.columns else 0.0)
        df_6365['total'] = df_6365['pension_12'] + df_6365['renta_indemn_65']
    else:
        df_6365 = pd.DataFrame(columns=[
            'tramo','Mes','fecha_mes','prestacion_mes','renta_indemn_obj_63','complemento_empresa_63',
            'comp_paro_ss_empresa','renta_indemn_63','renta_indemn_65','pension_12','Aportación_CESS','total'
        ])

    # Unión final
    df_detalle_unico = pd.concat([df_prest, df_pos, df_6365], ignore_index=True)
    if 'fecha_mes' in df_detalle_unico.columns:
        df_detalle_unico = df_detalle_unico.sort_values('fecha_mes', ascending=True).reset_index(drop=True)

    cols_oblig = [
        'tramo','Mes','Edad','fecha_mes',
        'prestacion_mes','renta_indemn_obj_63','complemento_empresa_63',
        'comp_paro_ss_empresa',
        'renta_indemn_63','renta_indemn_65','pension_12','Aportación_CESS','total'
    ]
    cols_final = [c for c in cols_oblig if c in df_detalle_unico.columns]
    cols_extra = [c for c in df_detalle_unico.columns if c not in cols_final]
    df_detalle_unico = df_detalle_unico[cols_final + cols_extra]

    # --- [7.95] Añadir columna 'Edad' (años cumplidos en cada 'fecha_mes') y colocarla detrás de 'Mes' ---
    if 'fecha_mes' in df_detalle_unico.columns:
        df_detalle_unico['Edad'] = df_detalle_unico['fecha_mes'].apply(lambda d: relativedelta(d, fecha_nacimiento).years)

    # --- [8] RESÚMENES (añadir sumas CESS) ---
    if verbose:
        print("[8] Preparando resúmenes")

    # Compensación SS empresa (SEPE)
    sum_comp_ss_empresa_total = float(df_detalle_paro['comp_paro_ss_empresa'].sum()) if not df_detalle_paro.empty else 0.0
    df_paro_hasta_63 = df_detalle_paro[df_detalle_paro['fecha_mes'] < f63].copy() if not df_detalle_paro.empty else pd.DataFrame()
    sum_comp_ss_empresa_hasta_63 = float(df_paro_hasta_63['comp_paro_ss_empresa'].sum()) if not df_paro_hasta_63.empty else 0.0

    # Prestación y complementos
    sum_prest_total = float(df_detalle_paro['prestacion_mes'].sum()) if not df_detalle_paro.empty else 0.0
    sum_comp_total = float(df_detalle_paro['complemento_empresa_63'].sum()) if not df_detalle_paro.empty else 0.0
    sum_total_durante_prest_total = float(df_detalle_paro['total_mes_63'].sum()) if not df_detalle_paro.empty else 0.0

    # (Incluir comp. SS en "Total durante prestación", como ya hacías)
    sum_total_durante_prest_total += sum_comp_ss_empresa_total

    sum_prest_hasta_63 = float(df_paro_hasta_63['prestacion_mes'].sum()) if not df_paro_hasta_63.empty else 0.0
    sum_comp_hasta_63 = float(df_paro_hasta_63['complemento_empresa_63'].sum()) if not df_paro_hasta_63.empty else 0.0
    sum_total_durante_prest_hasta_63 = float(df_paro_hasta_63['total_mes_63'].sum()) if not df_paro_hasta_63.empty else 0.0

    # Posprestación
    sum_posprest_63 = float(df_detalle_posprest_63['total_mes_63'].sum()) if not df_detalle_posprest_63.empty else 0.0

    # --- Sumas CESS (empresa) ---
    sum_cess_total = float(df_detalle_unico['Aportación_CESS'].sum()) if 'Aportación_CESS' in df_detalle_unico.columns else 0.0
    sum_cess_hasta_63 = float(df_detalle_unico.loc[df_detalle_unico['fecha_mes'] < f63, 'Aportación_CESS'].sum()) if 'Aportación_CESS' in df_detalle_unico.columns else 0.0
    sum_cess_63_65 = float(df_detalle_unico.loc[(df_detalle_unico['fecha_mes'] >= f63) & (df_detalle_unico['fecha_mes'] <= f65), 'Aportación_CESS'].sum()) if 'Aportación_CESS' in df_detalle_unico.columns else 0.0

    # Total hasta 63 (paro + posprestación + comp. SS total)
    sum_total_hasta_63 = sum_total_durante_prest_hasta_63 + sum_posprest_63 + sum_comp_ss_empresa_total

    # 63–65
    sum_pension_63_65 = float(df_detalle_63_65['pension_12'].sum()) if not df_detalle_63_65.empty else 0.0
    sum_renta_65 = float(df_detalle_63_65['renta_indemn_65'].sum()) if not df_detalle_63_65.empty else 0.0
    sum_total_63_65 = float(df_detalle_63_65['total_mes_63_65'].sum()) if not df_detalle_63_65.empty else 0.0

    # --- [9] EXCEL ---

    df_entrada = pd.DataFrame([
        {"Parámetro": "Modalidad", "Valor": modalidad},
        {"Parámetro": "Prestación SEPE activa", "Valor": "Sí" if tiene_SEPE else "No"},
        {"Parámetro": "Exención despido 180k", "Valor": "Sí" if aplica_exencion_despido_180k else "No"},
        {"Parámetro": "Exención rentas irregulares", "Valor": "Sí" if aplica_exencion_rentas_irregulares else "No"},
        {"Parámetro": "Fecha de nacimiento", "Valor": fecha_nacimiento.date()},
        {"Parámetro": "Fecha de baja (original)", "Valor": fecha_baja.date()},
        {"Parámetro": "Inicio prestación (día 1 más cercano)", "Valor": inicio_prest.date() if (tiene_SEPE and inicio_prest is not None) else "N/D"},
        {"Parámetro": "Número de hijos", "Valor": num_hijos},
        {"Parámetro": "Salario fijo anual", "Valor": round(salario_fijo_anual, 2)},
        {"Parámetro": "Bonus target anual", "Valor": round(bonus_target_anual, 2)},
        {"Parámetro": "Incentivos comerciales anuales", "Valor": round(incentivos_comerciales, 2)},
        {"Parámetro": "Incentivos anuales", "Valor": round(incentivos, 2)},
        {"Parámetro": "Retribución tiempo", "Valor": round(retribucion_tiempo, 2)},
        {"Parámetro": "Gratificación", "Valor": round(gratificacion, 2)},
        {"Parámetro": "Otros conceptos", "Valor": round(otros_conceptos, 2)},
        {"Parámetro": "Complementos anuales", "Valor": round(complementos, 2)},
        {"Parámetro": "% Renta hasta 63", "Valor": f"{pct_renta_hasta_63*100:.2f}%"},
        {"Parámetro": "% Renta hasta 65", "Valor": f"{pct_renta_hasta_65*100:.2f}%"},
        {"Parámetro": "% Reval. anual hasta 63", "Valor": f"{pct_reval_desde_63*100:.2f}%"},
        {"Parámetro": "Pensión bruta mensual (14 pagas)", "Valor": round(pension_bruta_mensual_14pagas, 2) if pension_bruta_mensual_14pagas is not None else "N/D"},
        {"Parámetro": "Fecha Jubilación Anticipada", "Valor": fant.date()},
        {"Parámetro": "Fecha Jubilación Ordinaria", "Valor": ford.date()},
        {"Parámetro": "Aplica linealidad", "Valor": "Sí" if _aplica_lineal else "No"},
        {"Parámetro": "Edad inicio linealidad (X)", "Valor": _edad_inicio_linealidad if _aplica_lineal else "N/D"},
    ])

    df_intermedios = pd.DataFrame([
        {"Grupo": "Paro", "Campo": "Base paro mensual (media 6 meses previos)", "Valor": round(base_paro_mensual, 2)},
        {"Grupo": "Paro", "Campo": "Días cotizados previos (últ. 6 años)", "Valor": dias_cotizados_previos},
        {"Grupo": "Paro", "Campo": "Días de prestación (por tabla)", "Valor": dias_prestacion},
        {"Grupo": "Paro", "Campo": "Mínimo mensual (por hijos)", "Valor": round(min_mensual, 2)},
        {"Grupo": "Paro", "Campo": "Máximo mensual (por hijos)", "Valor": round(max_mensual, 2)},
        {"Grupo": "Paro", "Campo": "% comp. SS empresa (SEPE)", "Valor": f"{PCT_COMP_SEPE_SS_EMPRESA*100:.2f}%"},
        {"Grupo": "Salario regulador", "Campo": "Anual (12 pagas)", "Valor": round(salario_regulador_anual, 2)},
        {"Grupo": "Salario regulador", "Campo": "Mensual", "Valor": round(salario_regulador_mensual, 2)},
        {"Grupo": "Salario regulador revalorizado a 63", "Campo": "Anual (12 pagas)", "Valor": round(salario_regulador_reval_a_63_anual, 2)},
        {"Grupo": "Salario regulador revalorizado a 63", "Campo": "Mensual", "Valor": round(salario_regulador_reval_a_63_mensual, 2)},
        {"Grupo": "63-65", "Campo": "Pensión homogeneizada (12 pagas)", "Valor": round((pension_bruta_mensual_14pagas * 14/12) if pension_bruta_mensual_14pagas else 0.0, 2)},
        {"Grupo": "CESS", "Campo": "Tipo CESS empresa", "Valor": f"{PCT_CESS_EMPRESA*100:.2f}%"},
        {"Grupo": "CESS", "Campo": "Coef. reductor CESS", "Valor": f"{COEF_REDUCTOR_CESS:.2f}"},
        {"Grupo": "CESS", "Campo": "MEI (desde 2023)", "Valor": f"{PCT_MEI_2023*100:.2f}%"},
        {"Grupo": "Linealidad", "Campo": "Inicio linealidad (mes siguiente a X)", "Valor": inicio_linealidad_mes.date() if inicio_linealidad_mes else "N/D"},
        {"Grupo": "Linealidad", "Campo": "Meses linealidad hasta 63", "Valor": meses_lineal_hasta_63},
        {"Grupo": "Linealidad", "Campo": "Meses linealidad 63→fin", "Valor": meses_lineal_desde_63},
        {"Grupo": "Linealidad", "Campo": "% lineal aplicado hasta 63 (si procede)", "Valor": round(pct_lineal_hasta_63, 6) if pct_lineal_hasta_63 is not None else "N/D"},
        {"Grupo": "Linealidad", "Campo": "% lineal aplicado 63→fin (si procede)", "Valor": round(pct_lineal_hasta_65, 6) if pct_lineal_hasta_65 is not None else "N/D"},
    ])

    df_salida = pd.DataFrame([
        {"Métrica": "Prestación contributiva total (hasta agotar)", "Pagador": "SEPE", "Valor": round(sum_prest_total, 2)},
        {"Métrica": "Complemento empresa total durante prestación", "Pagador": "Empresa", "Valor": round(sum_comp_total, 2)},
        {"Métrica": "Compensación SS empresa total (SEPE)", "Pagador": "Empresa", "Valor": round(sum_comp_ss_empresa_total, 2)},
        {"Métrica": "Aportación CESS total (Empresa)", "Pagador": "Empresa", "Valor": round(sum_cess_total, 2)},  
        {"Métrica": "Total percibido DURANTE prestación (total)", "Pagador": "Varios", "Valor": round(sum_total_durante_prest_total, 2)},
        {"Métrica": "Prestación contributiva hasta 63 (sólo prestación)", "Pagador": "SEPE", "Valor": round(sum_prest_hasta_63, 2)},
        {"Métrica": "Complemento empresa hasta 63 (sólo prestación)", "Pagador": "Empresa", "Valor": round(sum_comp_hasta_63, 2)},
        {"Métrica": "Compensación SS empresa (SEPE) hasta 63", "Pagador": "Empresa", "Valor": round(sum_comp_ss_empresa_hasta_63, 2)},
        {"Métrica": "Aportación CESS hasta 63 (Empresa)", "Pagador": "Empresa", "Valor": round(sum_cess_hasta_63, 2)},
        {"Métrica": "Renta posprestación hasta 63", "Pagador": "Empresa", "Valor": round(sum_posprest_63, 2)},
        {"Métrica": "Total percibido HASTA 63 (paro + posprestación)", "Pagador": "Varios", "Valor": round(sum_total_hasta_63, 2)},
        {"Métrica": "Total pensión 63-65 (12 pagas)", "Pagador": "Seguridad Social", "Valor": round(sum_pension_63_65, 2)},
        {"Métrica": "Total renta indemnizatoria 63-65", "Pagador": "Empresa", "Valor": round(sum_renta_65, 2)},
        {"Métrica": "Aportación CESS 63-65 (Empresa)", "Pagador": "Empresa", "Valor": round(sum_cess_63_65, 2)},  
        {"Métrica": "Total percibido 63-65", "Pagador": "Varios", "Valor": round(sum_total_63_65, 2)},
        {"Métrica": "Total percibido (baja → 65)", "Pagador": "Varios", "Valor": round(sum_total_hasta_63 + sum_total_63_65, 2)},
    ])

    if export_excel:
        if verbose:
            print(f"[10] Exportando libro: {export_excel_path}")
        try:
            with pd.ExcelWriter(export_excel_path, engine='openpyxl') as writer:
                df_entrada.to_excel(writer, index=False, sheet_name='Entrada')
                df_intermedios.to_excel(writer, index=False, sheet_name='Valores usados')
                df_salida.to_excel(writer, index=False, sheet_name='Salida')
                if not df_detalle_unico.empty:
                    df_detalle_unico.to_excel(writer, index=False, sheet_name='Detalle_Rentas')
                if incluir_tablas_entrada_en_libro:
                    df_minmax.to_excel(writer, index=False, sheet_name='IN_Prest_MinMax')
                    df_dur.to_excel(writer, index=False, sheet_name='IN_Duracion_Prest')
                    dfm.sort_values('fecha').to_excel(writer, index=False, sheet_name='IN_Bases_Mensuales')
        except Exception as e:
            print(f" ! Error exportando Excel de rentas: {e}")

    resultado = {
        "Modalidad": modalidad,
        "Prestacion_SEPE_Activa": bool(tiene_SEPE),
        "Aplica_Exencion_Despido_180k": bool(aplica_exencion_despido_180k),
        "Aplica_Exencion_Rentas_Irregulares": bool(aplica_exencion_rentas_irregulares),
        "Salario Regulador (anual)": round(salario_regulador_anual, 2),
        "Base Paro Mensual (media 6 meses)": round(base_paro_mensual, 2),
        "Días prestación": dias_prestacion,
        "Prestación total (hasta agotar)": round(sum_prest_total, 2),
        "Complemento total durante prestación": round(sum_comp_total, 2),
        "Compensación SS empresa total": round(sum_comp_ss_empresa_total, 2),
        "Compensación SS empresa hasta 63": round(sum_comp_ss_empresa_hasta_63, 2),
        "Aportación CESS total": round(sum_cess_total, 2),
        "Aportación CESS hasta 63": round(sum_cess_hasta_63, 2),
        "Aportación CESS 63-65": round(sum_cess_63_65, 2),
        "Prestación hasta 63": round(sum_prest_hasta_63, 2),
        "Complemento durante prestación hasta 63": round(sum_comp_hasta_63, 2),
        "Renta posprestación hasta 63": round(sum_posprest_63, 2),
        "Total hasta 63": round(sum_total_hasta_63, 2),
        "Total pensión 63-65 (12 pagas)": round(sum_pension_63_65, 2),
        "Total renta 63-65": round(sum_renta_65, 2),
        "Total 63-65": round(sum_total_63_65, 2),
        "Total baja→65": round(sum_total_hasta_63 + sum_total_63_65, 2),
        "Ruta Excel (rentas)": export_excel_path if export_excel else None,
        # Detalles
        "Detalle_Prestacion_Mensual": df_detalle_paro,
        "Detalle_PosPrestacion_hasta_63": df_detalle_posprest_63,
        "Detalle_63_65": df_detalle_63_65,
        # Si hubo tramo 65->Ordinaria lo anexamos
        "Detalle_65_Ordinaria": df_65_ord if not df_65_ord.empty else pd.DataFrame(),
        # Para otros módulos:
        "Ultima_pension": (
            pd.concat(
                [
                    df_detalle_63_65.filter(items=['fecha_mes', 'pension_12'], axis=1),
                    df_65_ord.filter(items=['fecha_mes', 'pension_12'], axis=1)
                ],
                ignore_index=True
            )
            .dropna(subset=['fecha_mes', 'pension_12'])   # evita filas inválidas
            .sort_values('fecha_mes')['pension_12']
            .tail(1)                                      # última fila
            .astype(float)                                # por si llega como texto
            .squeeze()                                    # serie -> escalar
            if (isinstance(df_detalle_63_65, pd.DataFrame) or isinstance(df_65_ord, pd.DataFrame))
            else 0.0
        ),        
        "mes_idx": mes_idx,
        "df_detalle_rentas": df_detalle_unico,
    }
    return resultado