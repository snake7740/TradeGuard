# Docker Desktop 冷重启 + 稳定性观察（诊断今晚引擎反复崩溃）
$ErrorActionPreference = "SilentlyContinue"

Get-Process python | Where-Object { $_.Id -ne $PID } | Stop-Process -Force
Get-Process | Where-Object { $_.Name -match "docker|DockerDesktop" } | Stop-Process -Force
Start-Sleep -Seconds 3
wsl --shutdown
Start-Sleep -Seconds 8
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"

$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 5
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
}
if (-not $ready) { Write-Output "engine NOT ready in 300s"; exit 1 }
Write-Output ("engine ready, waited " + (($i + 1) * 5) + "s; observing 120s")

$stable = $true
for ($j = 0; $j -lt 24; $j++) {
    Start-Sleep -Seconds 5
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { $stable = $false; break }
}
if ($stable) {
    Write-Output "STABLE for 120s"
} else {
    Write-Output ("UNSTABLE, died at observation second " + (($j + 1) * 5))
}
