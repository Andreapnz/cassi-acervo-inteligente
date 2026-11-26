[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [string]$TesseractRoot = 'C:\Program Files\Tesseract-OCR'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = Join-Path $project '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python da .venv nao encontrado: $PythonExe"
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).ProviderPath
$spec = Join-Path $project 'cassi.spec'
if (-not (Test-Path -LiteralPath $spec -PathType Leaf)) {
    throw "Arquivo cassi.spec nao encontrado: $spec"
}

# Use a fresh, isolated destination on D:. Never reuse existing build output.
$driveRoot = 'D:\'
if (-not (Test-Path -LiteralPath $driveRoot -PathType Container)) {
    throw 'A unidade D: nao esta disponivel. Nenhum build foi iniciado.'
}
$stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$runRoot = Join-Path $driveRoot "CASSI-Test-Build-$stamp"
$distRoot = Join-Path $runRoot 'dist'
$workRoot = Join-Path $runRoot 'work'
$appRoot = Join-Path $distRoot 'CASSI'
$protected = @(
    $project,
    (Join-Path $project 'cassi.db'),
    (Join-Path $project 'certificate_analysis.db'),
    (Join-Path (Split-Path -Parent $project) 'Acervos'),
    (Join-Path $env:ProgramData 'CASSI')
)
foreach ($path in @($runRoot, $distRoot, $workRoot)) {
    if (Test-Path -LiteralPath $path) {
        throw "Destino existente; nada sera sobrescrito: $path"
    }
    foreach ($protectedPath in $protected) {
        $full = [IO.Path]::GetFullPath($path).TrimEnd('\') + '\'
        $guard = [IO.Path]::GetFullPath($protectedPath).TrimEnd('\') + '\'
        if ($full.StartsWith($guard, [StringComparison]::OrdinalIgnoreCase) -or
            $guard.StartsWith($full, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Destino de build sobreposto a dados protegidos: $path / $protectedPath"
        }
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $TesseractRoot 'tesseract.exe') -PathType Leaf)) {
    throw "tesseract.exe nao encontrado em: $TesseractRoot"
}
foreach ($language in @('eng', 'por', 'osd')) {
    if (-not (Test-Path -LiteralPath (Join-Path $TesseractRoot "tessdata\$language.traineddata") -PathType Leaf)) {
        throw "Modelo OCR obrigatorio ausente: $language.traineddata"
    }
}
$tesseractLicense = Join-Path $TesseractRoot 'doc\LICENSE'
if (-not (Test-Path -LiteralPath $tesseractLicense -PathType Leaf)) {
    throw "Licenca do Tesseract nao encontrada: $tesseractLicense"
}

# The output/work directories are created only after all paths and inputs pass validation.
New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null
New-Item -ItemType Directory -Path $distRoot -ErrorAction Stop | Out-Null
New-Item -ItemType Directory -Path $workRoot -ErrorAction Stop | Out-Null
$preflightScript = Join-Path $workRoot 'tcltk_preflight.py'
$preflightOut = Join-Path $runRoot 'preflight.stdout.log'
$preflightErr = Join-Path $runRoot 'preflight.stderr.log'
$buildOut = Join-Path $runRoot 'pyinstaller.stdout.log'
$buildErr = Join-Path $runRoot 'pyinstaller.stderr.log'
$tesseractOut = Join-Path $runRoot 'tesseract-version.log'
    $tesseractErr = Join-Path $runRoot 'tesseract-version.stderr.log'

@'
import json
import sqlite3
import struct
import sys
import tkinter
from importlib.metadata import PackageNotFoundError, version
from PyInstaller.utils.hooks.tcl_tk import TclTkInfo
import fitz
import pytesseract
import streamlit

if sys.version_info[:3] != (3, 12, 10) or struct.calcsize("P") != 8:
    raise SystemExit("Expected CPython 3.12.10 x64")
tcl = tkinter.Tcl()
tcl_version = tcl.eval("info patchlevel")
root = tkinter.Tk()
root.withdraw()
root.update_idletasks()
tk_version = root.tk.call("package", "require", "Tk")
root.destroy()
info = TclTkInfo()
result = {
    "python": sys.version.split()[0],
    "architecture": struct.calcsize("P") * 8,
    "tcl": tcl_version,
    "tk": tk_version,
    "tcltk_available": info.available,
    "tcltk_data_files": len(info.data_files),
    "pyinstaller": version("PyInstaller"),
    "streamlit": version("streamlit"),
    "pytesseract": version("pytesseract"),
    "sqlite": sqlite3.sqlite_version,
    "pymupdf_import": getattr(fitz, "VersionBind", "unknown"),
}
try:
    result["pymupdf"] = version("PyMuPDF")
except PackageNotFoundError:
    result["pymupdf"] = None
print(json.dumps(result, ensure_ascii=False))
if not info.available or len(info.data_files) != 926:
    raise SystemExit("TclTkInfo nao confirmou available=True e 926 arquivos")
'@ | Set-Content -LiteralPath $preflightScript -Encoding UTF8

$previousTcl = [Environment]::GetEnvironmentVariable('TCL_LIBRARY', 'Process')
$previousTk = [Environment]::GetEnvironmentVariable('TK_LIBRARY', 'Process')
try {
    $basePrefixOutput = & $PythonExe -I -c 'import sys; print(sys.base_prefix)' 2>&1
    $basePrefixExit = $LASTEXITCODE
    if ($basePrefixExit -ne 0) { throw "Falha ao identificar sys.base_prefix: $basePrefixOutput" }
    $basePrefix = ($basePrefixOutput | Out-String).Trim()
    $tclLibrary = Join-Path $basePrefix 'tcl\tcl8.6'
    $tkLibrary = Join-Path $basePrefix 'tcl\tk8.6'
    if (-not (Test-Path -LiteralPath (Join-Path $tclLibrary 'init.tcl') -PathType Leaf)) {
        throw "init.tcl nao encontrado: $tclLibrary"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $tkLibrary 'tk.tcl') -PathType Leaf)) {
        throw "tk.tcl nao encontrado: $tkLibrary"
    }
    [Environment]::SetEnvironmentVariable('TCL_LIBRARY', $tclLibrary, 'Process')
    [Environment]::SetEnvironmentVariable('TK_LIBRARY', $tkLibrary, 'Process')

    $preflight = Start-Process -FilePath $PythonExe -ArgumentList @("`"$preflightScript`"") `
        -WorkingDirectory $project -RedirectStandardOutput $preflightOut `
        -RedirectStandardError $preflightErr -WindowStyle Hidden -Wait -PassThru
    Write-Output "PREFLIGHT_EXIT=$($preflight.ExitCode)"
    if (Test-Path $preflightOut) { Get-Content -LiteralPath $preflightOut }
    if (Test-Path $preflightErr) { Get-Content -LiteralPath $preflightErr }
    if ($preflight.ExitCode -ne 0) {
        throw "Pre-teste Tcl/Tk/PyInstaller falhou; PyInstaller nao foi iniciado. Consulte $runRoot"
    }

    $arguments = '-m PyInstaller --noconfirm --distpath "' + $distRoot +
        '" --workpath "' + $workRoot + '" "' + $spec + '"'
    $build = Start-Process -FilePath $PythonExe -ArgumentList $arguments `
        -WorkingDirectory $project -RedirectStandardOutput $buildOut `
        -RedirectStandardError $buildErr -WindowStyle Hidden -Wait -PassThru
    Write-Output "PYINSTALLER_EXIT=$($build.ExitCode)"
    Write-Output "BUILD_LOG=$buildOut"
    Write-Output "BUILD_ERROR_LOG=$buildErr"
    if ($build.ExitCode -ne 0) {
        if (Test-Path $buildOut) { Get-Content -LiteralPath $buildOut -Tail 80 }
        if (Test-Path $buildErr) { Get-Content -LiteralPath $buildErr -Tail 80 }
        throw "PyInstaller falhou. Artefatos de diagnostico preservados em $runRoot"
    }

    if (-not (Test-Path -LiteralPath (Join-Path $appRoot 'CASSI.exe') -PathType Leaf)) {
        throw "Build terminou sem CASSI.exe: $appRoot"
    }
    $ocrRoot = Join-Path $appRoot 'tesseract'
    $tessdataRoot = Join-Path $ocrRoot 'tessdata'
    New-Item -ItemType Directory -Path $tessdataRoot -ErrorAction Stop | Out-Null
    Copy-Item -LiteralPath (Join-Path $TesseractRoot 'tesseract.exe') -Destination $ocrRoot
    $sourceDlls = @(Get-ChildItem -LiteralPath $TesseractRoot -File -Filter '*.dll')
    if ($sourceDlls.Count -eq 0) { throw "Nenhuma DLL do Tesseract encontrada em $TesseractRoot" }
    $sourceDlls | Copy-Item -Destination $ocrRoot
    foreach ($language in @('eng', 'por', 'osd')) {
        Copy-Item -LiteralPath (Join-Path $TesseractRoot "tessdata\$language.traineddata") -Destination $tessdataRoot
    }
    foreach ($folder in @('configs', 'tessconfigs')) {
        $source = Join-Path $TesseractRoot "tessdata\$folder"
        if (Test-Path -LiteralPath $source -PathType Container) {
            Copy-Item -LiteralPath $source -Destination $tessdataRoot -Recurse
        }
    }
    $licenseRoot = Join-Path $ocrRoot 'licenses'
    New-Item -ItemType Directory -Path $licenseRoot -ErrorAction Stop | Out-Null
    Copy-Item -LiteralPath $tesseractLicense -Destination (Join-Path $licenseRoot 'TESSERACT-LICENSE.txt')
    foreach ($name in @('AUTHORS', 'README')) {
        $source = Join-Path $TesseractRoot "doc\$name"
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination $licenseRoot
        }
    }

    $requiredFiles = @(
        (Join-Path $appRoot 'CASSI.exe'),
        (Join-Path $appRoot '_internal\app.py'),
        (Join-Path $appRoot '_internal\search_dictionary.json'),
        (Join-Path $appRoot '_internal\streamlit\static\manifest.json'),
        (Join-Path $appRoot '_internal\_tcl_data\init.tcl'),
        (Join-Path $appRoot '_internal\_tk_data\tk.tcl'),
        (Join-Path $ocrRoot 'tesseract.exe'),
        (Join-Path $tessdataRoot 'eng.traineddata'),
        (Join-Path $tessdataRoot 'por.traineddata'),
        (Join-Path $tessdataRoot 'osd.traineddata'),
        (Join-Path $licenseRoot 'TESSERACT-LICENSE.txt')
    )
    $missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) })
    if ($missing.Count) { throw "Arquivos obrigatorios ausentes:`n$($missing -join "`n")" }
    $databasePayload = @(Get-ChildItem -LiteralPath $appRoot -Recurse -File -Filter '*.db' -ErrorAction SilentlyContinue)
    $pdfPayload = @(Get-ChildItem -LiteralPath $appRoot -Recurse -File -Filter '*.pdf' -ErrorAction SilentlyContinue)
    if ($databasePayload.Count -or $pdfPayload.Count) {
        throw 'O pacote de teste contem banco ou PDF; nenhum dado real deve entrar neste build.'
    }

    $tesseract = Start-Process -FilePath (Join-Path $ocrRoot 'tesseract.exe') `
        -ArgumentList '--version' -WorkingDirectory $ocrRoot `
        -RedirectStandardOutput $tesseractOut -RedirectStandardError $tesseractErr `
        -WindowStyle Hidden -Wait -PassThru
    if ($tesseract.ExitCode -ne 0) { throw "Tesseract empacotado nao inicializou (codigo $($tesseract.ExitCode))." }

    $exe = Get-Item -LiteralPath (Join-Path $appRoot 'CASSI.exe')
    $audit = [pscustomobject]@{
        build_root = $runRoot
        distribution = $appRoot
        executable = $exe.FullName
        executable_bytes = $exe.Length
        build_time = (Get-Date).ToString('o')
        tesseract_dll_count = $sourceDlls.Count
        traineddata = @('eng', 'por', 'osd')
        sqlite_payload_files = $databasePayload.Count
        pdf_payload_files = $pdfPayload.Count
        preflight = (Get-Content -LiteralPath $preflightOut -Raw | ConvertFrom-Json)
    }
    $auditPath = Join-Path $runRoot 'package-audit.json'
    $audit | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $auditPath -Encoding UTF8
    Write-Output "DISTRIBUTION=$appRoot"
    Write-Output "EXECUTABLE_BYTES=$($exe.Length)"
    Write-Output "TESSERACT_DLLS=$($sourceDlls.Count)"
    Write-Output "TESSERACT_VERSION_LOG=$tesseractOut"
    Write-Output "AUDIT=$auditPath"
    Write-Warning 'Build local de teste apenas. PyMuPDF/Inno Setup e licencas de DLLs do Tesseract ainda precisam de liberacao antes de distribuicao.'
} finally {
    [Environment]::SetEnvironmentVariable('TCL_LIBRARY', $previousTcl, 'Process')
    [Environment]::SetEnvironmentVariable('TK_LIBRARY', $previousTk, 'Process')
}
