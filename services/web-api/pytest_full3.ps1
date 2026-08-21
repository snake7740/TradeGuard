# 全量回归监督脚本 v3（dot-source 执行：`. .\pytest_full3.ps1`）
# 相对 v2 增强：每文件看门狗超时（超时杀进程标 TIMEOUT 继续）+ python 拉起失败重试
# （EDR 杀软间歇冻结 python/拒绝拉起，2026-08-21 取证）；断点 pf3_done.txt，进度 pf3.log
$py = "c:\MyGit\TradeGuard\.venv\Scripts\python.exe"
Set-Location "c:\MyGit\TradeGuard\services\web-api"
$done = @{}
if (Test-Path pf3_done.txt) {
    Get-Content pf3_done.txt | ForEach-Object { $done[$_] = $true }
}
$files = Get-ChildItem tests\test_*.py | Sort-Object Name
$fails = @()
Add-Content pf3.log "=== RUN START $(Get-Date -Format 'HH:mm:ss') ($($files.Count) files, $($done.Count) done)"
foreach ($f in $files) {
    if ($done[$f.Name]) { continue }
    $t0 = Get-Date
    $p = $null
    for ($try = 1; $try -le 4; $try++) {
        try {
            $p = Start-Process -FilePath $py -NoNewWindow -PassThru `
                -ArgumentList "-m", "pytest", "-q", $f.FullName `
                -RedirectStandardOutput "pf3_out.tmp" -RedirectStandardError "pf3_err.tmp"
            break
        } catch {
            Add-Content pf3.log "=== $($f.Name) SPAWN-FAIL try=$try ($($_.Exception.Message))"
            Start-Sleep 30
        }
    }
    if ($null -eq $p) {
        $fails += "$($f.Name)(spawn)"
        Add-Content pf3.log "=== $($f.Name) EXIT=SPAWNFAIL"
        Add-Content pf3_done.txt $f.Name
        continue
    }
    $finished = $p.WaitForExit(1680000)   # 28 分钟看门狗（慢环境冗余，正常 <10 分钟）
    if (-not $finished) {
        try { $p.Kill() } catch {}
        $fails += "$($f.Name)(timeout)"
        Add-Content pf3.log "=== $($f.Name) EXIT=TIMEOUT killed"
    } else {
        Add-Content pf3.log "=== $($f.Name) EXIT=$($p.ExitCode) $([int]((Get-Date) - $t0).TotalSeconds)s"
        if ($p.ExitCode -ne 0) { $fails += $f.Name }
    }
    Get-Content "pf3_out.tmp" -ErrorAction SilentlyContinue | Out-File -Append pf3.log -Encoding utf8
    Get-Content "pf3_err.tmp" -ErrorAction SilentlyContinue | Out-File -Append pf3.log -Encoding utf8
    Add-Content pf3_done.txt $f.Name
    Start-Sleep 5                          # 拉起冷却（规避 EDR 拦截窗口）
}
Add-Content pf3.log "=== ALL DONE $(Get-Date -Format 'HH:mm:ss'); FAILED_FILES=[$($fails -join ', ')]"
