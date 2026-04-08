# 📊 Simulador de Jubilación y Rentas (Modelo ERE Telefónica 2026)

Herramienta en Python para simular el impacto económico completo de una salida laboral (ERE o PSI), incluyendo:

* 📊 Cálculo de jubilación anticipada
* 💰 Proyección de rentas hasta los 65 años (SEPE + empresa + CESS)
* 🧾 Cálculo de exención fiscal de la indemnización
* 📈 Estimación del valor acumulado de la pensión hasta esperanza de vida
* 🔁 Simulación iterativa de fechas de jubilación anticipada

El resultado se exporta en Excel con detalle mensual y agregados.

Está probado sólo para el ERE de Telefónica Global Solutions de 2026, pero es válido para las jurídicas: Telefónida España,
Telefónica Soluciones, Telefónica Global Solutions, aunque con pocos cambios puede ser válido para el resto.

# 🚀 ¿Qué hace este proyecto?

Este repositorio implementa un **motor de simulación económico-fiscal** orientado a procesos de salida tipo ERE/PSI (especialmente casos tipo Telefónica 2026).

A partir de datos reales del trabajador, el sistema:

1. Reconstruye su historial de cotización
2. Calcula su jubilación anticipada
3. Proyecta ingresos hasta los 65 años
4. Determina la fiscalidad de la indemnización
5. Estima la pensión futura hasta esperanza de vida
6. Permite comparar múltiples escenarios de jubilación

# 🔄 Flujo de cálculo (end-to-end)

El flujo principal está orquestado en `app/main.py`:

```text
1. Carga configuración (.env)
2. Importa / genera bases de cotización
3. Calcula jubilación anticipada
4. Proyecta rentas hasta los 65
5. Calcula exención fiscal
6. Proyecta pensión futura
7. Simula fechas alternativas de jubilación
```

## 📁 Estructura del Proyecto

```text
app/
├── main.py                  # Orquestador principal
├── core.py                  # Configuración y utilidades
├── jubilacion.py            # Cálculo de jubilación anticipada
├── rentas.py                # Proyección de ingresos
├── exencion.py              # Fiscalidad de indemnización
├── estimador_pensiones.py   # Proyección de pensión
├── simulacion.py            # Simulación de escenarios
├── txt2bases_csv.py         # Parser de bases desde TXT
├── static/                  # Parámetros estáticos en ficheros TXT/CSV

data/
├── inputs/
├── outputs/

env.example.*
requirements.txt
```

# 📥 Entradas del sistema

## 1. Archivo `.env`

Define los parámetros del trabajador y del escenario. Explicado más abajo.

---

## 2. Bases de cotización

Entrada desde:

```text
data/inputs/
```

Opciones:

* ✔ CSV limpio (`bases_cotizacion_ok.txt`)
* ✔ TXT exportado desde PDF de Seguridad Social

El sistema puede convertir automáticamente el TXT a CSV.

---

# ⚙️ Funcionalidades principales

## 📊 1. Importación y normalización de bases

* Parser automático desde TXT (Adobe PDF)
* Limpieza de datos inconsistentes
* Generación de base mensual estructurada

---

## 👴 2. Cálculo de jubilación anticipada

* Modalidad ERE / PSI
* Causa involuntaria
* Proyección de bases futuras
* Revalorización anual
* Uso de bases máximas

---

## 💰 3. Proyección de rentas hasta 65 años

Incluye:

* Prestación por desempleo (media últimos 180 días)
* Renta empresa
* Convenio especial (CESS)
* Ajustes por:

  * Edad (antes/después de 63)
  * Linealidad opcional
  * Transformación de 14 a 12 pagas

---

## 🧾 4. Cálculo de exención fiscal

* Segmentación:

  * Pre 12/02/2012 → 45 días/año
  * Post → 33 días/año
* Tope de mensualidades
* Tope fiscal por territorio
* Reducción del 30% por rendimientos irregulares
* Diferenciación:

  * ✔ ERE → puede haber exención
  * ❌ PSI → no aplica exención

