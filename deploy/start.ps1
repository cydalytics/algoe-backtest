<#
.SYNOPSIS
    Start Algo E - the API and the web server together.

.DESCRIPTION
    Runs FastAPI on 8001 and the Next server on 3000, waits until both
    answer, and opens the board. Closing this window stops both.

    The browser only ever talks to 3000; the web server proxies /api
    through to 8001. So 8001 does not need to be reachable from anywhere
    except this machine, and a colleague on the LAN can open the board at
    http://<this-machine>:3000 without any further configuration.

.PARAMETER Source
    sim     internally consistent fake data - no database needed
    capture a previously recorded snapshot on disk
    sql     the live SQL Server, through the notebook's queries
            ("hkjc" and "live" mean the same thing)

.PARAMETER ApiPort / WebPort
    Override if something already owns 8001 or 3000.

.EXAMPLE
    .\start.ps1                  # whatever config.py defaults to
    .\start.ps1 -Source sql      # live
    .\start.ps1 -Source sim      # demo with no database
#>

[CmdletBinding()]
param(
    [ValidateSet("sim", "capture", "sql", "hkjc", "live")]
    [string]$Source,
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Fail($text) { Write-Host "`n$text`n" -ForegroundColor Red; exit 1 }
function Note($text) { Write-Host "    $text" -ForegroundColor DarkGray }

# --- prerequisites ---------------------------------------------------------
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Fail "python is not on PATH." }
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Fail "node is not on PATH." }

function Test-PortFree($port) {
    -not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}
if (-not (Test-PortFree $ApiPort)) { Fail "port $ApiPort is already in use. Use -ApiPort." }
if (-not (Test-PortFree $WebPort)) { Fail "port $WebPort is already in use. Use -WebPort." }

if ($Source) { $env:ALGOE_SOURCE = $Source }
$env:ALGOE_API_PORT = $ApiPort
# The web server reads this at start-up to know where to proxy /api.
$env:ALGOE_API_URL = "http://127.0.0.1:$ApiPort"
$env:PORT = $WebPort
$env:HOSTNAME = "0.0.0.0"

Write-Host "`nAlgo E" -ForegroundColor Cyan
Note ("source   {0}" -f ($(if ($Source) { $Source } else { "as configured" })))
Note "api      http://127.0.0.1:$ApiPort"
Note "board    http://localhost:$WebPort"

# --- start -----------------------------------------------------------------
$jobs = @()
try {
    Write-Host "`nStarting the API..." -ForegroundColor Cyan
    $api = Start-Process -PassThru -NoNewWindow -FilePath "python" `
        -ArgumentList "-m", "uvicorn", "src.api.main:app",
                      "--host", "127.0.0.1", "--port", $ApiPort `
        -WorkingDirectory (Join-Path $root "backend")
    $jobs += $api

    # The first tick builds every score grid, so the API answers /health
    # well before it can answer /desk. Wait on health, not on the board.
    $ready = $false
    foreach ($attempt in 1..60) {
        Start-Sleep -Milliseconds 1000
        if ($api.HasExited) { Fail "the API exited on start-up (see above)." }
        try {
            $null = Invoke-RestMethod "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 3
            $ready = $true
            break
        }
        catch { }
    }
    if (-not $ready) { Fail "the API did not come up within 60s." }
    Note "api ready"

    Write-Host "Starting the board..." -ForegroundColor Cyan
    $web = Start-Process -PassThru -NoNewWindow -FilePath "node" `
        -ArgumentList "server.js" -WorkingDirectory (Join-Path $root "web")
    $jobs += $web

    $ready = $false
    foreach ($attempt in 1..30) {
        Start-Sleep -Milliseconds 1000
        if ($web.HasExited) { Fail "the web server exited on start-up." }
        try {
            $null = Invoke-WebRequest "http://127.0.0.1:$WebPort/" -TimeoutSec 3 -UseBasicParsing
            $ready = $true
            break
        }
        catch { }
    }
    if (-not $ready) { Fail "the web server did not come up within 30s." }
    Note "board ready"

    Write-Host "`n  Open http://localhost:$WebPort" -ForegroundColor Green
    Write-Host "  Ctrl+C to stop`n" -ForegroundColor DarkGray
    if (-not $NoBrowser) { Start-Process "http://localhost:$WebPort" }

    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($p in $jobs) {
            if ($p.HasExited) {
                Write-Host "`na process exited - shutting down" -ForegroundColor Yellow
                return
            }
        }
    }
}
finally {
    Write-Host "Stopping..." -ForegroundColor DarkGray
    foreach ($p in $jobs) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
