# Generador ISO Múltiple

Aplicación de escritorio para Windows que distribuye contenido variable entre varias imágenes ISO y repite el contenido permanente en todas ellas. La distribución usa **First-Fit Decreasing (FFD)** y los nombres de salida son siempre automáticos:

```text
ISO01.iso
ISO02.iso
ISO03.iso
...
```

No se solicitan nombres ni se permite configurar etiquetas de volumen.

## Requisitos

- Windows 10 u 11 de 64 bits.
- Python 3.12 o posterior.
- Espacio libre suficiente para crear todos los ISO.

## Instalación

Abre PowerShell y ejecuta:

```powershell
cd "$env:USERPROFILE\Desktop\Generador_ISO_Multiple"
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Ejecución

Con el entorno virtual activado:

```powershell
python main.py
```

Flujo de trabajo:

1. Agrega archivos o carpetas permanentes; aparecerán en todos los ISO.
2. Agrega archivos o carpetas variables; sus archivos se distribuyen con FFD y se conserva la estructura de carpetas.
3. Selecciona 3.5, 3.7, 4.0, 4.2 GB o una capacidad personalizada.
4. Elige una carpeta de salida.
5. Pulsa **Calcular distribución** y revisa la vista previa.
6. Pulsa **Generar ISO**.

Las capacidades usan GB decimales (`1 GB = 1,000,000,000 bytes`), como las capacidades comerciales de medios ópticos. El cálculo reserva espacio para sectores, directorios y tablas ISO. Al terminar también se comprueba el tamaño real de cada imagen.

## Validaciones

La aplicación bloquea:

- contenido permanente que no cabe;
- un archivo o carpeta variable que no cabe junto con los permanentes;
- rutas inexistentes, ilegibles o que cambian antes de generar;
- rutas duplicadas y nombres que colisionarían en la raíz del ISO;
- enlaces simbólicos a carpetas;
- generación sin carpeta de salida.

Si ya existe `ISO01.iso`, `ISO02.iso`, etc., el archivo correspondiente se reemplaza únicamente después de que la nueva imagen se haya construido correctamente. Imágenes sobrantes de ejecuciones anteriores no se borran de forma automática.

## Formato de los ISO

Se usa pycdlib con ISO9660 nivel 3, Joliet nivel 3 y UDF 2.60. Los nombres que superan el limite de Joliet reciben un alias corto y estable en esa vista; UDF conserva el nombre original. UDF almacena fechas de creacion, modificacion y ultimo acceso, mientras Joliet conserva la fecha de modificacion para compatibilidad con Windows.

ISO9660 contiene obligatoriamente un campo interno de identificación de volumen. La aplicación lo deja **vacío** (relleno mínimo exigido por el formato), no lo muestra en la interfaz y no permite modificarlo.

Los temporales se crean en la carpeta de salida con un nombre oculto y terminación `.tmp`. Solo se publican como `ISOxx.iso` cuando todo el conjunto terminó; ante un error o cancelación se eliminan.

## Compilar un EXE con PyInstaller

Instala primero `requirements.txt` y, desde la raíz del proyecto, ejecuta:

```powershell
pyinstaller --noconfirm --clean --onefile --windowed `
  --name Generador_ISO_Multiple `
  --icon "assets\icons\app.ico" `
  --add-data "assets;assets" `
  main.py
```

El ejecutable quedará en:

```text
dist\Generador_ISO_Multiple.exe
```

La compilación debe hacerse en Windows y con Python 3.12+ de la misma arquitectura que el equipo de destino.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

## Módulos

- `main.py`: arranque de Qt.
- `gui.py`: interfaz, vista previa y ejecución en segundo plano.
- `packing.py`: planificación FFD y control de capacidad.
- `iso_builder.py`: construcción transaccional con `pycdlib`.
- `file_utils.py`: escaneo, tamaños, duplicados y manifiestos.

## Dependencias principales

- [PySide6](https://doc.qt.io/qtforpython-6/)
- [pycdlib](https://clalancette.github.io/pycdlib/)
- [PyInstaller](https://pyinstaller.org/)
