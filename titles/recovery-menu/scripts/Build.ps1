[CmdletBinding()]
param(
    # The Cafe SDK 2.12.13 and GHS v5.3.27 folders; default to CAFE_ROOT and GHS_ROOT.
    [string]$CafeRoot = $env:CAFE_ROOT,
    [string]$GhsRoot = $env:GHS_ROOT,
    [string]$DevkitPro = 'C:\devkitPro',
    [ValidateSet('DEBUG', 'NDEBUG', 'FDEBUG')]
    [string]$BuildType = 'NDEBUG',
    # With -PcfsRoot, the built title is also staged into that PCFS folder.
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
$GhsRoot = ConvertTo-WindowsPath $GhsRoot
if (-not $GhsRoot) { throw 'Pass -GhsRoot (the GHS v5.3.27 folder), or set GHS_ROOT.' }

$projectRoot = Split-Path -Parent $PSScriptRoot
$make = Join-Path $DevkitPro 'msys2\usr\bin\make.exe'

# The SDK's makefiles can't handle spaces in paths.
foreach ($folder in @($CafeRoot, $GhsRoot, $projectRoot)) {
    if ($folder -match '\s') { throw "The path has a space in it, which the Cafe SDK's makefiles can't handle: $folder" }
}

if (-not (Test-Path -LiteralPath $CafeRoot)) {
    throw "Cafe SDK not found: $CafeRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $GhsRoot 'ccppc.exe'))) {
    throw "GHS compiler not found: $GhsRoot"
}
if (-not (Test-Path -LiteralPath $make)) {
    throw "MSYS make not found: $make"
}

function ConvertTo-MsysDrivePath([string]$Path, [switch]$Cygdrive) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if ($resolved -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Expected a drive-qualified Windows path: $resolved"
    }
    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace('\', '/')
    if ($Cygdrive) {
        return "/cygdrive/$drive/$tail"
    }
    return "/$drive/$tail"
}

# The SDK makefiles call MSYS shell tools. Use explicit /c/... paths rather
# than cygpath's /home alias, which points at a different home in devkitPro.
$cafeUnix = ConvertTo-MsysDrivePath $CafeRoot
$ghsUnix = ConvertTo-MsysDrivePath $GhsRoot
# The build's own temporary folder: the user's may have a space in its path.
$tmpFolder = Join-Path $projectRoot 'build\tmp'
New-Item -ItemType Directory -Force -Path $tmpFolder | Out-Null
$tmpUnix = ConvertTo-MsysDrivePath $tmpFolder
$projectUnix = ConvertTo-MsysDrivePath $projectRoot

$env:CAFE_ROOT = $cafeUnix
$env:GHS_ROOT = $ghsUnix
$env:CAFE_GHS_VERSION = 'v5.3.27'
$env:CAFE_GHS_SUPPORTS_MAKERPL_DBG_SOURCE_ROOT = 'TRUE'
$env:CAFE_GHS_TLS_AVAILABLE = 'TRUE'
$env:CAFE_SDK = 'TRUE'
$env:TOOLCHAIN = 'ghs'
$env:USE_GHS = '1'
$env:PLATFORM = 'cafe'
$env:TMP = $tmpUnix

# The SDK's generated .dep files come out malformed on the second build
# ("missing separator"), so always build from clean objects; it takes seconds.
$objDir = Join-Path $projectRoot "build\obj\$BuildType"
if (Test-Path -LiteralPath $objDir) {
    Remove-Item -LiteralPath $objDir -Recurse -Force
}

& $make -C $projectUnix "BUILD_TYPE=$BuildType" install
if ($LASTEXITCODE -ne 0) {
    throw "Cafe build failed with exit code $LASTEXITCODE"
}

if ([string]::IsNullOrWhiteSpace($PcfsRoot)) {
    return
}
& (Join-Path $PSScriptRoot 'Stage-PCFS.ps1') -CafeRoot $CafeRoot -BuildType $BuildType -PcfsRoot $PcfsRoot
