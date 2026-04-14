#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import argparse
from pathlib import Path
from collections import defaultdict
import logging

MONTHS = [
    "Enero","Febrero","Marzo","Abril","Mayo","Junio",
    "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"
]


def normalize_value(v: str) -> str:
    if v is None:
        return ""
    v = v.strip()
    if v in ("", "nan", "None"):
        return ""
    return v


def read_input_csv(path: Path):
    """
    Lee el CSV de entrada de forma robusta. 
    Maneja archivos con cabeceras irregulares y proporciona logs de depuración.
    """
    rows = []
    
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as f:
            # 1. Inspección previa (Depuración)
            sample = f.read(4096)
            f.seek(0)
            
            # 2. Detección de dialecto con salvaguarda
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,")
                logging.debug(f"Dialecto detectado: delimitador='{dialect.delimiter}'")
            except Exception as e:
                logging.warning(f"No se pudo sniffear el dialecto, usando ';' por defecto. Error: {e}")
                dialect = csv.excel
                dialect.delimiter = ";"

            # 3. Lectura manual o DictReader
            # Nota: Dado que tu CSV tiene filas de Empresa y luego filas de Año, 
            # el DictReader puro va a fallar porque las columnas cambian de significado.
            reader = csv.reader(f, dialect=dialect)
            
            line_count = 0
            for i, raw_row in enumerate(reader):
                line_count += 1
                
                # Limpieza básica de la fila
                clean_row = [col.strip() for col in raw_row if col is not None]
                
                # Ignorar filas completamente vacías
                if not any(clean_row):
                    continue
                
                # DEPUREMOS: Si sospechas que no avanza, imprime el índice i
                logging.debug(f"Procesando línea {i}: {clean_row[:3]}...") # Solo los primeros 3 elementos
                
                # Aquí llamarías a tu lógica de "máquina de estados" 
                # (guardar empresa si la fila la contiene, o guardar año si es fila de datos)
                rows.append(clean_row)

            logging.info(f"Lectura finalizada. Total líneas leídas: {line_count}")
            return rows

    except FileNotFoundError:
        logging.error(f"Archivo no encontrado en la ruta: {path}")
        raise
    except Exception as e:
        logging.error(f"Error inesperado leyendo el CSV: {e}", exc_info=True)
        raise


def transform(rows):
    """
    Transforma filas (listas de strings) a estructura tabular:
    Año + Empresa + 12 meses.
    
    Implementa una máquina de estados para recordar la empresa actual
    mientras recorre las filas de años.
    """
    # MONTHS debe estar definida globalmente o dentro de la función
    months_names = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
                   "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    
    # key: (año, empresa) -> value: {mes: valor}
    result = defaultdict(lambda: {m: "" for m in months_names})
    
    empresa_actual = "DESCONOCIDA"
    
    for idx, r in enumerate(rows):
        # 1. Limpieza y depuración de la fila
        # r es una lista: ['', '2026', '---', '', ...]
        row_str = " ".join([str(c) for c in r]).strip()
        
        if not row_str:
            continue

        # 2. Detección de cambio de Empresa
        # Buscamos en la fila si existe la etiqueta de empresa
        if "Empresa/Razón Social:" in row_str:
            try:
                # Intentamos extraer lo que hay después de los dos puntos
                # Si el CSV está bien spliteado, estará en una de las celdas
                for i, cell in enumerate(r):
                    if "Empresa/Razón Social:" in cell:
                        # A veces el nombre está en la misma celda o en la siguiente
                        content = cell.split(":", 1)[1].strip()
                        if not content and (i + 1) < len(r):
                            content = r[i+1].strip()
                        if content:
                            empresa_actual = content
                            logging.debug(f"Línea {idx}: Empresa actualizada a -> {empresa_actual}")
                        break
            except Exception as e:
                logging.warning(f"Línea {idx}: Error al extraer nombre de empresa: {e}")
            continue

        # 3. Detección de fila de Año y Bases
        # Buscamos un año de 4 dígitos (19xx o 20xx)
        year = None
        year_idx = -1
        for i, cell in enumerate(r):
            cell_clean = cell.strip()
            if cell_clean.isdigit() and len(cell_clean) == 4 and cell_clean.startswith(("19", "20")):
                year = cell_clean
                year_idx = i
                break
        
        if year:
            key = (year, empresa_actual)
            # Extraemos los valores que siguen al año
            # En tu CSV, los meses están esparcidos; filtramos celdas vacías
            valores_fila = []
            for m_cell in r[year_idx + 1:]:
                val = m_cell.strip()
                # Consideramos valores válidos: números con coma, guiones o "Pendiente"
                if val:
                    valores_fila.append(val)
                
            # Asignamos los valores encontrados a los meses correspondientes
            # Solo llenamos hasta 12
            for i, val in enumerate(valores_fila[:12]):
                mes_nombre = months_names[i]
                result[key][mes_nombre] = val
            
            logging.debug(f"Línea {idx}: Procesado año {year} para {empresa_actual}")

    # 4. Construcción de la lista final
    final_rows = []
    for (year, empresa), months_dict in result.items():
        row = [year, empresa] + [months_dict[m] for m in months_names]
        final_rows.append(row)

    # 5. Ordenación robusta (Año desc, Empresa asc)
    try:
        final_rows.sort(key=lambda x: (-int(x[0]), x[1]))
    except ValueError as e:
        logging.error(f"Error al ordenar por año (¿hay años no numéricos?): {e}")

    return final_rows

def write_csv(rows, output_path: Path):
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Año", "Empresa"] + MONTHS)
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Normaliza CSV de bases a formato mensual por año")
    parser.add_argument("-i", "--input", required=False, help="CSV de entrada")
    parser.add_argument("-o", "--output", required=False, help="CSV de salida")
    args = parser.parse_args()

    # Obtiene la carpeta donde reside el script actual
    BASE_DIR = Path(__file__).resolve().parent

    # Construye la ruta: sube un nivel y entra en data/temp
    ruta_archivo = BASE_DIR.parent / "data" / "temp" / "BasesCotizacionAnonimizado.csv"
    input_path = Path(args.input) if args.input else Path(ruta_archivo)

    ruta_archivo = BASE_DIR.parent / "data" / "temp" / "BasesCotizacionAnonimizado.txt"
    output_path = Path(args.output) if args.output else Path(ruta_archivo)

    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {input_path}")

    rows = read_input_csv(input_path)
    transformed = transform(rows)
    write_csv(transformed, output_path)

    print(f"[OK] CSV generado: {output_path} (filas: {len(transformed)})")


if __name__ == "__main__":
    main()