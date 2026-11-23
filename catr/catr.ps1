# PowerShell wrapper for catr. PowerShell runs this before catr.cmd for `./catr`, so Ctrl+C
# doesn't bring up cmd.exe's "Terminate batch job (Y/N)?" prompt. Same Python lookup as
# catr.cmd: CATR_PYTHON if set, else the py launcher (py -3). Passes catr's exit code through.
$script = Join-Path $PSScriptRoot 'catr.py'

if ($env:CATR_PYTHON) {
    & $env:CATR_PYTHON -I $script @args
} elseif (Get-Command py -CommandType Application -ErrorAction SilentlyContinue) {
    & py -3 -I $script @args
} else {
    [Console]::Error.WriteLine('Python launcher (py) not found. Install Python 3 from python.org, or set CATR_PYTHON to a native Windows python.exe.')
    exit 1
}
exit $LASTEXITCODE
