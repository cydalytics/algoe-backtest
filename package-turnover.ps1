<#
.SYNOPSIS
    Build dist\algoe-turnover, a CPU-only turnover fit for the offline box.

.DESCRIPTION
    The parquet stays on the office machine. This zip is code only. On the
    box it reads Raw_Data (or the panel cache the backtest already built),
    fits a hit-probability x size model in numpy, and writes
    turnover_out\report.html.

    No GPU, no pip from the internet, no Node.

.PARAMETER Zip
    Also write dist\algoe-turnover.zip.

.EXAMPLE
    .\package-turnover.ps1 -Zip
#>
[CmdletBinding()]
param([switch]$Zip)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dist = Join-Path $root "dist\algoe-turnover"
$backend = Join-Path $root "backend"
$src = Join-Path $backend "src\backtest\parquet"

function Step($t) { Write-Host "`n>>> $t" -ForegroundColor Cyan }
function Note($t) { Write-Host "    $t" -ForegroundColor DarkGray }

Step "Checking the source"
$required = @(
    "__init__.py", "forecast.py", "config.py", "loader.py", "panel.py",
    "beliefs.py", "pricing.py", "cache.py", "probe.py",
    "poisson_betting.py", "inverse_poisson.py"
)
$missing = $required | Where-Object { -not (Test-Path (Join-Path $src $_)) }
if ($missing) { throw "$src is missing: $($missing -join ', ')" }

$leaks = $required |
    Where-Object { $_ -like "*.py" } |
    Where-Object { Select-String -Path (Join-Path $src $_) -Pattern '^\s*(from|import)\s+src\.' -Quiet }
if ($leaks) { throw "these would not import offline: $($leaks -join ', ')" }

Step "Selftest before packaging"
Push-Location $backend
try {
    $env:PYTHONPATH = "."
    & python -m src.backtest.parquet.forecast selftest
    if ($LASTEXITCODE -ne 0) { throw "selftest failed - not packaging" }
}
finally { Pop-Location }

Step "Clearing dist\algoe-turnover"
if (Test-Path $dist) {
    Get-ChildItem -Force $dist | Remove-Item -Recurse -Force -ErrorAction Stop
}
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$archive = Join-Path $root "dist\algoe-turnover.zip"
if (Test-Path $archive) { Remove-Item $archive -Force }

$code = Join-Path $dist "backtest"
New-Item -ItemType Directory -Force -Path $code | Out-Null
foreach ($f in $required) { Copy-Item -Force (Join-Path $src $f) $code }

Step "Verifying the bundle imports on its own"
Push-Location $dist
try {
    $env:PYTHONPATH = ""
    & python -m backtest.forecast selftest
    if ($LASTEXITCODE -ne 0) { throw "the packaged bundle does not run" }
}
finally { Pop-Location }

Get-ChildItem -Recurse -Force -Directory $dist -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

@'
pandas
numpy
pyarrow
'@ | Set-Content -Path (Join-Path $dist "requirements.txt") -Encoding UTF8

@'
@echo off
REM Proves the code on synthetic rows. Does not read Raw_Data.
setlocal
cd /d "%~dp0"
python -m backtest.forecast selftest
pause
'@ | Set-Content -Path (Join-Path $dist "0-selftest.bat") -Encoding ASCII

@'
@echo off
REM Fit on the office parquet and score the last 14 days against persistence.
REM Drag the Raw_Data folder onto this bat, or pass the path as the first argument.
setlocal
cd /d "%~dp0"
set "DATA=%~1"
if not defined DATA set "DATA=S:\Users\Yeung\20260520 Algo E\Raw_Data"
echo Fitting on "%DATA%"
echo CPU only. The parquet is not copied off this machine.
python -m backtest.forecast fit --data-dir "%DATA%" --out-dir "turnover_out" ^
    --start 2026-07-01 --end 2026-09-15 --holdout-days 14 --inner-days 7
echo.
echo Open turnover_out\report.html
pause
'@ | Set-Content -Path (Join-Path $dist "1-fit.bat") -Encoding ASCII

@'
# Turnover fit — office box

Code only. The parquet stays on this machine. No GPU and no internet.

Needs the same Python the extraction notebooks already use:

    python -c "import pandas, numpy, pyarrow; print('ok')"

## First time

1. Copy `algoe-turnover.zip` off the USB and extract it, for example to `D:\AlgoE\algoe-turnover`.
2. Double-click `0-selftest.bat`. You want `[selftest] PASS`.
3. Double-click `1-fit.bat`, or pass the parquet folder:

       1-fit.bat "S:\Users\Yeung\20260520 Algo E\Raw_Data"

4. Open `turnover_out\report.html`.

The fit uses the same pools as the backtest (HILO, HDC, CHLO, CHDC), so it
reuses `algoe_panel_cache` next to `Raw_Data` if `3-run.bat` already built
those days. Passing `--pools` with a different list builds a new cache
beside the data. Nothing is uploaded.

## What the window is split into

    fit days      coefficients are fitted here
    inner days    last 7 days before the hold-out; only picks alpha
    hold-out      last 14 days; never seen by the fit or by alpha

## What "good" looks like

Hold-out WAPE for the model is lower than persistence (next 5 min = last
5 min). The headline is every offered selection-bucket. The report also
shows the backtest cut (money this bucket or the last) so the two line up.

If alpha comes out 0, the model did not help on the inner days and the
forecast you would ship is persistence.

The `lag_state` rows show how much of the money on selections that were
quiet last bucket the model picks up. Persistence scores those as zero.

In-play SGA is dropped if you add SGA to `--pools`. SGA rarely has
per-selection odds, and the panel drops rows without odds.

## Update

Replace this folder with the new zip. Do not delete `Raw_Data`.
`turnover_out` is only the report; deleting it is safe.
'@ | Set-Content -Path (Join-Path $dist "START-HERE.md") -Encoding UTF8

if ($Zip) {
    Step "Zipping"
    Compress-Archive -Path $dist -DestinationPath $archive -CompressionLevel Optimal
    Note ("{0:N1} MB  {1}" -f ((Get-Item $archive).Length / 1MB), $archive)
}

Step "Done"
Write-Host "    $dist" -ForegroundColor Green
