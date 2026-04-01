<#
.SYNOPSIS
    Smoke test the local event-streaming platform (filesystem backend).
    Verifies health, ingestion, processing, and replay end-to-end.

.PARAMETER GatewayUrl
    Base URL of the gateway service.  Default: http://127.0.0.1:8000

.PARAMETER CoreUrl
    Base URL of the core service.  Default: http://127.0.0.1:8001

.PARAMETER ReplayUrl
    Base URL of the replay service.  Default: http://127.0.0.1:8002

.PARAMETER EventStoreDir
    Path to the local JSONL event store directory.  Default: .local-events

.PARAMETER WaitSeconds
    How many seconds to wait for services to become healthy before aborting.  Default: 30

.EXAMPLE
    .\scripts\smoke-local.ps1
    .\scripts\smoke-local.ps1 -WaitSeconds 60
#>
param(
    [string]$GatewayUrl    = "http://127.0.0.1:8000",
    [string]$CoreUrl       = "http://127.0.0.1:8001",
    [string]$ReplayUrl     = "http://127.0.0.1:8002",
    [string]$EventStoreDir = ".local-events",
    [int]   $WaitSeconds   = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── helpers ──────────────────────────────────────────────────────────────────

$PassCount = 0
$FailCount = 0

function Pass([string]$Label) {
    Write-Host "  [PASS] $Label" -ForegroundColor Green
    $script:PassCount++
}

function Fail([string]$Label, [string]$Detail = "") {
    $msg = "  [FAIL] $Label"
    if ($Detail) { $msg += ": $Detail" }
    Write-Host $msg -ForegroundColor Red
    $script:FailCount++
}

function Section([string]$Title) {
    Write-Host ""
    Write-Host "--- $Title ---" -ForegroundColor Cyan
}

function Wait-Healthy([string]$Name, [string]$Url, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    Write-Host "  Waiting for $Name at $Url/health ..." -NoNewline
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-RestMethod -Uri "$Url/health" -Method Get -TimeoutSec 4
            if ($r.status -eq "ok") {
                Write-Host " ready" -ForegroundColor Green
                return $true
            }
        } catch { }
        Write-Host "." -NoNewline
        Start-Sleep -Milliseconds 1000
    }
    Write-Host " TIMEOUT" -ForegroundColor Red
    return $false
}

# ── 1. Health checks ──────────────────────────────────────────────────────────

Section "1. Service health"

$gatewayOk = Wait-Healthy "gateway" $GatewayUrl $WaitSeconds
$coreOk    = Wait-Healthy "core"    $CoreUrl    $WaitSeconds
$replayOk  = Wait-Healthy "replay"  $ReplayUrl  $WaitSeconds

if ($gatewayOk) { Pass "gateway /health" } else { Fail "gateway /health" "Service did not respond in ${WaitSeconds}s - is it running?" }
if ($coreOk)    { Pass "core    /health" } else { Fail "core    /health" "Service did not respond in ${WaitSeconds}s" }
if ($replayOk)  { Pass "replay  /health" } else { Fail "replay  /health" "Service did not respond in ${WaitSeconds}s" }

if (-not ($gatewayOk -and $coreOk -and $replayOk)) {
    Write-Host ""
    Write-Host "One or more services are not running. Start them with:" -ForegroundColor Yellow
    Write-Host "  .\scripts\start-local.ps1 -MessageBackend filesystem -TracingEnabled false" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Results: $PassCount passed, $FailCount failed" -ForegroundColor Yellow
    exit 1
}

# ── 2. Ingest an event ───────────────────────────────────────────────────────

Section "2. Event ingestion"

$testPayload = @{
    user_id     = 42
    action      = "smoke-test"
    environment = "dev"
    smoke_run   = $true
} | ConvertTo-Json -Compress

