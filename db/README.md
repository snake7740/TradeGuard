# 存储端设计（PolarDB-PG 兼容层 / pgvector）

变更纪律：先改 docs/08 数据字典 → 再改 db/init/*.sql → 应用层同步。

## init 脚本序列（容器首启按文件名序执行）

| 文件 | 职责 |
|---|---|
| 01-schema.sql | 12 张业务表 DDL（DA-T-01~12）+ 索引 + BA-BR 阈值种子 |
| 02-roles.sql | 权限矩阵账号 tg_web / tg_app（03 §6 读写矩阵落地，DA-INV-05） |
| 03-umodel-fallback.sql | UnifiedModel Sprint 0 退化路径：fn_related_graph 图函数 |
| 04-invariants.sql | 不变量守护：DA-T-13 状态迁移白名单表 + DA-INV-01 触发器 + DA-INV-06 知识发布人工门控 |

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
