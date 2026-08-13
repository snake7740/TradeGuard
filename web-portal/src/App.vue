<template>
  <el-container class="layout">
    <el-aside width="210px">
      <div class="logo">TradeGuard 风控中枢</div>
      <el-menu :default-active="$route.path" router>
        <el-menu-item v-for="r in menus" :key="r.path" :index="r.path">
          {{ r.title }}<el-tag size="small" type="info" class="role">{{ r.role }}</el-tag>
        </el-menu-item>
      </el-menu>
      <div class="health">
        <el-tag :type="health === 'UP' ? 'success' : 'danger'" effect="dark">
          中间件：{{ health }}
        </el-tag>
      </div>
    </el-aside>
    <el-main><router-view /></el-main>
  </el-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { getHealth } from './api'

const menus = [
  { path: '/cases', title: '事件工作台', role: '风控运营' },
  { path: '/approvals', title: '审批门户', role: '审批官' },
  { path: '/kb', title: '知识库管理', role: '知识管理员' },
  { path: '/observe', title: '可观测面板', role: '值班员' },
]

const health = ref('…')
let timer
onMounted(() => {
  const probe = async () => {
    try { health.value = (await getHealth()).data.status } catch { health.value = 'DOWN' }
  }
  probe(); timer = setInterval(probe, 10000)
})
onUnmounted(() => clearInterval(timer))
</script>

<style>
body { margin: 0; }
.layout { height: 100vh; }
.logo { font-weight: 700; padding: 16px; font-size: 15px; }
.role { margin-left: 8px; }
.health { padding: 16px; }
</style>
