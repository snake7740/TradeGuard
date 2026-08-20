import { createRouter, createWebHistory } from 'vue-router'
import { currentRole, ROLES } from './role'

// 各角色首页（01 §6 用户旅程）：白名单外重定向回本角色主页面，避免 '/' 循环跳转
const HOME_BY_ROLE = {
  风控值班员: '/cases', 风控审批官: '/approvals',
  合规审计员: '/audit', 风控策略管理员: '/kb',
}

// 5 页面 × 4 角色（04 §10 人机界面设计）；meta.roles 声明可见角色白名单
const routes = [
  { path: '/', redirect: () => HOME_BY_ROLE[currentRole()] || '/observe' },
  { path: '/cases', name: 'cases', component: () => import('./views/CaseWorkbench.vue'),
    meta: { title: '案件工作台', roles: ['风控值班员'] } },  // A0 角色工作台分化：案件工作台专属值班员
  { path: '/approvals', name: 'approvals', component: () => import('./views/ApprovalPortal.vue'),
    meta: { title: '复核审批工作台', roles: ['风控审批官'] } },  // 审批官专属：人工复核队列 + 审批工单
  { path: '/audit', name: 'audit', component: () => import('./views/AuditQuery.vue'),
    meta: { title: '审计工作台', roles: ['合规审计员'] } },  // 审计员专属：审计回放 + 结果核验
  { path: '/kb', name: 'kb', component: () => import('./views/KnowledgeBase.vue'),
    meta: { title: '策略工作台', roles: ['风控策略管理员'] } },  // 策略管理员专属：知识审核发布
  { path: '/observe', name: 'observe', component: () => import('./views/Observability.vue'),
    meta: { title: '可观测面板', roles: [...ROLES] } },
  // 404 兜底：未知路径不再白屏，统一回到当前角色首页
  { path: '/:pathMatch(.*)*', redirect: () => HOME_BY_ROLE[currentRole()] || '/observe' },
]

const router = createRouter({ history: createWebHistory(), routes })

// 角色守卫：白名单外重定向到本角色首页（Sprint 0 角色存 localStorage，无登录态）
router.beforeEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · TradeGuard` : 'TradeGuard'
  if (to.meta.roles && !to.meta.roles.includes(currentRole())) {
    return { path: HOME_BY_ROLE[currentRole()] || '/observe' }
  }
  return true
})

export default router
