#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Convierte el PDF digital o el TXT exportado desde Adobe del
"Informe Integral de Bases de Cotización" a un CSV (separado por ';')
con columnas:
Año;Empresa;Enero;Febrero;...;Diciembre

Ejemplos de uso:
  python pdf_txt2bases_csv.py -i "Informe Bases Cotización Online.pdf" -o "Bases_Cotizacion.csv"
  python pdf_txt2bases_csv.py -i "Informe Bases Cotización Online.txt" -o "Bases_Cotizacion.csv"

Opciones:
  --include-pending   Incluye años con todos los meses 'Pendiente'/'---' (por defecto, excluye).
  --encoding ENC      Encoding del CSV (por defecto: utf-8-sig; usar 'latin-1' si Excel antiguo).
  --keep-txt          Si el input es PDF, conserva el TXT intermedio.
  --txt-output PATH   Si el input es PDF, guarda el TXT intermedio en esta ruta.
"""

from __future__ import annotations
import re
import csv
import argparse
from pathlib import Path
import sys
from typing import List, Tuple
import tempfile
import fitz  # pip install pymupdf
import logging
from core import setup_logging

MONTHS = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
          "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

# Empresa como aparece en el TXT exportado
COMPANY_RE = re.compile(r"^Empresa/Raz[óo]n Social:\s*(.+?)\s*$", re.IGNORECASE)

# Año al inicio de línea
YEAR_RE = re.compile(r"^((?:19|20)\d{2})\b(.*)$")

# Números estilo ES (permite 2.402,73 o 944,79 o 240,27, etc.)
NUM_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$|^\d+,\d{2}$")

def pdf_to_text(input_pdf: str | Path, output_txt: str | Path | None = None) -> Path:
    """
    Extrae el texto de un PDF digital de forma robusta.
    Incluye gestión de errores de cifrado, logs de progreso y validación de salida.
    """
    input_pdf = Path(input_pdf)
    
    # 1. Validación de entrada con logging informativo
    if not input_pdf.exists():
        logging.error(f"Fichero de entrada no encontrado: {input_pdf}")
        raise FileNotFoundError(f"No existe el archivo: {input_pdf}")

    if output_txt is None:
        output_txt = input_pdf.with_suffix(".txt")
    else:
        output_txt = Path(output_txt)

    logging.info(f"Iniciando extracción de texto: {input_pdf.name} -> {output_txt.name}")

    all_pages: List[str] = []

    try:
        # 2. Apertura segura del documento
        with fitz.open(input_pdf) as doc:
            # Verificar si el PDF tiene restricciones
            if doc.is_encrypted:
                logging.warning(f"El PDF '{input_pdf.name}' está cifrado. Si la extracción falla, intenta desprotegerlo.")

            for i, page in enumerate(doc):
                # Usamos el modo "text" que preserva mejor el orden de lectura de Adobe
                text = page.get_text("text").strip()
                
                if not text:
                    logging.debug(f"La página {i+1} parece estar vacía (posible imagen o escaneo).")
                
                all_pages.append(text)

        # 3. Escritura atómica y validación
        content = "\n\n".join(all_pages).strip() + "\n"
        
        # Aseguramos que el directorio de destino existe
        output_txt.parent.mkdir(parents=True, exist_ok=True)
        
        output_txt.write_text(content, encoding="utf-8")
        logging.info(f"Extracción completada con éxito ({len(all_pages)} páginas).")

    except fitz.FileDataError:
        logging.error(f"El archivo no parece un PDF válido o está corrupto: {input_pdf}")
        raise
    except PermissionError:
        logging.error(f"Sin permisos para escribir en: {output_txt}")
        raise
    except Exception as e:
        logging.error(f"Error inesperado durante la extracción del PDF: {e}")
        raise

    return output_txt


def prenormalize_all(text: str) -> str:
    """
    Normaliza el TXT completo para facilitar el parseo.
    Se han añadido mecanismos para evitar la pérdida de contexto en saltos de línea
    y mejorar la trazabilidad mediante logging.
    """
    if not text:
        logging.warning("Se ha recibido un texto vacío para normalizar.")
        return ""

    logging.debug("Iniciando pre-normalización del texto...")

    # 1. Estandarizar saltos de línea (maneja Windows/Unix/Mac antiguo)
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")

    # 2. Normalización de guiones y caracteres especiales de dibujo
    # Algunos PDFs usan guiones largos o símbolos de subrayado para las tablas
    t = t.replace("—", "-").replace("–", "-").replace("_", " ")
    
    # Tratamiento de rachas de guiones (evita que se peguen a números)
    # Sustituimos rachas de 2 o más guiones por un token limpio rodeado de espacios
    t = re.sub(r"-{2,}", " --- ", t)

    # 3. Eliminar ruido de cabeceras de meses
    # Usamos \s+ para detectar la cabecera incluso si los meses están muy separados
    meses_pattern = r"Enero\s+Febrero\s+Marzo\s+Abril\s+Mayo\s+Junio\s+Julio\s+Agosto\s+Septiembre\s+Octubre\s+Noviembre\s+Diciembre"
    t = re.sub(meses_pattern, " ", t, flags=re.IGNORECASE)

    # 4. Manejo de frases multilínea (Crítico para robustez)
    # La frase "Sin base registrada" a veces se rompe como "Sin base\nregistrada"
    # Usamos \s+ que incluye saltos de línea para unificar estos placeholders
    t = re.sub(r"Sin\s+base\s+registrada", " __SIN_BASE__ ", t, flags=re.IGNORECASE)
    t = re.sub(r"Pendiente\s+de\s+actualizar", " __PENDIENTE__ ", t, flags=re.IGNORECASE)

    # 5. Limpieza de espacios y tabulaciones
    # Sustituimos cualquier racha de espacios/tabs horizontales por un solo espacio
    t = re.sub(r"[ \t]+", " ", t)

    # 6. Reconstrucción línea a línea
    lines = []
    for line in t.split("\n"):
        clean_line = line.strip()
        # Solo conservamos líneas que tengan contenido real (no solo espacios o basura)
        if clean_line:
            lines.append(clean_line)

    result = "\n".join(lines)
    
    logging.debug(f"Pre-normalización finalizada. Tamaño reducido de {len(text)} a {len(result)} caracteres.")
    return result

def tokenize_months_from_text(text: str) -> List[str]:
    """
    Convierte una cadena de texto en una lista de valores de bases de cotización.
    Extrae números, indicadores de ausencia y estados, ignorando el ruido.
    """
    if not text:
        return []

    logging.debug(f"Tokenizando segmento: {text[:50]}...")

    # Mapeo de placeholders a valores finales del CSV
    TRANSFORM_MAP = {
        "__SIN_BASE__": "Sin base registrada",
        "__PENDIENTE__": "Pendiente",
        "---": "---"
    }

    tokens: List[str] = []
    
    # 1. Limpieza agresiva de puntuación de borde que no sea la coma decimal
    # Reemplazamos separadores comunes por espacios para un split limpio
    # No tocamos la coma ni el punto si están rodeados de números
    raw_text = text.replace(";", " ").replace("|", " ").replace(":", " ")
    
    # 2. Split por espacios en blanco (incluye tabs y saltos de línea)
    words = raw_text.split()

    for w in words:
        # Limpiamos puntos o comas que hayan quedado en los extremos del token
        # (ej: "4.500,00." -> "4.500,00")
        w = w.strip(". ,") 
        
        if not w:
            continue

        # Caso A: Es un placeholder pre-normalizado
        if w in TRANSFORM_MAP:
            tokens.append(TRANSFORM_MAP[w])
            continue

        # Caso B: Es un número con formato español (1.234,56 o 123,45)
        if NUM_RE.match(w):
            tokens.append(w)
            continue
        
        # Caso C: Token compuesto (ej: "---Sin") - Salvaguarda por si falló la pre-normalización
        if "---" in w:
            tokens.append("---")
            # Si después del guion hay algo más, lo re-evaluamos (recursión ligera o limpieza)
            rest = w.replace("---", "").strip()
            if rest:
                # Intentamos procesar lo que quedó pegado al guion
                tokens.extend(tokenize_months_from_text(rest))
            continue

        # DEPUREMOS: Si activas el debug, verás qué palabras está ignorando
        logging.debug(f"Token ignorado por no cumplir patrones: '{w}'")

    return tokens

def parse_company_block(company: str, lines: List[str], start_idx: int) -> Tuple[List[List[str]], int]:
    """
    Recorre las líneas para extraer años y meses asociados a una empresa.
    Implementa una lógica de recolección multilínea robusta.
    """
    rows: List[List[str]] = []
    i = start_idx
    n = len(lines)

    while i < n:
        line = lines[i].strip()

        # 1. Condición de parada: Si detectamos otra empresa, salimos para que 
        # el bucle principal de txt_to_rows gestione el cambio de contexto.
        # Usamos search en lugar de match por si hay basura al inicio.
        if COMPANY_RE.search(line) and i != start_idx:
            logging.debug(f"Fin de bloque para '{company}' detectado por nueva empresa.")
            break
            
        # 2. Buscar patrón de año
        m = YEAR_RE.search(line)
        if not m:
            i += 1
            continue

        year = m.group(1)
        # Extraemos el resto de la línea después del año para procesar meses pegados
        pos_year = line.find(year)
        tail = line[pos_year + len(year):].strip()

        months: List[str] = []
        # Añadimos lo que hubiera en la misma línea del año
        months.extend(tokenize_months_from_text(tail))

        # 3. Recolección Multilínea Proactiva
        # Si no tenemos 12 meses, miramos las líneas siguientes
        j = i + 1
        while j < n and len(months) < 12:
            next_line = lines[j].strip()
            
            # Si la línea siguiente es un nuevo año o empresa, dejamos de buscar meses
            if YEAR_RE.search(next_line) or COMPANY_RE.search(next_line) or "Régimen:" in next_line:
                break
            
            # Si la línea tiene contenido, intentamos extraer meses
            new_tokens = tokenize_months_from_text(next_line)
            if new_tokens:
                months.extend(new_tokens)
                logging.debug(f"Año {year}: Recolectados {len(new_tokens)} tokens extra en línea {j}")
            
            j += 1

        # 4. Validación y Normalización
        # Si el año es válido pero no hay meses, puede ser una fila de cabecera vacía;
        # aun así, lo guardamos para no perder la cronología, rellenando con vacío.
        final_months = (months + [""] * 12)[:12]
        
        rows.append([year, company] + final_months)
        
        # 5. Avance del puntero
        # Importante: i debe ser j si hemos consumido líneas, 
        # pero si j no avanzó, i debe avanzar al menos 1.
        i = max(j, i + 1)

    return rows, i


def txt_to_rows(txt_path: str | Path, include_pending: bool = False) -> List[List[str]]:
    """
    Coordina la transformación de texto bruto a una lista de filas procesadas.
    Gestiona la lectura, normalización, extracción por bloques y ordenación final.
    """
    txt_path = Path(txt_path)
    if not txt_path.exists():
        logging.error(f"El archivo de texto no existe: {txt_path}")
        return []

    # 1. Lectura con manejo de errores de encoding
    try:
        # Usamos errors="replace" para no morir si hay un carácter extraño en el TXT
        text = txt_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logging.error(f"Error crítico leyendo {txt_path}: {e}")
        return []

    # 2. Pre-normalización global (la función que refinamos antes)
    text = prenormalize_all(text)
    lines = text.split("\n")
    
    all_rows: List[List[str]] = []
    i = 0
    n = len(lines)
    
    logging.info(f"Analizando {n} líneas de texto normalizado...")

    # 3. Bucle principal de extracción
    while i < n:
        line = lines[i].strip()
        
        # Ignorar líneas vacías
        if not line:
            i += 1
            continue

        # Detectar inicio de bloque de empresa
        # Usamos search() por robustez frente a espacios iniciales
        mc = COMPANY_RE.search(line)
        if mc:
            current_company = mc.group(1).strip()
            logging.debug(f"Entrando en bloque de empresa: {current_company}")
            
            # Saltamos la línea de la empresa para empezar a buscar años debajo
            i += 1
            
            # Llamamos a nuestra función de bloque refinada
            rows, next_i = parse_company_block(current_company, lines, i)
            
            if rows:
                all_rows.extend(rows)
                logging.info(f"Extraídas {len(rows)} filas para: {current_company}")
            
            # El puntero avanza a donde nos diga el bloque (evita re-procesar)
            i = next_i
            continue

        # Si no es una empresa ni estamos dentro de un bloque, avanzamos
        i += 1

    # 4. Filtrado de años "vacíos" (Pendientes/Guiones)
    # Movido a una lógica de comprensión de listas más clara
    if not include_pending:
        original_count = len(all_rows)
        # Una fila se mantiene si al menos uno de los meses tiene un dato real (no --- ni Pendiente)
        all_rows = [
            r for r in all_rows 
            if any(m not in ("---", "Pendiente", "", " ") for m in r[2:])
        ]
        diff = original_count - len(all_rows)
        if diff > 0:
            logging.info(f"Filtrado: se han eliminado {diff} filas sin datos (años pendientes).")

    # 5. Ordenación final robusta
    # Año descendente (más reciente arriba), Empresa ascendente
    def sort_key(r: List[str]):
        try:
            # r[0] es el Año. Si no es numérico, lo mandamos al final (año 0)
            return (-int(r[0]), r[1].lower())
        except (ValueError, IndexError):
            return (0, r[1].lower() if len(r) > 1 else "")

    all_rows.sort(key=sort_key)
    
    logging.info(f"Transformación completada. Total: {len(all_rows)} registros.")
    return all_rows


def input_to_txt(
    input_path: str | Path, 
    txt_output: str | Path | None = None, 
    keep_txt: bool = False
) -> Tuple[Path, bool]:
    """
    Normaliza la entrada (PDF o TXT) a un archivo de texto procesable.
    Retorna: (Ruta al archivo TXT, Flag indicando si es un archivo temporal).
    """
    input_path = Path(input_path).resolve()

    if not input_path.exists():
        logging.error(f"Archivo de entrada no encontrado: {input_path}")
        raise FileNotFoundError(f"No existe el archivo: {input_path}")

    suffix = input_path.suffix.lower()

    # Caso 1: Ya es un archivo de texto
    if suffix == ".txt":
        logging.info(f"Entrada reconocida como TXT: {input_path.name}")
        return input_path, False

    # Caso 2: Es un PDF y hay que extraer el texto
    if suffix == ".pdf":
        logging.info(f"Entrada reconocida como PDF. Iniciando conversión...")
        
        # Determinar dónde guardar el TXT resultante
        if txt_output is not None:
            txt_path = Path(txt_output).resolve()
            is_temp = False
        elif keep_txt:
            txt_path = input_path.with_suffix(".txt")
            is_temp = False
        else:
            # Creamos un temporal que no se borre automáticamente (delete=False)
            # para poder procesarlo después de cerrar esta función.
            with tempfile.NamedTemporaryFile(prefix="bases_ext_", suffix=".txt", delete=False) as tmp:
                txt_path = Path(tmp.name)
            is_temp = True

        try:
            # Llamamos a nuestra función robusta de extracción
            pdf_to_text(input_path, txt_path)
            logging.debug(f"Texto extraído correctamente en: {txt_path}")
            return txt_path, is_temp
        except Exception as e:
            logging.error(f"Fallo en la conversión de PDF a TXT: {e}")
            if is_temp and txt_path.exists():
                txt_path.unlink() # Limpiar si falló
            raise

    # Caso 3: Formato no soportado
    logging.error(f"Extensión '{suffix}' no soportada.")
    raise ValueError(f"El archivo debe ser .pdf o .txt (recibido: {suffix})")

def write_csv(rows: List[List[str]], out_path: str | Path, encoding: str = "utf-8-sig") -> Path:
    """
    Escribe los datos extraídos en un archivo CSV formateado para Excel.
    Garantiza que la estructura sea Año;Empresa;Enero...Diciembre.
    """
    out_path = Path(out_path).resolve()
    
    try:
        # 1. Asegurar que el directorio de destino existe
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 2. Apertura del archivo
        # newline="" es fundamental según la doc de csv para evitar líneas en blanco extra en Windows
        with out_path.open("w", encoding=encoding, newline="") as f:
            # Usamos ; como delimitador tal como pediste
            writer = csv.writer(
                f, 
                delimiter=";", 
                quoting=csv.QUOTE_MINIMAL,
                strict=True
            )

            # 3. Escritura de cabeceras
            header = ["Año", "Empresa"] + MONTHS
            writer.writerow(header)

            # 4. Escritura de datos con validación de seguridad
            for idx, row in enumerate(rows):
                # Verificamos que la fila tenga la longitud esperada (2 + 12 = 14)
                if len(row) != 14:
                    logging.warning(f"Fila {idx} con longitud inesperada ({len(row)}). Ajustando...")
                    # Forzamos la longitud por si acaso
                    row = (row + [""] * 14)[:14]
                
                writer.writerow(row)

        logging.info(f"Archivo CSV generado exitosamente en: {out_path}")
        return out_path

    except PermissionError:
        logging.error(f"Error de permisos: No se pudo escribir en '{out_path}'. "
                      "Asegúrate de que el archivo no esté abierto en Excel.")
        raise
    except Exception as e:
        logging.error(f"Error inesperado al escribir el CSV: {e}")
        raise

def main():
    ap = argparse.ArgumentParser(
        description="Convierte PDF digital o TXT del Informe de Bases a CSV separado por ';'"
    )
    # Argumentos de entrada/salida
    ap.add_argument("-i", "--input", required=True, help="Ruta al PDF digital o al TXT exportado.")
    ap.add_argument("-o", "--output", required=True, help="Ruta de salida CSV (separado por ';').")
    
    # Argumentos de comportamiento
    ap.add_argument("--include-pending", action="store_true",
                    help="Incluye años totalmente 'Pendiente'/'---' (por defecto, excluidos).")
    ap.add_argument("--encoding", default="utf-8-sig",
                    help="Encoding del CSV (p. ej., 'utf-8-sig' o 'latin-1').")
    
    # Argumentos de archivos intermedios
    ap.add_argument("--keep-txt", action="store_true",
                    help="Si el input es PDF, conserva el TXT intermedio junto al PDF.")
    ap.add_argument("--txt-output",
                    help="Si el input es PDF, guarda el TXT intermedio en esta ruta específica.")
    
    # Depuración
    ap.add_argument("--debug", action="store_true", help="Muestra mensajes detallados de depuración.")
    # Argumento para el archivo de log
    ap.add_argument("--log", help="Ruta para guardar el archivo de log (ej: proceso.log).")    

    args = ap.parse_args()
    
    # 1. Iniciar Logs lo antes posible
    setup_logging(args.debug, log_file=args.log)
    logging.info("--- Iniciando proceso de extracción ---")

    txt_path = None
    is_temp = False

    try:
        # 2. Paso a TXT (Conversión o detección)
        txt_path, is_temp = input_to_txt(
            args.input,
            txt_output=args.txt_output,
            keep_txt=args.keep_txt
        )

        # 3. Extracción de datos (Lógica de negocio)
        rows = txt_to_rows(txt_path, include_pending=args.include_pending)
        
        if not rows:
            logging.warning("No se extrajo ninguna fila de datos. Revisa si el PDF es digital o una imagen.")
            return

        # 4. Generación de salida
        out = write_csv(rows, args.output, encoding=args.encoding)

        # Mensajes finales claros
        print(f"\n[ÉXITO] Proceso finalizado.")
        print(f" -> CSV generado: {out}")
        print(f" -> Total registros: {len(rows)}")
        
        if is_temp:
            logging.debug(f"El archivo temporal {txt_path} será eliminado.")
        elif txt_path and txt_path.exists():
            logging.info(f"Archivo intermedio conservado en: {txt_path}")

    except FileNotFoundError as e:
        logging.error(f"Fichero no encontrado: {e}")
        sys.exit(1)
    except Exception as e:
        # Captura cualquier error no controlado y lo registra
        logging.critical(f"Error inesperado durante la ejecución: {e}", exc_info=args.debug)
        sys.exit(1)

    finally:
        # 5. Limpieza garantizada de temporales
        if is_temp and txt_path is not None and txt_path.exists():
            try:
                txt_path.unlink()
                logging.debug("Archivo temporal eliminado correctamente.")
            except Exception as e:
                logging.warning(f"No se pudo eliminar el temporal {txt_path}: {e}")


if __name__ == "__main__":
    # Aseguramos que el script pueda cerrarse con Ctrl+C elegantemente
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Ejecución cancelada por el usuario.")
        sys.exit(0)