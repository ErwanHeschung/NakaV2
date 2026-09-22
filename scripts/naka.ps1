<#
.SYNOPSIS
    Naka from a terminal: start it, ask what it is doing, free the card.

.DESCRIPTION
    The port of the old scripts/naka, which drove Docker. There is no
    container now, so this is a thin wrapper over the tray and the server's
    own endpoints — everything here can be done from the tray menu and the
    panel as well.

    Windows blocks unsigned scripts by default. Either allow your own:

        Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

    or run this one without changing anything:

        powershell -ExecutionPolicy Bypass -File scripts\naka.ps1 status

.PARAMETER Command
    start    the tray, if it is not already running
    stop     quit the tray, and the server and llama-server with it
    status   the server's state, in one line
    unload   free the graphics card, for a game
    load     load the model back and warm it
    panel    open the panel in a browser
    logs     follow naka.log

.EXAMPLE
    .\scripts\naka.ps1 unload
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'unload', 'load', 'panel', 'logs')]
    [string]$Command = 'status'
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$Data = if ($env:NAKA_DATA_DIR) { $env:NAKA_DATA_DIR } else { "$env:LOCALAPPDATA\Naka" }
# 127.0.0.1, never localhost: the name resolves to ::1 first on Windows, and
# against an IPv4-only server every connection stalls there first.
$Base = 'http://127.0.0.1:8000'

function Get-Health {
    try { Invoke-RestMethod -Uri "$Base/health" -TimeoutSec 2 } catch { $null }
}

function Invoke-Ops([string]$Path, [int]$TimeoutSec = 120) {
    try {
        Invoke-RestMethod -Method Post -Uri "$Base$Path" -TimeoutSec $TimeoutSec
    } catch {
        Write-Error "Naka is not answering on $Base — start it first."
    }
}

switch ($Command) {
    'start' {
        if (Get-Health) { Write-Host 'Naka is already running.'; break }
        $pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
        if (-not (Test-Path $pythonw)) { Write-Error "no .venv here; run uv sync --group tray" }
        Start-Process -FilePath $pythonw -ArgumentList 'naka.pyw' -WorkingDirectory $Root
        Write-Host 'Starting. The icon appears by the clock; the panel opens with it.'
    }
    'stop' {
        # The tray owns the server, and the server owns llama-server, each
        # through a job object — so this takes all three down.
        Get-Process -Name 'pythonw', 'Naka' -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and ($_.Path -like "$Root*" -or $_.Name -eq 'Naka') } |
            Stop-Process -Force
        Write-Host 'Stopped.'
    }
    'status' {
        $health = Get-Health
        if (-not $health) { Write-Host "Nothing answering on $Base."; break }
        $ops = Invoke-RestMethod -Uri "$Base/ops/status" -TimeoutSec 5
        $state = if ($ops.llm_up) { 'awake' } elseif ($ops.models_loaded) { 'speech only' } else { 'sleeping' }
        $vram = '{0:N1} of {1:N1} GB used' -f ($ops.vram_used_mb / 1024), ($ops.vram_total_mb / 1024)
        Write-Host "$state — $vram, idle $($ops.idle_seconds)s"
        if ($ops.llm_exit_code -ne $null) {
            # Why the language model is down, which is the question worth
            # answering when it will not wake up.
            Write-Host "llama-server exited with $($ops.llm_exit_code):"
            $ops.llm_tail | ForEach-Object { Write-Host "  $_" }
        }
    }
    'unload' { Invoke-Ops '/ops/unload' | Out-Null; Write-Host 'The card is free.' }
    'load'   { Invoke-Ops '/ops/load'   | Out-Null; Write-Host 'Loaded and warm.' }
    'panel'  { Start-Process "$Base/ui/" }
    'logs'   { Get-Content -Path (Join-Path $Data 'logs\naka.log') -Tail 40 -Wait }
}
