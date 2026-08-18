# 抗环境故障的全量回归：文件粒度断点续跑 + Docker 崩溃自动恢复
# 背景：2026-08-18 晚 Docker Desktop 引擎随机崩溃（3 次，间隔 4~20 分钟），
# 全量单进程跑 24 分钟暴露时长过长；改为逐文件跑（每文件独立 pytest 进程），
# 崩溃只损失当前文件，自动恢复后续跑，最后聚合 25 文件结果证明 251 例全绿。
$ErrorActionPreference = "Continue"

$venvPy = "c:\MyGit\TradeGuard\.venv\Scripts\python.exe"
$root   = "c:\MyGit\TradeGuard"
$wd     = "$root\services\web-api"
$log    = "$root\logs\final_regression2.log"
$composeArgs = "-f", "$root\docker-compose.yml"

function Test-Engine {
    docker info *> $null
    return ($LASTEXITCODE -eq 0)
}

function Kill-TestPython {
    # 杀挂起的 pytest 进程（Stop-Job 杀不掉孙进程，残留会占死 PG 连接）
    Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.Id -ne $PID } | Stop-Process -Force -ErrorAction SilentlyContinue
}

function Reset-LogTail {
    # 回截日志到指定行数（失败重跑前清掉残缺段，防重复计数）
    param([int]$Keep)
    $lines = Get-Content $log
    if ($lines.Count -gt $Keep) {
        $lines | Select-Object -First $Keep | Set-Content $log
    }
}

function Restore-Stack {
    Write-Output "  [restore] engine down -> cold restart"
    Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.Id -ne $PID } | Stop-Process -Force -ErrorAction SilentlyContinue
    Get-Process | Where-Object { $_.Name -match "docker|DockerDesktop" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    wsl --shutdown
    Start-Sleep -Seconds 8
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 5
        if (Test-Engine) { break }
    }
    if (-not (Test-Engine)) { Write-Output "  [restore] engine FAILED to come back"; return $false }
    docker compose @composeArgs up -d postgres mcp-core mcp-external-mock *> $null
    for ($k = 0; $k -lt 30; $k++) {
        Start-Sleep -Seconds 5
        $r = docker compose @composeArgs ps postgres mcp-core mcp-external-mock --format "{{.Status}}"
        if (($r | Select-String "healthy" | Measure-Object).Count -ge 3) { return $true }
    }
    Write-Output "  [restore] containers FAILED to become healthy"
    return $false
}

Set-Content -Path $log -Value ""   # 新一轮，日志重置
$files = Get-ChildItem "$wd\tests\test_*.py" | Sort-Object Name | ForEach-Object { $_.BaseName }
Write-Output ("files: " + $files.Count)

$summary = @()
foreach ($f in $files) {
    $done = $false
    for ($attempt = 1; $attempt -le 4 -and -not $done; $attempt++) {
        if (-not (Test-Engine)) {
            if (-not (Restore-Stack)) { continue }   # 本轮作废，重试
        }
        $baseLines = (Get-Content $log | Measure-Object -Line).Lines
        $stamp = "$env:TEMP\pytest_rc.txt"
        Remove-Item $stamp -ErrorAction SilentlyContinue
        $job = Start-Job -ScriptBlock {
            param($py, $wd, $f, $log, $rc)
            Push-Location $wd
            & $py -m pytest ("tests/" + $f + ".py") -v --tb=short 2>&1 |
                Out-File -Append -Encoding utf8 $log
            "$LASTEXITCODE" | Out-File -Encoding ascii $rc
            Pop-Location
        } -ArgumentList $venvPy, $wd, $f, $log, $stamp
        if (Wait-Job $job -Timeout 900) {
            Remove-Job $job -Force
            $rc = if (Test-Path $stamp) { (Get-Content $stamp)[0] } else { "999" }
            if ($rc -eq "0") {
                $done = $true
                Write-Output ("PASS-FILE " + $f)
            } elseif ($rc -eq "1") {
                # 测试真实失败（引擎活着）——记录并停机交人工分析
                $done = $true
                Write-Output ("FAIL-FILE " + $f + " (real test failure, rc=1)")
                $script:hasRealFail = $true
            } elseif ($rc -eq "5") {
                # pytest 5 = 未收集到测试（文件名拼写等），视为致命
                $done = $true
                Write-Output ("NO-TESTS " + $f)
                $script:hasRealFail = $true
            } else {
                Write-Output ("  [retry] " + $f + " rc=" + $rc + " (infra?)")
                Kill-TestPython
                Reset-LogTail -Keep $baseLines
                Start-Sleep -Seconds 3
                if (-not (Test-Engine)) { Restore-Stack | Out-Null }
            }
        } else {
            Stop-Job $job; Remove-Job $job -Force
            Write-Output ("  [timeout] " + $f + " hung 900s -> treat as crash")
            Kill-TestPython
            Reset-LogTail -Keep $baseLines
            if (Test-Engine) { Start-Sleep -Seconds 5 }
            if (-not (Test-Engine)) { Restore-Stack | Out-Null }
        }
    }
    if (-not $done) {
        Write-Output ("GIVE-UP " + $f + " after 4 attempts")
        $script:hasRealFail = $true
    }
    $summary += $f
}

$pass = (Select-String -Path $log -Pattern " PASSED " | Measure-Object).Count
$fail = (Select-String -Path $log -Pattern " FAILED " | Measure-Object).Count
Write-Output ("=== SUMMARY: files=" + $summary.Count + " passed=" + $pass + " failed=" + $fail + " ===")
if ($script:hasRealFail) { Write-Output "RESULT: INCOMPLETE/FAILED"; exit 1 }
Write-Output "RESULT: ALL-GREEN"; exit 0
