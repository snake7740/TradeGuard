# 贡献指南（Contributing Guide）

感谢关注 TradeGuard！本项目是「高风险人工决策域」的多 Agent 协同处置参考实现（交易欺诈风控），欢迎以任何形式参与。

## 快速上手

```bash
# 1. 克隆并启动全栈（Docker Desktop 需已运行）
docker compose up -d --build

# 2. 初始化演示数据
docker compose run --rm data-generator python generate.py --scale small

# 3. 验证：10 容器 healthy + 门户 http://localhost:8300
docker compose ps

# 4. 跑全量测试（206 例，含 MCP 实链路，约 8 分钟）
cd services/web-api && python -m pytest tests -q
```

详细架构与端口说明见 [README.md](README.md) 与 [docs/00-总则.md](docs/00-总则.md)。

## 开发约定

### 分支与提交

- 分支命名：`feat/<主题>` / `fix/<缺陷号>` / `docs/<主题>` / `chore/<主题>`
- 提交信息：`类型(范围): 摘要`，例：`feat(api): 审批单提交处置端点（API-W-23）`
- 一次提交聚焦一件事；文档与代码变更尽量分开提交

### 代码规范

- **Python（web-api / mcp-\*）**：Python 3.12+，类型标注（`dict[str, Any]` 风格），ruff 兼容格式；新端点必须带 OpenAPI 契约同步（`docs/openapi/tradeguard-openapi.yaml`）
- **Vue（web-portal）**：Vue 3 `<script setup>` + Element Plus，视图遵循 `page-head/page-body` 骨架（见 `web-portal/src/styles/global.css` 设计系统）
- **SQL 迁移**：`db/init/` 下递增编号，必须幂等（可重复执行）；角色权限只增不减（`02-roles.sql`）

### 测试要求（硬性）

任何行为变更必须伴随测试：

- 新端点/状态机转移 → `tests/test_routes_contract.py` / `test_state_machine.py` 补用例
- 新技能（app/skills/\*.py）→ 对应 `test_<skill>.py`，演示=测试回放原则
- 提交前本地全绿：`python -m pytest tests -q`（CI 同口径，见 `.github/workflows/ci.yml`）

### 安全红线

- 密钥/凭据一律走环境变量（`.env.example` 有清单），**严禁入库**（gitleaks 在 CI 强制扫描）
- 新外部输入必须过 `api_guards.py` 校验；新角色必须过 ACL 三写（`test_acl_contract.py`）
- 审计不可变：`audit_log` 只增不改，涉及新审计动作需在 `docs/03-数据架构DA.md` 登记

## PR 流程

1. Fork → 分支开发 → 本地全量测试绿
2. PR 描述包含：动机、变更点、测试证据（本地输出摘要）
3. CI（全量测试 + 前端构建 + SAST + 密钥扫描）全绿后评审
4. Squash 合并，提交信息遵循上述规范

## 报告缺陷

提 Issue 请附：复现步骤、`docker compose ps` 输出、相关容器日志（`docker logs <svc> --tail 100`）。安全漏洞请勿公开 Issue，参见 [SECURITY 备注](README.md#安全)。

## 场景扩展

想让这套骨架跑其他高风险决策域（理赔审批/设备风控/内容风控）？参见 `docs/01-业务架构BA.md` 的能力映射章节——12 态状态机、human_only 门控、审批工单、append-only 审计链均为领域无关资产。
