# AA-SK-05 knowledge-sedimentation · 复盘与知识沉淀

> 承载：AA-AG-05（合规审计 Agent）｜ 确定性内核：services/web-api/app/skills/retrospective.py

## 九属性契约（02 §4）

| 属性 | 内容 |
|---|---|
| 用途 | 生成复盘摘要、提取欺诈手法特征、提交知识入库申请（BA-BP-04） |
| 输入 | `{case_id, full_case_context}` |
| 输出 | `{retrospective, pattern_candidate, kb_application_id}` |
| 调用条件 | 事件结案且审计报告通过 |
| 依赖工具 | AA-MCP-01 `submit_kb_application`；DA-KB-01 向量化写入（仅申请通过后） |
| 失败处理 | 向量化失败→暂存待重试队列，不丢申请单 |
| 安全边界 | 仅能提交入库申请；发布须人工确认（BA-BR-11，DA-INV-06） |
| 复用价值 | 手法库反哺 AA-SK-02，经验闭环 |
| 协同关系 | AA-AG-05 调用；产出经人工确认后供 AA-AG-03 检索 |

## 确定性执行步骤

1. **复盘摘要**：汇总信号/图谱/处置/核验四段，生成 retrospective（模板化，LLM 可润色）；
2. **特征提取**：pattern_candidate = {手法类型, 信号指纹, 图特征, 命中规则 BR 列表}；
3. **入库申请**：`submit_kb_application(case_id, pattern)` → kb_document status=pending（DA-T-09）；
4. **人工门控**：发布仅 human:* 可置 published（DB 触发器 tg.actor 守护，DA-INV-06）；
5. **向量化**：发布后切块写 kb_embedding（pgvector HNSW），失败入重试队列。

## 验收锚点

SC-05（人工发布门控）、SC-06（配置驱动）、BA-BR-11。
