# TradeGuard Agent Skills（官方技能库，9 属性规范）

> **这是什么 / 给谁看**：5 个官方 Agent 技能的可执行定义，供 AgentTeams Worker（AA-AG-02~05）加载执行。
> 面向**Agent 编排者 / 后端开发者**。零基础请先读[根 README](../README.md) 与 [docs/02 §4](../docs/02-应用架构AA.md)。
> 技能 = 能力抽象（9 属性契约）；确定性内核实现见各文件「确定性实现」列指向的代码。

本目录是 [02 §4 Skill 清单](../docs/02-应用架构AA.md#4-skill-清单9-属性全量) 的落地产物：
5 个官方 Agent 技能的可执行定义，供 AgentTeams Worker（AA-AG-02~05）加载执行，
元数据经 `scripts/nacos_register.py` 注册到 Nacos Skills Registry（TA-C-05，US-E1-03）。

| 技能 | 文件 | 承载 Agent | 确定性实现 |
| --- | --- | --- | --- |
| AA-SK-01 signal-aggregation | [AA-SK-01-signal-aggregation.md](./AA-SK-01-signal-aggregation.md) | AA-AG-02 | services/web-api/app/skills/aggregation.py |
| AA-SK-02 fraud-investigation | [AA-SK-02-fraud-investigation.md](./AA-SK-02-fraud-investigation.md) | AA-AG-03 | services/web-api/app/skills/investigation.py |
| AA-SK-03 disposition-execution | [AA-SK-03-disposition-execution.md](./AA-SK-03-disposition-execution.md) | AA-AG-04 | services/mcp-core/server.py execute_disposition |
| AA-SK-04 compliance-audit | [AA-SK-04-compliance-audit.md](./AA-SK-04-compliance-audit.md) | AA-AG-05 | services/web-api/app/skills/verification.py |
| AA-SK-05 knowledge-sedimentation | [AA-SK-05-knowledge-sedimentation.md](./AA-SK-05-knowledge-sedimentation.md) | AA-AG-05 | services/web-api/app/skills/knowledge.py + verification.py `_retrospective` |

## 打包规范（自包含 frontmatter）

每个 skill 文件首部携带 YAML frontmatter（扁平 `key: value` 单行键值，不依赖 PyYAML），
使单文件即一个可分发的自包含技能包：编排器读 frontmatter 即可完成装配，无需解析正文。

| 键 | 必填 | 说明 |
| --- | --- | --- |
| name | ✓ | 必须与文件名 stem 一致（注册名即文件名） |
| version | ✓ | 语义化版本，与 CHANGELOG 同步 |
| description | ✓ | 一句话能力描述（含业务规则锚点） |
| agent | ✓ | 承载 Agent（AA-AG-02~05） |
| entrypoint | ✓ | 确定性内核代码入口（仓库根相对路径） |
| depends-mcp | ✓ | 依赖的 AA-MCP 工具，逗号分隔 |
| depends-tables | ✓ | 读写的 DA 层数据表，逗号分隔 |
| tests | ✓ | 测试文件路径，逗号分隔（仓库根相对） |
| test-cases | ✓ | 测试用例数（质量指标，与实际 `def test_` 计数一致） |
| degradation-paths | ✓ | 降级路径，逗号分隔（无 LLM/无 KB/图查询降级等） |
| depth-limit | 可选 | 递归边界（仅递归类技能，如 AA-SK-02 图扩展 2 跳） |

防漂移门禁：`python scripts/skill_pack_validate.py`（CI unit-test job 内置，fail-fast）
校验必填键齐全、name/文件名一致、entrypoint/tests 文件存在、test-cases 与实际计数一致；
漂移即红，杜绝「文档与代码脱节、测试数夸大」。

消费侧（运行时注册表）：web-api `app/skills/loader.py` 以同源解析规则在运行时装载
全部 AA-SK 包并校验 entrypoint 可导入，经 `GET /api/skills`（API-W-24）对外暴露
元数据与降级路径——第三方 Agent/门户无需翻源码即可发现与分派；坏包不阻断
（loadable=false + error 留痕）。

执行纪律：

1. 每个技能先跑**确定性规则内核**（可单测、可回放），LLM 仅做推理增强层——无 Key 时闭环不断；
2. 技能 I/O 契约与 openapi components.schemas 同源，杜绝两套数据结构漂移；
3. 技能调用全部携带事由（reason）与 trace_id，落审计（BA-BR-09/10）。

## 安装与发布（生态 install 消费，维度3闭合项）

技能包不止留在仓库内——第三方生态可一条命令安装消费（新智基座维度3
「能被 install 消费」闭合）：

```bash
# 发布方（仓库维护者）：生成/刷新发布清单（包清单+版本+sha256，入库）
python scripts/skill_install.py manifest

# 消费方（第三方 Agent 平台）：clone 后一条命令安装，逐包 sha256 + 零漂移校验
python scripts/skill_install.py install                # 默认装到 ~/.tradeguard-skills
python scripts/skill_install.py install --target D:/x  # 指定目标目录
python scripts/skill_install.py verify                 # 安装后完整性复核
```

- `RELEASE-MANIFEST.json`：发布索引元数据（包清单/版本/sha256/测试数/发布 commit），
  即 registry 的清单层；发布通道 = git 仓库 + 清单，公共 registry 上架为后续运营动作；
- 安装回执 `INSTALL-RECEIPT.json`：装了什么版本、sha、源自哪个发布 commit，可审计；
- 坏包拒装不阻断：sha256 不符或 frontmatter 漂移即拒装并留痕（同运行时注册表纪律）。
