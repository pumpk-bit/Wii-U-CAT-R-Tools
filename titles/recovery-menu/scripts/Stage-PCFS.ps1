[CmdletBinding()]
param(
    # The Cafe SDK 2.12.13 folder; defaults to CAFE_ROOT.
    [string]$CafeRoot = $env:CAFE_ROOT,
    [ValidateSet('DEBUG', 'NDEBUG', 'FDEBUG')]
    [string]$BuildType = 'NDEBUG',
    # The PCFS folder the console sees (catr pcfs root).
    [Parameter(Mandatory = $true)]
    [string]$PcfsRoot
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

$projectRoot = Split-Path -Parent $PSScriptRoot
$titleIdHigh = '00050010'
$titleIdLow = '1f700700'

$sourceRpx = Join-Path $projectRoot "build\$BuildType\cat_recovery_menu.rpx"
if (-not (Test-Path -LiteralPath $sourceRpx)) {
    throw "Built RPX not found: $sourceRpx"
}

$titleRoot = Join-Path $PcfsRoot "data\mlc\sys\title\$titleIdHigh\$titleIdLow"
$codeRoot = Join-Path $titleRoot 'code'
$contentRoot = Join-Path $titleRoot 'content'
$metaRoot = Join-Path $titleRoot 'meta'
New-Item -ItemType Directory -Force -Path $codeRoot, $contentRoot, $metaRoot | Out-Null

Copy-Item -LiteralPath $sourceRpx -Destination (Join-Path $codeRoot 'cat_recovery_menu.rpx') -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'metadata\code\app.xml') -Destination $codeRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'metadata\code\cos.xml') -Destination $codeRoot -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'metadata\content\pcfs-proof.txt') -Destination $contentRoot -Force

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

$manifest = Get-ChildItem -LiteralPath $titleRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
    [pscustomobject]@{
        RelativePath = $_.FullName.Substring($titleRoot.Length + 1)
        Length = $_.Length
        SHA256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
    }
}
$manifestJson = ($manifest | ConvertTo-Json) -join [Environment]::NewLine
[System.IO.File]::WriteAllText((Join-Path $projectRoot 'PCFS-MANIFEST.json'), $manifestJson + [Environment]::NewLine, $utf8NoBom)

Write-Host "Staged CAT Recovery Menu to $titleRoot"
