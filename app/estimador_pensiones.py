# estimador_pensiones.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Tuple, Optional, Iterable, List, Any, Literal
import pandas as pd
from core import RUTA_TABLA_IPC, _append_triplets_to_sheet, RUTA_EV

@dataclass
class ProyeccionPensiones:
    df_mensual: pd.DataFrame  # columnas: fecha_mes, mes, anio, factor_ipc_acum, pension_12
    total_pension_65_enadelante: float
    esperanza_vida_anios: float
    total_pension_63_65: float = 0.0
    total_pension: float = 0.0

# -------------------------
# Lectura de ficheros
# -------------------------

def cargar_esperanza_vida(path_txt: str) -> pd.DataFrame:
    """Lee 'Año;Hombres;Mujeres' (punto y coma) y devuelve DataFrame con columnas ['Año','Hombres','Mujeres'].
    Se toleran encabezados con mayúsculas/minúsculas indistintas.
    """
    df = pd.read_csv(path_txt, sep=';', engine='python')
    # Normaliza nombres
    rename_map = {}
    for c in df.columns:
        lc = c.strip().lower()
        if lc in ('año', 'ano', 'anio', 'year'):
            rename_map[c] = 'Año'
        elif lc.startswith('hombre'):
            rename_map[c] = 'Hombres'
        elif lc.startswith('mujer'):
            rename_map[c] = 'Mujeres'
    if rename_map:
        df = df.rename(columns=rename_map)
    return df[['Año','Hombres','Mujeres']]


# -------------------------
# Núcleo de proyección
# -------------------------

def _esperanza_vida_para(sexo: str, anio: int, df_ev: pd.DataFrame) -> float:
    row = df_ev.loc[df_ev['Año'] == anio]
    if row.empty:
        mayores = df_ev[df_ev['Año'] > anio].sort_values('Año').head(1)
        menores = df_ev[df_ev['Año'] < anio].sort_values('Año', ascending=True).tail(1)
        row = mayores if not mayores.empty else menores
    if row.empty:
        return 0.0
    sexo_up = (sexo or '').strip().upper()
    if sexo_up.startswith('H'):  # Hombre
        return float(str(row.iloc[0]['Hombres']).replace(',', '.'))
    else:
        return float(str(row.iloc[0]['Mujeres']).replace(',', '.'))

def _pension_12_desde_mensual_14(pension_mensual_14: float) -> float:
    return float(pension_mensual_14) * 14.0 / 12.0

def proyectar_pension(
    *,
    sexo: str,
    fecha_jub_ordinaria: datetime,
    fecha_nacimiento: datetime,
    pension_bruta_mensual_12: float,
    df_ev: pd.DataFrame,
    ipc_map: dict,
    mes_idx: int,
    verbose: bool = True,
    total_pension_63_65: Optional[float] = None,
) -> ProyeccionPensiones:
    """
    Proyecta pensiones mensuales (en 12 pagas) desde el mes *siguiente* a la fecha de jubilación ordinaria
    hasta completar la esperanza de vida restante, con **revalorización anual discreta** (enero).
    """
    anio_ref = fecha_jub_ordinaria.year
    ev_anios = _esperanza_vida_para(sexo, anio_ref, df_ev)

    # Años exactos a la ordinaria y esperanza restante desde la ordinaria
    anios_jub_ordinaria = round((fecha_jub_ordinaria - fecha_nacimiento).days / 365.25, 6)
    anios_hasta_muerte   = max(0.0, ev_anios - anios_jub_ordinaria)
    meses = max(0, int(round(anios_hasta_muerte * 12)))
    if verbose:
        print(f"[Proyección pensión] Sexo: {sexo}, Fecha jubilación ordinaria: {fecha_jub_ordinaria.date()}, "
              f"Fecha nacimiento: {fecha_nacimiento.date()}, Edad ordinaria: {anios_jub_ordinaria:.2f} años, "
              f"Esperanza vida: {ev_anios:.2f} años, Meses a proyectar: {meses}, "
              f"Pensión bruta mensual (12 pagas): {pension_bruta_mensual_12:.2f} EUR")

    # IPC por año (fracción)
    base_12_inicial = float(pension_bruta_mensual_12)
    if verbose:
        print(f"[Proyección pensión] IPC estimado por año: {ipc_map}")

    registros = []
    # Empezamos a devengar al mes siguiente de la fecha ordinaria
    current = pd.Timestamp(year=fecha_jub_ordinaria.year, month=fecha_jub_ordinaria.month, day=1) + pd.offsets.MonthBegin(1)

    # Nivel vigente al inicio: si arrancamos en enero, aplicamos IPC de ese año; si no, se mantiene hasta enero siguiente
    nivel_anual = base_12_inicial * (1.0 + ipc_map.get(int(current.year), 0.0)) if int(current.month) == 1 else base_12_inicial
    anio_en_curso = int(current.year)
    mes_idx_int = mes_idx
    if verbose:
        print(f"[Proyección pensión] Nivel inicial anual (12 pagas) en {current.date()}: {nivel_anual:.2f} EUR")

    for i in range(meses):
        # Si cambia el año (enero), revaloriza con IPC del nuevo año
        if int(current.year) != anio_en_curso:
            anio_en_curso = int(current.year)
            nivel_anual = nivel_anual * (1.0 + ipc_map.get(anio_en_curso, 0.0))

        pension_mes = round(nivel_anual, 2)
        registros.append({
            'fecha_mes': current.to_pydatetime(),
            'Mes': str(int(current.year)).zfill(4) + '-' + str(int(current.month)).zfill(2),
            'anio': int(current.year),
            'factor_ipc_acum': None,   # en revalorización discreta no se compone mensual
            'mes_idx': mes_idx_int,
            'pension_12': pension_mes,
            '__year': int(current.year),
            'irpf_tributa_mes': pension_mes,  # tributa íntegramente
        })
        current = current + pd.offsets.MonthBegin(1)
        mes_idx_int += 1

    df_out = pd.DataFrame(registros)
    total = float(round(df_out['pension_12'].sum(), 2)) if not df_out.empty else 0.0
    if verbose:
        print(f"[Proyección pensión] Total renta proyectada: {total:.2f} EUR sobre {len(df_out)} meses.")
    return ProyeccionPensiones(df_mensual=df_out, total_pension_65_enadelante=total, esperanza_vida_anios=float(ev_anios), 
                               total_pension_63_65=total_pension_63_65, total_pension=total + (total_pension_63_65 or 0.0))

