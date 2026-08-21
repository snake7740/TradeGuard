# web-api 后端设计（Sprint 0 架构模板 → Sprint 1-8 全量落地）

> **这是什么 / 给谁看**：TradeGuard 的后端服务（FastAPI）——人类操作面的唯一后端，也是事件驱动闭环的承接侧。
> 面向**后端开发者**。零基础请先读[根 README](../../README.md) 的「快速开始 / 技术栈一览 / 项目地图」，
> 再读 [docs/02 应用架构](../../docs/02-应用架构AA.md) 了解 5 Agent / 5 Skill 分工，最后看本文档的分层结构。
> 启动：`docker compose up -d --build web-api`（或一键 `scripts/start_all.py`）；代码入口 `app/main.py`。

FastAPI 应用，承载 API-W-01~22 契约（docs/openapi/tradeguard-openapi.yaml，22 路径）。
三端真实调用链（04 §10.1）中"人类操作面"的唯一后端，也是事件驱动闭环的承接侧
（EventWorker 自动消费 CaseRegistered，AA-CL-01/02）。

## 分层结构

```
app/
├─ main.py               # 应用工厂 + lifespan 装配（pool/publisher/repositories/
│                        #   EventWorker 启停/30s 扫描循环：审批升级 BA-BR-13 + 核验超时 BA-BR-08）
├─ schemas.py            # Pydantic 入参模型（与 openapi.yaml 逐项对齐）
├─ repositories.py       # 仓储层：一聚合根一仓储（Case/Approval/Kb）；
│                        #   transition 事务内 set_config('tg.actor') 供 DB actor 守卫
├─ api_guards.py         # bearer 鉴权（TG_API_TOKEN）+ 写操作审计 api.request（US-E7-01）
├─ core/                 # 核心域（零基础设施依赖，可直接单测，06 §3）
│  ├─ state_machine.py   # 案件状态机：12 态 / 19 事件 / 22 条迁移路径（02 §7，DA-INV-01）
│  ├─ events.py          # 事件发布端口/适配器：进程内总线（必达）+ RocketMQ（尽力而为）
│  ├─ event_worker.py    # 闭环承接：DB 轮询主力 2s 扫 REGISTERED + case_id 单飞锁（TG_EVENT_WORKER 开关）
│  ├─ config_service.py  # Nacos 阈值快照 5s 热加载 + sys_config 镜像（SC-06）
│  └─ tracing.py         # skill_span 埋点（US-E7-04，API-W-20 追溯）
├─ skills/               # 四内核技能（确定性内核，纯函数 + 编排分离）
│  ├─ aggregation.py     # AA-SK-01：velocity/降噪/加权评分/四路由裁决（noise/auto_release/investigate/all_fail）
│  ├─ investigation.py   # AA-SK-02：假设匹配 + 图谱 + 黑名单加分 + 证据固化
│  ├─ disposition.py     # AA-SK-03：先转 DISPOSING 后执行 + 重试分类 + 审批闭环
│  └─ verification.py    # AA-SK-04：核验三分支（一致≠回滚/不一致反向/无凭证升级）+ _retrospective
└─ api/                  # 路由层：按资源分文件，API-W 全量声明
   ├─ health.py          # W-15 健康探针（pg/rocketmq/mcp-core/mcp-external）
   ├─ alerts.py          # W-01 立案（202 + trace_id，CaseRegistered 事件源）
   ├─ cases.py           # W-02~07/17~19/22（列表/详情/信号/图谱/证据/复核/聚合/调查/核验/处置凭证列表）
   ├─ approvals.py       # W-08/09（审批队列含 requested_action/escalated_at / 批准驳回→状态机）
   ├─ audit.py           # W-10 审计追溯
   ├─ kb.py              # W-11~13（知识申请/发布/驳回）
   ├─ events_stream.py   # W-14 SSE（进程内总线，21 事件名扁平信封）
   ├─ config.py          # W-16 阈值 GET/PUT（Nacos 写回 + DB 镜像）
   ├─ observability.py   # W-20 技能 span 追溯
   └─ demo.py            # W-21 演示候选主体（无在办案件的账户）
```

## 核心设计模式

| 模式 | 落点 | 依据 |
| --- | --- | --- |
| 状态机 | `core/state_machine.py` 纯函数迁移表 + actor 守卫（human_only） | 02 §7、DA-INV-01 |
| 仓储 | `repositories.py` 聚合根边界即模块边界 | 03 §9.1/§9.4 |
| 端口/适配器 | EventPublisher：进程内总线必达 + RocketMQ 尽力而为（可替换） | 03 §9.2、TA-C-06 |
| 乐观锁 | `CaseRepository.transition`：UPDATE ... WHERE version=$n | DA-T-03、US-E5-06 |
| 契约先行 | 先改 openapi.yaml → schemas.py → 路由 | 07 §5 |
| 双守护 | 应用层状态机 + DB 触发器（04-invariants 白名单 + trg_case_actor_gate actor 守卫） | DA-INV-01、工作流 E |

## 写路径模板

`CaseRepository.transition(case_id, event, actor, expected_version)`：
状态机校验 → 事务内 `set_config('tg.actor', actor)` → 乐观锁 UPDATE →
audit_log 留痕（同事务）→ 发布领域事件（透传案件 trace_id）。
DB 侧 trg_case_actor_gate 按 actor 前缀拦截：E-ACTOR-REQUIRED → E-BAD-TRANSITION →
五对 human-only 迁移守卫（E-HUMAN-ONLY-DB）。

## 已交付里程碑（原 TODO）

- ✅ RocketMQPublisher 真实投递（rocketmq-client-python 0.5.0rc2，case-events 实测有消息）
- ✅ 复核确认自动建审批工单（review_confirm → API-M-11）
- ✅ 批准→自动执行至 DISPOSED / 驳回→RollbackToReview 回退禁用自动通道
- ✅ 发布后向量化入库 kb_embedding（确定性哈希 embedding，1024 维）
- ✅ Nacos 动态配置热加载 + 阈值全链路真接线（SC-06，mcp-core 70 线同源）
- ✅ EventWorker 闭环承接 + 审批升级/核验超时扫描（lifespan 挂载）
