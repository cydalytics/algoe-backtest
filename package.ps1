<#
.SYNOPSIS
    Build a self-contained Algo E bundle for the offline machine.

.DESCRIPTION
    Produces dist/algoe-mvp, a folder that runs with nothing but Python and
    Node already on the target box - no npm registry, no pip index.

    The frontend ships as a Next standalone server: .next/standalone carries
    only the node_modules it actually imports, so the 900 MB development
    node_modules never has to cross the air gap.

    The backend ships as source. It is source on this machine too, which is
    the point: what you debug there is what you read here.

    Optionally vendors Python wheels with -Wheels. Only do that from a
    machine whose Python version and architecture match the target, since
    wheels are built per interpreter.

.PARAMETER Wheels
    Download the pip wheels into vendor/ so the target can install with
    --no-index. Requires internet on THIS machine.

.PARAMETER PythonVersion
    The interpreter the TARGET machine runs, as "311" or "312". Binary
    wheels are compiled per interpreter version: a cp312 wheel simply will
    not install on 3.11, and pip's error for it names a file rather than
    the reason. Defaults to this machine's version, which is only right
    when both machines match.

    Find it on the target with:  python -c "import sys;print(sys.version)"

.PARAMETER Platform
    The target's wheel platform tag. Almost always win_amd64 - that is
    64-bit Windows on Intel or AMD. win32 is 32-bit Python, win_arm64 is
    Windows on ARM. Getting this wrong is the other half of why a wheel
    that looks right refuses to install.

    Find it on the target with:  python -c "import sysconfig;print(sysconfig.get_platform())"

.PARAMETER Zip
    Also produce dist/algoe-mvp.zip.

.EXAMPLE
    .\package.ps1 -Zip
    .\package.ps1 -Wheels -PythonVersion 311 -Zip
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
$dist = Join-Path $root "dist\algoe-mvp"

function Step($text) { Write-Host "`n>>> $text" -ForegroundColor Cyan }
function Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }

# ---------------------------------------------------------------------------
Step "Clearing dist\algoe-mvp"
# Empty it rather than delete it. A shell sitting in it, or Explorer with it
# open, holds a handle on the directory itself but not on what is inside, and
# failing the whole build over that is not worth it.
#
# Only this bundle's own output is cleared. dist also holds algoe-backtest from
# package-backtest.ps1, and wiping all of dist would silently destroy it.
if (Test-Path $dist) {
    Get-ChildItem -Force $dist | Remove-Item -Recurse -Force -ErrorAction Stop
}
$mvpZip = Join-Path $root "dist\algoe-mvp.zip"
if (Test-Path $mvpZip) { Remove-Item $mvpZip -Force }
New-Item -ItemType Directory -Force -Path $dist | Out-Null

# ---------------------------------------------------------------------------
Step "Building the frontend"
Push-Location (Join-Path $root "frontend")
try {
    if (-not (Test-Path "node_modules")) {
        throw "frontend/node_modules is missing - run npm install here first"
    }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw "next build failed" }
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
Step "Assembling the web server"
# Next's standalone output is deliberately incomplete: it omits the static
# assets and public files, expecting the deployment to place them. Miss this
# and the page loads with no CSS, which looks like a broken build.
$web = Join-Path $dist "web"
$standalone = Join-Path $root "frontend\.next\standalone"
if (-not (Test-Path (Join-Path $standalone "server.js"))) {
    throw "no standalone output - is output:'standalone' still in next.config.ts?"
}
Copy-Item -Recurse -Force $standalone $web
Copy-Item -Recurse -Force (Join-Path $root "frontend\.next\static") `
                          (Join-Path $web ".next\static")
if (Test-Path (Join-Path $root "frontend\public")) {
    Copy-Item -Recurse -Force (Join-Path $root "frontend\public") `
                              (Join-Path $web "public")
}
Note ("web: {0:N1} MB" -f ((Get-ChildItem -Recurse $web | Measure-Object Length -Sum).Sum / 1MB))

# ---------------------------------------------------------------------------
Step "Copying the backend"
$api = Join-Path $dist "backend"
New-Item -ItemType Directory -Force -Path $api | Out-Null
foreach ($item in @("src", "scripts", "tests", "requirements.txt")) {
    Copy-Item -Recurse -Force (Join-Path $root "backend\$item") $api
}
# Caches are per-interpreter and a stale one on the target is a real hazard.
Get-ChildItem -Recurse -Force -Directory $api -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem -Recurse -Force -File $api -Include "*.pyc", "*.pyo" |
    Remove-Item -Force
# The parquet engine keeps a rendered sample report for the standalone bundle to
# ship. The desk app never serves it, so it is a megabyte of dead weight here.
$sample = Join-Path $api "src\backtest\parquet\sample_report"
if (Test-Path $sample) { Remove-Item -Recurse -Force $sample }
Note ("backend: {0} python files" -f (Get-ChildItem -Recurse $api -Filter *.py).Count)

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
    Note "wheels for $tag - the target machine must match this exactly"

    # --only-binary=:all: is what makes this reliable. Without it pip is
    # free to hand back a source tarball, which then needs a compiler on
    # the offline machine, which is exactly what is not there.
    & python -m pip download -r (Join-Path $api "requirements.txt") `
        -d $vendor `
        --python-version $PythonVersion --platform $Platform `
        --only-binary=:all:
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "pip download failed; the bundle ships without wheels"
    }
    else {
        $wrong = Get-ChildItem $vendor -Filter *.whl |
            Where-Object { $_.Name -match "cp3\d{2}" -and $_.Name -notmatch "cp$PythonVersion" }
        if ($wrong) {
            Write-Warning ("these wheels are not for cp{0}: {1}" -f
                $PythonVersion, ($wrong.Name -join ", "))
        }
        Note ("vendor: {0} files, all tagged for {1}" -f
            (Get-ChildItem $vendor).Count, $tag)
        Set-Content -Path (Join-Path $vendor "TARGET.txt") -Value @"
These wheels are for:

    Python  3.$($PythonVersion.Substring(1))
    Platform $Platform
    Tag     $tag

Confirm the target machine matches before installing:

    python -c "import sys, sysconfig; print(sys.version); print(sysconfig.get_platform())"

Then install with no network:

    python -m pip install --no-index --find-links vendor -r backend\requirements.txt
"@
    }
}

# ---------------------------------------------------------------------------
Step "Writing launchers"
Copy-Item -Force (Join-Path $root "deploy\*") $dist

# ---------------------------------------------------------------------------
if ($Zip) {
    Step "Zipping"
    $archive = Join-Path $root "dist\algoe-mvp.zip"
    Compress-Archive -Path $dist -DestinationPath $archive -CompressionLevel Optimal
    Note ("{0:N1} MB  {1}" -f ((Get-Item $archive).Length / 1MB), $archive)
}

Step "Done"
Write-Host "    $dist" -ForegroundColor Green
Write-Host "    Copy it to the offline machine and read START-HERE.md`n"
