# 全量回归监督脚本 v2（dot-source 执行：`. .\pytest_full.ps1`，规避嵌套 powershell 下 python 不被拉起问题）
# 完成清单落盘 pf2_done.txt，被杀后重跑自动跳过已完成文件；进度逐文件落 pf2.log
$py = "c:\MyGit\TradeGuard\.venv\Scripts\python.exe"
Set-Location "c:\MyGit\TradeGuard\services\web-api"
$done = @{}
if (Test-Path pf2_done.txt) {
    Get-Content pf2_done.txt | ForEach-Object { $done[$_] = $true }
}
$files = Get-ChildItem tests\test_*.py | Sort-Object Name
$fails = @()
Add-Content pf2.log "=== RUN START $(Get-Date -Format 'HH:mm:ss') ($($files.Count) files, $($done.Count) done)"
foreach ($f in $files) {
    if ($done[$f.Name]) { continue }
    $t0 = Get-Date
    & $py -m pytest -q $f.FullName 2>&1 | Out-File -Append pf2.log -Encoding utf8
    $code = $LASTEXITCODE
    $dt = [int]((Get-Date) - $t0).TotalSeconds
    Add-Content pf2.log "=== $($f.Name) EXIT=$code ${dt}s"
    if ($code -ne 0) { $fails += $f.Name }
    Add-Content pf2_done.txt $f.Name
}
Add-Content pf2.log "=== ALL DONE $(Get-Date -Format 'HH:mm:ss'); FAILED_FILES=[$($fails -join ', ')]"
