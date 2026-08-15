<template>
  <el-container class="layout">
    <el-aside width="224px" class="sidebar">
      <div class="logo">
        <span class="logo-mark">TG</span>
        <span class="logo-text">TradeGuard<small>交易风控中枢</small></span>
      </div>
      <el-menu :default-active="$route.path" router class="side-menu"
        background-color="transparent" text-color="#b6c2d9" active-text-color="#ffffff">
        <el-menu-item v-for="m in visibleMenus" :key="m.path" :index="m.path">
          {{ m.title }}
        </el-menu-item>
      </el-menu>
      <div class="side-footer">TradeGuard · 交易风控中枢 v1.4</div>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <div class="topbar-title">{{ $route.meta.title || 'TradeGuard' }}</div>
        <div class="topbar-right">
          <!-- 顶栏角色一键切换（localStorage 承载 + 路由白名单守卫，01 §6） -->
          <el-tooltip placement="bottom"
            content="四个角色对应风控闭环中值班、审批、审计、知识管理四个环节，切换以体验不同岗位视角">
            <el-select v-model="role" size="small" class="role-select" @change="onRoleChange">
              <el-option v-for="r in ROLES" :key="r" :value="r" :label="r" />
            </el-select>
          </el-tooltip>
          <!-- 中间件健康探针（API-W-15，10s 轮询）：DEGRADED 时悬停可见故障组件 -->
          <el-tooltip placement="bottom" :content="healthTip">
            <span class="health">
              <i class="dot" :class="healthDot" />
              {{ healthText }}
            </span>
          </el-tooltip>
        </div>
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getHealth } from './api'
import { ROLES, currentRole, setRole } from './role'

// 5 页面 × 4 角色（01 §6 用户旅程 × 04 §10）；roles 为空数组=全角色可见
const menus = [
  { path: '/cases', title: '案件工作台', roles: ['风控值班员'] },
  { path: '/approvals', title: '审批门户', roles: ['风控审批官'] },
  { path: '/audit', title: '审计查询', roles: ['合规审计员'] },
  { path: '/kb', title: '知识库管理', roles: ['风控策略管理员'] },
  { path: '/observe', title: '可观测面板', roles: [] },
]

const router = useRouter()
const route = useRoute()
const role = ref(currentRole())
const visibleMenus = computed(() =>
  menus.filter((m) => m.roles.length === 0 || m.roles.includes(role.value)))

// 角色切换：写回 localStorage；当前页面对新角色不可见时跳回其角色首页
function onRoleChange(r) {
  setRole(r)
  const allowed = route.meta.roles
  if (allowed && !allowed.includes(r)) router.push('/')
}

// 健康状态：UP 全部正常 / DEGRADED 部分组件降级（悬停提示故障明细）/ DOWN 探针失联
const health = ref({ status: '…', components: {} })
let timer
const COMPONENT_NAMES = { postgres: '数据库', rocketmq: '消息队列', 'mcp-core': '业务 MCP', 'mcp-external': '外部数据 MCP' }
const healthDot = computed(() => health.value.status === 'UP' ? 'up' : health.value.status === 'DEGRADED' ? 'warn' : 'down')
const healthText = computed(() => health.value.status === 'UP' ? '系统正常' : health.value.status === 'DEGRADED' ? '部分组件降级' : '系统探测失败')
const healthTip = computed(() => {
  const parts = Object.entries(health.value.components || {})
    .map(([k, v]) => `${COMPONENT_NAMES[k] || k}：${v === 'UP' ? '正常' : '异常'}`)
  return parts.length ? parts.join('；') : '健康探针无响应，请检查 web-api 服务'
})
onMounted(() => {
  const probe = async () => {
    try { health.value = (await getHealth()).data } catch { health.value = { status: 'DOWN', components: {} } }
  }
  probe(); timer = setInterval(probe, 10000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style>
.layout { height: 100vh; }

/* ---------- 深色品牌侧栏 ---------- */
.sidebar {
  display: flex; flex-direction: column;
  background: linear-gradient(180deg, #101c3a 0%, #16294f 100%);
  color: #fff;
}
.logo { display: flex; align-items: center; gap: 10px; padding: 18px 16px 16px; }
.logo-mark {
  width: 34px; height: 34px; border-radius: 9px; flex-shrink: 0;
  background: linear-gradient(135deg, #2f54eb, #597ef7);
  display: inline-flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: 14px; letter-spacing: 0.5px;
}
.logo-text { font-weight: 700; font-size: 16px; line-height: 1.2; display: flex; flex-direction: column; }
.logo-text small { font-weight: 400; font-size: 11px; color: #8fa0c2; margin-top: 2px; }

/* 菜单项：圆角块状，激活态品牌色填充 */
.side-menu { border-right: none; padding: 0 12px; flex: 1; }
.side-menu .el-menu-item {
  height: 42px; line-height: 42px; border-radius: 8px; margin-bottom: 6px;
  font-size: 14px;
}
.side-menu .el-menu-item:hover { background: rgba(255, 255, 255, 0.08) !important; }
.side-menu .el-menu-item.is-active {
  background: linear-gradient(90deg, #2f54eb, #4a6cf0) !important;
  font-weight: 600;
}
.side-footer { padding: 14px 16px; font-size: 11px; color: #64748f; }

/* ---------- 顶栏 ---------- */
.topbar {
  height: 56px; display: flex; align-items: center; justify-content: space-between;
  background: #fff; border-bottom: 1px solid var(--tg-border);
}
.topbar-title { font-size: 16px; font-weight: 700; color: var(--tg-text-main); }
.topbar-right { display: flex; align-items: center; gap: 14px; }
.role-select { width: 158px; }
.health { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--tg-text-sub); }
.health .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.health .dot.up { background: #52c41a; box-shadow: 0 0 0 3px rgba(82, 196, 26, 0.15); }
.health .dot.warn { background: #faad14; box-shadow: 0 0 0 3px rgba(250, 173, 20, 0.15); }
.health .dot.down { background: #ff4d4f; box-shadow: 0 0 0 3px rgba(255, 77, 79, 0.15); }

/* ---------- 内容区 ---------- */
.el-main { background: var(--tg-bg-page); padding: 20px 24px; overflow-y: auto; }
</style>
