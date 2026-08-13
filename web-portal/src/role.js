// 角色上下文（04 §10 人机界面：4 页面 × 4 角色）
// Sprint 0：localStorage 承载角色切换（演示用）；Sprint 1 接统一认证后改为令牌解析。
export const ROLES = ['风控运营', '审批官', '知识管理员', '值班员']

export function currentRole() {
  return localStorage.getItem('tg-role') || '风控运营'
}

export function setRole(role) {
  if (ROLES.includes(role)) localStorage.setItem('tg-role', role)
}
