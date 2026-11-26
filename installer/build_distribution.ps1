param(
    [string]$TesseractRoot = 'C:\Program Files\Tesseract-OCR',
    [string]$InnoCompiler = 'C:\Program Files\Inno Setup 7\ISCC.exe',
    [string]$PythonExe = '',
    [string]$ArchiveRoot = '',
    [string]$DistributionRoot = '',
    [string]$WorkRoot = '',
    [string]$SourceBundleRoot = ''
)
$ErrorActionPreference = 'Stop'

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'Caminho de build vazio.' }
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $rootPath = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Length -gt $rootPath.Length) {
        $fullPath = $fullPath.TrimEnd([char[]]@([char]92, [char]47))
    }
    return $fullPath
}

function Test-PathOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$PathA,
        [Parameter(Mandatory = $true)][string]$PathB
    )
    $a = Get-NormalizedFullPath $PathA
    $b = Get-NormalizedFullPath $PathB
    if ($a.Equals($b, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    $aPrefix = if ($a.EndsWith('\')) { $a } else { $a + '\' }
    $bPrefix = if ($b.EndsWith('\')) { $b } else { $b + '\' }
    return $a.StartsWith($bPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
        $b.StartsWith($aPrefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-IsolatedBuildPaths {
    param(
        [Parameter(Mandatory = $true)][string]$DistributionPath,
        [Parameter(Mandatory = $true)][string]$WorkPath,
        [Parameter(Mandatory = $true)][string[]]$ProtectedPaths
    )
    $distribution = Get-NormalizedFullPath $DistributionPath
    $work = Get-NormalizedFullPath $WorkPath
    if (Test-Path -LiteralPath $distribution) {
        throw "A pasta de distribuicao ja existe; nada sera sobrescrito: $distribution"
    }
    if (Test-Path -LiteralPath $work) {
        throw "A pasta de trabalho ja existe; nada sera sobrescrito: $work"
    }
    if (Test-PathOverlap $distribution $work) {
        throw "As pastas de distribuicao e trabalho sao iguais ou se sobrepoem: '$distribution' e '$work'."
    }
    foreach ($protectedPath in $ProtectedPaths) {
        if ([string]::IsNullOrWhiteSpace($protectedPath)) { continue }
        if (Test-PathOverlap $distribution $protectedPath) {
            throw "A pasta de distribuicao se sobrepoe a um caminho de dados protegido: '$distribution' / '$protectedPath'."
        }
        if (Test-PathOverlap $work $protectedPath) {
            throw "A pasta de trabalho se sobrepoe a um caminho de dados protegido: '$work' / '$protectedPath'."
        }
    }
    return [pscustomobject]@{ DistributionRoot = $distribution; WorkRoot = $work }
}

$project = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($SourceBundleRoot) -or
    -not (Test-Path -LiteralPath $SourceBundleRoot -PathType Container)) {
    throw 'SourceBundleRoot deve apontar para as fontes correspondentes e licencas AGPL validadas; instalador bloqueado.'
}
$SourceBundleRoot = (Resolve-Path -LiteralPath $SourceBundleRoot).ProviderPath
foreach ($requiredSourceFile in @(
    'AGPL-3.0.txt', 'CASSI-corresponding-source.zip', 'SOURCE-MANIFEST.json',
    'PYMUPDF-2.1-source.tar.gz', 'MUPDF-1.28.6-source.tar.gz', 'TESSERACT-5.5.4-source.tar.gz'
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceBundleRoot $requiredSourceFile) -PathType Leaf)) {
        throw "Fonte/aviso AGPL obrigatorio ausente em SourceBundleRoot: $requiredSourceFile"
    }
}
if (-not (Test-Path -LiteralPath $InnoCompiler -PathType Leaf)) {
    throw "Inno Setup Compiler nao encontrado: $InnoCompiler. Informe o caminho correto com -InnoCompiler."
}
$InnoCompiler = (Resolve-Path -LiteralPath $InnoCompiler).ProviderPath
$innoVersionOutput = (& $InnoCompiler --version 2>&1 | Out-String).Trim()
$innoVersionExitCode = $LASTEXITCODE
if ($innoVersionExitCode -ne 0) {
    throw "Nao foi possivel consultar a versao do Inno Setup em '$InnoCompiler' (codigo $innoVersionExitCode): $innoVersionOutput"
}
$innoVersionMatch = [regex]::Match($innoVersionOutput, '(?i)(?:Inno\s+Setup(?:\s+Compiler)?\s*)?(\d+\.\d+(?:\.\d+)?)')
if (-not $innoVersionMatch.Success) {
    throw "Versao do Inno Setup nao reconhecida em '$InnoCompiler': $innoVersionOutput"
}
$innoVersion = [version]$innoVersionMatch.Groups[1].Value
Write-Output "INNO_SETUP_VERSION=$innoVersion"
if ($innoVersion.Major -ne 7) {
    throw "Incompatibilidade: este fluxo requer Inno Setup 7.x; encontrado $innoVersion em '$InnoCompiler'."
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $python = Join-Path $project '.venv\Scripts\python.exe'
} else {
    $python = $PythonExe
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python nao encontrado: $python" }
$python = (Resolve-Path -LiteralPath $python).ProviderPath
$pythonBasePrefixOutput = & $python -I -c 'import sys; print(sys.base_prefix)' 2>&1
if ($LASTEXITCODE -ne 0) { throw "Nao foi possivel identificar a instalacao-base do Python: $pythonBasePrefixOutput" }
$pythonBasePrefix = ($pythonBasePrefixOutput | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($pythonBasePrefix)) { throw "O Python nao retornou sys.base_prefix: $python" }
$database = Join-Path $project 'cassi.db'
if (-not (Test-Path -LiteralPath $database -PathType Leaf)) { throw "Indice inicial nao encontrado: $database" }
if ([string]::IsNullOrWhiteSpace($ArchiveRoot)) {
    $ArchiveRoot = Join-Path (Split-Path -Parent $project) 'Acervos'
}
if (-not (Test-Path -LiteralPath $ArchiveRoot -PathType Container)) { throw "Acervo inicial nao encontrado: $ArchiveRoot" }
if ([string]::IsNullOrWhiteSpace($env:ProgramData) -or -not (Test-Path -LiteralPath $env:ProgramData -PathType Container)) {
    throw 'A variavel de processo ProgramData nao aponta para uma pasta acessivel; pacote inicial cancelado.'
}
$programDataRoot = (Resolve-Path -LiteralPath $env:ProgramData).ProviderPath
$tclLibrary = Join-Path $pythonBasePrefix 'tcl\tcl8.6'
$tkLibrary = Join-Path $pythonBasePrefix 'tcl\tk8.6'
if (-not (Test-Path -LiteralPath $tclLibrary -PathType Container)) { throw "Biblioteca Tcl nao encontrada: $tclLibrary" }
if (-not (Test-Path -LiteralPath $tkLibrary -PathType Container)) { throw "Biblioteca Tk nao encontrada: $tkLibrary" }
if (-not (Test-Path -LiteralPath (Join-Path $TesseractRoot 'tesseract.exe'))) {
    throw "Tesseract nao encontrado: $TesseractRoot"
}
foreach ($language in @('eng', 'por', 'osd')) {
    if (-not (Test-Path -LiteralPath (Join-Path $TesseractRoot "tessdata\$language.traineddata"))) {
        throw "Idioma OCR ausente: $language.traineddata"
    }
}
$bundleAuditPath = Join-Path $TesseractRoot 'package-audit.json'
$versionLogPath = Join-Path $TesseractRoot 'tesseract-version.log'
if (-not (Test-Path -LiteralPath $bundleAuditPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $versionLogPath -PathType Leaf)) {
    throw 'Auditoria/versao do Tesseract ausente; pacote bloqueado.'
}
$bundleAudit = Get-Content -LiteralPath $bundleAuditPath -Raw | ConvertFrom-Json
$missingAuditLanguages = @()
foreach ($auditLanguage in @('eng', 'por', 'osd')) {
    if ($auditLanguage -notin $bundleAudit.languages) { $missingAuditLanguages += $auditLanguage }
}
if ($bundleAudit.tesseract_version -notmatch '5\.5\.4' -or
    $bundleAudit.jbig_artifact_or_imports -ne $false -or $missingAuditLanguages.Count -gt 0) {
    throw 'Auditoria do Tesseract indica versao/idiomas/JBIG incompat?veis; pacote bloqueado.'
}
$runtimeDlls = @(Get-ChildItem -LiteralPath $TesseractRoot -Filter '*.dll' -File)
if ($runtimeDlls.Count -lt 17 -or (Get-ChildItem -LiteralPath $TesseractRoot -Filter '*jbig*' -File).Count -gt 0) {
    throw 'Conjunto DLL do Tesseract incompleto ou contem JBIG; pacote bloqueado.'
}
foreach ($requiredNotice in @('TESSDATA-LICENSE.txt', 'tesseract-copyright.txt',
                              'MICROSOFT-VC-RUNTIME-NOTICE.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $TesseractRoot $requiredNotice) -PathType Leaf)) {
        throw "Aviso/licenca obrigatoria ausente do bundle Tesseract: $requiredNotice"
    }
}
$previousTclLibrary = [Environment]::GetEnvironmentVariable('TCL_LIBRARY', 'Process')
$previousTkLibrary = [Environment]::GetEnvironmentVariable('TK_LIBRARY', 'Process')
try {
    [Environment]::SetEnvironmentVariable('TCL_LIBRARY', $tclLibrary, 'Process')
    [Environment]::SetEnvironmentVariable('TK_LIBRARY', $tkLibrary, 'Process')
    $pyinstallerCheck = & $python -m PyInstaller --version 2>&1
    $pyinstallerCheckExitCode = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable('TCL_LIBRARY', $previousTclLibrary, 'Process')
    [Environment]::SetEnvironmentVariable('TK_LIBRARY', $previousTkLibrary, 'Process')
}
if ($pyinstallerCheckExitCode -ne 0) { throw "PyInstaller nao esta instalado no Python selecionado: $python. Consulte requirements-build.txt." }
$stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
if ([string]::IsNullOrWhiteSpace($DistributionRoot)) {
    $buildRoot = Join-Path $project "dist\CASSI-$stamp"
} else {
    $buildRoot = $DistributionRoot
}
if ([string]::IsNullOrWhiteSpace($WorkRoot)) {
    $work = Join-Path $project "build\pyinstaller-$stamp"
} else {
    $work = $WorkRoot
}
$protectedDataPaths = @(
    $database,
    (Join-Path $project 'certificate_analysis.db'),
    $ArchiveRoot,
    (Join-Path $programDataRoot 'CASSI')
)
$validatedBuildPaths = Assert-IsolatedBuildPaths `
    -DistributionPath $buildRoot -WorkPath $work -ProtectedPaths $protectedDataPaths
$buildRoot = $validatedBuildPaths.DistributionRoot
$work = $validatedBuildPaths.WorkRoot
$distributionParent = Split-Path -Parent $buildRoot
if (-not (Test-Path -LiteralPath $distributionParent -PathType Container)) {
    [System.IO.Directory]::CreateDirectory($distributionParent) | Out-Null
}
New-Item -ItemType Directory -Path $buildRoot -ErrorAction Stop | Out-Null
$initialDataRoot = Join-Path $buildRoot 'initial-data'
$initialDataCommand = @(
    '-I', (Join-Path $PSScriptRoot 'prepare_initial_data.py'),
    '--database', $database,
    '--archive-root', $ArchiveRoot,
    '--output', $initialDataRoot,
    '--programdata-root', $programDataRoot,
    '--expected-documents', '82',
    '--expected-pages', '683'
)
$initialDataOutput = & $python @initialDataCommand 2>&1
$initialDataExitCode = $LASTEXITCODE
if ($initialDataExitCode -ne 0) {
    $initialDataOutput | Write-Error
    throw "Preparacao do pacote inicial falhou (codigo $initialDataExitCode); nenhum instalador sera publicado."
}
$initialDataManifest = Join-Path $initialDataRoot 'manifest.json'
$initialDataPayload = Join-Path $initialDataRoot 'CASSI'
if (-not (Test-Path -LiteralPath $initialDataManifest -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $initialDataPayload 'cassi.db') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $initialDataPayload 'initial-data-manifest.json') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $initialDataPayload 'Acervos') -PathType Container)) {
    throw 'Manifesto ou payload inicial incompleto; build/instalador bloqueado.'
}
$initialDataOutput | Write-Output
$previousTclLibrary = [Environment]::GetEnvironmentVariable('TCL_LIBRARY', 'Process')
$previousTkLibrary = [Environment]::GetEnvironmentVariable('TK_LIBRARY', 'Process')
try {
    [Environment]::SetEnvironmentVariable('TCL_LIBRARY', $tclLibrary, 'Process')
    [Environment]::SetEnvironmentVariable('TK_LIBRARY', $tkLibrary, 'Process')
    & $python -m PyInstaller --noconfirm --distpath $buildRoot --workpath $work (Join-Path $project 'cassi.spec')
    $pyinstallerBuildExitCode = $LASTEXITCODE
} finally {
    [Environment]::SetEnvironmentVariable('TCL_LIBRARY', $previousTclLibrary, 'Process')
    [Environment]::SetEnvironmentVariable('TK_LIBRARY', $previousTkLibrary, 'Process')
}
if ($pyinstallerBuildExitCode -ne 0) { throw 'PyInstaller falhou; artefatos de diagnostico foram preservados.' }
$appDir = Join-Path $buildRoot 'CASSI'
$ocrDir = Join-Path $appDir 'tesseract'
$dataDir = Join-Path $ocrDir 'tessdata'
New-Item -ItemType Directory -Path $dataDir -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath (Join-Path $TesseractRoot 'tesseract.exe') -Destination $ocrDir
Get-ChildItem -LiteralPath $TesseractRoot -Filter '*.dll' -File | Copy-Item -Destination $ocrDir
foreach ($language in @('eng', 'por', 'osd')) {
    Copy-Item -LiteralPath (Join-Path $TesseractRoot "tessdata\$language.traineddata") -Destination $dataDir
}
foreach ($folder in @('configs', 'tessconfigs')) {
    $sourceFolder = Join-Path $TesseractRoot "tessdata\$folder"
    if (Test-Path -LiteralPath $sourceFolder) {
        Copy-Item -LiteralPath $sourceFolder -Destination $dataDir -Recurse
    }
}
Get-ChildItem -LiteralPath $TesseractRoot -Filter '*.txt' -File |
    Copy-Item -Destination $ocrDir -ErrorAction Stop
$sourceOutput = Join-Path $appDir 'source'
if (Test-Path -LiteralPath $sourceOutput) {
    throw "A pasta de fontes ja existe no pacote novo; nada sera sobrescrito: $sourceOutput"
}
New-Item -ItemType Directory -Path $sourceOutput -ErrorAction Stop | Out-Null
Get-ChildItem -LiteralPath $SourceBundleRoot -File |
    Where-Object { $_.Name -notmatch 'before-license-entries' } |
    Copy-Item -Destination $sourceOutput -ErrorAction Stop
$sourceLicenseDir = Join-Path $SourceBundleRoot 'licenses'
if (-not (Test-Path -LiteralPath $sourceLicenseDir -PathType Container)) {
    throw 'Diretorio de licencas das dependencias ausente do pacote de fontes.'
}
Copy-Item -LiteralPath $sourceLicenseDir -Destination $sourceOutput -Recurse -ErrorAction Stop
$sourceManifestPath = Join-Path $sourceOutput 'SOURCE-MANIFEST.json'
$sourceManifest = Get-Content -LiteralPath $sourceManifestPath -Raw | ConvertFrom-Json
$sourceArchive = Join-Path $sourceOutput $sourceManifest.cassi_source_archive.path
if (-not (Test-Path -LiteralPath $sourceArchive -PathType Leaf) -or
    (Get-FileHash -LiteralPath $sourceArchive -Algorithm SHA256).Hash -ne $sourceManifest.cassi_source_archive.sha256) {
    throw 'Hash do codigo-fonte CASSI diverge do manifesto; instalador bloqueado.'
}
foreach ($sourceFile in @('CASSI-corresponding-source.zip', 'AGPL-3.0.txt', 'SOURCE-MANIFEST.json',
                          'PYMUPDF-2.1-source.tar.gz', 'MUPDF-1.28.6-source.tar.gz',
                          'TESSERACT-5.5.4-source.tar.gz')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceOutput $sourceFile) -PathType Leaf)) {
        throw "A copia das fontes/licencas falhou: $sourceFile; instalador bloqueado."
    }
}
foreach ($auditFile in @('package-audit.json', 'tesseract-version.log')) {
    $auditSource = Join-Path $TesseractRoot $auditFile
    if (-not (Test-Path -LiteralPath $auditSource -PathType Leaf)) {
        throw "Auditoria do Tesseract ausente: $auditSource"
    }
    Copy-Item -LiteralPath $auditSource -Destination $ocrDir -ErrorAction Stop
}
$packagedModels = @(Get-ChildItem -LiteralPath $dataDir -Filter '*.traineddata' -File | Select-Object -ExpandProperty Name)
foreach ($language in @('eng', 'por', 'osd')) {
    if ("$language.traineddata" -notin $packagedModels) {
        throw "Modelo OCR nao foi incorporado ao pacote: $language.traineddata"
    }
}
if ((Get-ChildItem -LiteralPath $ocrDir -Filter '*.dll' -File).Count -lt 17 -or
    -not (Test-Path -LiteralPath (Join-Path $ocrDir 'TESSDATA-LICENSE.txt')) -or
    -not (Test-Path -LiteralPath (Join-Path $ocrDir 'MICROSOFT-VC-RUNTIME-NOTICE.txt'))) {
    throw 'A verificacao do bundle OCR falhou; instalador nao sera publicado.'
}
Copy-Item -LiteralPath (Join-Path $ocrDir 'package-audit.json') -Destination $appDir -ErrorAction Stop
Copy-Item -LiteralPath (Join-Path $ocrDir 'tesseract-version.log') -Destination $appDir -ErrorAction Stop
$installerDir = Join-Path $buildRoot 'installer'
New-Item -ItemType Directory -Path $installerDir -ErrorAction Stop | Out-Null
& $InnoCompiler "/DCassiDistDir=$appDir" "/DCassiInitialDataDir=$initialDataPayload" "/DCassiTesseractRoot=$TesseractRoot" "/O$installerDir" (Join-Path $PSScriptRoot 'CASSI.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup falhou; o pacote PyInstaller foi preservado.' }
$size = (Get-ChildItem -LiteralPath $appDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Output "PACOTE_PYINSTALLER=$appDir"
Write-Output "INSTALADOR=$installerDir"
Write-Output "TAMANHO_PACOTE_BYTES=$size"
