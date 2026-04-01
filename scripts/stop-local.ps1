Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

$killed = 0
Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -in @("python.exe", "pythonw.exe") -and
        $_.CommandLine -match "uvicorn" -and
        $_.CommandLine -match "services\.(gateway|core|replay)\.app:app"
    } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        $killed++
    }

Write-Output "Stopped $killed local service process(es)."
