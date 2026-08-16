# web-portal 前端设计（Vue 3 + Element Plus + Vite，Sprint 0 模板 → Sprint 1-8 全量落地）

> **这是什么 / 给谁看**：TradeGuard 的人机操作门户（Vue 3 + Element Plus）——5 页面 × 4 角色，所有数据实时来自 web-api。
> 面向**前端开发者**。零基础请先读[根 README](../README.md) 的「快速开始 / 操作指引」。
> 启动：`cd web-portal && npm install && npm run dev`（开发）或 `npm run build`（构建）；生产随 compose 起（:8300）。

定位：人机操作面（04 §10.1 三端真实调用链），5 页面 × 4 角色；**零内置静态数据**——
所有展示数据实时来自 web-api，SSE 实时推送领域事件。

## 结构与实现实况

```
src/
├─ api.js        # 契约层：API-W-01~15/17~19/21~22 全量声明（20 个编号注释在案），
│                # 函数名对齐 openapi operationId + 统一错误信封解包（detail.code/message）
│                # W-14 SSE：subscribeCaseEvents → EventSource('/api/events/stream')
│                # W-20 Trace：getTraces → /api/observability/traces
│                # W-16 阈值配置为后端契约（PUT 经测试/场景追溯验证），门户暂不设管理页
├─ labels.js     # 业务语汇层：12 状态/21 事件/处置动作/路由结论/审计动作的中文范围
│                #   + 字段名翻译 + 错误码人性化提示（friendlyError），页面文案统一走此层，
│                #   契约编号与英文枚举不外泄到 UI
├─ role.js       # 角色上下文（风控值班员/风控审批官/合规审计员/风控策略管理员）
├─ router.js     # 路由 + meta.roles 白名单守卫（越权重定向首页）
├─ App.vue       # 布局骨架：侧边导航 + 中间件健康灯（10s 轮询 API-W-15）
└─ views/        # 5 页面全部填实
   ├─ CaseWorkbench.vue    # 案件工作台（W-02~07/17~19/21/22）：信号/图谱/证据字段对齐实况，
   │                       #   risk_flag 三态判定修复，按状态接线"推进聚合/启动调查/触发核验"
   │                       #   流水线按钮，演示候选主体取 W-21 真实接口
   ├─ ApprovalPortal.vue   # 审批门户（W-08/09）：展示 requested_action/requested_amount、
   │                       #   escalated_at 超时标红（BA-BR-13）、decide 走 X-Operator 人类通道
   ├─ AuditQuery.vue       # 审计查询（W-10，SC-08 全链追溯，ts/basis 字段对齐）
   ├─ KnowledgeBase.vue    # 知识库管理（W-11~13，人工发布 human:* 守卫）
   └─ Observability.vue    # 可观测面板（W-14 SSE 事件流 + W-20 技能 span 追溯，event/start_ts 对齐）
```

## 设计思路

1. **契约驱动**：api.js 每个函数标注 API-W 编号；新增接口先改 openapi.yaml，
   后端落地后前端跟进，禁止前端私造字段（Sprint 8 契约对账：五页字段逐项核对）；
2. **角色守卫**：路由 meta.roles 声明页面可见角色，beforeEach 统一拦截；
   演示环境角色存 localStorage 切换（可观测面板全角色开放）；
3. **SSE 而非轮询**：领域事件推送走 API-W-14 EventSource（进程内总线 21 事件名扁平信封），
   事件键 `event`、时间键按各端点实况（span 用 start_ts epoch 秒）；
4. **组件库选型**：Element Plus 提供表格/表单/标签套件，聚焦业务编排不自绘基础件；
5. **讲人话且专业**：页面文案经 labels.js 统一为风控业务范围（立案/聚合/调查取证/
   审批把关/处置凭证/核验/归档），状态带五阶段进度与下一步指引（NEXT_STEP），
   错误提示给可操作建议而非工程堆栈；契约编号（API-W-xx、E-xxx 仅保留在括注）
   与英文枚举不直接暴露给操作者。

## 已交付（原 TODO，Sprint 1-8）

- ✅ CaseWorkbench 详情抽屉接 W-04/05/06（信号/图谱/证据）+ W-07 复核 → 审批闭环
- ✅ ApprovalPortal decide 调用 + escalated_at 标红（US-E5-03，BA-BR-13）
- ✅ Observability 事件流时间线 + Trace 追溯（US-E7-03/04）
- ✅ 角色切换 UI（App.vue 侧栏，role.js setRole）
- ✅ `npm run build` 通过；demo_playbook 3/3 追溯经真实 HTTP 走本门户同款契约
