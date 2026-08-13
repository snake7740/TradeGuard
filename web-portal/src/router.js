import { createRouter, createWebHistory } from 'vue-router'
import { currentRole } from './role'

// 4 页面 × 4 角色（04 §10 人机界面设计）；meta.roles 声明可见角色白名单
const routes = [
  { path: '/', redirect: '/cases' },
  { path: '/cases', name: 'cases', component: () => import('./views/CaseWorkbench.vue'),
    meta: { title: '事件工作台', roles: ['风控运营', '值班员'] } },
  { path: '/approvals', name: 'approvals', component: () => import('./views/ApprovalPortal.vue'),
    meta: { title: '审批门户', roles: ['审批官', '风控运营'] } },
  { path: '/kb', name: 'kb', component: () => import('./views/KnowledgeBase.vue'),
    meta: { title: '知识库管理', roles: ['知识管理员'] } },
  { path: '/observe', name: 'observe', component: () => import('./views/Observability.vue'),
    meta: { title: '可观测面板', roles: ['值班员', '风控运营'] } },
]

const router = createRouter({ history: createWebHistory(), routes })

// 角色守卫：白名单外重定向到首页（Sprint 0 角色存 localStorage，无登录态）
router.beforeEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · TradeGuard` : 'TradeGuard'
  if (to.meta.roles && !to.meta.roles.includes(currentRole())) return { path: '/' }
  return true
})

export default router
