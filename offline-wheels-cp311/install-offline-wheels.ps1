# Rejoin the split wheels and install them with no internet.
# Python 3.11, 64-bit Windows. Run from the conda env that already imports sklearn.
# An existing conda torch is removed first. Line 148 in torch\__init__.py is that old
# copy; the 2.14.1 CPU wheel raises the same loader error at line 292.
param([switch]$RuntimeOnly)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Ensure-Wheels {
    $wheels = Join-Path $PSScriptRoot "wheels"
    New-Item -ItemType Directory -Force -Path $wheels | Out-Null
    $haveTorch = Get-ChildItem $wheels -Filter "torch-2.14.1+cpu-*.whl" -ErrorAction SilentlyContinue
    $haveDeps = Get-ChildItem $wheels -Filter "filelock-*.whl" -ErrorAction SilentlyContinue
    if ($RuntimeOnly -and $haveTorch -and $haveDeps) {
        return $wheels
    }

    Get-ChildItem -Filter *.part01 | ForEach-Object {
        $base = $_.BaseName
        $out = Join-Path $wheels $base
        $fs = [System.IO.File]::Create($out)
        try {
            Get-ChildItem -Filter ($base + ".part*") | Sort-Object Name | ForEach-Object {
                $in = [System.IO.File]::OpenRead($_.FullName)
                try { $in.CopyTo($fs) } finally { $in.Close() }
            }
        } finally { $fs.Close() }
        Write-Host "joined $base"
    }

    Get-ChildItem -Filter "wheels-*.zip" | ForEach-Object {
        tar -xf $_.FullName -C $wheels
    }

    $want = Get-Content (Join-Path $PSScriptRoot "SHA256SUMS.txt") | Where-Object { $_.Trim() -ne "" }
    foreach ($line in $want) {
        $hash, $name = $line -split "\s+", 2
        $path = Join-Path $wheels $name
        if (-not (Test-Path $path)) { throw "missing $name after extract" }
        $got = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
        if ($got -ne $hash) { throw "checksum mismatch: $name" }
    }
    Write-Host "checksums ok"
    return $wheels
}

function Install-CpuTorch([string]$wheels) {
    $diagnose = Join-Path $PSScriptRoot "diagnose-torch-dlls.py"
    Write-Host "torch before replacement:"
    python $diagnose --identity

    $site = (python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
    if (-not (Test-Path $site)) { throw "site-packages not found: $site" }
    Write-Host "site-packages $site"

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    python -m pip uninstall -y torch
    $ErrorActionPreference = $previous

    $torchDir = Join-Path $site "torch"
    if (Test-Path $torchDir) {
        $backup = Join-Path $PSScriptRoot "torch-old-backup"
        if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }
        Move-Item $torchDir $backup
        Write-Host "moved the old torch folder to $backup"
    }
    Get-ChildItem $site -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "torch-*.dist-info" -or $_.Name -like "torch-*.egg-info" -or $_.Name -like "pytorch-*.dist-info" } |
        ForEach-Object {
            Write-Host "removing $($_.Name)"
            Remove-Item $_.FullName -Recurse -Force
        }

    $whl = Get-ChildItem $wheels -Filter "torch-2.14.1+cpu-*.whl" | Select-Object -First 1
    if (-not $whl) { throw "torch 2.14.1 CPU wheel not found in $wheels" }
    python -m pip install --no-index --find-links $wheels --force-reinstall --no-deps $whl.FullName
    if ($LASTEXITCODE -ne 0) { throw "torch wheel install failed" }
    python -m pip install --no-index --find-links $wheels filelock "typing-extensions>=4.10.0" "setuptools>=77.0.3" "sympy>=1.13.3" "networkx>=2.5.1" jinja2 "fsspec>=0.8.5"
    if ($LASTEXITCODE -ne 0) { throw "torch dependency install failed" }

    Write-Host "torch after replacement:"
    python $diagnose --identity
}

function Copy-VcRuntimeIntoTorch {
    $src = Join-Path $PSScriptRoot "vc_runtime"
    if (-not (Test-Path $src)) { throw "missing vc_runtime folder" }
    $lib = python -c "import importlib.util, pathlib, sys; s = importlib.util.find_spec('torch'); sys.exit(2) if s is None or not s.submodule_search_locations else print(pathlib.Path(list(s.submodule_search_locations)[0]) / 'lib')"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($lib)) {
        throw "torch is not installed yet, so the VC runtime was not copied"
    }
    $lib = $lib.Trim()
    if (-not (Test-Path $lib)) { throw "torch lib folder not found: $lib" }
    $n = 0
    Get-ChildItem $src -Filter *.dll | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $lib $_.Name) -Force
        $n += 1
    }
    Write-Host "copied $n VC runtime DLLs into $lib"
}

$wheels = Ensure-Wheels
if (-not $RuntimeOnly) {
    python -m pip install --no-index --find-links $wheels lightgbm xgboost catboost
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

Install-CpuTorch $wheels
Copy-VcRuntimeIntoTorch

python -c "import lightgbm,xgboost,catboost,torch; print('lightgbm', lightgbm.__version__); print('xgboost', xgboost.__version__); print('catboost', catboost.__version__); print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
if ($LASTEXITCODE -ne 0) {
    python (Join-Path $PSScriptRoot "diagnose-torch-dlls.py")
    throw "torch 2.14.1 CPU still failed to import. Send the 'missing imports' and 'load test' lines above."
}
Write-Host "cuda False is correct: this is the CPU build."
