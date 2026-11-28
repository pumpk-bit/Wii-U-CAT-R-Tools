param(
    # Where CatR-Tools.exe goes. Default: this repo's ReleaseBuilds folder (git ignores it).
    [string]$Output = '',
    # The catr sources built into it (this repo's catr folder).
    [string]$Catr = (Join-Path $PSScriptRoot '..\catr'),
    # A workspace folder (backups, captures) to build in, for an exe you only use on this PC: its path
    # is stored in the exe. Default: none, and the exe uses the Workspace folder next to itself.
    [string]$Workspace = '',
    # Default: this repo's pcfsserver\pcfsserver.exe (sprig's, see pcfsserver\CHANGES.md).
    [string]$PcfsServer = ''
)
$ErrorActionPreference = 'Stop'
# Builds one file, CatR-Tools.exe: the window, "CatR-Tools ARGS" for catr on the
# command line, and catr itself with a private Python runtime and pcfsserver.exe (unpacked to
# %LOCALAPPDATA%\CatR-Tools on first use). Needs Python 3.12 or newer (CATR_PYTHON, else py -3) and the
# .NET 10 SDK to build, and the internet the first time (nuget.org, for the SDK's own packs). Running it
# needs only the .NET 10 Desktop Runtime.
if ($env:CATR_PYTHON) { $python = $env:CATR_PYTHON; $pythonArgs = @('-I') } else { $python = 'py'; $pythonArgs = @('-3', '-I') }
if (-not (Get-Command $python -CommandType Application -ErrorAction SilentlyContinue)) {
    throw "Python not found ($python). Install Python 3 from python.org with its py launcher, or set CATR_PYTHON to a python.exe."
}
if (-not (Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue) -or
    -not (dotnet --list-sdks | Where-Object { $_ -match '^(\d+)\.' -and [int]$Matches[1] -ge 10 })) {
    throw 'The .NET 10 SDK is needed: https://dotnet.microsoft.com/download/dotnet/10.0'
}
if (-not $Output) { $Output = Join-Path $PSScriptRoot '..\ReleaseBuilds' }
$workspaceArgs = @()
if ($Workspace) {
    $Workspace = (Resolve-Path $Workspace).Path
    $workspaceArgs = @('--workspace', $Workspace)
}
if (-not $PcfsServer) { $PcfsServer = Join-Path $PSScriptRoot '..\pcfsserver\pcfsserver.exe' }

$project = Join-Path $PSScriptRoot 'CatR-Tools.csproj'
$bundle = Join-Path $PSScriptRoot 'obj\bundle\catr-bundle.zip'
$nugetConfig = Join-Path $PSScriptRoot 'NuGet.Config'

# The licenses of what is built in: shipped inside the exe, and next to it.
$licenses = Join-Path $PSScriptRoot 'obj\bundle\licenses'
New-Item -ItemType Directory -Force $licenses | Out-Null
Copy-Item (Join-Path $PSScriptRoot '..\pcfsserver\LICENSE-sprig') (Join-Path $licenses 'LICENSE-sprig.txt') -Force
Copy-Item (Join-Path $PSScriptRoot '..\pcfsserver\THIRD-PARTY-NOTICES.txt') (Join-Path $licenses 'THIRD-PARTY-NOTICES-pcfsserver.txt') -Force
Copy-Item (Join-Path (Resolve-Path $Catr) 'src\catr\vendor\LICENSE-iosuhax.md') (Join-Path $licenses 'LICENSE-iosuhax.txt') -Force
$pythonHome = & $python @pythonArgs -c 'import sys; print(sys.base_prefix if sys.version_info >= (3, 12) else str())'
if ($LASTEXITCODE -ne 0 -or -not $pythonHome) { throw "Python 3.12 or newer is needed ($python)." }
if (-not (Test-Path (Join-Path $pythonHome 'LICENSE.txt'))) {
    throw "No LICENSE.txt in $pythonHome. Build with a Python from python.org (set CATR_PYTHON to its python.exe)."
}
Copy-Item (Join-Path $pythonHome 'LICENSE.txt') (Join-Path $licenses 'LICENSE-Python.txt') -Force

& $python @pythonArgs (Join-Path $PSScriptRoot 'Bundle\make_bundle.py') --catr (Resolve-Path $Catr) @workspaceArgs --pcfs-server (Resolve-Path $PcfsServer) --licenses $licenses --out $bundle
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# One README: the window, the command line and catr's commands. Built into the exe
# (Help > Documentation) and copied next to it.
$helpDoc = Join-Path $PSScriptRoot 'obj\bundle\help.md'
& $python @pythonArgs (Join-Path $PSScriptRoot 'Bundle\make_readme.py') --app (Join-Path $PSScriptRoot 'README.md') --catr (Join-Path (Resolve-Path $Catr) 'README.md') --out $helpDoc
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

dotnet restore $project -r win-x64 -p:RestoreConfigFile=$nugetConfig
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$staging = Join-Path $PSScriptRoot 'obj\single-publish'
dotnet publish $project -c Release -r win-x64 --self-contained false -o $staging --no-restore
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

New-Item -ItemType Directory -Force $Output | Out-Null
$Output = (Resolve-Path $Output).Path
Copy-Item (Join-Path $staging 'CatR-Tools.exe') (Join-Path $Output 'CatR-Tools.exe') -Force
Copy-Item $helpDoc (Join-Path $Output 'README.md') -Force
New-Item -ItemType Directory -Force (Join-Path $Output 'licenses') | Out-Null
Copy-Item (Join-Path $licenses '*') (Join-Path $Output 'licenses') -Force
# The plugins for the console's SD card, next to the exe, and the debugger plugin's PC side.
Copy-Item (Join-Path $PSScriptRoot '..\plugins\*\*.ipx') $Output -Force
Copy-Item (Join-Path $PSScriptRoot '..\plugins\dk-tcp\dk_gdb.py') $Output -Force
# The CAT Recovery Menu's install folder, for System Config Tool on the SD card, when it has been built
# (titles\recovery-menu\scripts\Build-Install.ps1 needs the Cafe SDK and GHS, so this build doesn't run it).
$menuInstall = Join-Path $PSScriptRoot '..\titles\recovery-menu\Install'
$menus = @(Get-ChildItem -LiteralPath $menuInstall -Directory -Filter 'CAT-Recovery-Menu-*' -ErrorAction SilentlyContinue)
if ($menus) {
    foreach ($menu in $menus) {
        $target = Join-Path $Output $menu.Name
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Copy-Item -LiteralPath $menu.FullName -Destination $target -Recurse
    }
} else {
    Write-Warning 'No CAT Recovery Menu in the release: build it first with titles\recovery-menu\scripts\Build-Install.ps1.'
}
$where = if ($Workspace) { "workspace $Workspace" } else { 'no workspace built in' }
Write-Host "Built: $(Join-Path $Output 'CatR-Tools.exe') ($where)"
