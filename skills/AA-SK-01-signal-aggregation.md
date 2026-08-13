# AA-SK-01 signal-aggregation · 多源信号聚合与降噪

> 承载：AA-AG-02（信号聚合 Agent）｜ 确定性内核：services/web-api/app/skills/aggregation.py

## 九属性契约（02 §4）

| 属性 | 内容 |
|---|---|
| 用途 | 聚合四类数据源信号，标准化、降噪、评分（BA-BP-02）；计算 velocity 频次特征（BA-BR-14） |
| 输入 | `{subject_id, subject_type, time_window}` |
| 输出 | `{signals[], risk_score, degraded_sources[]}` |
| 调用条件 | 事件立案后由 AA-AG-01 分派触发 |
| 依赖工具 | AA-MCP-01 `query_transactions`；AA-MCP-02 `query_credit/query_complaint/query_sentiment` |
| 失败处理 | 单源超时 5s 重试 2 次→降级置空；全源失败→E-AGG-ALL-FAIL 转人工 |
| 安全边界 | 查询自动附带事由字段；返回不含证件号明文 |
| 复用价值 | 主体风险画像（授信审批、商户准入） |
| 协同关系 | AA-AG-02 调用；输出供 AA-AG-01 分级、AA-AG-03 调查 |

## 确定性执行步骤（规则内核，LLM 缺席时的完整闭环）

1. **采集**：并行调四类源（交易流水/征信/投诉/舆情），单源失败记入 `degraded_sources[]` 不中断；
2. **标准化**：各源字段经防腐层翻译为 `risk_signal` 统一 Schema（DA-T-04，source_type/signal_type/severity）；
3. **降噪合并**：同 `(subject, signal_type, 1h 窗口)` 重复信号合并为 1 条，severity 取 max、count 累计；
4. **velocity 特征**：统计主体 1h/24h 交易笔数与金额填入 `velocity_json`（BA-BR-14：1h≥10 笔或 24h≥50 笔 +30 分）；
5. **评分**：基础分 = Σ(signal severity × 权重[交易0.4/征信0.2/投诉0.25/舆情0.15]) + velocity 加分，封顶 100；
6. **落库**：信号 insert DA-T-04（只增），评分结果经 `SignalsAggregated` 事件交状态机 → INVESTIGATING；低风险（<40）走 `NoiseDismissed` → ARCHIVED。

## 验收锚点

SC-11（velocity 触发）、SC-01（低风险自动放行）、单测覆盖 ≥60%（US-E3-03）。
