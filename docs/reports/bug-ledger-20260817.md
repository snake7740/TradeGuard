# BUG 台账 — 浏览器多角色协同实测（2026-08-17）

来源：四轮 browser_use 实测 + 用户手测反馈。分类维度：A 产品语义 / B 系统协同 / C 前端 UX / D 错误契约。
处置状态：已修复 / 待决策 / 误报澄清。修复后须按文档回写纪律在 05 追溯矩阵留痕（本轮 = R-46）。
验证基线（2026-08-17 复测）：pytest test_routes.py 19 passed（含 BUG-01/05 回归 2 例）+ npm run build 通过 + web-api/web-portal 镜像重建 + 活体探针（404 信封 / severity=high 全 black / severity=low 全 none-0）+ 浏览器三场景（A 高风险 75 分入调查链 PASS；B 抽屉自动刷新 PASS；C 格式校验 PASS）。

| 编号 | 分类 | 严重度 | 现象 | 根因定位 | 修复方案 | 状态 |
|---|---|---|---|---|---|---|
| BUG-01 | A | 高 | 门户"新建演示案件-高风险"从不走高风险路径：实测 CASE-…-75435a risk_score=23、CASE-…-21ea25 risk_score=0，均低分自动放行/降噪归档 | `app/api/demo.py` /api/demo/subjects `ORDER BY random()` 全表随机且有意不过滤风险等级（注释"保留选择权"）；`web-portal/src/views/CaseWorkbench.vue` triggerDemo 只取 `items[0]`，list_flag/risk_level 未用；severity 不参与聚合评分 | 后端 subjects 支持 severity 过滤（high→black/block 名单主体，黑名单垫分 75 必走调查链；low→none 干净主体；medium 保持随机）；前端传参 | 已修复（复测：CASE-…-33a244 score=75 入 INVESTIGATING） |
| BUG-02 | B | 高 | 纯门户/浏览器操作下审批队列永远无工单：INVESTIGATING 后"启动调查"→PENDING_APPROVAL 死等，D2 主流程必须靠外部脚本代 Agent 提交处置 | 职责边界设计使处置提交归 Agent（02 §3.3，门户无按钮是正确的）；但 EventWorker 只承接到 INVESTIGATING，AgentTeams 活体 Agent 无事件驱动调度承接——"Agent 何时被触发"存在自动化空档 | 方案甲落地（R-46）：EventWorker 注入 investigation+disposition 双内核，`TG_DELEGATE_INVESTIGATING_SECONDS=900`（compose on，代码缺省 0=OFF 不扰测试）；滞留案代 Agent 走 AA-SK-02→AA-SK-03(freeze)，幂等键 `<case>:delegate` | 已修复（容器实测：222 案积压以 ~5 案/分钟持续消化；audit_log 留痕 `agent:AA-AG-03` 调查完成 + `AA-AG-04` 建单；待决审批工单每 ~13s 新增一张） |
| BUG-03 | C | 低 | 案件详情抽屉打开期间状态不自动更新（曾被误判为"前后端不一致"） | CaseWorkbench.vue onEvent 防抖回调 `if (!detailVisible.value) load()`——抽屉打开时什么都不做 | 抽屉打开时改为调 refreshDetail()（仅刷案件基本信息，轻量） | 已修复（复测：审批经同源 fetch 放行后 2~4s 抽屉自动「待审批」→「已处置」，时间线同步新增） |
| BUG-04 | C | 低 | /observe Trace 查询输短案号（"325265"）仅提示"暂无 Trace 数据"，无格式提示（API 数据实际存在） | Observability.vue loadTraces 无输入校验；查询为 case_id 精确匹配 | 提交前正则校验 `^CASE-\d{8}-[0-9a-f]{6}$`，不合法 ElMessage.warning 提示格式 | 已修复（复测：短号触发黄色格式告警且不发起查询；完整案号正常返回 span） |
| BUG-05 | D | 中 | `GET /api/cases/CASE-…/events` 返回 `{"detail":"Not Found"}`（FastAPI 默认格式）；而契约内 404 返回 `{"code":"E-NOT-FOUND","message":…}`——同一系统两种 404 信封 | 该路径从未存在于契约/代码（事件流唯一契约路径为 /api/events/stream；按案件回放历史由 /api/audit/{case_id} 承载）。属契约外路径，但默认 404 信封与 R-20 错误语义不统一 | main.py 注册 StarletteHTTPException 处理器：404 且 detail 为默认文案时改写为契约信封（code=E-NOT-FOUND + path 提示），其余状态码不动 | 已修复（复测：原始 URL 返回 404 + E-NOT-FOUND 信封 + 正确契约路径指引。用户次日复测仍见旧信封，系 web-api 容器未随代码重建——镜像重建后 8300 门户 / 8200 直连双路复测均返回契约信封。教训：改后端代码后必须 `docker compose up -d --build web-api`） |
| BUG-06 | B | 低 | `docker logs web-api` 只有 uvicorn 访问日志，应用层（tradeguard.*）INFO 日志全部丢失：EventWorker 启动/委托/重试在 stdout 无痕，本轮排障只能靠 audit_log/DB 逆向取证 | uvicorn 默认 log config 只配置 uvicorn.* 自家 logger，root logger 无 handler；Python lastResort 兜底仅输出 WARNING+，INFO 被静默丢弃 | main.py 模块级 `logging.basicConfig(level=TG_LOG_LEVEL 缺省 INFO)` 补 root handler；镜像重建后 EventWorker 生命周期与委托动作可直查容器日志 | 已修复（复测：重建后 docker logs 出现「EventWorker 已启动（…INVESTIGATING 委托 900s/30s）」与「EventWorker 委托 CASE-… 完成」） |
| BUG-07 | D | 中 | 可观测面板"Nacos 控制台"外链 `http://localhost:8848/nacos` 点击后仅显示纯文本提示（"Nacos Console default port is 8080, and the path is /."），且探测标签显示"可达"与实际不可用不符 | 双重失效：①Nacos v3.2.3 控制台独立于 8848 主端口（容器 8080，路径 /，302→/next/），`/nacos` 为 v2 时代路径已移除；②容器 8080 未发布到宿主（宿主 8080 又被本机服务占用）；探测用 no-cors fetch，有 HTTP 响应即标"可达"，无法识别"路径错误的内容页" | compose nacos 增发布 `127.0.0.1:8850:8080`（回环，避开宿主 8080 占用）；前端外链改 `http://localhost:8850/`；start_all COMPOSE_PORTS 补 8850 端口清障项 | 已修复（复测：缓存击穿 URL 下链接 href=8850、标签"可达"；新标签打开进入 Nacos v3 控制台 `/next/#/register` 初始化密码引导页——与 Higress /init 同为"首次初始化未完成"形态，属环境实况非缺陷） |

