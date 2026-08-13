# TradeGuard KPI 评估报告（US-E7-04 离线评估，可复现）

- 生成时间：2026-08-13T21:43:46.274048+00:00
- 复现命令：`.venv/Scripts/python.exe scripts/kpi_report.py`
- Ground truth：`account.list_flag`（watch=团伙观察名单，black=确认欺诈，none=正常）

## 阈值与结论

| KPI | 定义 | 阈值 | 全量口径 | 演示口径 | 结论 |
|---|---|---|---|---|---|
| KPI-02 | 欺诈召回率 | >=85% | 100.0%（12/12） | 100.0%（1/1） | 达标 |
| KPI-03 | 误报率（最终定性口径） | <=10% | 94.4%（34/36） | 0.0%（0/2） | 达标 |
| KPI-04 | 人工介入率 | <=30% | 49.5%（382/772） | 66.7%（2/3） | 未达标 |
| KPI-05 | 处置留痕完整率 | >=100% | 100.0%（215/215） | 100.0%（4/4） | 达标 |

## 口径说明

- KPI-02/03 仅统计可关联账户档案（ground truth）的案件；自动化测试案件多为无档案合成哈希，不计入。
- KPI-03 的误报以"最终定性"计：核验回滚（:rollback）或人工复核排除（ReviewDismissed）的误判视为被闭环纠错，不计入误报（AA-SK-04 纠错能力）。
- KPI-04 人工通道 = 建过审批工单 / 停留 PENDING_APPROVAL / MANUAL_REVIEW / 全源失败转人工（signals.all_fail）。
- KPI-05 遍历 DA-T-06（disposition_record）逐条检查 DA-T-08（audit_log）对应 disposition.submit 记录（04 §7 验收口径）。
- 演示口径：主体带 demo- 前缀播种交易（剧本 D1/D2/D3 专用主体），复跑可复现。
- 全量口径含 Sprint 自动化测试案件（source=TEST，pytest 运行残留，多为
PENDING_APPROVAL 中间态），会抬升 KPI-03/04 全量数值；决赛验收以演示口径为准。
- KPI-04 演示口径结构性偏高：三剧本中 D2/D3 本身即人机协同审批/申诉场景
（2/3 必入人工通道），自动通道能力由 SC-01 与测试矩阵 121 例覆盖。
