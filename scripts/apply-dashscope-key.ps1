# 将 secrets/dashscope.env 中的 Key 注入 AgentTeams（LLM 凭据仅存网关侧，04 §4）
# 前置：先运行 scripts/set-dashscope-key.ps1 录入 Key
# 用法：powershell -ExecutionPolicy Bypass -File scripts/apply-dashscope-key.ps1
$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot "..\secrets\dashscope.env"
if (-not (Test-Path $envFile)) { throw "未找到 secrets/dashscope.env，请先运行 scripts/set-dashscope-key.ps1" }

# 加载但不打印（只取变量，不输出文件内容）
$line = (Get-Content $envFile -Raw).Trim()
if ($line -notmatch '^DASHSCOPE_API_KEY=(.+)$') { throw "dashscope.env 格式异常（期望 DASHSCOPE_API_KEY=...）" }
$env:AGENTTEAMS_LLM_API_KEY = $Matches[1]
Write-Host ("Key 已载入内存：前缀={0}*** 长度={1}（不回显全文）" -f $env:AGENTTEAMS_LLM_API_KEY.Substring(0,3), $env:AGENTTEAMS_LLM_API_KEY.Length)

# 与 scripts/install-agentteams.md 同源的官方安装/更新参数
$env:AGENTTEAMS_NON_INTERACTIVE = "1"
$env:AGENTTEAMS_LLM_PROVIDER    = "qwen"
$env:AGENTTEAMS_REGISTRY        = "higress-registry.cn-hangzhou.cr.aliyuncs.com"

$installSh = Join-Path $PSScriptRoot "agentteams-install.sh"
Write-Host "== 以真实 Key 重跑官方安装（manager 更新），Key 经环境变量传递，不落命令行 =="
bash $installSh manager
if ($LASTEXITCODE -ne 0) { throw "agentteams-install.sh 执行失败（exit=$LASTEXITCODE）" }

$env:AGENTTEAMS_LLM_API_KEY = $null   # 用完即清
Write-Host "注入完成。验证：控制台 http://localhost:18088 创建 Worker（AA-AG-01~05）→ Matrix 房间分派任务（US-E1-02 验收）"
