[CmdletBinding()]
param(
    [string]$BuildRoot = 'D:\CASSI-Test-Build-20261124-121742-38437497',
    [string]$TesseractRoot = 'C:\Program Files\Tesseract-OCR'
)

$ErrorActionPreference = 'Stop'

$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
$AppRoot = Join-Path $BuildRoot 'dist\CASSI'
$ExePath = Join-Path $AppRoot 'CASSI.exe'
$TesseractTarget = Join-Path $AppRoot 'tesseract'
$StagingRoot = Join-Path $AppRoot 'tesseract.pending'
$TessdataTarget = Join-Path $StagingRoot 'tessdata'
$LicenseTarget = Join-Path $StagingRoot 'licenses'
$AuditPath = Join-Path $BuildRoot 'package-audit.json'
$AuditPending = Join-Path $BuildRoot 'package-audit.pending.json'
$VersionLog = Join-Path $BuildRoot 'tesseract-version.log'
$VersionErrorLog = Join-Path $BuildRoot 'tesseract-version.stderr.log'
$PreflightLog = Join-Path $BuildRoot 'preflight.stdout.log'
$BuildLog = Join-Path $BuildRoot 'pyinstaller.stdout.log'
$BuildErrorLog = Join-Path $BuildRoot 'pyinstaller.stderr.log'
$ExpectedExeHash = '56310AA6F86B9430B541F4B9CA543E585E34FBCEF0720519D0E0910686133502'

foreach ($Path in @($BuildRoot, $AppRoot, $ExePath, (Join-Path $AppRoot '_internal'), $TesseractRoot, (Join-Path $TesseractRoot 'tesseract.exe'), (Join-Path $TesseractRoot 'doc\LICENSE'))) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "Caminho obrigatório ausente: $Path" }
}
foreach ($Path in @($TesseractTarget, $StagingRoot, $AuditPath, $AuditPending, $VersionLog, $VersionErrorLog)) {
    if (Test-Path -LiteralPath $Path) { throw "Destino já existente; nada será sobrescrito: $Path" }
}
foreach ($Path in @($PreflightLog, $BuildLog, $BuildErrorLog)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Log do build ausente: $Path" }
}

$ExeItem = Get-Item -LiteralPath $ExePath
if ($ExeItem.Length -ne 19882437) { throw "Tamanho inesperado de CASSI.exe: $($ExeItem.Length) bytes" }
$ExeHash = (Get-FileHash -LiteralPath $ExePath -Algorithm SHA256).Hash
if ($ExeHash -ne $ExpectedExeHash) { throw "Hash inesperado de CASSI.exe: $ExeHash" }
$BuildCompletionPattern = 'Build complete! The results are available in:'
$BuildCompletionFound = @(
    Select-String -LiteralPath $BuildLog, $BuildErrorLog -Pattern $BuildCompletionPattern -SimpleMatch -ErrorAction SilentlyContinue
).Count -gt 0
if (-not $BuildCompletionFound) { throw 'Os logs stdout/stderr não comprovam conclusão bem-sucedida do PyInstaller.' }

$RequiredPackageFiles = @(
    (Join-Path $AppRoot '_internal\app.py'),
    (Join-Path $AppRoot '_internal\search_dictionary.json'),
    (Join-Path $AppRoot '_internal\streamlit\static\manifest.json'),
    (Join-Path $AppRoot '_internal\_tcl_data\init.tcl'),
    (Join-Path $AppRoot '_internal\_tk_data\tk.tcl')
)
foreach ($Path in $RequiredPackageFiles) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Recurso obrigatório do pacote ausente: $Path" }
}

$SourceDlls = @(Get-ChildItem -LiteralPath $TesseractRoot -File -Filter '*.dll')
if ($SourceDlls.Count -ne 51) { throw "Esperadas 51 DLLs do Tesseract; encontradas $($SourceDlls.Count)." }
foreach ($Language in @('eng', 'por', 'osd')) {
    $Model = Join-Path $TesseractRoot "tessdata\$Language.traineddata"
    if (-not (Test-Path -LiteralPath $Model -PathType Leaf)) { throw "Modelo obrigatório ausente: $Model" }
}
foreach ($Path in @((Join-Path $TesseractRoot 'tessdata\configs'), (Join-Path $TesseractRoot 'tessdata\tessconfigs'))) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { throw "Configuração Tesseract obrigatória ausente: $Path" }
}

New-Item -ItemType Directory -Path $TessdataTarget | Out-Null
New-Item -ItemType Directory -Path $LicenseTarget | Out-Null
Copy-Item -LiteralPath (Join-Path $TesseractRoot 'tesseract.exe') -Destination $StagingRoot
foreach ($Dll in $SourceDlls) { Copy-Item -LiteralPath $Dll.FullName -Destination $StagingRoot }
foreach ($Language in @('eng', 'por', 'osd')) {
    Copy-Item -LiteralPath (Join-Path $TesseractRoot "tessdata\$Language.traineddata") -Destination $TessdataTarget
}
foreach ($Directory in @('configs', 'tessconfigs')) {
    Copy-Item -LiteralPath (Join-Path $TesseractRoot "tessdata\$Directory") -Destination $TessdataTarget -Recurse
}
Copy-Item -LiteralPath (Join-Path $TesseractRoot 'doc\LICENSE') -Destination (Join-Path $LicenseTarget 'TESSERACT-LICENSE.txt')
foreach ($Name in @('AUTHORS', 'README')) {
    $Source = Join-Path $TesseractRoot "doc\$Name"
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        Copy-Item -LiteralPath $Source -Destination $LicenseTarget
    }
}

