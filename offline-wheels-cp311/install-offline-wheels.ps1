# Rejoin the split wheels and install them with no internet.
# Python 3.11, 64-bit Windows. Run from the conda env that already imports sklearn.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
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
python -c "import lightgbm,xgboost,catboost,torch; print('lightgbm', lightgbm.__version__); print('xgboost', xgboost.__version__); print('catboost', catboost.__version__); print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
Write-Host "cuda False is correct: this is the CPU build."
