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
from typing import List, Tuple
import tempfile

import fitz  # pip install pymupdf

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
    Extrae el texto de un PDF digital y lo guarda en un .txt
    de forma parecida a 'Guardar como texto' de Adobe Acrobat.
    """
    input_pdf = Path(input_pdf)

    if not input_pdf.exists():
        raise FileNotFoundError(f"No existe el archivo: {input_pdf}")

    if output_txt is None:
        output_txt = input_pdf.with_suffix(".txt")
    else:
        output_txt = Path(output_txt)

    all_pages: List[str] = []

    with fitz.open(input_pdf) as doc:
        for page in doc:
            text = page.get_text("text")
            all_pages.append(text.rstrip())

    content = "\n\n".join(all_pages).strip() + "\n"
    output_txt.write_text(content, encoding="utf-8")

    return output_txt

def prenormalize_all(text: str) -> str:
    """
    Normaliza el TXT completo para facilitar el parseo:
      - Sustituye guiones raros (—, –) por '-'
      - Bloques de 2+ guiones -> '---'
      - Inserta espacios si hay '---' pegado a texto (p. ej. '---Sin' -> '--- Sin')
      - Une variantes de 'Sin base registrada' -> __SIN_BASE__ (cruza líneas)
      - Une 'Pendiente de actualizar' -> __PENDIENTE__ (cruza líneas)
      - Quita form-feeds y comprime espacios consecutivos
    """
    t = text.replace("\r\n", "\n").replace("\r", "\n")

    # Quitar form feed y pies/cabeceras frecuentes (opcional, no imprescindible)
    t = t.replace("\f", "\n")
    # Algunas cabeceras/pies no molestan si las ignoramos por patrón de año/empresa.

    # Normalización de guiones
    t = t.replace("—", "-").replace("–", "-")
    t = re.sub(r"-{4}", "--- ---.", t)  # casos raros de 4 guiones seguidos, los separamos para no perder info
    t = re.sub(r"-{2,}", "---", t)   # Cualquier racha de 2 o más '-' -> '---'

    # Evitar '---' pegado a tokens
    t = re.sub(r"(---)(?=\S)", r"\1\n", t)

    # Cabecera de meses (si aparece en línea) no la necesitamos
    t = re.sub(r"Enero\s+Febrero\s+Marzo\s+Abril\s+Mayo\s+Junio\s+Julio\s+Agosto\s+Septiembre\s+Octubre\s+Noviembre\s+Diciembre",
               "", t, flags=re.IGNORECASE)

    # Placeholder para 'Sin base registrada' (admite saltos/espacios/símbolos intermedios)
    t = re.sub(r"Sin\s*base\s*registrada", "__SIN_BASE__", t, flags=re.IGNORECASE)

    # Placeholder para 'Pendiente de actualizar'
    t = re.sub(r"Pendiente\s*de\s*actualizar", "__PENDIENTE__", t, flags=re.IGNORECASE)

    # Comprimir espacios/tabs repetidos (dejamos saltos de línea)
    t = re.sub(r"[ \t]+", " ", t)

    # Limpiar líneas de solo espacios
    t = "\n".join([ln.strip() for ln in t.split("\n")])

    return t


def tokenize_months_from_text(text: str) -> List[str]:
    """
    Convierte una tira de texto en tokens de meses.
    Acepta:
      - __SIN_BASE__ -> 'Sin base registrada'
      - __PENDIENTE__ -> 'Pendiente'
      - --- (cualquier racha de guiones ya normalizada)
      - 4.909,50 (números con coma decimal)
    Ignora ruido residual.
    """
    tokens: List[str] = []
    for w in text.strip().split():
        if w == "__SIN_BASE__":
            tokens.append("Sin base registrada")
        elif w == "__PENDIENTE__":
            tokens.append("Pendiente")
        elif w == "---":
            tokens.append("---")
        elif NUM_RE.match(w):
            tokens.append(w)
        else:
            # A veces quedan pegados a signos ; , | . Intentamos separar y reintentar
            parts = re.split(r"([;,\|])", w)
            for part in parts:
                part = part.strip()
                if not part or part in (";", ",", "|"):
                    continue
                if part == "__SIN_BASE__":
                    tokens.append("Sin base registrada")
                elif part == "__PENDIENTE__":
                    tokens.append("Pendiente")
                elif part == "---":
                    tokens.append("---")
                elif NUM_RE.match(part):
                    tokens.append(part)
                # otros restos se ignoran
    return tokens


def parse_company_block(company: str, lines: List[str], start_idx: int) -> Tuple[List[List[str]], int]:
    """
    Recorre líneas desde start_idx, parsea filas (Año + 12 meses) hasta que
    encuentre una nueva empresa o se acaben las líneas.
    Devuelve (rows, next_index).
    """
    rows: List[List[str]] = []
    i = start_idx
    n = len(lines)

    while i < n:
        line = lines[i].strip()

        # Parada: nueva empresa o fin de bloque
        if COMPANY_RE.match(line) or line.startswith("Régimen:"):
            break

        m = YEAR_RE.match(line)
        if not m:
            i += 1
            continue

        year = m.group(1)
        tail = m.group(2).strip()

        # Recolectar tokens de meses, acumulando líneas siguientes hasta tener 12
        months: List[str] = []
        # Tokens de la misma línea del año
        months.extend(tokenize_months_from_text(tail))

        j = i + 1
        while len(months) < 12 and j < n:
            nxt = lines[j].strip()

            # Si empieza otro año/empresa/régimen, paramos (no comernos el siguiente bloque)
            if YEAR_RE.match(nxt) or COMPANY_RE.match(nxt) or nxt.startswith("Régimen:"):
                break

            # Añadir tokens (también recoge los "Sin base registrada" partidos por líneas)
            months.extend(tokenize_months_from_text(nxt))
            j += 1

        # Normalizar longitud a 12 columnas
        months = months[:12] + [""] * max(0, 12 - len(months))

        rows.append([year, company] + months)

        # Continuar desde la última línea usada
        i = j

    return rows, i


def txt_to_rows(txt_path: str | Path, include_pending: bool = False) -> List[List[str]]:
    txt_path = Path(txt_path)
    text = txt_path.read_text(encoding="utf-8", errors="ignore")

    # 1) Pre-normalización global
    text = prenormalize_all(text)

    # 2) Partir en líneas
    lines = text.split("\n")

    all_rows: List[List[str]] = []
    current_company: str | None = None
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].strip()

        # Detectar empresa
        mc = COMPANY_RE.match(line)
        if mc:
            current_company = mc.group(1).strip()
            i += 1
            # Parsear las filas que pertenezcan a esta empresa
            rows, next_i = parse_company_block(current_company, lines, i)
            all_rows.extend(rows)
            i = next_i
            continue

        i += 1

    # 3) Filtrado opcional de años totalmente pendientes/guiones
    if not include_pending:
        def keep_row(r: List[str]) -> bool:
            months = r[2:]
            return not all(m in ("Pendiente", "---", "") for m in months)
        all_rows = [r for r in all_rows if keep_row(r)]

    # 4) Orden: año descendente, empresa ascendente
    def sort_key(r: List[str]):
        try:
            y = int(r[0])
        except Exception:
            y = 0
        return (-y, r[1])

    all_rows.sort(key=sort_key)
    return all_rows

def input_to_txt(input_path: str | Path, txt_output: str | Path | None = None, keep_txt: bool = False) -> tuple[Path, bool]:
    """
    Acepta PDF o TXT.
    - Si es TXT, lo devuelve tal cual.
    - Si es PDF, lo convierte antes a TXT.
    Devuelve: (ruta_txt, is_temp)
    """
    input_path = Path(input_path)

    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo: {input_path}")

    suffix = input_path.suffix.lower()

    if suffix == ".txt":
        return input_path, False

    if suffix == ".pdf":
        if txt_output is not None:
            txt_path = Path(txt_output)
            pdf_to_text(input_path, txt_path)
            return txt_path, False

        if keep_txt:
            txt_path = input_path.with_suffix(".txt")
            pdf_to_text(input_path, txt_path)
            return txt_path, False

        tmp = tempfile.NamedTemporaryFile(prefix="bases_pdf_", suffix=".txt", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        pdf_to_text(input_path, tmp_path)
        return tmp_path, True

    raise ValueError("El input debe ser un archivo .pdf o .txt")


def write_csv(rows: List[List[str]], out_path: str | Path, encoding: str = "utf-8-sig") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding=encoding, newline="") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow(["Año", "Empresa"] + MONTHS)
        for r in rows:
            w.writerow(r)
    return out_path


def main():
    ap = argparse.ArgumentParser(
        description="Convierte PDF digital o TXT del Informe de Bases a CSV separado por ';'"
    )
    ap.add_argument("-i", "--input", required=True, help="Ruta al PDF digital o al TXT exportado.")
    ap.add_argument("-o", "--output", required=True, help="Ruta de salida CSV (separado por ';').")
    ap.add_argument("--include-pending", action="store_true",
                    help="Incluye años totalmente 'Pendiente'/'---' (por defecto, excluidos).")
    ap.add_argument("--encoding", default="utf-8-sig",
                    help="Encoding del CSV (p. ej., 'utf-8-sig' o 'latin-1').")
    ap.add_argument("--keep-txt", action="store_true",
                    help="Si el input es PDF, conserva el TXT intermedio junto al PDF.")
    ap.add_argument("--txt-output",
                    help="Si el input es PDF, guarda el TXT intermedio en esta ruta.")
    args = ap.parse_args()

    txt_path = None
    is_temp = False

    try:
        txt_path, is_temp = input_to_txt(
            args.input,
            txt_output=args.txt_output,
            keep_txt=args.keep_txt
        )

        rows = txt_to_rows(txt_path, include_pending=args.include_pending)
        out = write_csv(rows, args.output, encoding=args.encoding)

        print(f"[OK] CSV generado: {out}  (filas: {len(rows)})")
        if Path(args.input).suffix.lower() == ".pdf":
            print(f"[OK] TXT intermedio usado: {txt_path}")

    finally:
        if is_temp and txt_path is not None and txt_path.exists():
            try:
                txt_path.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()