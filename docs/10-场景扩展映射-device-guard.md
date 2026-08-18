# 场景扩展映射：device-guard（账户盗用守护）——第二场景同骨架实证

> 目的：以第二业务场景证明骨架泛化能力（场景价值维度的关键证据）。测试载体：
> `services/web-api/tests/test_scenario_device_guard.py`（SC-DG-01~05，5 例全绿）。
> 立案口径 source_type=TEST（与既有场景矩阵一致，KPI 业务口径隔离不受测试残留影响）。

## 1. 场景定义

| 维度 | trade-guard（第一场景） | device-guard（第二场景） |
|---|---|---|
| 业务问题 | 交易反欺诈（跑分/盗卡/大额突发） | 账户盗用守护（设备指纹异常/同设备多账户团伙） |
| 触发信号 | velocity_anomaly / large_amount_burst | device_anomaly |
| 图谱依据 | SAME_PAYEE（共享收款方） | SAME_DEVICE（共享设备指纹，transaction.device_fp_hash 派生） |
| 假设定性 | 跑分 / 盗卡 | 团伙盗刷（match_hypothesis SAME_DEVICE 分支零改动命中） |
| 处置动作 | block / freeze / reduce / release | 同一动作集（API-M-11） |

## 2. 元素级复用映射（零改动实证）

| 场景元素 | 骨架复用点 | 实证用例 |
|---|---|---|
| 设备异常信号接入 | DA-T-04 risk_signal（type 自由列，source 白名单 tx） | SC-DG-01/04/05 |
| 同设备团伙发现 | v_graph_edge SAME_DEVICE 边 + fn_related_graph（2 跳） | SC-DG-01/02/04 |
| 假设规则定性 | match_hypothesis SAME_DEVICE → 团伙盗刷 | SC-DG-01/04 |
| KB 手法引用 | DA-KB-01 检索 + doc_id 引用纪律（SC-05） | SC-DG-01 |
| 黑名单风险加分 | BA-BR-06（+30，API-M-13 幂等打标） | SC-DG-02 |
| 中风险人机边界 | BA-BR-01（40-69 无凭证拒自动处置，E-DISP-SCOPE） | SC-DG-03 |
| 高风险审批门控 | BA-BR-02（E-DISP-AUTH 建单转 PENDING_APPROVAL，SC-02） | SC-DG-04 |
| AG-01 合规互审 | R-47（处置建议证据充分性/过度处置风险，verdict 并入审批单） | SC-DG-04 |
| 审批闭环执行 | approve → DISPOSING → executed → DISPOSED | SC-DG-04 |
| 核验归档+复盘 | AA-SK-04 verify（一致→ARCHIVED）+ AA-SK-05 复盘入库申请（pending） | SC-DG-04 |
| 审计全链回放 | BA-BR-09（investigation.complete / disposition.reviewed / approval.create / approval.decide / disposition.submit / verification.run） | SC-DG-04 |
| 处置幂等 | DA-T-03（同幂等键重投返回首次凭证不重复执行） | SC-DG-04 尾段 |
| KB 记忆反哺 | R-48（待定假设以信号特征词检索 KB，命中升级定性） | SC-DG-05 |

## 3. 场景差异点（声明式接入清单）

新场景接入**未改任何应用代码**，差异全部通过声明式数据表达：

1. **信号类型**：`risk_signal.type='device_anomaly'`（varchar(40) 自由列，无需迁移）；
2. **图谱边**：SAME_DEVICE 由既有视图 `v_graph_edge` 从 `transaction.device_fp_hash` 派生（03-umodel-fallback.sql 四类边之一，本场景只是首次消费）；
3. **知识库**：device 手法文档（kb_document）按 DA-INV-06 人工发布，检索/反哺机制零改动；
4. **评分聚合**：本实证经 `record_case_signals` 直写分数；若需规则引擎接入新信号加分，扩展点在 AggregationService（见 CONTRIBUTING.md「场景扩展」）。

## 4. 复现命令

```powershell
cd services/web-api
..\..\.venv\Scripts\python.exe -m pytest tests\test_scenario_device_guard.py -v
```

前提：docker compose 全栈在线（postgres / mcp-core / mcp-external-mock）。

## 5. 结论

第二场景 device-guard 从信号接入到核验归档全链 13 个骨架能力点全部零改动复用；
唯一场景特定资产是两篇 KB 手法文档（声明式数据）。这验证了 02-应用架构 的
骨架假设：状态机/门控/审计/幂等/记忆五层是场景无关的基础设施，场景是数据。