---

## 📈 5. Proyección de pensión futura

* Desde jubilación hasta esperanza de vida
* Basado en:

  * IPC
  * Tablas de esperanza de vida
* Salida:

  * Serie mensual
  * Total acumulado

---

## 🔁 6. Simulación de fechas de jubilación

Funcionalidad clave del proyecto:

* Itera mes a mes la fecha de jubilación anticipada
* Calcula para cada escenario:

  * Rentas totales
  * Fiscalidad
  * Pensión acumulada
* Exporta comparativa a Excel

👉 Ideal para encontrar la fecha óptima de salida.

---

# 📤 Salidas

Generadas en:

```text
data/outputs/
```

Principales archivos:

* 📄 `Resumen_Calculo_Jubilacion.xlsx`
* 📄 `Resumen_Rentas.xlsx`
* 📄 Resultados de simulación

## ☑️ Prerrequisitos

- Python 3.12.10 ó superior (https://www.python.org/downloads/)
- Git (p. ej. git version 2.53.0.windows.2) (https://git-scm.com/install/windows)

## 🚀 Instalación Rápida

1.  **Clonar y preparar entorno:**
    ```bash
    git clone <repositorio>
    cd <repositorio>
    python -m venv venv
    source venv/bin/activate  # venv\Scripts\activate o ó .\venv\Scripts\Activate.ps1 en Windows
    ```

2.  **Instalar dependencias:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configurar Datos:**
    - Extrae tu informe pdf de bases de cotización de:
        https://portal.seg-social.gob.es/wps/portal/importass/importass/Categorias/Vida+laboral+e+informes/Informes+de+tus+cotizaciones/Informe+de+bases+de+cotizacion
    - Abre el informe en pdf con Adobe Acrobat y guárdalo como txt en `data/inputs/Informe Bases Cotización Online.txt`.
    - Tras la primera ejecución, tendrás un nuevo fichero csv en `data/inputs/bases_cotizacion.txt` que podrás retocar.
        - Recuerda que si quieres que se utilice el archivo retocado, lo deberás guardar como `data/inputs/bases_cotizacion_ok.txt`
    - Todos los parámetros utilizados para la simulación están en `app/static/` para consulta y actualización si fuera necesario.
    - Todos los datos personales se tienen que introducir en el archivo `.env`. Este archivo se coloca en la ruta raíz del proyecto y es completamente privado, no se comparte con nadie. Existen ejemplos para TdE DC, TdE FC, TGS. (env.example.tde.dc.txt, env.example.tde.fc.txt, env.example.tgs.txt)

## ⚙️ Configuración de Parámetros (`.env`)

El archivo `.env` centraliza los datos del trabajador y las condiciones del escenario. A continuación se detallan los parámetros disponibles:

### 📅 Fechas
* **`FECHA_NACIMIENTO`**: Fecha de nacimiento del trabajador. Se usa para calcular la edad exacta en el momento de la baja y las edades de jubilación (63, 65 y ordinaria).
* **`FECHA_INICIO_RELACION`**: Fecha de antigüedad en la empresa. Crucial para determinar la exención fiscal.
* **`FECHA_BAJA`**: Fecha efectiva de salida de la empresa (inicio de la situación de desempleo).
* **`FECHA_JUBILACION_ANTICIPADA`**: Fecha objetivo en la que el trabajador desea jubilarse efectivamente.

### 💰 Compensación y Salarios
* **`SALARIO_FIJO_ANUAL`** a **`POLIZA_SALUD`**: Conceptos retributivos. Algunos se incluyen en el cálculo del Salario Regulador y otros sólo se usan para calcular la exención fiscal.
* **`NUM_HIJOS`**: Número de hijos para el cálculo del **Complemento de Brecha de Género** y para determinar los topes máximos de la prestación por desempleo.

### 🛠️ Modalidad y Parámetros Económicos
* **`MODALIDAD`**: Define el marco legal del cálculo (`ERE` para despido colectivo o `PSI` para planes individuales). Cambia la lógica de indemnización y cotización.
* **`PCT_RENTA_HASTA_63 / 65`**: Porcentaje del salario neto/bruto que la empresa garantiza como renta mensual en cada tramo de edad.
* **`PCT_REVAL_CONVENIO`**: Incremento anual estimado que se aplicará a los pagos del Convenio Especial con la Seguridad Social (CESS).
* **`SEXO`** y **`APLICAR_BRECHA_GENERO`**: Determinan si se suma el complemento mensual por hijo en la pensión final (automático para mujeres, opcional para hombres según requisitos).
* **`SEDE_FISCAL`**: Se usa para definir el máximo exento (`ESTATAL`, `VIZCAYA`, `NAVARRA`, etc.). La única excepción a los 180k exentos es para la sede VIZCAYA o BIZKAIA (183.600,00 €).

### 📉 Linealidad y Ajustes (NUEVO)
* **`APLICAR_LINEALIDAD`**: Si es `true`, el simulador ajusta la renta para que el trabajador perciba una cantidad constante (lineal) desde una edad determinada, compensando la caída de ingresos en el tramo final de edad.
* **`EDAD_INICIO_LINEALIDAD`**: Edad (ej. 61) a partir de la cual se busca equilibrar la renta neta mensual.

### 📂 Entradas y Salidas
* **`INCLUIR_PENDIENTE`**: Filtrado opcional de años totalmente pendientes/guiones en Informe Bases Cotización Online.txt. Se recomienda dejar a 'false'.
* **`EXPORT_EXCEL`**: Activa o desactiva la generación del informe detallado.

## 📋 Ejemplos de Uso

```python
python ./app/main.py
```

```python
from app.jubilacion import calcular_jubilacion_anticipada
from datetime import datetime

# Ejemplo: Jubilación ERE con 2 hijos (Mujer)
df_bases_in = pd.read_csv("bases_cotizacion_ok.txt", sep=";", encoding="utf-8-sig")
resultado = calcular_jubilacion_anticipada(
    fecha_nacimiento=datetime(1968, 01, 01),
    fecha_baja_ere_despido=datetime(2026, 03, 01),
    fecha_jubilacion_anticipada=datetime(2036, 03, 01),
    modalidad="ERE",
    causa_involuntaria=True,
    aplicar_incremento_2=True,
    pct_reval_convenio=0.02,
    verbose=True,
    df_bases_in=df_bases_in,
    export_libro_excel=True,
    export_libro_excel_path="Resumen_Calculo_Jubilacion.xlsx",
    incluir_tablas_entrada_en_libro=True,
    activar_regla_prebaja_max=True,
    num_hijos=2,
    aplicar_brecha_genero=False,
    sexo="MUJER",
)
print(f"Pensión Bruta: {resultado['Pensión Bruta Mensual']} €")
```

## 🔒 Seguridad y Git

El archivo `.gitignore` está configurado para proteger tu privacidad:
- **Ignora** los archivos `.env` con tus datos personales.
- **Ignora** el contenido de `data/inputs/` y `data/outputs/` para que tus bases de cotización reales y tus informes finales no se suban a GitHub.
- **Mantiene** la estructura de carpetas gracias a los archivos `.gitkeep`.

## ⚠️ Nota Legal

Este software es una herramienta de apoyo y consulta basada en la interpretación de la ley vigente. Los cálculos definitivos deben ser validados siempre por uno mismo y carecen de cualquier valor contractual o legal.
👉 Revisar siempre con asesor fiscal.

---

# 🧠 Próximas mejoras sugeridas

* Interfaz web / dashboard
* Visualización avanzada (gráficos)

---

# 📄 Licencia

MIT License

---

# 🙌 Contribuciones

Pull requests y mejoras son bienvenidas.
Sólo se atenderán issues y peticiones de mejora desde https://github.com/mrg321/prejubilacion/issues
