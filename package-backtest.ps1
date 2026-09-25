<#
.SYNOPSIS
    Build dist\algoe-backtest, the standalone parquet backtest for the offline box.

.DESCRIPTION
    Everything needed to go from raw parquet to a finished HTML report, with no
    internet and no web server: the backtest package, the desk's Poisson modules,
    launchers, and the README.

    This is deliberately separate from package.ps1, which builds the live desk
    (Next server + FastAPI). Nothing here needs Node, and nothing here needs the
    API to be running - it is a script you run and a file you open.

.PARAMETER Wheels
    Vendor the Python wheels so the target can install with --no-index. Requires
    internet on THIS machine, and the wheel tags must match the TARGET
    interpreter, not this one.

.PARAMETER PythonVersion
    Target interpreter as "311" or "312". Binary wheels are per-version: a cp312
    wheel will not install on 3.11. Find it on the target with
        python -c "import sys;print(sys.version)"

.PARAMETER Zip
    Also produce dist\algoe-backtest.zip.

.EXAMPLE
    .\package-backtest.ps1 -Zip
    .\package-backtest.ps1 -Wheels -PythonVersion 311 -Zip
#>

[CmdletBinding()]
param(
    [switch]$Wheels,
    [ValidatePattern('^3\d{2}$')]
    [string]$PythonVersion,
    [ValidateSet("win_amd64", "win32", "win_arm64")]
    [string]$Platform = "win_amd64",
    [switch]$Zip
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dist = Join-Path $root "dist\algoe-backtest"
$backend = Join-Path $root "backend"
$src = Join-Path $backend "src\backtest\parquet"

function Step($t) { Write-Host "`n>>> $t" -ForegroundColor Cyan }
function Note($t) { Write-Host "    $t" -ForegroundColor DarkGray }

# ---------------------------------------------------------------------------
# The engine lives in the backend so the web app and this bundle run the same
# code. Three of its modules reach back into the rest of the backend and are
# deliberately left out: they are what turns cached days into the workbench's
# fact table, which is an API concern. The bundle's own path to a report goes
# through run.py and does not touch them.
Step "Checking the source"
$required = @(
    "__init__.py", "run.py", "config.py", "loader.py", "probe.py", "panel.py",
    "beliefs.py", "bins.py", "evaluate.py", "optimize.py", "pricing.py", "cache.py",
    "report_html.py", "selftest.py", "README.md",
    "poisson_betting.py", "inverse_poisson.py"
)
$excluded = @("adapter.py", "jobs.py", "service.py")

$missing = $required | Where-Object { -not (Test-Path (Join-Path $src $_)) }
if ($missing) {
    throw "$src is missing: $($missing -join ', ')"
}
Note "$($required.Count) files present, $($excluded.Count) API-only files skipped"

# Anything shipped must stand alone. A stray "from src." would only fail once
# the bundle was already on the offline machine.
$leaks = $required |
    Where-Object { $_ -like "*.py" } |
    Where-Object { Select-String -Path (Join-Path $src $_) -Pattern '^\s*(from|import)\s+src\.' -Quiet }
if ($leaks) {
    throw "these would not import offline - they depend on the backend: $($leaks -join ', ')"
}
Note "no backend imports in the shipped modules"

# ---------------------------------------------------------------------------
# The selftest is the gate. It builds synthetic parquet in the raw schema, runs
# the full pipeline including the optimizer, checks the pricer against
# poisson_betting, and asserts the report came out. Shipping a bundle that fails
# it means debugging on the air-gapped machine, which is the whole thing we are
# trying to avoid.
Step "Running the selftest before packaging"
Push-Location $backend
try {
    $env:PYTHONPATH = "."
    & python -m src.backtest.parquet.run selftest
    if ($LASTEXITCODE -ne 0) { throw "selftest failed - not packaging" }
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
Step "Clearing dist\algoe-backtest"
if (Test-Path $dist) {
    Get-ChildItem -Force $dist | Remove-Item -Recurse -Force -ErrorAction Stop
}
New-Item -ItemType Directory -Force -Path $dist | Out-Null
# A zip left over from a previous build would be mistaken for this one.
$archive = Join-Path $root "dist\algoe-backtest.zip"
if (Test-Path $archive) { Remove-Item $archive -Force }

# ---------------------------------------------------------------------------
Step "Copying the backtest"
$code = Join-Path $dist "backtest"
New-Item -ItemType Directory -Force -Path $code | Out-Null
foreach ($f in $required) {
    Copy-Item -Force (Join-Path $src $f) $code
}
# A sample report so the reader can see the output format before their own run
# finishes, and so a broken run is obvious by comparison.
if (Test-Path (Join-Path $src "sample_report\report.html")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $dist "sample_report") | Out-Null
    Copy-Item -Force (Join-Path $src "sample_report\*") (Join-Path $dist "sample_report")
    Note "sample_report included"
}
Note ("backtest: {0} python files" -f (Get-ChildItem $code -Filter *.py).Count)

# ---------------------------------------------------------------------------
# The bundle must import as a package, because the engine uses relative imports
# so that one copy of it can serve both the API and this. That means running it
# as "python -m backtest.run" from the bundle root, never "python run.py".
Step "Verifying the bundle imports on its own"
Push-Location $dist
try {
    $env:PYTHONPATH = ""
    & python -m backtest.run --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "the packaged bundle does not import" }
}
finally { Pop-Location }
Note "python -m backtest.run works from the bundle root"

