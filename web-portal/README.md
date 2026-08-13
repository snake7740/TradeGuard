# web-portal 前端设计（Vue 3 + Element Plus + Vite）

定位：人机操作面（04 §10），4 页面 × 4 角色；**零内置静态数据**——
所有展示数据实时来自 web-api（04 §10.1 三端真实调用链）。

## 结构与实现路径

```
src/
├─ api.js        # 契约层：API-W-01~15 全量声明，函数名对齐 openapi operationId
│                # + 统一错误信封解包（detail.code/message）
├─ role.js       # 角色上下文（风控运营/审批官/知识管理员/值班员）
├─ router.js     # 路由 + meta.roles 白名单守卫（越权重定向首页）
├─ App.vue       # 布局骨架：侧边导航 + 中间件健康灯（10s 轮询 API-W-15）
└─ views/        # 4 页面骨架（Sprint 1 起按 US 填实）
   ├─ CaseWorkbench.vue    # 事件工作台（W-02~07 消费方）
   ├─ ApprovalPortal.vue   # 审批门户（W-08/09，BA-BR-13 超时标红）
   ├─ KnowledgeBase.vue    # 知识库管理（W-11~13）
   └─ Observability.vue    # 可观测面板（W-14 SSE 事件流）
```

## 设计思路

1. **契约驱动**：api.js 每个函数标注 API-W 编号；新增接口先改 openapi.yaml，
   后端落地后前端跟进，禁止前端私造字段；
2. **角色守卫**：路由 meta.roles 声明页面可见角色，beforeEach 统一拦截；
   Sprint 0 角色存 localStorage（演示切换），Sprint 1 接统一认证；
3. **SSE 而非轮询**：领域事件推送走 API-W-14 EventSource，后端切 RocketMQ 时
   前端协议不变（端口/适配器收益同后端）；
4. **组件库选型**：Element Plus 提供表格/表单/标签套件，聚焦业务编排不自绘基础件。

## TODO（Sprint 1+）

- CaseWorkbench：详情抽屉接 W-04/05/06（信号/图谱/证据），复核按钮接 W-07（US-E5-05）
- ApprovalPortal：decide 调用 + 倒计时标红（US-E5-03，BA-BR-13）
- Observability：事件流时间线 + 状态机迁移高亮（US-E7-03）
- 角色切换 UI（App.vue 侧栏，接 role.js setRole）