$StagedDlls = @(Get-ChildItem -LiteralPath $StagingRoot -File -Filter '*.dll')
if ($StagedDlls.Count -ne $SourceDlls.Count) { throw "Quantidade de DLLs copiada divergente: origem $($SourceDlls.Count), staging $($StagedDlls.Count)." }
foreach ($Dll in $SourceDlls) {
    $CopiedDll = Join-Path $StagingRoot $Dll.Name
    if ((Get-FileHash -LiteralPath $Dll.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $CopiedDll -Algorithm SHA256).Hash) {
        throw "Hash divergente na DLL copiada: $($Dll.Name)"
    }
}
foreach ($Language in @('eng', 'por', 'osd')) {
    $SourceModel = Join-Path $TesseractRoot "tessdata\$Language.traineddata"
    $CopiedModel = Join-Path $TessdataTarget "$Language.traineddata"
    if ((Get-FileHash -LiteralPath $SourceModel -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $CopiedModel -Algorithm SHA256).Hash) {
        throw "Hash divergente no modelo copiado: $Language.traineddata"
    }
}

$TesseractExe = Join-Path $StagingRoot 'tesseract.exe'
& $TesseractExe --version 1> $VersionLog 2> $VersionErrorLog
$TesseractExitCode = $LASTEXITCODE
if ($TesseractExitCode -ne 0) { throw "Tesseract --version falhou com código $TesseractExitCode. Consulte $VersionErrorLog" }
$VersionText = (Get-Content -LiteralPath $VersionLog -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($VersionText)) { $VersionText = (Get-Content -LiteralPath $VersionErrorLog -Raw).Trim() }
if ($VersionText -notmatch 'tesseract\s+v?\d') { throw 'A saída de versão do Tesseract não pôde ser validada.' }

$DbPayload = @(Get-ChildItem -LiteralPath $AppRoot -Recurse -File -Filter '*.db' -ErrorAction SilentlyContinue)
$PdfPayload = @(Get-ChildItem -LiteralPath $AppRoot -Recurse -File -Filter '*.pdf' -ErrorAction SilentlyContinue)
if ($DbPayload.Count -gt 0 -or $PdfPayload.Count -gt 0) { throw 'O pacote contém bancos ou PDFs; a auditoria foi bloqueada.' }

$Audit = [pscustomobject]@{
    build_root = $BuildRoot
    distribution = $AppRoot
    executable = $ExePath
    executable_bytes = $ExeItem.Length
    executable_sha256 = $ExeHash
    build_time = (Get-Date).ToString('o')
    tesseract_source = $TesseractRoot
    tesseract_version = $VersionText
    tesseract_exit_code = $TesseractExitCode
    tesseract_dll_count = $StagedDlls.Count
    traineddata = @('eng', 'por', 'osd')
    license_files = @('tesseract\licenses\TESSERACT-LICENSE.txt')
    sqlite_payload_files = $DbPayload.Count
    pdf_payload_files = $PdfPayload.Count
    preflight = (Get-Content -LiteralPath $PreflightLog -Raw | ConvertFrom-Json)
}
$Audit | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $AuditPending -Encoding UTF8

Move-Item -LiteralPath $StagingRoot -Destination $TesseractTarget
Move-Item -LiteralPath $AuditPending -Destination $AuditPath

if (-not (Test-Path -LiteralPath (Join-Path $TesseractTarget 'tesseract.exe') -PathType Leaf)) { throw 'Validação final: tesseract.exe ausente.' }
if (@(Get-ChildItem -LiteralPath $TesseractTarget -File -Filter '*.dll').Count -ne 51) { throw 'Validação final: quantidade de DLLs incorreta.' }
foreach ($Language in @('eng', 'por', 'osd')) {
    if (-not (Test-Path -LiteralPath (Join-Path $TesseractTarget "tessdata\$Language.traineddata") -PathType Leaf)) { throw "Validação final: idioma $Language ausente." }
}
if (-not (Test-Path -LiteralPath (Join-Path $TesseractTarget 'licenses\TESSERACT-LICENSE.txt') -PathType Leaf)) { throw 'Validação final: licença do Tesseract ausente.' }
if (-not (Test-Path -LiteralPath $AuditPath -PathType Leaf)) { throw 'Validação final: package-audit.json ausente.' }

Write-Output "DISTRIBUTION=$AppRoot"
Write-Output "EXECUTABLE_BYTES=$($ExeItem.Length)"
Write-Output "EXECUTABLE_SHA256=$ExeHash"
Write-Output "TESSERACT_DLLS=$($StagedDlls.Count)"
Write-Output "TESSERACT_VERSION_LOG=$VersionLog"
Write-Output "AUDIT=$AuditPath"
Write-Warning 'Pacote local de teste. Pendências de licenciamento ainda impedem distribuição comercial.'
