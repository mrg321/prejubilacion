# jubilacion.py
# -*- coding: utf-8 -*-
from typing import Optional
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from core import (EPS_TOLERANCIA_MAXIMA, RUTA_BASES_COTIZACION, RUTA_INCREMENTO_BASES_REGULADORAS, RUTA_COEFICIENTES_JAI,
                  RUTA_COEFICIENTES_JAV, RUTA_EVOLUCION_PENSION_MAXIMA, RUTA_BASES_MAXIMAS, UMBRAL_MIN_MATCH, UMBRAL_ULTIMOS_MESES,
                  RUTA_BRECHA_GENERO, RUTA_TABLA_IPC)
from core import (
    _read_table, _month_start, _month_end, _add_months,
    _validar_entradas_jubilacion, _media_180_dias_previos_para_paro
)

from core import Escenario

# ------------------------------
# Helper "puro" a nivel módulo
# ------------------------------
def _detalle_calculo_para_fecha(
    f,
    *,
    ultima_real: datetime,
    inicio_sepe: datetime,
    fin_sepe: datetime,
    inicio_ce: datetime | None = None,
    fecha_jubilacion_anticipada: datetime | None = None,
    elegible_max: bool = False,
    pct_reval_convenio: float = 0.02,
    revalorizar_convenio_en_enero: bool = True,
    pre_baja_max_mode: bool = False,
    umbral_ultimos_meses: int = 12,
    umbral_min_match: int = 12,
    fecha_corte_cess: datetime | None = None,
    aplicar_incremento_2: bool = False,
    # NUEVO:
    muestra_sepe: bool = True,
) -> str:
    """Devuelve la descripción textual del criterio usado para la base de cotización
    del mes *f*, en función del contexto de cálculo suministrado por parámetros.
    No depende de variables de cierre ni de *locals()* dentro de la función principal.
    """
    f0 = datetime(f.year, f.month, 1)

    # 1) Histórico
    if f0 <= ultima_real:
        return (
            "Valor original del histórico de bases (fichero de importación). "
            "Si valor no numérico 'p. ej. Sin base registrada' BASE MÍNIMA."
        )

    # 2) SEPE
    if muestra_sepe and (inicio_sepe <= f0 < fin_sepe):
        return ("Prestación por desempleo: base constante = media diaria de los 180 días previos "
                "(base mensual/30) * 30. Sin revalorización.")


    # 3) Convenio Especial (incluye corte CESS si procede)
    if inicio_ce is not None and f0 >= _month_start(inicio_ce):
        if (
            fecha_corte_cess is not None
            and fecha_jubilacion_anticipada is not None
            and _month_start(fecha_corte_cess) <= f0 <= _month_start(fecha_jubilacion_anticipada)
        ):
            return (
                "Fin de aportación de CESS por la Empresa. La base de cotización es "
                "la correspondiente según la pensión retributiva (no se calcula aquí)."
            )
        if elegible_max:
            return (
                f"Convenio Especial: base máxima del año. Si no hay dato del año, se proyecta desde el último "
                f"año disponible con revalorización anual del {pct_reval_convenio:.2%}."
            )
        else:
            if revalorizar_convenio_en_enero:
                return (
                    f"Convenio Especial: última base previa a la baja, revalorizada cada enero al "
                    f"{pct_reval_convenio:.2%} anual."
                )
            else:
                return (
                    f"Convenio Especial: última base previa a la baja, revalorizada por aniversario al "
                    f"{pct_reval_convenio:.2%} anual."
                )

    # 4) Pre-baja
    if pre_baja_max_mode:
        return (
            "Proyección pre-baja: BASE MÁXIMA del año (criterio activado: ≥ "
            f"{umbral_min_match}/{umbral_ultimos_meses} últimas CONOCIDAS = máximas; "
            f"si faltan años en máximas, se proyecta con {pct_reval_convenio:.2%})."
        )
    else:
        if aplicar_incremento_2:
            return (
                "Proyección pre-baja: última base real revalorizada un 2% anual "
                "por año natural desde el último año real."
            )
        else:
            return "Proyección pre-baja: igual a la última base real sin revalorización."


