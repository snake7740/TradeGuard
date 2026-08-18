---
name: AA-SK-05-knowledge-sedimentation
version: 1.5.0
description: 复盘与知识沉淀（BA-BP-04，人工发布门控 BA-BR-11）
agent: AA-AG-05
entrypoint: services/web-api/app/skills/knowledge.py
depends-mcp: submit_kb_application, search_kb
depends-tables: kb_document, kb_embedding, audit_log
tests: services/web-api/tests/test_knowledge.py
test-cases: 3
degradation-paths: 向量化失败不回滚发布可重试, 检索无命中返回空不阻断定性
---

# AA-SK-05 knowledge-sedimentation · 复盘与知识沉淀

> 承载：AA-AG-05（合规审计 Agent）｜ 确定性内核：services/web-api/app/skills/knowledge.py（向量化/检索）+ verification.py `_retrospective`（复盘申请）

## 九属性契约（02 §4）

| 属性 | 内容 |
| --- | --- |
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

1. **复盘摘要**：汇总信号/证据/处置/核验四段（VerificationService._retrospective，归档后自动触发），生成 retrospective；
2. **特征提取**：pattern_candidate = {手法类型, 信号指纹, 图特征, 命中规则 BR 列表}；
3. **入库申请**：API-M-05 `submit_kb_application(case_id, category, title, content)` → kb_document status=pending（DA-T-09，tg_app 写角色）；
4. **人工门控**：发布仅 human:* 可置 published——应用层守卫 + 事务内 `set_config('tg.actor')` + DB 触发器 kb_human_gate 三重守护（DA-INV-06，绕过直置被拒 E-KB-HUMAN-GATE）；
5. **向量化**：publish_and_index 发布后定长 200 字切块，确定性哈希 embedding（字符一/二/三元组→1024维 L2 归一）写 kb_embedding（ON CONFLICT DO NOTHING，tg_web 仅 INSERT 权限）；检索 SIMILARITY_MIN=0.22，仅 published 可见；向量化失败不回滚发布，申请单保留可重试。

落地入口：API-W-12 `/api/kb/applications/{doc_id}/publish`（委托 publish_and_index）。

## 验收锚点

SC-05（人工发布门控 + 检索命中附 doc_id）、SC-06（配置驱动）、BA-BR-11。测试载体：services/web-api/tests/test_knowledge.py（3 例，knowledge.py 覆盖率 92%，110/110 全绿）。
