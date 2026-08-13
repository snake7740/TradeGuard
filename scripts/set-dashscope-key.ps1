# 安全录入 DashScope API Key（US-E1-02 解锁件）
# 设计（04 §4 凭据治理）：Key 仅存本地 secrets/（gitignore）与 Higress 网关，仓库与日志零明文。
# 用法：pwsh/powershell -ExecutionPolicy Bypass -File scripts/set-dashscope-key.ps1
$ErrorActionPreference = "Stop"
$secretDir = Join-Path $PSScriptRoot "..\secrets"
$envFile   = Join-Path $secretDir "dashscope.env"

# 1) 不回显输入（SecureString，不进命令历史、不打印）
$secure = Read-Host -Prompt "请粘贴 DashScope API Key（输入不回显）" -AsSecureString
$plain  = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

# 2) 格式自检（百炼 Key 以 sk- 开头；不打印 Key 本体）
if ($plain.Length -lt 20) { throw "Key 长度异常（$($plain.Length) 字符），请确认来源：https://www.aliyun.com/product/bailian" }
Write-Host ("格式自检通过：前缀={0}***  长度={1}" -f $plain.Substring(0,3), $plain.Length)

# 3) 写入 gitignore 目录（覆盖旧值）
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
Set-Content -Path $envFile -Value "DASHSCOPE_API_KEY=$plain" -Encoding ascii -NoNewline
$plain = $null; $secure = $null   # 立即清除内存变量

# 4) 泄露自检：确认 git 不会跟踪该文件
Push-Location (Join-Path $PSScriptRoot "..")
git check-ignore -q secrets/dashscope.env
if ($LASTEXITCODE -eq 0) { Write-Host "防泄露自检 OK：secrets/dashscope.env 已被 .gitignore 排除" }
else { Write-Host "警告：文件未被 gitignore，请检查 .gitignore" -ForegroundColor Red }
Pop-Location

Write-Host "完成。后续使用：scripts/apply-dashscope-key.ps1（仅注入 Higress 网关，不打印 Key）"