# --------------------------------------------------------------------
# Función principal (refactorizada para usar el helper de módulo)
# --------------------------------------------------------------------
def calcular_jubilacion_anticipada(
    fecha_nacimiento: datetime,
    fecha_jubilacion_anticipada: datetime,
    fecha_baja_ere_despido: datetime,  # fecha de baja por ERE/Despido objetivo
    # --- Parametrización de escenario ---
    modalidad: str = "ERE",                   # "ERE" | "PSI" | "OTRO"
    escenario: Optional[Escenario] = None,   # si viene, tiene prioridad sobre 'modalidad'
    tiene_prestacion_desempleo: Optional[bool] = None,  # override fino
    meses_prestacion: Optional[int] = None,             # override fino
    causa_involuntaria: Optional[bool] = None,          # override fino
    aplicar_incremento_2: bool = False,  # aplica solo en tramo pre-baja
    pct_reval_convenio: float = 0.02,  # % anual para proyectar CE o años sin máximas
    revalorizar_convenio_en_enero: bool = True,
    verbose: bool = True,
    df_bases_in: pd.DataFrame = None,  # DataFrame de bases mensual (fecha, base)
    # --- Excel unificado ---
    export_libro_excel: bool = True,
    export_libro_excel_path: str = "Resumen_Calculo_Jubilacion.xlsx",
    incluir_tablas_entrada_en_libro: bool = True,
    # --- Regla pre-baja a máximas (configurable) ---
    activar_regla_prebaja_max: bool = True,
    # --- NUEVOS PARÁMETROS BRECHA GÉNERO ---
    num_hijos: int = 0,
    sexo: str = "mujer", # "mujer" | "hombre"
    aplicar_brecha_genero: bool = False,
):
    """
    Calcula la pensión bruta mensual (14 pagas) en jubilación anticipada.
    Incluye proyección de bases (histórico, pre-baja, SEPE 24 meses, CE), BR,
    reducciones, topes y Excel unificado con pestañas IN_* y Bases_Proyectadas.
    """
    # -------------------------- [0] VALIDACIÓN --------------------------
    _validar_entradas_jubilacion(
        fecha_nacimiento=fecha_nacimiento,
        fecha_baja_ere_despido=fecha_baja_ere_despido,
        fecha_jubilacion_anticipada=fecha_jubilacion_anticipada,
        causa_involuntaria=causa_involuntaria,
        df_bases_in=df_bases_in
    )

    # -------------------------- [0] RESOLUCIÓN DE ESCENARIO -------------------
    esc = escenario or Escenario.from_modalidad(modalidad)

    # Permitir overrides explícitos si el usuario los pasa
    tiene_SEPE = tiene_prestacion_desempleo if tiene_prestacion_desempleo is not None else esc.tiene_prestacion_desempleo
    meses_SEPE = meses_prestacion if meses_prestacion is not None else esc.meses_prestacion

    # PSI fuerza voluntaria; si el llamador fija explicitamente algo, respetar su override
    causa_invol = causa_involuntaria if causa_involuntaria is not None else esc.causa_involuntaria

    # -------------------------- [1] CARGA DE DATOS ----------------------
    if verbose:
        print("\n[1] CARGA DE DATOS")
        print(" -> Leyendo ficheros de bases, máximas, incrementos y reducciones...")


    if df_bases_in is not None:
        if not isinstance(df_bases_in, pd.DataFrame):
            raise ValueError("df_bases_in debe ser un DataFrame de pandas.")
        df_bases = df_bases_in.copy()
    else:
        try:
            # Nota: en el original se leía un TXT local; mantenemos la lógica
            df_bases = pd.read_csv('bases_cotizacion_OK.txt', sep=';', decimal=',')
        except Exception as e:
            raise FileNotFoundError(
                f"No se pudo leer '{RUTA_BASES_COTIZACION}'. Verifica ruta, separador y formato."
            ) from e

    try:
        df_incrementos = pd.read_csv(RUTA_INCREMENTO_BASES_REGULADORAS, sep=';', decimal=',')
    except Exception as e:
        raise FileNotFoundError(
            f"No se pudo leer '{RUTA_INCREMENTO_BASES_REGULADORAS}'. Verifica ruta, separador y formato."
        ) from e

    try:
        df_reducciones_involuntaria = pd.read_csv(RUTA_COEFICIENTES_JAI, sep=';')
    except Exception as e:
        raise FileNotFoundError(
            f"No se pudo leer '{RUTA_COEFICIENTES_JAI}'. Verifica ruta, separador y formato."
        ) from e

    try:
        df_reducciones_voluntaria = pd.read_csv(RUTA_COEFICIENTES_JAV, sep=';')
    except Exception as e:
        raise FileNotFoundError(
            f"No se pudo leer '{RUTA_COEFICIENTES_JAV}'. Verifica ruta, separador y formato."
        ) from e

    try:
        df_pensiones_maximas = pd.read_csv(RUTA_EVOLUCION_PENSION_MAXIMA, sep=';', decimal=',')
    except Exception as e:
        raise FileNotFoundError(
            f"No se pudo leer '{RUTA_EVOLUCION_PENSION_MAXIMA}'. Verifica ruta, separador y formato."
        ) from e
    
    try:
        df_ipc = pd.read_csv(RUTA_TABLA_IPC, sep=';', decimal=',')
        # Limpiamos el % si existe y convertimos a float (tanto por uno)
        df_ipc['IPC_val'] = df_ipc['IPC'].str.replace('%', '').str.replace(',', '.').astype(float) / 100.0
        dict_ipc = dict(zip(df_ipc['Anio'].astype(int), df_ipc['IPC_val']))
    except Exception as e:
        raise FileNotFoundError(f"No se pudo leer '{RUTA_TABLA_IPC}'.") from e

    # --- Helper: pensión máxima mensual por año desde df_pensiones_maximas ---
    def pension_max_mensual_for_year(y: int) -> float:
        fila = df_pensiones_maximas[df_pensiones_maximas['Año'] == y]
        if fila.empty:
            # Política por defecto: error claro si falta el año
            raise ValueError(
                f"No se encuentra la pensión máxima mensual para el año {y} en '{RUTA_EVOLUCION_PENSION_MAXIMA}'."
            )
        return float(fila['Pensión Mensual (€)'].values[0])

    # Valor de referencia "2025" (sustituye al parámetro eliminado)
    pension_max_mensual_2025_val = pension_max_mensual_for_year(2025)

    try:
        df_bases_maximas_raw = pd.read_csv(RUTA_BASES_MAXIMAS, sep=';', decimal='.')
        df_max = df_bases_maximas_raw.copy()
        df_max.columns = [c.strip() for c in df_max.columns]
        df_max = (
            df_max.groupby('Año', as_index=False)['Base Máxima Mensual (€)']
            .max()
            .rename(columns={'Base Máxima Mensual (€)': 'base_max'})
        )
        dict_base_max = dict(zip(df_max['Año'].astype(int), df_max['base_max'].astype(float)))
        min_max_year = int(df_max['Año'].min())
        max_max_year = int(df_max['Año'].max())
    except Exception as e:
        raise FileNotFoundError(
            f"No se pudo leer '{RUTA_BASES_MAXIMAS}'. Verifica ruta, separador y formato."
        ) from e


    # 1. Carga del fichero Brecha_Genero.txt
    try:
        df_brecha = pd.read_csv(RUTA_BRECHA_GENERO, sep=';', decimal=',')
        # Diccionario para búsqueda rápida por año
        dict_brecha = dict(zip(df_brecha['Anio'].astype(int), df_brecha['Mensual'].astype(float)))
    except Exception as e:
        if verbose: print(f" ! Aviso: No se pudo cargar '{RUTA_BRECHA_GENERO}': {e}. Se asumirá 0.")
        dict_brecha = {}


    # Diccionario de base mínima mensual (€) por año si existe en el fichero
    dict_base_min, min_min_year, max_min_year = {}, None, None
    if 'Base Mínima Mensual (€)' in df_bases_maximas_raw.columns:
        df_min = (
            df_bases_maximas_raw.groupby('Año', as_index=False)['Base Mínima Mensual (€)']
            .min()
            .rename(columns={'Base Mínima Mensual (€)': 'base_min'})
        )
        dict_base_min = dict(zip(df_min['Año'].astype(int), df_min['base_min'].astype(float)))
        min_min_year = int(df_min['Año'].min())
        max_min_year = int(df_min['Año'].max())
    else:
        dict_base_min = {}

    if verbose:
        print(f" -> Bases: {len(df_bases)} filas")
        print(f" -> Años con base máxima disponibles: {min_max_year}-{max_max_year} ({len(df_max)} años)")

    # Helper local: base máxima por año (proyecta si falta)
    def base_max_for_year(y: int) -> float:
        if y in dict_base_max:
            return float(dict_base_max[y])
        if y > max_max_year:
            return float(dict_base_max[max_max_year]) * ((1.0 + pct_reval_convenio) ** (y - max_max_year))
        return float(dict_base_max[min_max_year])

    # NUEVO: helper base mínima por año
    def base_min_for_year(y: int) -> float:
        """
        Devuelve la Base Mínima Mensual (€) para el año y.
        Preferente: dato explícito 'Base Mínima Mensual (€)' si está en el fichero de máximas.
        Si no hay columna de mínimas: usa ancla del primer año con máximas y una heurística conservadora.
        Para años futuros, revaloriza con pct_reval_convenio.
        """
        if dict_base_min:
            if y in dict_base_min:
                return float(dict_base_min[y])
            if max_min_year is not None and y > max_min_year:
                return float(dict_base_min[max_min_year]) * ((1.0 + pct_reval_convenio) ** (y - max_min_year))
            if min_min_year is not None:
                return float(dict_base_min[min_min_year])
        # Fallback si no hay columna de mínimas: aproximar al 20% del tope máx.
        anchor = dict_base_max.get(min_max_year, 0.0)
        return float(anchor) * 0.2

    if verbose:
        print(f" -> Bases: {len(df_bases)} filas")
        print(f" -> Incrementos BR: {len(df_incrementos)} filas")
        print(f" -> Tabla reducciones involuntaria: {len(df_reducciones_involuntaria)} filas")
        print(f" -> tabla reducciones voluntaria: {len(df_reducciones_voluntaria)} filas")
        print(f" -> Años con base máxima disponibles: {min_max_year}-{max_max_year} ({len(df_max)} años)")
        if dict_base_min:
            print(f" -> Años con base mínima disponibles: {min_min_year}-{max_min_year} ({len(df_min)} años)")

    # ---------------- [2] HISTÓRICO + TRAMOS ----------------
    if verbose:
        print("\n[2] PREPARACIÓN DE BASES Y CÓMPUTO DE MESES/AÑOS COTIZADOS")
        print(" -> Transformando bases anchas a formato mensual...")

    meses = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
             'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
    marcadores_nulos = {'---', 'Pendiente', 'Sin base', 'nan', '', 'None'}

    # 2.0 Histórico mensual compacto
    bases_lista = []
    for _, row in df_bases.iterrows():
        anio = int(row['Año'])
        for i, mes in enumerate(meses, start=1):
            raw = str(row.get(mes, '')).strip()
            if raw not in marcadores_nulos:
                try:
                    base_float = float(raw.replace('.', '').replace(',', '.'))
                    bases_lista.append({'fecha': datetime(anio, i, 1), 'base': base_float})
                except ValueError:
                    # NUEVO: sustituir valor no numérico por la base mínima del año
                    base_min = base_min_for_year(anio)
                    bases_lista.append({'fecha': datetime(anio, i, 1), 'base': base_min})
                    if verbose:
                        print(
                            f" ! Aviso: valor no numérico '{raw}' en {mes}-{anio} → "
                            f"se sustituye por BASE MÍNIMA {base_min:.2f} €"
                        )

    df_hist = pd.DataFrame(bases_lista)
    if df_hist.empty:
        raise ValueError("No se han encontrado bases válidas en el fichero de bases.")

    df_hist = (
        df_hist.groupby('fecha', as_index=False)['base']
        .sum()
        .sort_values('fecha', ascending=True)
        .reset_index(drop=True)
    )

    ultima_real = df_hist['fecha'].max()
    if verbose:
        print(
            f" -> Última base real: {ultima_real.date()} "
            f"(base={df_hist.loc[df_hist['fecha']==ultima_real,'base'].iloc[0]:.2f} €)"
        )

    # Helpers sobre extendido
    def get_base_mensual_para_mes(df: pd.DataFrame, mes_fecha: datetime) -> float:
        mes_fecha = _month_start(mes_fecha)
        s = df.set_index('fecha')['base']
        if mes_fecha in s.index:
            return float(s.loc[mes_fecha])
        prev = s[s.index < mes_fecha]
        return float(prev.iloc[-1]) if not prev.empty else 0.0

    def media_180_dias_previos(fecha_inicio: datetime, df_ext: pd.DataFrame) -> float:
        return _media_180_dias_previos_para_paro(fecha_inicio, df_ext)

    # Timeline extendido
    df_ext = df_hist.copy()

    # [2.A-0] Chequeo "últimas N CONOCIDAS a máxima" para pre-baja
    pre_baja_max_mode = False
    pre_baja_evaluados = 0
    pre_baja_matches = 0
    if activar_regla_prebaja_max and UMBRAL_ULTIMOS_MESES > 0 and UMBRAL_MIN_MATCH > 0:
        df_ult = (
            df_hist[df_hist['fecha'] <= ultima_real]
            .sort_values('fecha')
            .tail(UMBRAL_ULTIMOS_MESES)
            .copy()
        )
        pre_baja_evaluados = len(df_ult)
        if pre_baja_evaluados >= UMBRAL_MIN_MATCH:
            for _, r in df_ult.iterrows():
                y = int(r['fecha'].year)
                base_real = float(r['base'])
                base_max_y = base_max_for_year(y)
                if base_real + EPS_TOLERANCIA_MAXIMA >= base_max_y:
                    pre_baja_matches += 1
            pre_baja_max_mode = (pre_baja_matches >= UMBRAL_MIN_MATCH)
        if verbose:
            print(
                f" -> Chequeo 'últimas {UMBRAL_ULTIMOS_MESES} CONOCIDAS a máxima': "
                f"{'SÍ' if pre_baja_max_mode else 'NO'} "
                f"(evaluados={pre_baja_evaluados}, a_máxima={pre_baja_matches}, umbral={UMBRAL_MIN_MATCH})"
            )

    # [2.A] Pre-baja
    if verbose:
        print("\n[2.A] PROYECCIÓN PRE-BAJA (hasta el mes anterior a la baja)")

    inicio_prebaja = _add_months(ultima_real, 1)
    fin_prebaja = _add_months(_month_start(fecha_baja_ere_despido), -1)

    if inicio_prebaja <= fin_prebaja:
        curr = inicio_prebaja
        ultima_base = float(df_hist.loc[df_hist['fecha'] == ultima_real, 'base'].iloc[0])
        if pre_baja_max_mode:
            while curr <= fin_prebaja:
                y = curr.year
                base_proj = base_max_for_year(y)
                df_ext = pd.concat(
                    [df_ext, pd.DataFrame([{'fecha': curr, 'base': base_proj}])],
                    ignore_index=True
                )
                curr = _add_months(curr, 1)
            if verbose:
                print(
                    " -> Pre-baja proyectada a BASE MÁXIMA "
                    f"(criterio: ≥ {UMBRAL_MIN_MATCH}/{UMBRAL_ULTIMOS_MESES} últimas CONOCIDAS a máxima)."
                )
        else:
            while curr <= fin_prebaja:
                base_proj = ultima_base
                if aplicar_incremento_2:
                    anios_dif = curr.year - ultima_real.year
                    base_proj = ultima_base * (1.02 ** anios_dif)
                df_ext = pd.concat(
                    [df_ext, pd.DataFrame([{'fecha': curr, 'base': base_proj}])],
                    ignore_index=True
                )
                curr = _add_months(curr, 1)
            if verbose:
                print(
                    f" -> Pre-baja con "
                    f"{'incremento 2% por año natural' if aplicar_incremento_2 else 'última base sin revalorización'}."
                )
    if verbose:
        print(f" -> Añadidos meses pre-baja: {len(df_ext) - len(df_hist)}")

