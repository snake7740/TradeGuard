# web-api 后端设计（Sprint 0 架构模板）

FastAPI 应用，承载 API-W-01~15 契约（docs/openapi/tradeguard-openapi.yaml）。
三端真实调用链（04 §10.1）中"人类操作面"的唯一后端。

## 分层结构

```
app/
├─ main.py               # 应用工厂 + lifespan 依赖装配（pool/publisher/repositories）
├─ schemas.py            # Pydantic 入参模型（与 openapi.yaml 逐条对齐）
├─ repositories.py       # 仓储层：一聚合根一仓储（Case/Approval/Kb）
├─ core/                 # 核心域（零基础设施依赖，可直接单测，06 §3）
│  ├─ state_machine.py   # 案件状态机：12 态 / 16 事件 / 18 条迁移路径（02 §7，DA-INV-01）
│  └─ events.py          # 事件发布端口/适配器：InMemory（Sprint 0）→ RocketMQ（Sprint 1）
└─ api/                  # 路由层：按资源分文件，API-W 全量声明
   ├─ health.py          # W-15 健康探针
   ├─ alerts.py          # W-01 立案（CaseRegistered 事件源）
   ├─ cases.py           # W-02~07（列表/详情/信号/图谱/证据/人工复核）
   ├─ approvals.py       # W-08/09（审批队列/批准驳回→状态机）
   ├─ audit.py           # W-10 审计回放
   ├─ kb.py              # W-11~13（知识申请/发布/驳回）
   └─ events_stream.py   # W-14 SSE 领域事件推送
```

## 核心设计模式

| 模式 | 落点 | 依据 |
|---|---|---|
| 状态机 | `core/state_machine.py` 纯函数迁移表 + actor 守卫（human_only） | 02 §7、DA-INV-01 |
| 仓储 | `repositories.py` 聚合根边界即模块边界 | 03 §9.1/§9.4 |
| 端口/适配器 | EventPublisher：InMemory ↔ RocketMQ 可替换 | 03 §9.2、TA-C-06 |
| 乐观锁 | `CaseRepository.transition`：UPDATE ... WHERE version=$n | DA-T-03、US-E5-06 |
| 契约先行 | 先改 openapi.yaml → schemas.py → 路由；未实现端点返回 501 + US 归属 | 07 §5 |

## 写路径模板（Sprint 1 全部照此展开）

`CaseRepository.transition(case_id, event, actor, expected_version)`：
状态机校验 → 乐观锁 UPDATE → audit_log 留痕（同事务）→ 发布领域事件。
API-W-07（复核）与 API-W-09（审批决）是该模板的首两个实现样本。

## 双守护说明

应用层状态机是第一道防线；存储层 `db/init/04-invariants.sql` 触发器为第二道防线，
两者迁移表必须逐条一致（变更时同步修改，纪律见 04-invariants.sql 头注）。

## TODO 清单（Sprint 1+）

- `core/events.py` RocketMQPublisher 投递实现（US-E3-04）
- `api/cases.py` 复核确认自动建审批工单（US-E4-05）
- `api/approvals.py` 驳回触发回滚任务链（US-E5-04）
- `api/kb.py` 发布后向量化入库 kb_embedding（US-E6-05）
- Nacos 动态配置读取（BR 阈值，US-E1-03/SC-06）
