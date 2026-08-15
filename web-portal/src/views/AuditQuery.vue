<template>
  <!-- 审计查询（API-W-10 消费方，01 §6 合规审计员旅程，SC-08；SSE 事件驱动实时刷新） -->
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">审计查询</div>
        <div class="page-desc">输入案件编号回放完整操作审计链：每一步由谁、在何时、基于什么依据做出（含追踪标识 trace_id），全程留痕、不可篡改，满足合规追溯要求。</div>
      </div>
    </div>
    <div class="page-body">
    <el-row align="middle">
      <el-space>
        <!-- 便捷选择：最近 10 笔案件，免去手工抄录编号 -->
        <el-select v-model="caseId" filterable placeholder="选择或输入案件编号" style="width:320px">
          <el-option v-for="c in recent" :key="c.case_id" :value="c.case_id"
            :label="`${c.case_id} · ${statusLabel(c.status)}`" />
        </el-select>
        <el-button type="primary" :loading="loading" @click="query">回放审计链</el-button>
        <span class="hint">案件编号也可在「案件工作台」列表中获取</span>
      </el-space>
    </el-row>
    <el-card v-if="queried" class="result" :header="`审计时间线 · ${queriedCaseId}`">
      <el-timeline v-if="records.length">
        <el-timeline-item v-for="(a, i) in records" :key="i" :timestamp="a.ts" placement="top">
          <b>{{ a.actor }}</b>
          <el-tag size="small" class="action">{{ auditActionLabel(a.action) }}</el-tag>
          <div class="detail">依据：{{ a.basis }}</div>
          <div v-if="a.trace_id" class="detail trace">trace：{{ a.trace_id }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="该案件暂无审计记录" />
    </el-card>
    <el-empty v-else description="选择或输入案件编号后点击「回放审计链」" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getAuditTrail, getCases, openEventStream } from '../api'
import { auditActionLabel, statusLabel, friendlyError } from '../labels'

const caseId = ref('')
const queriedCaseId = ref('')
const queried = ref(false)
const loading = ref(false)
const records = ref([])
const recent = ref([])

// 最近案件快捷选择（API-W-02 取最新 10 笔）+ SSE 事件驱动实时刷新（全页面实时闭环）
async function refreshRecent() {
  try { recent.value = (await getCases({ page: 1, size: 10 })).data.items } catch { /* 快捷项失败不阻断手工输入 */ }
}
let es, debounce
function onEvent() {
  clearTimeout(debounce)
  debounce = setTimeout(async () => {
    await refreshRecent()
    // 已回放的案件若仍有未结留痕（如案件仍在流转），静默刷新审计链
    if (queried.value && queriedCaseId.value) {
      try {
        const d = (await getAuditTrail(queriedCaseId.value)).data
        records.value = Array.isArray(d) ? d : d?.items || []
      } catch { /* 刷新失败保留上次结果 */ }
    }
  }, 1200)
}
onMounted(() => {
  refreshRecent()
  es = openEventStream(onEvent)
})
onUnmounted(() => { clearTimeout(debounce); es && es.close() })

async function query() {
  const id = caseId.value.trim()
  if (!id) { ElMessage.warning('请选择或输入案件编号'); return }
  loading.value = true
  try {
    const d = (await getAuditTrail(id)).data
    records.value = Array.isArray(d) ? d : d?.items || []
    queriedCaseId.value = id
    queried.value = true
  } catch (e) { ElMessage.error(friendlyError(e, '查询失败')) } finally { loading.value = false }
}
</script>

<style scoped>
.result { margin-top: 16px; }
.action { margin-left: 8px; }
.detail { color: var(--tg-text-sub); font-size: 13px; margin-top: 4px; }
.detail.trace { font-family: Consolas, monospace; }
</style>
