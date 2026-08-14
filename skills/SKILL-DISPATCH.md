# Skill 调度矩阵 · 官方技能 × 本机技能全量编排

本矩阵约束所有 Sprint 的执行方式：**每个阶段的开发用对应领域 skill，每个 Sprint 的
Review 必须过五道元 skill 门控**（防止多 Agent 协作幻觉叠加）。

## 1. 阶段 → 本机领域 skill 映射

| 阶段/工件 | 强制使用的本机 skill | 适用 Sprint |
|---|---|---|
| 架构决策/构件评审 | architecture-skill、thinking-frameworks | 全部 |
| 后端 Python 服务（web-api/MCP/skills 内核） | python-skill | S1–S7 |
| REST/MCP 契约变更 | api-design（先改 openapi.yaml 再改代码） | 全部 |
| 数据库变更（DDL/触发器/权限） | db-design、mysql-skill→本项目为 PG，以 db-design 为主 | S1/S3/S5 |
| 单元/集成/契约测试 | tdd-skill（先测后码）、auto-testing、test-automation | 全部 |
| 前端 Vue3 门户 | vue-skill、ui-ux-expert、wcag-design | S5–S6（E7） |
| Docker/编排/镜像 | docker、devops-deploy | S1（重建镜像）、S7 |
| 数据管道（合成数据/velocity） | data-pipeline、analytics-data-analysis | S2 |
| 演示剧本与评估 | analytics-data-analysis、summarize | S7 |
| Word/PDF 交付物（如需） | docx、pdf | 决赛材料 |

## 2. 每 Sprint Review 必过五道元 skill 门控

| 元 skill | 评审问题 |
|---|---|
| ao-essence-injector | 完成是否有证据（命令输出/测试结果）？是否存在无依据声称？ |
| meta-cognitive-evolution | 本 Sprint 产出是否服务全局业务闭环（M1→M3→决赛）？ |
| engineering-first-principles | 五问门控：需求/边界/证据/回滚/幻觉检测是否全部有答案？ |
| engineering-problem-solving | 是否减法优先（复用 Sprint 0 模板而非新写）？改动是否外科手术式？ |
| engineering-execution-protocol | P0 中断恢复模板是否可用？执行模式（独立/受编排）是否声明？ |

## 3. Sprint → 官方技能（AA-SK）建设排期

| Sprint | 建设的官方技能 | 确定性内核位置 | 验收场景 |
|---|---|---|---|
| S2（E3） | AA-SK-01 signal-aggregation | app/skills/aggregation.py | SC-01、SC-11 |
| S3–S4（E5） | AA-SK-03 disposition-execution | mcp-core execute_disposition | SC-02/03/07 |
| S5–S6（E4+E6） | AA-SK-02 fraud-investigation | app/skills/investigation.py | SC-05 |
| S5–S6（E6） | AA-SK-04 compliance-audit、AA-SK-05 knowledge-sedimentation | app/skills/verification.py（核验三分支 + `_retrospective` 复盘入库申请，无独立 retrospective.py） | SC-04/08 |
| S7 | 全部技能回放评估 | scripts/demo_playbook.py（D1~D3 三剧本，演示=测试回放） | 3/3 |
| S8 | 事件驱动闭环（EventWorker）+ 门控加固 + 契约对账 | app/core/event_worker.py、services/mcp-core/server.py | SC-01~11 + demo_playbook 3/3 |

## 4. 执行纪律

1. 开发前：对照上表加载领域 skill 的规范执行，不凭记忆写代码；
2. 契约变更：api-design 纪律——docs/openapi/*.yaml 为唯一事实来源，先契约后代码；
3. 测试：tdd-skill 红绿循环，SC 场景即 BDD 验收测试（pytest 实现）；
4. Review：按 §2 五问逐项给出证据结论，写入 docs/07 对应 Sprint 执行记录；
5. 无 LLM Key 期间：官方技能跑确定性内核（skills/*.md 中的"确定性执行步骤"），
   Key 到位后 LLM 作为推理增强层叠加，闭环不断。