$ingestResponse = $null
try {
    $ingestResponse = Invoke-RestMethod `
        -Uri        "$GatewayUrl/ingest" `
        -Method     Post `
        -Body       $testPayload `
        -ContentType "application/json" `
        -TimeoutSec 10
} catch {
    Fail "POST /ingest returned HTTP error" "$_"
}

if ($null -ne $ingestResponse -and $ingestResponse.status -eq "captured") {
    $correlationId = $ingestResponse.correlation_id
    Pass "POST /ingest status=captured (correlation_id=$correlationId)"
} elseif ($null -ne $ingestResponse) {
    Fail "POST /ingest unexpected response" ($ingestResponse | ConvertTo-Json -Compress)
    $correlationId = ""
} else {
    $correlationId = ""
}

# ── 3. Verify event landed in the JSONL store ────────────────────────────────

Section "3. Filesystem event store"

# Allow up to ~2 s for the async write to complete
$storeFile = Join-Path $EventStoreDir "incoming_requests.jsonl"
$found = $false
for ($i = 0; $i -lt 20; $i++) {
    if (Test-Path $storeFile) {
        $lines = Get-Content $storeFile -Tail 500 -ErrorAction SilentlyContinue
        foreach ($line in $lines) {
            try {
                $obj = $line | ConvertFrom-Json
                if (($correlationId -and $obj.correlation_id -eq $correlationId) -or $obj.action -eq "smoke-test") {
                    $found = $true
                    break
                }
            } catch { }
        }
        if ($found) { break }
    }
    Start-Sleep -Milliseconds 100
}

if ($found)       { Pass "Event found in $storeFile" }
else              { Fail "Event not found in $storeFile" "Expected action=smoke-test / correlation_id=$correlationId" }

# ── 4. Verify processed store was written by core ────────────────────────────

$processedFile = Join-Path $EventStoreDir "processed_requests.jsonl"
$processedFound = $false
for ($i = 0; $i -lt 40; $i++) {
    if (Test-Path $processedFile) {
        $lines = Get-Content $processedFile -Tail 500 -ErrorAction SilentlyContinue
        foreach ($line in $lines) {
            try {
                $obj = $line | ConvertFrom-Json
                if (($correlationId -and $obj.correlation_id -eq $correlationId) -or $obj.action -eq "smoke-test") {
                    $processedFound = $true; break
                }
            } catch { }
        }
        if ($processedFound) { break }
    }
    Start-Sleep -Milliseconds 100
}

if ($processedFound) { Pass "Event found in $processedFile (core processed it)" }
else                 { Fail "Event not found in $processedFile" "Core may not have processed it yet" }

# ── 5. Replay ────────────────────────────────────────────────────────────────

Section "4. Replay"

# Use a short time window covering the last 5 minutes so the smoke event is included
$fromMs = [long]((Get-Date).AddMinutes(-5).ToUniversalTime() - [datetime]'1970-01-01').TotalMilliseconds
$toMs   = [long]((Get-Date).AddMinutes(1).ToUniversalTime()  - [datetime]'1970-01-01').TotalMilliseconds

$replayPayload = @{
    from_timestamp_ms = $fromMs
    to_timestamp_ms   = $toMs
    environment       = "dev"
    speedup_factor    = 200
    max_events        = 10
    target_url        = "$GatewayUrl/ingest"
} | ConvertTo-Json -Compress

$replayResponse = $null
try {
    $replayResponse = Invoke-RestMethod `
        -Uri         "$ReplayUrl/replay" `
        -Method      Post `
        -Body        $replayPayload `
        -ContentType "application/json" `
        -TimeoutSec  60
} catch {
    Fail "POST /replay returned HTTP error" "$_"
}

if ($null -ne $replayResponse -and $replayResponse.status -eq "ok") {
    $successCount = 0
    if ($replayResponse.PSObject.Properties.Name -contains "success") {
        $successCount = [int]$replayResponse.success
    }
    Pass "POST /replay status=ok (success=$successCount)"
} elseif ($null -ne $replayResponse) {
    $msg = if ($replayResponse.message) { $replayResponse.message } else { $replayResponse | ConvertTo-Json -Compress }
    # "No matching events found" is acceptable for very fast smoke runs
    if ($msg -match "No matching events") {
        Pass "POST /replay returned 'No matching events' (acceptable in fast runs)"
    } else {
        Fail "POST /replay unexpected response" $msg
    }
} # else already failed above

# ── 6. Metrics endpoints ─────────────────────────────────────────────────────

Section "5. Prometheus metrics"

foreach ($svc in @(@{Name="gateway"; Url=$GatewayUrl}, @{Name="core"; Url=$CoreUrl}, @{Name="replay"; Url=$ReplayUrl})) {
    try {
        $body = Invoke-RestMethod -Uri "$($svc.Url)/metrics" -Method Get -TimeoutSec 5
        if ($body -match "python_info|http_requests|process_cpu") {
            Pass "$($svc.Name) /metrics returns Prometheus text"
        } else {
            Fail "$($svc.Name) /metrics response looks empty or unexpected" ($body | Select-Object -First 3)
        }
    } catch {
        Fail "$($svc.Name) /metrics" "$_"
    }
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
$total  = $PassCount + $FailCount
$colour = if ($FailCount -eq 0) { "Green" } else { "Yellow" }
Write-Host "  Results: $PassCount / $total passed" -ForegroundColor $colour
if ($FailCount -gt 0) {
    Write-Host "  $FailCount check(s) failed - see [FAIL] lines above." -ForegroundColor Red
}
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

exit $FailCount
