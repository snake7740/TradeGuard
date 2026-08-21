# 文件粒度断点续跑监督脚本（长时全量回归抗中断：每文件独立 pytest 进程，
# 完成清单落盘，被杀后重跑自动跳过已完成文件；进度逐文件落 pytest_files.log）
# ❗执行方式：必须 dot-source（`. .\pytest_files.ps1`）——嵌套 powershell -File 方式下 python 不被拉起（2026-08-21 实证，全部文件 0s EXIT 空）；v2 副本见 pytest_full.ps1
$py = "c:\MyGit\TradeGuard\.venv\Scripts\python.exe"
Set-Location "c:\MyGit\TradeGuard\services\web-api"
$done = @{}
if (Test-Path pytest_files_done.txt) {
    Get-Content pytest_files_done.txt | ForEach-Object { $done[$_] = $true }
}
$files = Get-ChildItem tests\test_*.py | Sort-Object Name
$fails = @()
Add-Content pytest_files.log "=== RUN START $(Get-Date -Format 'HH:mm:ss') ($($files.Count) files, $($done.Count) done)"
foreach ($f in $files) {
    if ($done[$f.Name]) { continue }
    $t0 = Get-Date
    & $py -m pytest -q $f.FullName 2>&1 | Out-File -Append pytest_files.log -Encoding utf8
    $code = $LASTEXITCODE
    $dt = [int]((Get-Date) - $t0).TotalSeconds
    Add-Content pytest_files.log "=== $($f.Name) EXIT=$code ${dt}s"
    if ($code -ne 0) { $fails += $f.Name }
    Add-Content pytest_files_done.txt $f.Name
}
Add-Content pytest_files.log "=== ALL DONE $(Get-Date -Format 'HH:mm:ss'); FAILED_FILES=[$($fails -join ', ')]"
