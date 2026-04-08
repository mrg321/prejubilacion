# 📊 Simulador de Jubilación y Rentas (Modelo ERE Telefónica 2026)

Este ecosistema de scripts en Python permite realizar proyecciones financieras complejas para trabajadores en transición a la jubilación. Está diseñado específicamente para contemplar las reformas de la **Ley 21/2021** y las actualizaciones de bases y complementos previstas para **2026**. Está probado sólo para el ERE de Telefónica Global Solutions de 2026, pero es válido para las jurídicas: Telefónida España,
Telefónica Soluciones, Telefónica Global Solutions, aunque con pocos cambios puede ser válido para el resto.

## 📁 Estructura del Proyecto

* **`/app`**: Núcleo de la lógica.
    * `main.py`: Punto de entrada del simulador.
    * `jubilacion.py`: Cálculo de pensión, coeficientes reductores y brecha de género.
    * `rentas.py`: Proyección de ingresos (Paro, Subsidios, CESS) hasta los 65 años.
    * `core.py`: Funciones auxiliares, manejo de fechas y validaciones.
    * `simulacion.py`: Simulador para obtener la edad ópdima de jubilación anticipada.
    * `exencion.py` : Cálculo de la exención fiscal.
    * `static/`: Tablas maestras de la Seguridad Social (IPC, bases mín/máx, coeficientes).
    * `txt2bases_csv`: Convierte el fichero 'Informe Integral de Bases de Cotización' txt obtenido desde pdf a csv.
* **`/data`**: Gestión de información persistente.
    * `inputs/`: Historial de bases de cotización del usuario (`.txt` o `.csv`).
    * `outputs/`: Informes detallados generados en formato Excel.
* **`.env`**: Configuración de variables de entorno (no incluido en el repo por seguridad).

## 🛠️ Funcionalidades Destacadas

### 1. Cálculo de Pensión con Brecha de Género
El sistema integra automáticamente el **Complemento de Brecha de Género** leyendo de `app/static/Brecha_Genero.txt`. 
- Permite hasta 4 hijos.
- Aplica proyecciones de revalorización anuales (IPC).
- Compatible con jubilación anticipada involuntaria y voluntaria.

### 2. Base Reguladora de Paro "Días Reales"
A diferencia de otros simuladores, `rentas.py` calcula la base media de los últimos 180 días utilizando el **calendario natural**.
- Identifica meses de 28, 30 y 31 días para un divisor exacto.
- Ajusta el resultado a la base mensual de 30 días requerida por el SEPE.

### 3. Simulación de Escenarios
A través de `core.py`, se pueden definir escenarios de inflación (IPC) y de incremento de bases máximas para años futuros (2027-2040).

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

---

### Tips de mantenimiento:
> [!TIP]
> Si actualizas las bases de cotización en `data/inputs/`, recuerda borrar el contenido de `app/__pycache__/` para asegurar que Python refresque todas las referencias lógicas en la próxima ejecución.