## 误报澄清（复核后排除，防止重复排查）

| 现象 | 澄清 | 复核证据 |
|---|---|---|
| 知识库已发布文档行仍显示"审核发布/驳回"按钮（第四轮代理判 FAIL） | 守卫正常：操作列 `v-if="row.status==='pending'"`，已发布行显示"已完成审核" | API 实查：published 仅 601d2bf7（本轮发布那条）；代理看到的带按钮行是队列中另外 3 条 pending 复盘申请 |
| /observe "暂无 Trace 数据"疑为 span 丢失 | span 数据完好 | API 直查 CASE-20260816-325265 返回 AA-SK-01~04 全部 ok；系输短案号所致（已转 BUG-04 修 UX） |
| 控制台 ERR_ABORTED（图谱请求/SSE） | 请求取消类噪音（关闭弹窗/SSE 正常关闭），非缺陷 | 两轮 console 汇总均无报错级日志 |
| BUG-3 首轮复测 FAIL（抽屉 10s 无变化） | 测试协议缺陷：打开抽屉时案件已到 INVESTIGATING 稳态（BUG-2 空档使其不再流转），无事件即无刷新，行为正确 | 有效协议（同源 fetch 审批放行 + 抽屉打开观察）下 2~4s 自动更新，PASS |
| BUG-3 二轮复测 FAIL（双标签审批后抽屉不动） | 前置动作未发生：代理无法切换审批角色（直连 /approvals 被角色守卫重定向），审批从未提交，案件未流转 | DB 复核 approval_record.decision 仍为 NULL；改用页面内同源 fetch 后 PASS |
| BUG-4 首轮复测 FAIL（无格式告警） | 浏览器磁盘缓存旧 chunk（index.html 启发式缓存 → 引用旧哈希 JS）；容器内产物实为新代码 | 容器 grep 命中新文案；缓存击穿 URL（?cb=随机串）复测即 PASS |
| BUG-7 首轮复测 FAIL（外链仍显示 8848/nacos） | 同上缓存同型：portal 镜像已重建且容器 grep 命中 localhost:8850、零命中 8848/nacos，代理浏览器命中磁盘旧 chunk | `docker compose exec web-portal grep -r localhost:8850 …/assets` 命中；缓存击穿 URL 复测 href=8850 + 点击进入 v3 控制台 PASS |
| 可观测面板外链"Higress 网关控制台"打开是初始化引导页、Nacos 控制台打开是 /next/#/register 初始化密码页 | 均为"首次初始化未完成"的环境实况（CLAUDE.md 已声明 Higress /init；Nacos v3 同型），非缺陷；初始化需设置管理员密码，属部署动作不应由测试代理代做 | Higress 8001 呈"初始化管理员账号"表单；Nacos 8850 /next/#/register 呈初始化密码界面 |

## 低危文档漂移（顺手项，随 R-46 一并收口）

1. openapi.yaml CaseId pattern `^CASE-\d{8}-\d{4}$` 与代码生成 `CASE-日期-6位hex` 不符 → 修正 pattern
2. main.py FastAPI description "API-W-01~20" → 22
3. openapi.yaml info.description "6+3 个工具" → 12+3
4. 00-总则表头版本 v1.4.11 → 与修订记录对齐（v1.4.19）

## 测试过程缺陷（非产品 BUG，仅记录）

- pytest 全量 184 例中 test_verification_passed_chain 存在 EventWorker 同库抢跑竞态（R-37 同型）：单独重跑 5/5 过。按项目纪律跑全量前应关容器 EventWorker。