def proyectar_una_opcion(
    *,
    sexo: str,
    fecha_jub_ordinaria: datetime,
    fecha_nacimiento: datetime,
    pension_opc1_mensual_12: float,
    export_excel: bool = False,
    export_excel_path: Optional[str] = None,
    mes_idx: int = 0,
    verbose: bool = True,
    total_pension_63_65: Optional[float] = None,
) -> ProyeccionPensiones:
    """
    Proyecta una opción y, si export_excel=True, actualiza Resumen_Rentas.xlsx:
      - Entrada -> 'Pension_Largo_Plazo_Entradas' (3 columnas)
      - Valores usados -> 'Pension_Largo_Plazo_Valores_Usados' (3 columnas)
      - Salida -> 'Pension_Largo_Plazo_Salidas' (3 columnas)
      - Detalle_Rentas -> apende filas de la opción seleccionada (1 o 2) como 'PENSION'
    """
    
    #RUTA_EV = 'Esperanza_Vida.txt'  # Formato: Año;Hombres;Mujeres
 
    # Cargar datos de esperanza de vida
    df_ev = cargar_esperanza_vida(RUTA_EV)
 
    # Cargar datos de IPC
    try:
        df_ipc = pd.read_csv(RUTA_TABLA_IPC, sep=';', decimal=',')
        # Limpiamos el % si existe y convertimos a float (tanto por uno)
        df_ipc['IPC_val'] = df_ipc['IPC'].str.replace('%', '').str.replace(',', '.').astype(float) / 100.0
        dict_ipc = dict(zip(df_ipc['Anio'].astype(int), df_ipc['IPC_val']))
    except Exception as e:
        raise FileNotFoundError(f"No se pudo leer '{RUTA_TABLA_IPC}'.") from e

    if verbose:
        print(f"[Cargar datos] Esperanza de vida cargada desde '{RUTA_EV}', {len(df_ev)} registros.")
        print(f"[Cargar datos] Estimación IPC cargada desde '{RUTA_TABLA_IPC}', {len(df_ipc)} registros.")

    # --- Proyección de una opcion ---
    p1 = proyectar_pension(
        sexo=sexo,
        fecha_jub_ordinaria=fecha_jub_ordinaria,
        fecha_nacimiento=fecha_nacimiento,
        pension_bruta_mensual_12=pension_opc1_mensual_12,
        df_ev=df_ev,
        ipc_map=dict_ipc,
        mes_idx = mes_idx,
        verbose=verbose,
        total_pension_63_65=total_pension_63_65,
    )
    if verbose:
        print(f"[Proyección completa] Única opción proyectada.")
    
    # --- Exportar a Excel si procede ---
    if export_excel:
        if verbose:
            print(f"[Exportar Excel] Actualizando '{export_excel_path}' con resultados de la proyección.")
        if not export_excel_path:
            raise ValueError("export_excel=True requiere export_excel_path con la ruta del Excel.")

        # 1) ENTRADAS
        entradas_triplets = [
            ('Pension_Largo_Plazo_Entradas', 'sexo', sexo),
            ('Pension_Largo_Plazo_Entradas', 'fecha_ordinaria', str(fecha_jub_ordinaria.date())),
            ('Pension_Largo_Plazo_Entradas', 'pension_base_12_opc1', float(pension_opc1_mensual_12)),
            
            ('Pension_Largo_Plazo_Entradas', 'fuente_esperanza_vida', 'df_ev (en memoria)'),
            ('Pension_Largo_Plazo_Entradas', 'fuente_ipc', 'df_ipc (en memoria)'),
        ]
        if verbose:
            print(f"[Exportar Excel] Apending entradas: {entradas_triplets}")
        _append_triplets_to_sheet(export_excel_path, 'Entrada', 'Pension_Largo_Plazo_Entradas', entradas_triplets)

        # 2) VALORES USADOS (añadimos la opción elegida)
        valores_triplets = [
            ('Pension_Largo_Plazo_Valores_Usados', 'esperanza_vida_anios', p1.esperanza_vida_anios),
            ('Pension_Largo_Plazo_Valores_Usados', 'meses_proyectados_opc1', len(p1.df_mensual)),
            ('Pension_Largo_Plazo_Valores_Usados', 'metodo_revalorizacion', 'IPC anual discreto (enero)'),            
        ]
        if verbose:
            print(f"[Exportar Excel] Apending valores usados: {valores_triplets}")
        _append_triplets_to_sheet(export_excel_path, 'Valores usados', 'Pension_Largo_Plazo_Valores_Usados', valores_triplets)

        # 3) SALIDAS
        salidas_triplets = [
            ('Pension_Largo_Plazo_Salidas', 'total_pension_desde_65', p1.total_pension_65_enadelante),   
            ('Pension_63_65', 'total_pension_63_65', total_pension_63_65 if total_pension_63_65 is not None else 'N/A'),
            ('Pension_Total_63_Enadelante', 'total_pension_63_enadelante', (p1.total_pension_65_enadelante + total_pension_63_65) 
             if total_pension_63_65 is not None else 'N/A'),  
        ]
        if verbose:
            print(f"[Exportar Excel] Apending salidas: {salidas_triplets}")
        _append_triplets_to_sheet(export_excel_path, 'Salida', 'Pension_Largo_Plazo_Salidas', salidas_triplets)

        # 4) DETALLE_RENTAS: apende la opción seleccionada
        try:
            df_det = pd.read_excel(export_excel_path, sheet_name='Detalle_Rentas', engine='openpyxl')
        except Exception:
            df_det = pd.DataFrame(columns=[
                'tramo','Mes','fecha_mes','prestacion_mes','renta_indemn_63','renta_indemn_65',
                'complemento_empresa_63','comp_paro_ss_empresa','pension_12','total','irpf_tributa_mes'
            ])

        # Normaliza columnas necesarias
        for col in ['prestacion_mes','renta_indemn_63','renta_indemn_65','complemento_empresa_63',
                    'mes_idx','comp_paro_ss_empresa','pension_12','total','irpf_tributa_mes']:
            if col not in df_det.columns:
                df_det[col] = 0.0
        if 'fecha_mes' not in df_det.columns:
            df_det['fecha_mes'] = pd.NaT
        if 'Mes' not in df_det.columns:
            df_det['Mes'] = 'N/A'
        if 'tramo' not in df_det.columns:
            df_det['tramo'] = ''
        
        # Determina último Mes numérico
        try:
            last_mes = pd.to_numeric(df_det['Mes'], errors='coerce').fillna(0).astype(int).max()
        except Exception:
            last_mes = 0

        seleccion = p1
        df_pens = seleccion.df_mensual.copy()

        nuevos = pd.DataFrame({
            'tramo': 'PENSION',
            'Mes': df_pens['Mes'],
            'fecha_mes': pd.to_datetime(df_pens['fecha_mes']),
            'prestacion_mes': 0.0,
            'renta_indemn_63': 0.0,
            'renta_indemn_65': 0.0,
            'complemento_empresa_63': 0.0,
            'comp_paro_ss_empresa': 0.0,
            'mes_idx': df_pens['mes_idx'],
            'pension_12': df_pens['pension_12'],
            '__year': df_pens['__year'],
            'irpf_tributa_mes': df_pens['pension_12'],            
        })
        nuevos['total'] = nuevos[['prestacion_mes','renta_indemn_63','renta_indemn_65','mes_idx',
                                  'complemento_empresa_63','comp_paro_ss_empresa','pension_12']].sum(axis=1)

        # Alinear columnas/orden
        for c in df_det.columns:
            if c not in nuevos.columns:
                nuevos[c] = 0.0 if pd.api.types.is_numeric_dtype(df_det[c]) else (pd.NaT if 'fecha' in c.lower() else '')
        nuevos = nuevos[df_det.columns]

        df_det_ext = pd.concat([df_det, nuevos], ignore_index=True)
        if verbose:
            print(f"[Exportar Excel] Detalle_Rentas actualizado con {len(nuevos)} filas de la opción proyectada.")
        with pd.ExcelWriter(export_excel_path, engine='openpyxl', mode='a', if_sheet_exists='replace') as w:
            df_det_ext.to_excel(w, sheet_name='Detalle_Rentas', index=False)
    return p1