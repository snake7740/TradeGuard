// 角色上下文（01 §6 用户旅程 × 04 §10 人机界面：5 页面 × 4 角色）
// Sprint 0：localStorage 承载角色切换（演示用）；Sprint 1 接统一认证后改为令牌解析。
export const ROLES = ['风控值班员', '风控审批官', '合规审计员', '风控策略管理员']

export function currentRole() {
  const r = localStorage.getItem('tg-role')
  return ROLES.includes(r) ? r : ROLES[0]   // 旧角色名残留时回退默认角色
}

export function setRole(role) {
  if (ROLES.includes(role)) localStorage.setItem('tg-role', role)
}