# [2.B] SEPE: solo si aplica
    if verbose:
        print("\n[2.B] PERÍODO DE PRESTACIÓN POR DESEMPLEO (opcional)")

    inicio_sepe = _month_start(fecha_baja_ere_despido)
    fin_sepe = _add_months(inicio_sepe, max(0, int(meses_SEPE)))  # exclusivo

    if tiene_SEPE and meses_SEPE > 0:
        base_paro_mensual = media_180_dias_previos(inicio_sepe, df_ext)
        df_sepe = []
        curr = inicio_sepe
        while curr < fin_sepe and curr <= _month_start(fecha_jubilacion_anticipada):
            df_sepe.append({'fecha': curr, 'base': base_paro_mensual})
            curr = _add_months(curr, 1)
        df_sepe = pd.DataFrame(df_sepe)
        if not df_sepe.empty:
            df_ext = pd.concat([df_ext, df_sepe], ignore_index=True)
        if verbose:
            print(f" -> SEPE activado: base={base_paro_mensual:.2f} €, meses={len(df_sepe)}")
    else:
        if verbose:
            print(" -> SEPE desactivado para este escenario (p.ej. PSI)")
            
    # [2.C] CESS desde fin SEPE (o desde la baja si no hay SEPE) hasta la anticipada
    if verbose:
        print("\n[2.C] CONVENIO ESPECIAL (hasta la fecha de jubilación anticipada)")

    inicio_ce = _month_start(fin_sepe) if (tiene_SEPE and meses_SEPE > 0) else _month_start(fecha_baja_ere_despido)

    if inicio_ce <= _month_start(fecha_jubilacion_anticipada):
        ventana_5a_ini = _add_months(inicio_sepe, -60)
        ventana_5a_fin = _add_months(inicio_sepe, -1)
        cuenta_max = 0
        eps = 0.5
        df_activo = (
            df_ext[(df_ext['fecha'] >= ventana_5a_ini) & (df_ext['fecha'] <= ventana_5a_fin)]
            .copy().sort_values('fecha')
        )
        for _, r in df_activo.iterrows():
            y = r['fecha'].year
            base_mes = float(r['base'])
            base_max_y = dict_base_max.get(y, None)
            if base_max_y is not None and base_mes + eps >= base_max_y:
                cuenta_max += 1
        elegible_max = (cuenta_max >= 24)
        if verbose:
            print(
                f" -> Meses a base máxima (5 años previos a baja): {cuenta_max} -> "
                f"{'ELEGIBLE' if elegible_max else 'NO elegible'}"
            )

        df_ce = []
        curr = inicio_ce
        base_ref_no_elegible = get_base_mensual_para_mes(df_ext, _add_months(inicio_sepe, -1))
        anio_inicio_ce = inicio_ce.year
        while curr <= _month_start(fecha_jubilacion_anticipada):
            y = curr.year
            if elegible_max:
                if y in dict_base_max:
                    base_mes = dict_base_max[y]
                else:
                    if y > max_max_year:
                        base_mes = dict_base_max[max_max_year] * ((1.0 + pct_reval_convenio) ** (y - max_max_year))
                    else:
                        base_mes = dict_base_max[min_max_year]
            else:
                if revalorizar_convenio_en_enero:
                    n_escalones = max(0, y - anio_inicio_ce)
                    base_mes = base_ref_no_elegible * ((1.0 + pct_reval_convenio) ** n_escalones)
                else:
                    diff = relativedelta(curr, inicio_ce)
                    n_escalones = diff.years + (1 if diff.months or diff.days else 0)
                    base_mes = base_ref_no_elegible * ((1.0 + pct_reval_convenio) ** max(0, n_escalones))
            df_ce.append({'fecha': curr, 'base': base_mes})
            curr = _add_months(curr, 1)
        df_ce = pd.DataFrame(df_ce)
        if not df_ce.empty:
            df_ext = pd.concat([df_ext, df_ce], ignore_index=True)
        if verbose:
            tramo = f"{inicio_ce.date()} a {_month_start(fecha_jubilacion_anticipada).date()}"
            print(f" -> Meses CE añadidos ({tramo}): {len(df_ce)}")
            print(f" * Ejemplo primer CE: {df_ce.iloc[0]['fecha'].date()} -> {df_ce.iloc[0]['base']:.2f} €")
            print(f" * Elegibilidad base máxima: {'Sí' if elegible_max else 'No'}")

    # Re-agrupación y orden
    df_temp = (
        df_ext.groupby('fecha', as_index=False)['base']
        .sum().sort_values('fecha', ascending=False).reset_index(drop=True)
    )

    # Cotizados a la anticipada
    meses_cotizados_anticipada = len(df_temp[df_temp['fecha'] <= fecha_jubilacion_anticipada])
    anios_cotizados_anticipada = meses_cotizados_anticipada / 12.0
    if verbose:
        print("\n[2.X] RESUMEN BASES")
        print(f" -> Meses cotizados (histórico+proyección): {meses_cotizados_anticipada}")
        print(f" -> Años cotizados reales en la anticipada: {anios_cotizados_anticipada:.2f}")

    # NUEVO: fecha de 63 años (para partir CE por política CESS)
    fecha_63 = fecha_nacimiento + relativedelta(years=63)

    # ---------------- [2.5] PREPARACIÓN PARA LIBRO (solo trazas) ----------------
    if verbose:
        print("\n[2.5] PREPARACIÓN DE BASES PARA EL LIBRO UNIFICADO")
        print(" -> Pestaña 'Bases_Proyectadas'")
    # (La construcción de df_bases_export se hace más adelante, tras el posible ajuste CESS)

    # ---------------- [3] EDAD ORDINARIA Y ADELANTO ----------------
    if verbose:
        print("\n[3] EDAD ORDINARIA Y ADELANTO (CÁLCULO EXACTO)")

    fecha_65 = fecha_nacimiento + relativedelta(years=65)
    fecha_67 = fecha_nacimiento + relativedelta(years=67)

    df_cum = df_temp.sort_values('fecha', ascending=True).copy()
    df_cum['mes_cotizado'] = (df_cum['base'] > 0).astype(int)
    df_cum['cum_meses'] = df_cum['mes_cotizado'].cumsum()

    UMBRAL_38_5_ANIOS = int(38.5 * 12)  # 462 meses
    mask_65 = df_cum['fecha'] <= fecha_65
    cum_a_65 = int(df_cum.loc[mask_65, 'cum_meses'].iloc[-1]) if mask_65.any() else 0

    if cum_a_65 >= UMBRAL_38_5_ANIOS:
        fecha_ordinaria = fecha_65
        criterio_edad = "65 años (ya alcanzaba 38,5 años)"
    else:
        fila_umbral = df_cum.loc[df_cum['cum_meses'] >= UMBRAL_38_5_ANIOS]
        if not fila_umbral.empty:
            fecha_alcance = fila_umbral.iloc[0]['fecha']
            if fecha_alcance <= fecha_67:
                fecha_ordinaria = fecha_alcance
                criterio_edad = "mes de alcance de 38,5 años (antes de los 67)"
            else:
                fecha_ordinaria = fecha_67
                criterio_edad = "tope 67 años (no se alcanzan 38,5 antes)"
        else:
            fecha_ordinaria = fecha_67
            criterio_edad = "tope 67 años (no se alcanzan 38,5 en histórico/proyección)"

    if fecha_ordinaria > fecha_jubilacion_anticipada:
        dif = relativedelta(fecha_ordinaria, fecha_jubilacion_anticipada)
        meses_adelanto = dif.years * 12 + dif.months
    else:
        meses_adelanto = 0

    # 1) No tiene sentido anticipar antes de los 63
    if _month_start(fecha_jubilacion_anticipada) < _month_start(fecha_63):
        if verbose:
            print("\n[3.A] REVISIÓN DE COHERENCIA: Anticipada antes de los 63 años")
        raise ValueError(
            "No tiene sentido acceder a la jubilación anticipada antes de los 63 años "
            f"(Anticipada: {fecha_jubilacion_anticipada.date()}, 63 años: {fecha_63.date()})."
        )

    # 2) Si la ordinaria > 65, tampoco tiene sentido anticipada PREVIA a (ordinaria - 24m)
    if _month_start(fecha_ordinaria) > _month_start(fecha_65):
        fecha_umbral_24m = _add_months(_month_start(fecha_ordinaria), -24)
        if _month_start(fecha_jubilacion_anticipada) < fecha_umbral_24m:
            adelanto_real = meses_adelanto  # valor real, antes de topes
            if verbose:
                print(
                    "\n[3.B] REVISIÓN DE COHERENCIA: Anticipada más de 24 meses antes de la ordinaria cuando esta es > 65 años"
                )
            raise ValueError(
                "No tiene sentido acceder a la jubilación anticipada antes de los 24 meses previos a la "
                "jubilación ordinaria cuando la edad ordinaria supera los 65 años; cálculo innecesario y "
                "con impacto negativo en la Pensión contributiva. "
                f"(Anticipada: {fecha_jubilacion_anticipada.date()}, Ordinaria: {fecha_ordinaria.date()}, "
                f"adelanto real: {adelanto_real} meses)"
            )

    # 3) Topes de adelanto
    max_adelanto = 48 if causa_invol else 24
    meses_adelanto = min(meses_adelanto, max_adelanto)

    if verbose:
        print(f" -> Meses cotizados acumulados a los 65: {cum_a_65} ({cum_a_65/12:.2f} años)")
        print(f" -> Fecha ordinaria determinada: {fecha_ordinaria.date()} ({criterio_edad})")
        print(
            f" -> Meses de adelanto: {meses_adelanto} (máx {max_adelanto} por "
            f"{'involuntaria' if causa_involuntaria else 'voluntaria'})"
        )

    # --------- AJUSTE CE POR POLÍTICA CESS 63->Anticipada con excepción 24 meses ---------
    # Solo aplica si la anticipada es posterior a los 63
    if _month_start(fecha_jubilacion_anticipada) > _month_start(fecha_63) and 'inicio_ce' in locals():
        # Corte CESS = max(63 años, (ordinaria - 24 meses) + 1 mes)
        fecha_corte_cess_ordinaria = _add_months(_month_start(fecha_ordinaria), -24)
        fecha_corte_cess = max(
            _add_months(_month_start(fecha_63), 1),  # 63 años + 1 mes
            _add_months(fecha_corte_cess_ordinaria, 1)  # ordinaria - 24 meses + 1 mes
        )

        # Rango CE a revisar: desde el inicio de CE hasta la fecha de anticipada
        ce_ini = _month_start(inicio_ce)
        ce_fin = _month_start(fecha_jubilacion_anticipada)

        # Ajustamos df_ext: para meses CE >= fecha_corte_cess y <= anticipada => base 0.0
        mask_ce = (df_ext['fecha'] >= ce_ini) & (df_ext['fecha'] <= ce_fin)
        mask_cese = mask_ce & (df_ext['fecha'] >= fecha_corte_cess)
        if mask_cese.any():
            df_ext.loc[mask_cese, 'base'] = 0.0

        # Re-agrupamos después del ajuste para que todo downstream use los nuevos valores
        df_temp = (
            df_ext.groupby('fecha', as_index=False)['base']
            .sum()
            .sort_values('fecha', ascending=False)
            .reset_index(drop=True)
        )

        # (Opcional) Recalcular métricas de resumen si quieres imprimir con verbose
        meses_cotizados_anticipada = len(df_temp[df_temp['fecha'] <= fecha_jubilacion_anticipada])
        anios_cotizados_anticipada = meses_cotizados_anticipada / 12.0
        if verbose:
            print("\n[2.C*] AJUSTE CE POR POLÍTICA CESS >=63 Y EXCEPCIÓN 24M")
            print(f" -> Fecha 63: {fecha_63.date()}, Fecha ordinaria: {fecha_ordinaria.date()}")
            print(
                f" -> Corte CESS (aporta hasta): {fecha_corte_cess.date()}, luego base 0 hasta anticipada"
            )
            print(
                f" -> Meses cotizados (tras ajuste) hasta anticipada: {meses_cotizados_anticipada} "
                f"({anios_cotizados_anticipada:.2f} años)"
            )
    else:
        fecha_corte_cess = None

    # --------- RECONSTRUCCIÓN Bases_Proyectadas (única, tras posible ajuste) ---------
    df_bases_export = (
        df_ext.groupby('fecha', as_index=False)['base']
        .sum()
        .sort_values('fecha')
    )
    df_bases_export = df_bases_export[df_bases_export['fecha'] <= fecha_jubilacion_anticipada].copy()
    df_bases_export['Año'] = df_bases_export['fecha'].dt.year
    df_bases_export['Mes'] = df_bases_export['fecha'].dt.month

    # Contexto para helper puro
    ctx = dict(
        ultima_real=ultima_real,
        inicio_sepe=inicio_sepe,
        fin_sepe=fin_sepe,
        inicio_ce=inicio_ce if 'inicio_ce' in locals() else None,
        fecha_jubilacion_anticipada=fecha_jubilacion_anticipada,
        elegible_max=('elegible_max' in locals() and bool(elegible_max)),
        pct_reval_convenio=pct_reval_convenio,
        revalorizar_convenio_en_enero=revalorizar_convenio_en_enero,
        pre_baja_max_mode=pre_baja_max_mode,
        umbral_ultimos_meses=UMBRAL_ULTIMOS_MESES,
        umbral_min_match=UMBRAL_MIN_MATCH,
        fecha_corte_cess=fecha_corte_cess,
        aplicar_incremento_2=aplicar_incremento_2,
    )

    df_bases_export['Detalle_Cálculo'] = df_bases_export['fecha'].apply(
        lambda f: _detalle_calculo_para_fecha(f, **ctx)
    )
    df_bases_export = df_bases_export.sort_values('fecha', ascending=True)[
        ['Año', 'Mes', 'fecha', 'base', 'Detalle_Cálculo']
    ]

    # Helper para calcular la base actualizada y el índice de actualización (IA) según los años y el IPC acumulado
    def calcular_base_actualizada(base_nominal: float, anio_base: int, anio_jub: int, dict_ipc: dict) -> tuple[float, float]:
        """
        Calcula la base actualizada y el índice de actualización (IA).
        Criterio: Si anio_base < (anio_jub - 2), se aplica IPC acumulado.
        """
        anio_limite = anio_jub - 2
        
        if anio_base >= anio_limite:
            return round(base_nominal, 2), 1.0
        
        ia = 1.0
        # Multiplicamos (1 + IPC) desde el año de la base hasta el año previo al límite
        for y in range(anio_base, anio_limite):
            ipc_y = dict_ipc.get(y, 0.02) # Default 2% si no existe dato
            
            # MODIFICACIÓN: Si el IPC es negativo, el factor es 1 (no resta)
            factor_anual = max(1.0, 1.0 + ipc_y)
            ia *= factor_anual            
            
        base_actualizada = round(base_nominal * ia, 2)
        return base_actualizada, round(ia, 6)

    # Aplicamos la actualización a df_temp (histórico + proyección) y a df_bases_export para tener 
    #   las bases actualizadas en el libro
    anio_jub = fecha_jubilacion_anticipada.year

    # Aplicamos la actualización a cada fila del histórico/proyección
    actualizados = df_temp.apply(
        lambda r: calcular_base_actualizada(r['base'], r['fecha'].year, anio_jub, dict_ipc),
        axis=1, result_type='expand'
    )
    df_temp['base_actualizada'] = actualizados[0]
    df_temp['indice_actualizacion'] = actualizados[1]

    # Asegurar que df_bases_export también tenga estas columnas para el Excel
    df_bases_export['base_actualizada'] = df_bases_export.apply(
        lambda r: calcular_base_actualizada(r['base'], r['Año'], anio_jub, dict_ipc)[0], axis=1
    )
    df_bases_export['indice_actualizacion'] = df_bases_export.apply(
        lambda r: calcular_base_actualizada(r['base'], r['Año'], anio_jub, dict_ipc)[1], axis=1
    )

    # ---------------- [4] BASE REGULADORA ----------------
    if verbose:
        print("\n[4] BASE REGULADORA")
        print(" -> Buscando configuración del año de acceso...")

    config_rows = df_incrementos[
        df_incrementos['Año de acceso a la jubilación'] == fecha_jubilacion_anticipada.year
    ]
    if config_rows.empty:
        raise ValueError(
            f"No existe configuración de BR para el año {fecha_jubilacion_anticipada.year} en 'Incremento_bases_reguladoras.txt'."
        )
    config = config_rows.iloc[0]
    divisor = float(config['Divisor (dividir entre)'])
    n_bases = int(config['Nº bases de mayor importe'])
    periodo_meses = int(config['Período de meses anteriores'])

    if verbose:
        print(f" -> Período: {periodo_meses}, Nº mejores: {n_bases}, Divisor: {divisor}")

    ultimas_m_bases = df_temp[df_temp['fecha'] <= fecha_jubilacion_anticipada].head(periodo_meses).copy()
    mejores_n_bases = ultimas_m_bases.sort_values('base_actualizada', ascending=False).head(n_bases)
    ultimas_300_bases = ultimas_m_bases.sort_values('base_actualizada', ascending=False).head(300)

    # Cálculo de la BR según Disposición Transitoria 34ª (Selección de las mejores bases de cotización de los últimos N años -
    # según configuración del año de acceso- y división entre el divisor correspondiente):
    base_reguladora_inicial_1 = mejores_n_bases['base_actualizada'].sum() / divisor
    # Cálculo de la BR según cálculo tradicional (Bases de Cotización de los últimos 25 años, es decir, 300 meses):
    base_reguladora_inicial_2 = ultimas_300_bases['base_actualizada'].sum() / 350
    # Selección de la BR más favorable para el trabajador:
    base_reguladora_inicial = max(base_reguladora_inicial_1, base_reguladora_inicial_2)

    if verbose:
        print(f" -> BR (mejores {n_bases}/{divisor}): {base_reguladora_inicial_1:.2f} €")
        print(f" -> BR (últimos 300/350): {base_reguladora_inicial_2:.2f} €")
        print(f" -> BR seleccionada: {base_reguladora_inicial:.2f} €")

    # ---------------- [4.1] REDUCCIÓN POR COTIZACIÓN ----------------
    if verbose:
        print("\n[4.1] REDUCCIÓN POR COTIZACIÓN (<37 años)")

    meses_objetivo_37 = 37 * 12
    meses_faltantes = max(0, int(np.ceil(meses_objetivo_37 - meses_cotizados_anticipada)))

    if anios_cotizados_anticipada >= 37.0 or meses_faltantes == 0:
        reduccion_cotizacion_pct = 0.0
        br_post_cotizacion = base_reguladora_inicial
    else:
        primeros16 = min(meses_faltantes, 16)
        restantes = max(0, meses_faltantes - 16)
        reduccion_cotizacion_pct = (primeros16 * 0.0018) + (restantes * 0.0019)
        br_post_cotizacion = base_reguladora_inicial * (1 - reduccion_cotizacion_pct)

    # ---------------- [4.2] TOPE PENSIÓN MÁX. AÑO JUB ----------------
    if verbose:
        print("\n[4.2] PENSIÓN MÁXIMA ESTIMADA EN EL AÑO DE JUBILACIÓN")

    anio_jubilacion = fecha_jubilacion_anticipada.year
    fila_pension_max = df_pensiones_maximas[df_pensiones_maximas['Año'] == anio_jubilacion]
    if fila_pension_max.empty:
        raise ValueError(
            f"No se encuentra la pensión máxima para {anio_jubilacion} en 'Evolucion_pension_maxima.txt'."
        )
    pension_max_mensual_est_anio_jub = float(fila_pension_max['Pensión Mensual (€)'].values[0])
    pension_max_anual_est_anio_jub = float(fila_pension_max['Pensión Anual (€)'].values[0])

    # ---------------- [5] REDUCCIÓN POR ADELANTO ----------------
    if verbose:
        print("\n[5] REDUCCIONES POR ADELANTO (según cotizados reales en la anticipada)")

    if anios_cotizados_anticipada < 38.5:
        col_reduccion = 'Menos de 38 años y 6 meses'
    elif anios_cotizados_anticipada < 41.5:
        col_reduccion = 'Menos de 41 años y 6 meses'
    elif anios_cotizados_anticipada < 44.5:
        col_reduccion = 'Menos de 44 años y 6 meses'
    else:
        col_reduccion = '44 años y 6 meses o más'

    if causa_invol:
        fila_red = df_reducciones_involuntaria[df_reducciones_involuntaria['Meses de adelanto'] == meses_adelanto]
    else:
        fila_red = df_reducciones_voluntaria[df_reducciones_voluntaria['Meses de adelanto'] == meses_adelanto]

    if fila_red.empty:
        raise ValueError(f"No se encuentra porcentaje de reducción para {meses_adelanto} meses de adelanto.")

    pct_reduccion_str = fila_red[col_reduccion].values[0]
    pct_reduccion_valor = float(str(pct_reduccion_str).replace('%', '').replace(',', '.')) / 100.0

    br_reducida_por_adelanto = br_post_cotizacion * (1 - pct_reduccion_valor)

    # Si la causa es involuntaria, la reducción por adelanto se aplica directamente sobre la BR reducida por cotización,
    # Si la causa es voluntaria, la reducción por adelanto se aplica a la pensión máxima. 
    if causa_involuntaria:
        #pension_maxima_teorica_1 = pension_maxima_teorica_2 = br_reducida_por_adelanto
        pension_maxima_teorica_2 = br_reducida_por_adelanto
    else:
        # Opción 1: tope 2025
        #pension_maxima_teorica_1 = pension_max_mensual_2025_val * (1 - pct_reduccion_valor)
        # Opción 2: tope estimado año jubilación
        pension_maxima_teorica_2 = pension_max_mensual_est_anio_jub * (1 - pct_reduccion_valor)

    # ---------------- [6] TOPES REDUCIDOS ----------------
    # La reducción por adelanto también afecta al tope máximo, que se reduce en un 0.5% por cada trimestre de adelanto.
    # Se calculan 2 opciones de tope máximo reducido:
    #   1) aplicando la reducción al tope de 2025, y 
    #   2) aplicando la reducción al tope estimado para el año de jubilación.
    if verbose:
        print("\n[6] TOPE DE PENSIÓN MÁXIMA REDUCIDA")

    trimestres_adelanto = int(np.ceil(meses_adelanto / 3)) if meses_adelanto > 0 else 0
    #pension_maxima_reducida_1 = pension_max_mensual_2025_val * (1 - (trimestres_adelanto * 0.005))
    pension_maxima_reducida_2 = pension_max_mensual_est_anio_jub * (1 - (trimestres_adelanto * 0.005))


    # ---------------- [6.5] CÁLCULO COMPLEMENTO BRECHA GÉNERO ----------------
    anio_jubilacion = fecha_jubilacion_anticipada.year
    importe_brecha_hijo = dict_brecha.get(anio_jubilacion, 0.0)
    
    # Si no hay dato para ese año futuro, proyectamos con el último disponible + 2%
    if importe_brecha_hijo == 0.0 and dict_brecha:
        ultimo_anio = max(dict_brecha.keys())
        distancia = anio_jubilacion - ultimo_anio
        if distancia > 0:
            importe_brecha_hijo = dict_brecha[ultimo_anio] * (1.02 ** distancia)

    complemento_brecha_total = 0.0
    # Aplicar si es mujer o si se fuerza por parámetro (hombres con derecho)
    # Nota: El máximo legal suele ser 4 hijos
    if (sexo.lower() == "mujer" or aplicar_brecha_genero) and num_hijos > 0:
        n_hijos_efectivos = min(num_hijos, 4)
        complemento_brecha_total = importe_brecha_hijo * n_hijos_efectivos

    if verbose:
        print(f"\n[X] COMPLEMENTO BRECHA DE GÉNERO")
        print(f" -> Año: {anio_jubilacion}, Hijos: {num_hijos}, Sexo: {sexo}")
        print(f" -> Importe mensual total: {complemento_brecha_total:.2f} €")



    # ---------------- [7] RESULTADOS ----------------
    #pension_final_1 = min(pension_maxima_teorica_1, pension_maxima_reducida_1)
    pension_final_2 = min(pension_maxima_teorica_2, pension_maxima_reducida_2)


    # Sumamos el complemento a las pensiones brutas mensuales calculadas
    #pension_final_1_con_brecha = pension_final_1 + complemento_brecha_total
    pension_final_2_con_brecha = pension_final_2 + complemento_brecha_total

    if verbose:
        print("\n[7] RESULTADO FINAL Y RESUMEN")
        print(f" BR Inicial: {base_reguladora_inicial:.2f} €")
        print(f" Reducción por Cotización: {reduccion_cotizacion_pct * 100:.2f} %")
        print(f" Reducción por Adelanto: {pct_reduccion_valor * 100:.2f} %")
        #print(f" Tope Máxima Reducida (2025): {pension_maxima_reducida_1:.2f} €")
        print(f" Tope Máxima Reducida (est. {anio_jubilacion}): {pension_maxima_reducida_2:.2f} €")
        #print(f" Pensión bruta mensual (opción 1): {pension_final_1:.2f} €")
        print(f" Pensión bruta mensual (opción 2): {pension_final_2:.2f} €")
        #print(f" Pensión bruta mensual con Brecha (opción 1): {pension_final_1_con_brecha:.2f} €")
        print(f" Pensión bruta mensual con Brecha (opción 2): {pension_final_2_con_brecha:.2f} €")
    # ---------------- [7.5] EXCEL UNIFICADO ----------------
    if export_libro_excel:
        if verbose:
            print("\n[7.5] EXPORTACIÓN LIBRO UNIFICADO")
            print(f" -> Generando Excel en: '{export_libro_excel_path}'")

        def si_no(x: bool) -> str:
            return "Sí" if bool(x) else "No"

        def fmt_date(d):
            try:
                return d.date()
            except Exception:
                return d

        elegible_max_safe = ('elegible_max' in locals() and isinstance(elegible_max, (bool, np.bool_))) and elegible_max
        cuenta_max_safe = int(cuenta_max) if 'cuenta_max' in locals() else None
        base_ref_no_elegible_safe = float(base_ref_no_elegible) if 'base_ref_no_elegible' in locals() else None

        df_entrada = pd.DataFrame([
            {"Parámetro": "Fecha de nacimiento", "Valor": fmt_date(fecha_nacimiento)},
            {"Parámetro": "Fecha de baja (ERE/Despido)", "Valor": fmt_date(fecha_baja_ere_despido)},
            {"Parámetro": "Fecha de jubilación anticipada", "Valor": fmt_date(fecha_jubilacion_anticipada)},
            {"Parámetro": "Causa involuntaria", "Valor": si_no(causa_involuntaria)},
            {"Parámetro": "Aplicar incremento 2% (pre-baja)", "Valor": si_no(aplicar_incremento_2)},
            {"Parámetro": "% revalorización convenio", "Valor": f"{pct_reval_convenio*100:.2f}%"},
            {"Parámetro": "Revalorizar CE en enero", "Valor": si_no(revalorizar_convenio_en_enero)},
            {"Parámetro": "Tope mensual 2025 (14 pagas)", "Valor": pension_max_mensual_2025_val},
            {"Parámetro": "Regla pre-baja a máximas", "Valor": "Activa" if activar_regla_prebaja_max else "Inactiva"},
            {"Parámetro": "Umbral últimas conocidas (N)", "Valor": UMBRAL_ULTIMOS_MESES},
            {"Parámetro": "Umbral mínimo a máxima (match)", "Valor": UMBRAL_MIN_MATCH},
            {"Parámetro": "Tolerancia máxima (eps)", "Valor": EPS_TOLERANCIA_MAXIMA},
            {"Parámetro": "Sexo", "Valor": sexo},
            {"Parámetro": "Aplicar brecha género", "Valor": si_no(aplicar_brecha_genero)},
            {"Parámetro": "Número de hijos", "Valor": num_hijos},
        ])

        datos_intermedios = [
            {"Grupo": "Tramos y Fechas", "Campo": "Última base real (fecha)", "Valor": fmt_date(ultima_real)},
            {"Grupo": "Tramos y Fechas", "Campo": "Inicio pre-baja", "Valor": fmt_date(inicio_prebaja)},
            {"Grupo": "Tramos y Fechas", "Campo": "Fin pre-baja", "Valor": fmt_date(fin_prebaja)},
            {"Grupo": "Tramos y Fechas", "Campo": "Inicio SEPE", "Valor": fmt_date(inicio_sepe)},
            {"Grupo": "Tramos y Fechas", "Campo": "Fin SEPE (exclusivo)", "Valor": fmt_date(fin_sepe)},
            {"Grupo": "Tramos y Fechas", "Campo": "Inicio CE", "Valor": fmt_date(inicio_ce)},
            # Pre-baja (trazabilidad)
            {"Grupo": "Pre-baja", "Campo": "Últimas conocidas evaluadas", "Valor": pre_baja_evaluados},
            {"Grupo": "Pre-baja", "Campo": "Meses a máxima detectados", "Valor": pre_baja_matches},
            {"Grupo": "Pre-baja", "Campo": "Umbral requerido", "Valor": f"{UMBRAL_MIN_MATCH}/{UMBRAL_ULTIMOS_MESES}"},
            {"Grupo": "Pre-baja", "Campo": "Regla aplicada",
             "Valor": "Proyección a base máxima" if pre_baja_max_mode else ("Última base + 2% anual" if aplicar_incremento_2 else "Última base sin revalorización")},
            {"Grupo": "SEPE", "Campo": "Base mensual en SEPE (media 180d * 30)", 
             "Valor": round(base_paro_mensual, 2) if tiene_SEPE and meses_SEPE > 0 else "N/A"},
            {"Grupo": "Convenio Especial", "Campo": "Elegible base máxima (24 en 5 años)", "Valor": "Sí" if elegible_max_safe else "No"},
            {"Grupo": "Convenio Especial", "Campo": "Meses a base máxima (5 años)", "Valor": cuenta_max_safe if cuenta_max_safe is not None else "N/D"},
            {"Grupo": "Convenio Especial", "Campo": "Base ref. no elegible (última pre-baja)", "Valor": round(base_ref_no_elegible_safe, 2) if base_ref_no_elegible_safe else "N/D"},
            {"Grupo": "Convenio Especial", "Campo": "Años base máxima disponibles", "Valor": f"{min_max_year}-{max_max_year}"},
            {"Grupo": "Base Reguladora", "Campo": "Período de meses (anteriores)", "Valor": periodo_meses},
            {"Grupo": "Base Reguladora", "Campo": "Nº mejores bases", "Valor": n_bases},
            {"Grupo": "Base Reguladora", "Campo": "Divisor", "Valor": divisor},
            {"Grupo": "Base Reguladora", "Campo": "BR (mejores/divisor)", "Valor": round(base_reguladora_inicial_1, 2)},
            {"Grupo": "Base Reguladora", "Campo": "BR (300/350)", "Valor": round(base_reguladora_inicial_2, 2)},
            {"Grupo": "Base Reguladora", "Campo": "BR seleccionada", "Valor": round(base_reguladora_inicial, 2)},
            {"Grupo": "Edad & Adelanto", "Campo": "Meses cotizados a los 65", "Valor": cum_a_65},
            {"Grupo": "Edad & Adelanto", "Campo": "Fecha ordinaria", "Valor": fmt_date(fecha_ordinaria)},
            {"Grupo": "Edad & Adelanto", "Campo": "Criterio edad", "Valor": criterio_edad},
            {"Grupo": "Edad & Adelanto", "Campo": "Meses de adelanto", "Valor": meses_adelanto},
            {"Grupo": "Edad & Adelanto", "Campo": "Trimestres (o fracción)", "Valor": int(np.ceil(meses_adelanto/3)) if meses_adelanto>0 else 0},
        ]
        df_intermedios = pd.DataFrame(datos_intermedios)

        df_salida = pd.DataFrame([
            {"Métrica": "BR Inicial", "Valor": round(base_reguladora_inicial, 2)},
            {"Métrica": "Reducción por Cotización (%)", "Valor": round(reduccion_cotizacion_pct * 100, 2)},
            {"Métrica": "BR tras Cotización", "Valor": round(br_post_cotizacion, 2)},
            {"Métrica": "Reducción por Adelanto (%)", "Valor": round(pct_reduccion_valor * 100, 2)},
            {"Métrica": "BR tras Adelanto", "Valor": round(br_reducida_por_adelanto, 2)},
            #{"Métrica": "Tope Máxima Reducida (2025)", "Valor": round(pension_maxima_reducida_1, 2)},
            {"Métrica": f"Tope Máxima Reducida (est. {anio_jubilacion})", "Valor": round(pension_maxima_reducida_2, 2)},
            #{"Métrica": "Pensión Bruta Mensual (opción 1)", "Valor": round(pension_final_1, 2)},
            {"Métrica": "Pension estimada máxima mensual (2025)", "Valor": round(pension_max_mensual_2025_val, 2)},
            #{"Métrica": "Porcentaje de reducción (opción 1)", "Valor": round((1 - pension_final_1 / pension_max_mensual_2025_val) * 100, 2)},
            {"Métrica": "Pensión Bruta Mensual", "Valor": round(pension_final_2, 2)},
            {"Métrica": f"Pension estimada máxima mensual (est. {anio_jubilacion})", "Valor": round(pension_max_mensual_est_anio_jub, 2)},
            {"Métrica": "Porcentaje de reducción", "Valor": round((1 - pension_final_2 / pension_max_mensual_est_anio_jub) * 100, 2)},
            {"Métrica": "Años Cotizados (anticipada)", "Valor": round(anios_cotizados_anticipada, 2)},
            {"Métrica": "Meses Adelanto", "Valor": meses_adelanto},
            {"Métrica": "Ruta Excel (este libro)", "Valor": export_libro_excel_path},
            {"Métrica": "Brecha género", "Valor": round(complemento_brecha_total, 2)},
            #{"Métrica": "Pensión Bruta Mensual (opción 1) INC. BRECHA", "Valor": round(pension_final_1_con_brecha, 2)},
            {"Métrica": "Pensión Bruta Mensual (opción 2) INC. BRECHA", "Valor": round(pension_final_2_con_brecha, 2)},            
        ])

        try:
            with pd.ExcelWriter(export_libro_excel_path, engine='openpyxl') as writer:
                df_entrada.to_excel(writer, index=False, sheet_name='Datos de entrada')
                df_intermedios.to_excel(writer, index=False, sheet_name='Datos intermedios')
                df_salida.to_excel(writer, index=False, sheet_name='Datos de salida')
                # Bases
                df_bases_export.to_excel(writer, index=False, sheet_name='Bases_Proyectadas')
                # Tablas de entrada
                if incluir_tablas_entrada_en_libro:
                    df_bases.to_excel(writer, index=False, sheet_name='IN_Bases')
                    df_incrementos.to_excel(writer, index=False, sheet_name='IN_Inc_BR')
                    df_reducciones_involuntaria.to_excel(writer, index=False, sheet_name='IN_Red_Invol')
                    df_reducciones_voluntaria.to_excel(writer, index=False, sheet_name='IN_Red_Vol')
                    df_pensiones_maximas.to_excel(writer, index=False, sheet_name='IN_Pens_Max')
                    df_bases_maximas_raw.to_excel(writer, index=False, sheet_name='IN_Bases_Max_Raw')
                    df_max.to_excel(writer, index=False, sheet_name='IN_Bases_Max_Norm')
            if verbose:
                print(" -> Libro unificado exportado correctamente.")
        except Exception as e:
            print(f" ! Error exportando el libro unificado: {e}")

    # DataFrame para pasar a rentas
    df_bases_mensuales = df_temp.copy().sort_values('fecha').loc[:, ['fecha', 'base']]

    return {
        "BR Inicial": round(base_reguladora_inicial, 2),
        "Reducción por Cotización (%)": round(reduccion_cotizacion_pct * 100, 2),
        "BR tras Cotización": round(br_post_cotizacion, 2),
        "Reducción por Adelanto (%)": round(pct_reduccion_valor * 100, 2),
        "BR tras Adelanto": round(br_reducida_por_adelanto, 2),
        #"Porcentaje de reducción (opción 1)": round((1 - pension_final_1 / pension_max_mensual_2025_val) * 100, 2),
        "Porcentaje de reducción": round((1 - pension_final_2 / pension_max_mensual_est_anio_jub) * 100, 2),
        #"Tope Máxima Reducida (2025)": round(pension_maxima_reducida_1, 2),
        "Tope Máxima Reducida (est. año jubilación)": round(pension_maxima_reducida_2, 2),
        #"Pensión Bruta Mensual (opción 1)": round(pension_final_1, 2),
        "Pensión Bruta Mensual": round(pension_final_2, 2),
        "Años Cotizados (anticipada)": round(anios_cotizados_anticipada, 2),
        "Meses Adelanto": meses_adelanto,
        "Ruta Excel (unificado)": export_libro_excel_path if export_libro_excel else None,
        "df_bases_mensuales": df_bases_mensuales,
        "Fecha ordinaria": fecha_ordinaria,
        "Brecha género": round(complemento_brecha_total, 2), # Nueva métrica
        #"Pensión Bruta Mensual (opción 1)": round(pension_final_1_con_brecha, 2), # Actualizado
        "Pensión Bruta Mensual (opción 2)": round(pension_final_2_con_brecha, 2), # Actualizado        
    }
