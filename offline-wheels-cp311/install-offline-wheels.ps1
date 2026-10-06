# Rejoin the split wheels and install them with no internet.
# Python 3.11, 64-bit Windows. Run from the conda env that already imports sklearn.
# -RuntimeOnly copies the Visual C++ DLLs into an already-installed torch and retries the import.
param([switch]$RuntimeOnly)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

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

if (-not $RuntimeOnly) {
    $wheels = Join-Path $PSScriptRoot "wheels"
    New-Item -ItemType Directory -Force -Path $wheels | Out-Null

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

    python -m pip install --no-index --find-links $wheels lightgbm xgboost catboost torch
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

Copy-VcRuntimeIntoTorch

python -c "import lightgbm,xgboost,catboost,torch; print('lightgbm', lightgbm.__version__); print('xgboost', xgboost.__version__); print('catboost', catboost.__version__); print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
if ($LASTEXITCODE -ne 0) {
    throw "torch failed to import. shm.dll is present; a Visual C++ DLL it needs was not. The copy above puts those DLLs next to it. If it still fails, run vc_redist.x64.exe as administrator, open a new prompt, and run fix-torch-runtime.bat again."
}
Write-Host "cuda False is correct: this is the CPU build."
