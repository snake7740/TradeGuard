# 存储端设计（PolarDB-PG 兼容层 / pgvector）

变更纪律：先改 docs/08 数据字典 → 再改 db/init/*.sql → 应用层同步。

## init 脚本序列（容器首启按文件名序执行）

| 文件 | 职责 |
|---|---|
| 01-schema.sql | 12 张业务表 DDL（DA-T-01~12）+ 索引 + BA-BR 阈值种子 |
| 02-roles.sql | 权限矩阵账号 tg_web / tg_app（03 §6 读写矩阵落地，DA-INV-05） |
| 03-umodel-fallback.sql | UnifiedModel Sprint 0 退化路径：fn_related_graph 图函数 |
| 04-invariants.sql | 不变量守护：DA-T-13 状态迁移白名单表 + DA-INV-01 触发器 + DA-INV-06 知识发布人工门控 |
| 05-approval-extension.sql | Sprint 3-4（E5 处置审批回滚）DA-T-07 扩展：approval_record 增列 requested_action / requested_amount（E-DISP-AUTH 建单携带处置请求上下文，批准后 AA-SK-03 据此执行，SC-02）+ escalated_at（BA-BR-13 审批时效升级标记，SC-09 超时扫描器写入）；幂等可重跑（IF NOT EXISTS） |
| 06-closedloop-fix.sql | 闭环修复轮（v1.4.4）：状态白名单补对（DISPOSING→MANUAL_REVIEW）+ sys_config 活键补播（br-05/br-08/br-14 系列）+ 2027 年月分区（BA-BR-12）；幂等可重跑，内容已双写 01/04（新卷一致），运行卷经 `docker exec psql -f` 手工收敛 |
| 07-case-actor-gate.sql | 闭环修复轮（v1.4.4，工作流 E）：risk_case 状态变更人类门控触发器 trg_case_actor_gate（检查序：E-ACTOR-REQUIRED → 白名单 E-BAD-TRANSITION → 五对 human-only E-HUMAN-ONLY-DB）；依赖应用层 repositories.transition 事务内 set_config('tg.actor') 就位后启用，独立成文避免中间态拦截；幂等可重跑 |

## 设计思路

1. **不变量双守护**：DA-INV 优先在应用层实现（状态机/幂等键），存储层触发器是
   第二道防线——绕过应用层的直连写入同样被拒绝；
2. **只增表靠权限而非触发器**：risk_signal / case_evidence / audit_log 对应用角色
   REVOKE UPDATE/DELETE（02-roles.sql），比触发器更省开销且语义显式；
3. **状态迁移白名单表化**：case_state_transition 存白名单而非硬编码在触发器里，
   便于审计（SELECT 即见全量迁移路径）与后续 Nacos 动态治理；
4. **分区表按月**：transaction RANGE(ts) 月分区，支撑 BA-BR-12 归档（DROP PARTITION）；
5. **向量索引**：kb_embedding HNSW（vector_cosine_ops），Sprint 1 RAG 检索主路径。

## 与应用层状态机的一致性

04-invariants.sql 白名单与 services/web-api/app/core/state_machine.py TRANSITIONS
必须逐条一致；迁移路径变更顺序：02 §7 状态图 → OpenAPI 枚举 → 本文件 → 应用层。
