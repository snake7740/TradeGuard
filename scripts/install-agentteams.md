# AgentTeams（原 HiClaw）安装说明（US-E1-02）

依据 docs/04 §3 部署形态声明：**AgentTeams 按官方 Quick Install 脚本独立部署，不纳入 docker-compose**。

## 前置

- Docker 已就绪（本仓库 `docker compose ps` 全绿）；
- Windows 宿主机走 WSL2（Ubuntu）执行官方 sh 脚本；GitHub 直连受限时安装脚本经 ghfast.top 代理预下载（本目录 `agentteams-install.sh`，官方源 agentscope-ai/AgentTeams）。

## 步骤（Sprint 0 实测路径）

1. WSL2 内非交互安装 Manager（镜像源钉死阿里云仓库，避免 ghcr 直连超时）：

```bash
export AGENTTEAMS_NON_INTERACTIVE=1
export AGENTTEAMS_LLM_PROVIDER=qwen
export AGENTTEAMS_LLM_API_KEY=<从 secrets/dashscope.env 载入，见下方"Key 安全供给">
export AGENTTEAMS_REGISTRY=higress-registry.cn-hangzhou.cr.aliyuncs.com
bash agentteams-install.sh manager
```

1. 安装完成后进入 Manager 控制台（Element Web，默认网关端口 18080 / 控制台 18001），按 docs/02 §3 Identity 清单创建 6 个 Worker：
   - AA-AG-01 主控调度 / AA-AG-02 信号聚合 / AA-AG-03 欺诈调查 / AA-AG-04 处置执行 / AA-AG-05 审计复盘 / AA-AG-06 知识助手（B 端问答，API-W-27）；
   - 身份 Prompt 写入：职能边界、可用 Skill（AA-SK-01~05）、安全约束（只读边界/审批门控）；
2. 凭据安全（Key 安全供给，仓库零明文）：
   - 录入：`powershell -ExecutionPolicy Bypass -File scripts/set-dashscope-key.ps1`（SecureString 不回显，写入 gitignore 的 `secrets/dashscope.env`）；
   - 注入：`powershell -ExecutionPolicy Bypass -File scripts/apply-dashscope-key.ps1`（经环境变量传官方安装脚本，不落命令行/历史）；
   - 治理：Worker 仅持 consumer token，DashScope 等真实凭据仅存网关侧（04 §4 第 5 条）；`secrets/` 已加入 .gitignore，脚本内置 `git check-ignore` 自检；
3. 网络互通：官方安装创建独立 Docker 网络，用 `docker network connect tradeguard_tradeguard-net <agentteams容器>` 与 compose 栈打通；
4. 验证：开一个 Matrix 房间，Manager 可向 Worker 分派测试任务 → US-E1-02 验收通过。

## 降级声明（04 §4）

若当前版本自定义 Skill 装载受限：Skill 逻辑内置 Worker 可用工具集（MCP 工具 + Prompt 级能力声明），Skill 清单与 9 属性定义不变。
