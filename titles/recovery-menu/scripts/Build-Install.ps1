[CmdletBinding()]
param(
    # The Cafe SDK 2.12.13 and GHS v5.3.27 folders; default to CAFE_ROOT and GHS_ROOT.
    [string]$CafeRoot = $env:CAFE_ROOT,
    [string]$GhsRoot = $env:GHS_ROOT,
    [string]$DevkitPro = 'C:\devkitPro',
    [ValidateSet('DEBUG', 'NDEBUG', 'FDEBUG')]
    [string]$BuildType = 'NDEBUG',
    # Defaults to CAT-Recovery-Menu-vNN, from <title_version> in metadata\code\app.xml.
    [string]$OutputName
)

$ErrorActionPreference = 'Stop'

# CAFE_ROOT and GHS_ROOT may be in Windows form (C:\x) or MSYS/Cygwin form (/c/x, /cygdrive/c/x).
function ConvertTo-WindowsPath([string]$Path) {
    if ($Path -match '^(?:/cygdrive)?/([A-Za-z])/(.*)$') {
        return "$($Matches[1].ToUpperInvariant()):\$($Matches[2].Replace('/', '\'))"
    }
    return $Path
}
$CafeRoot = ConvertTo-WindowsPath $CafeRoot
if (-not $CafeRoot) { throw 'Pass -CafeRoot (the Cafe SDK 2.12.13 folder), or set CAFE_ROOT.' }
$GhsRoot = ConvertTo-WindowsPath $GhsRoot
if (-not $GhsRoot) { throw 'Pass -GhsRoot (the GHS v5.3.27 folder), or set GHS_ROOT.' }

$projectRoot = Split-Path -Parent $PSScriptRoot
$masterRoot = Join-Path $projectRoot 'master'
$codeRoot = Join-Path $masterRoot 'code'
$contentRoot = Join-Path $masterRoot 'content'
$metaRoot = Join-Path $masterRoot 'meta'
$tempBase = Join-Path $projectRoot 'master-temp'
$tempRoot = Join-Path $tempBase (Get-Date -Format 'yyyyMMdd-HHmmss')
$outputRoot = Join-Path $projectRoot 'Install'
$masteringRoot = Join-Path $CafeRoot 'system\bin\tool\mastering'
$makeCfMaster = Join-Path $masteringRoot 'makecfmaster.exe'

if (-not $OutputName) {
    [xml]$appInfo = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'metadata\code\app.xml')
    $OutputName = 'CAT-Recovery-Menu-v{0:D2}' -f [Convert]::ToInt32($appInfo.app.title_version.'#text', 16)
}

# Build.ps1 throws if the build fails.
& (Join-Path $PSScriptRoot 'Build.ps1') -CafeRoot $CafeRoot -GhsRoot $GhsRoot -DevkitPro $DevkitPro -BuildType $BuildType

New-Item -ItemType Directory -Force -Path $codeRoot, $contentRoot, $metaRoot, $tempRoot, $outputRoot | Out-Null

$builtRpx = Join-Path $projectRoot "build\$BuildType\cat_recovery_menu.rpx"
Copy-Item -LiteralPath $builtRpx -Destination (Join-Path $codeRoot 'cat_recovery_menu.rpx') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'metadata\code\app.xml') -Destination $codeRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'metadata\code\cos.xml') -Destination $codeRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'metadata\content-installed\pcfs-proof.txt') -Destination $contentRoot -Force

$fontShaderSource = Join-Path $CafeRoot 'data\disc\content\assets\shaders\lib_demo\cafe\demoFont.gsh'
$fontShaderTarget = Join-Path $contentRoot 'assets\shaders\lib_demo\cafe\demoFont.gsh'
if (-not (Test-Path -LiteralPath $fontShaderSource)) {
    throw "Required DEMO font shader not found: $fontShaderSource"
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $fontShaderTarget) | Out-Null
Copy-Item -LiteralPath $fontShaderSource -Destination $fontShaderTarget -Force

$sdkMetaRoot = Join-Path $CafeRoot 'data\mlc\sys\title\00050010\1f700500\meta'
foreach ($asset in 'bootDrcTex.tga','bootLogoTex.tga','bootMovie.h264','bootTvTex.tga','iconTex.tga') {
    Copy-Item -LiteralPath (Join-Path $sdkMetaRoot $asset) -Destination $metaRoot -Force
}

# Windows PowerShell 5.1 reads BOM-less files as ANSI and has no utf8NoBOM
# encoding, so read and write UTF-8 explicitly and keep this script ASCII-only.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
$sctJapaneseName = -join ([char[]](0x30B7,0x30B9,0x30C6,0x30E0,0x30B3,0x30F3,0x30D5,0x30A3,0x30B0,0x30C4,0x30FC,0x30EB))

$metaText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $sdkMetaRoot 'meta.xml')
$metaText = $metaText.Replace('000500101f700500', '000500101f700700')
$metaText = $metaText.Replace('System Config Tool', 'CAT Recovery Menu')
$metaText = $metaText.Replace($sctJapaneseName, 'CAT Recovery Menu')
# Keep meta.xml's title_version in step with app.xml (hex there, decimal here).
$appXml = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'metadata\code\app.xml')
if ($appXml -notmatch '<title_version[^>]*>([0-9A-Fa-f]+)</title_version>') {
    throw 'title_version not found in metadata\code\app.xml'
}
$titleVersion = '{0:D5}' -f [Convert]::ToInt32($Matches[1], 16)
$metaText = [regex]::Replace($metaText, '(<title_version type="unsignedInt" length="4">)\d+(</title_version>)', "`${1}$titleVersion`${2}")
$metaText = $metaText.Replace('<e_manual type="unsignedInt" length="4">1</e_manual>', '<e_manual type="unsignedInt" length="4">0</e_manual>')
[System.IO.File]::WriteAllText((Join-Path $metaRoot 'meta.xml'), $metaText, $utf8NoBom)

if (-not (Test-Path -LiteralPath $makeCfMaster)) {
    throw "makecfmaster.exe not found: $makeCfMaster"
}

$env:CAFE_ROOT = $CafeRoot
$env:CAFE_TEMP = $tempRoot
$env:CAFE_DATA_DIR = Join-Path $projectRoot 'data'
$env:CAFE_CONTENT_DIR = $contentRoot
$env:CAFE_META_DIR = $metaRoot
$env:CAFE_SAVE_DIR = Join-Path $CafeRoot 'save'
$env:CAFE_SLC_DIR = Join-Path $CafeRoot 'data\slc'
$env:CAFE_MLC_DIR = Join-Path $CafeRoot 'data\mlc'
$env:PATH = "$masteringRoot;$(Join-Path $CafeRoot 'system\bin\tool');$env:PATH"

$wumadBase = Join-Path $projectRoot $OutputName
Push-Location $CafeRoot
try {
    & $makeCfMaster archive -r (Join-Path $codeRoot 'cat_recovery_menu.rpx') -o $wumadBase -w $tempRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Master archive creation failed with exit code $LASTEXITCODE"
    }

    & $makeCfMaster installimage -i "$wumadBase.wumad" -o $outputRoot -w $tempRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Install image creation failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

Write-Host "Created $wumadBase.wumad"
Write-Host "Created install image under $outputRoot"
