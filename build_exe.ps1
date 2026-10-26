$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $PythonVersion = $null
    foreach ($Candidate in @("3.13", "3.12")) {
        & py "-$Candidate" --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $PythonVersion = $Candidate
            break
        }
    }
    if (-not $PythonVersion) {
        throw "Instala Python 3.12 o 3.13 de 64 bits y vuelve a intentarlo."
    }
    & py "-$PythonVersion" -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno virtual." }
}

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "No se pudo preparar pip." }

& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar las dependencias. Revisa la conexión a Internet y vuelve a intentarlo." }

& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "Generador_ISO_Multiple" `
    --icon "assets\icons\app.ico" `
    --add-data "assets;assets" `
    "main.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller no pudo generar el ejecutable." }

$Exe = Join-Path $PSScriptRoot "dist\Generador_ISO_Multiple.exe"
Write-Host "Ejecutable actualizado: $Exe" -ForegroundColor Green