# After the verify, not before: importing the package is what writes the cache,
# and a .pyc built by this machine's interpreter is at best useless on the target.
Get-ChildItem -Recurse -Force -Directory $dist -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force

# ---------------------------------------------------------------------------
Step "Writing requirements and launchers"
@"
# The backtest itself needs only these three.
pandas
numpy
pyarrow
# scipy powers the SLSQP theta solve in --optimize. Without it the optimizer
# stage still runs, but falls back to a coarser grid search.
scipy
"@ | Set-Content -Path (Join-Path $dist "requirements.txt") -Encoding UTF8

# The engine uses relative imports so the API and this bundle share one copy of
# it, which means it has to be started as a module from the bundle root. Hence
# the "cd /d %~dp0" in every launcher: run.py on its own would not import.
@'
@echo off
REM Verify the code on synthetic data in a temp folder. Ignores out\facts.
setlocal
cd /d "%~dp0"
echo Selftest uses synthetic data in a temp folder. Copied out\facts is ignored.
python -m backtest.run selftest
pause
'@ | Set-Content -Path (Join-Path $dist "0-selftest.bat") -Encoding ASCII

@'
@echo off
REM Step 1: check the data. Reads parquet metadata only, finishes in seconds.
setlocal
cd /d "%~dp0"
set "DATA=%~1"
if not defined DATA set "DATA=S:\Users\Yeung\20260520 Algo E\Raw_Data"
echo Probing "%DATA%"
python -m backtest.run probe --data-dir "%DATA%" --out-dir "out"
echo.
echo Read out\probe.txt before running the backtest.
pause
'@ | Set-Content -Path (Join-Path $dist "1-probe.bat") -Encoding ASCII

@'
@echo off
REM Step 2: two days only, to confirm the run works end to end.
setlocal
cd /d "%~dp0"
set "DATA=%~1"
if not defined DATA set "DATA=S:\Users\Yeung\20260520 Algo E\Raw_Data"
python -m backtest.run run --data-dir "%DATA%" --out-dir "out-smoke" ^
    --max-days 2 --optimize --optimize-every 12 --optimize-max-buckets 300
echo.
echo Open out-smoke\report.html
pause
'@ | Set-Content -Path (Join-Path $dist "2-smoke.bat") -Encoding ASCII

@'
@echo off
REM Step 3: turnover + belief on every row. No TG/SUP solver.
setlocal
cd /d "%~dp0"
set "DATA=%~1"
if not defined DATA set "DATA=S:\Users\Yeung\20260520 Algo E\Raw_Data"
echo Scoring turnover and belief on every row. TG/SUP solve is 4-optimize.bat.
python -m backtest.run run --data-dir "%DATA%" --out-dir "out" ^
    --start 2026-07-01 --end 2026-09-15
echo.
echo Open out\report.html for WAPE and ECE. Then 4-optimize.bat for TG/SUP lift.
pause
'@ | Set-Content -Path (Join-Path $dist "3-run.bat") -Encoding ASCII

@'
@echo off
REM Step 4: sample-solve TG/SUP on already-cached days. Slow. Does not rebuild panels.
setlocal
cd /d "%~dp0"
set "DATA=%~1"
if not defined DATA set "DATA=S:\Users\Yeung\20260520 Algo E\Raw_Data"
echo Solving TG/SUP on cached days. Turnover and belief scores stay as they are.
python -m backtest.run run --data-dir "%DATA%" --out-dir "out" ^
    --start 2026-07-01 --end 2026-09-15 --optimize
echo.
echo Open out\report.html — section 4 is the solver.
pause
'@ | Set-Content -Path (Join-Path $dist "4-optimize.bat") -Encoding ASCII

# ---------------------------------------------------------------------------
if ($Wheels) {
    Step "Vendoring Python wheels"
    if (-not $PythonVersion) {
        $PythonVersion = (python -c "import sys;print('{}{}'.format(*sys.version_info[:2]))")
        Note "no -PythonVersion given; using this machine's $PythonVersion"
    }
    $tag = "cp$PythonVersion-$Platform"
    $vendor = Join-Path $dist "vendor"
    New-Item -ItemType Directory -Force -Path $vendor | Out-Null
    Note "wheels for $tag - the target must match this exactly"
    # --only-binary=:all: or pip may hand back a source tarball, which then needs
    # a compiler on the offline machine.
    & python -m pip download -r (Join-Path $dist "requirements.txt") -d $vendor `
        --python-version $PythonVersion --platform $Platform --only-binary=:all:
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "pip download failed; the bundle ships without wheels"
    }
    else {
        Set-Content -Path (Join-Path $vendor "TARGET.txt") -Value @"
These wheels are for Python 3.$($PythonVersion.Substring(1)) on $Platform ($tag).

Confirm the target matches:
    python -c "import sys, sysconfig; print(sys.version); print(sysconfig.get_platform())"

Install with no network:
    python -m pip install --no-index --find-links vendor -r requirements.txt
"@
        Note ("vendor: {0} files" -f (Get-ChildItem $vendor -File).Count)
    }
}

# ---------------------------------------------------------------------------
Step "Copying the README"
Copy-Item -Force (Join-Path $src "README.md") (Join-Path $dist "START-HERE.md")

if ($Zip) {
    Step "Zipping"
    Compress-Archive -Path $dist -DestinationPath $archive -CompressionLevel Optimal
    Note ("{0:N1} MB  {1}" -f ((Get-Item $archive).Length / 1MB), $archive)
}

Step "Done"
Write-Host "    $dist" -ForegroundColor Green
Write-Host "    Copy it over, then read START-HERE.md`n"
