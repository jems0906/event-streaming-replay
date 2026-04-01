param(
    [string]$EnvironmentName = "dev",
    [ValidateSet("filesystem", "kafka")]
    [string]$MessageBackend = "filesystem",
    [string]$EventStoreDir = ".local-events",
    [string]$CoreProcessUrl = "http://127.0.0.1:8001/process",
    [string]$KafkaBootstrapServers = "localhost:9092",
    [ValidateSet("PLAINTEXT", "SSL", "SASL_SSL", "SASL_PLAINTEXT")]
    [string]$KafkaSecurityProtocol = "PLAINTEXT",
    [string]$KafkaSaslMechanism = "",
    [string]$KafkaSaslUsername = "",
    [SecureString]$KafkaSaslPassword = $null,
    [ValidateSet("true", "false")]
    [string]$TracingEnabled = "false"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvActivate = Join-Path $repoRoot ".venv\Scripts\Activate.ps1"

if (-not (Test-Path $venvActivate)) {
    throw "Virtual environment not found at $venvActivate"
}

function Convert-SecureStringToPlainText {
    param([SecureString]$SecureValue)

    if ($null -eq $SecureValue) {
        return ""
    }

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Escape-SingleQuotedPowerShellString {
    param([string]$Value)
    return ($Value -replace "'", "''")
}

$kafkaSaslPasswordPlain = Convert-SecureStringToPlainText -SecureValue $KafkaSaslPassword

function Start-ServiceWindow {
    param(
        [string]$ServiceName,
        [int]$Port,
        [string]$ModulePath
    )

    $environmentValue = Escape-SingleQuotedPowerShellString $EnvironmentName
    $serviceNameValue = Escape-SingleQuotedPowerShellString $ServiceName
    $messageBackendValue = Escape-SingleQuotedPowerShellString $MessageBackend
    $eventStoreDirValue = Escape-SingleQuotedPowerShellString $EventStoreDir
    $coreProcessUrlValue = Escape-SingleQuotedPowerShellString $CoreProcessUrl
    $kafkaBootstrapValue = Escape-SingleQuotedPowerShellString $KafkaBootstrapServers
    $kafkaProtocolValue = Escape-SingleQuotedPowerShellString $KafkaSecurityProtocol
    $kafkaMechanismValue = Escape-SingleQuotedPowerShellString $KafkaSaslMechanism
    $kafkaUsernameValue = Escape-SingleQuotedPowerShellString $KafkaSaslUsername
    $kafkaPasswordValue = Escape-SingleQuotedPowerShellString $kafkaSaslPasswordPlain
    $tracingValue = Escape-SingleQuotedPowerShellString $TracingEnabled

    $repoRootValue = Escape-SingleQuotedPowerShellString $repoRoot
    $venvActivateValue = Escape-SingleQuotedPowerShellString $venvActivate

    $envLines = @(
        "$env:ENVIRONMENT='$environmentValue'",
        "$env:SERVICE_NAME='$serviceNameValue'",
        "$env:MESSAGE_BACKEND='$messageBackendValue'",
        "$env:EVENT_STORE_DIR='$eventStoreDirValue'",
        "$env:CORE_PROCESS_URL='$coreProcessUrlValue'",
        "$env:KAFKA_BOOTSTRAP_SERVERS='$kafkaBootstrapValue'",
        "$env:KAFKA_SECURITY_PROTOCOL='$kafkaProtocolValue'",
        "$env:KAFKA_SASL_MECHANISM='$kafkaMechanismValue'",
        "$env:KAFKA_SASL_USERNAME='$kafkaUsernameValue'",
        "$env:KAFKA_SASL_PASSWORD='$kafkaPasswordValue'",
        "$env:TRACING_ENABLED='$tracingValue'"
    )

    $commandParts = @(
        "Set-Location '$repoRootValue'",
        ". '$venvActivateValue'"
    ) + $envLines + @(
        "uvicorn $ModulePath --host 0.0.0.0 --port $Port"
    )

    $command = $commandParts -join "; "
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $command | Out-Null
}

Start-ServiceWindow -ServiceName "gateway" -Port 8000 -ModulePath "services.gateway.app:app"
Start-ServiceWindow -ServiceName "core" -Port 8001 -ModulePath "services.core.app:app"
Start-ServiceWindow -ServiceName "replay" -Port 8002 -ModulePath "services.replay.app:app"

Write-Output "Started local services in separate PowerShell windows: gateway(8000), core(8001), replay(8002)."
Write-Output "Backend mode: $MessageBackend"
if ($MessageBackend -eq "filesystem") {
    Write-Output "Filesystem event store: $EventStoreDir"
}
Write-Output "Use scripts\\stop-local.ps1 to stop uvicorn processes when finished."
