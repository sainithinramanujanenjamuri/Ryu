# RYU AI Command Center - Unified Services Preflight
# Checks infrastructure -> Starts Channel Daemon

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  RYU AI - Services Preflight" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Check Infrastructure (PostgreSQL on 5432 and Redis on 6379)
Write-Host "[1/2] Checking Infrastructure (Postgres 5432 / Redis 6379)..." -ForegroundColor Yellow
$pgConn = Test-NetConnection -ComputerName 127.0.0.1 -Port 5432 -InformationLevel Quiet
$rdConn = Test-NetConnection -ComputerName 127.0.0.1 -Port 6379 -InformationLevel Quiet

if (-not ($pgConn -and $rdConn)) {
    Write-Host "      Starting Docker containers..." -ForegroundColor Gray
    try {
        docker compose -f "$RepoRoot\deploy\docker-compose.yml" up -d | Out-Null
        Start-Sleep -Seconds 3
    } catch {
        Write-Host "      [WARN] Could not auto-start Docker. Ensure Postgres and Redis are running." -ForegroundColor DarkYellow
    }
}
Write-Host "      [OK] Infrastructure check complete." -ForegroundColor Green

# 2. Check Channel Daemon
Write-Host "[2/2] Checking Channel Daemon on http://127.0.0.1:8420..." -ForegroundColor Yellow
$daemonRunning = $false
try {
    $res = Invoke-RestMethod -Uri "http://127.0.0.1:8420/api/v1/health" -TimeoutSec 2 -ErrorAction Stop
    if ($res.status -eq "healthy") {
        $daemonRunning = $true
    }
} catch {}

if (-not $daemonRunning) {
    Write-Host "      Starting Channel Daemon in background..." -ForegroundColor Gray
    $pythonExe = "$RepoRoot\.env\Scripts\python.exe"
    if (-not (Test-Path $pythonExe)) {
        $pythonExe = "python.exe"
    }
    Start-Process -FilePath $pythonExe -ArgumentList "-m channels.daemon" -WorkingDirectory $RepoRoot -WindowStyle Hidden
    
    # Wait for daemon to become healthy
    $attempts = 0
    while ($attempts -lt 15) {
        Start-Sleep -Milliseconds 500
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:8420/api/v1/health" -TimeoutSec 1 -ErrorAction Stop
            if ($h.status -eq "healthy") {
                $daemonRunning = $true
                break
            }
        } catch {}
        $attempts++
    }
}

if ($daemonRunning) {
    Write-Host "      [OK] Channel Daemon active and healthy." -ForegroundColor Green
} else {
    Write-Host "      [WARN] Daemon starting up..." -ForegroundColor Yellow
}
Write-Host "============================================================" -ForegroundColor Cyan
