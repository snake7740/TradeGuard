<template>
  <!-- 审计工作台（合规审计员专属工作台，A0 角色工作台分化；API-W-10 消费方，01 §6 合规审计员旅程，SC-08；SSE 事件驱动实时刷新） -->
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">审计工作台</div>
        <div class="page-desc">审计员专属工作台：输入案件编号回放完整操作审计链——每一步由谁、在何时、基于什么依据做出（含追踪标识 trace_id），全程留痕、不可篡改；已处置案件可触发结果核验，一致后归档。满足合规追溯要求。</div>
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
        <el-button :loading="prechecking" @click="runPrecheck">专家清单预检</el-button>
        <span class="hint">案件编号也可在「案件工作台」列表中获取</span>
      </el-space>
    </el-row>
    <!-- D1 专家清单预检（US-E10）：处置要件逐项体检，裁决前快速定位缺口（只读，不推进状态） -->
    <el-card v-if="precheck" class="result" :header="`专家清单预检 · ${queriedCaseId}`">
      <template #extra>
        <el-tag :type="precheck.passed ? 'success' : 'danger'" effect="light">
          {{ precheck.passed ? '体检通过（无硬性缺口）' : '存在硬性缺口，请先补齐' }}
        </el-tag>
      </template>
      <el-table :data="precheck.items" size="small" stripe>
        <el-table-column prop="name" label="检查项" width="220" />
        <el-table-column label="结果" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'ok' ? 'success' : row.status === 'warn' ? 'warning' : 'danger'">
              {{ row.status === 'ok' ? '通过' : row.status === 'warn' ? '关注' : '缺口' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="basis" label="依据" show-overflow-tooltip />
      </el-table>
    </el-card>
    <el-card v-if="queried" class="result" :header="`审计时间线 · ${queriedCaseId}`">
      <!-- 结果核验入口（API-W-19/22，US-E6-01/02，AA-SK-04 审计员旅程）：
           DISPOSED 待核验时提供一键核验（取最新 executed 凭证），一致→归档+复盘入库 -->
      <el-alert v-if="caseInfo.status === 'DISPOSED'" type="warning" :closable="false"
        show-icon class="verify-bar">
        <template #title>
          案件待核验（已执行处置，需比对凭证与实际状态一致后归档，BA-BR-08 时效内完成）
        </template>
        <el-button type="primary" size="small" :loading="verifying" class="verify-btn"
          @click="runVerify">触发结果核验</el-button>
      </el-alert>
      <el-alert v-else-if="verifyResult" type="success" :closable="false" show-icon class="verify-bar"
        :title="`核验一致，案件已归档（复盘已提入库申请：${verifyResult}，发布须策略管理员人工审核）`" />
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
import { getAuditTrail, getAuditPrecheck, getCases, getCase, getDispositions, verifyCase, openEventStream } from '../api'
import { auditActionLabel, statusLabel, friendlyError, routeLabel } from '../labels'

const caseId = ref('')
const queriedCaseId = ref('')
const queried = ref(false)
const loading = ref(false)
const records = ref([])
const recent = ref([])
const caseInfo = ref({})   // 所选案件状态（DISPOSED 时展示核验入口）
const verifying = ref(false)
const verifyResult = ref('')  // 核验一致后的复盘入库单号
const prechecking = ref(false)
const precheck = ref(null)    // D1 专家清单预检结果（US-E10）

// 专家清单预检（D1）：只读体检，不改变案件状态；未选案件时提示先回放
async function runPrecheck() {
  const id = caseId.value.trim() || queriedCaseId.value
  if (!id) { ElMessage.warning('请选择或输入案件编号'); return }
  prechecking.value = true
  try {
    const d = (await getAuditPrecheck(id)).data
    if (d.code) { ElMessage.error(d.message); precheck.value = null; return }
    precheck.value = d
    queriedCaseId.value = id
  } catch (e) { ElMessage.error(friendlyError(e, '预检失败')) }
  finally { prechecking.value = false }
}

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
    verifyResult.value = ''
    precheck.value = null
    try { caseInfo.value = (await getCase(id)).data } catch { caseInfo.value = {} }
  } catch (e) { ElMessage.error(friendlyError(e, '查询失败')) } finally { loading.value = false }
}

// ---- 结果核验（API-W-19，AA-SK-04）：取最新 executed 凭证比对，一致→VERIFIED→ARCHIVED ----
async function runVerify() {
  verifying.value = true
  try {
    // API-W-22：取最近一条执行成功的处置记录（多凭证时按业务取最新）
    const { data } = await getDispositions(queriedCaseId.value)
    const executed = (data.items || []).filter((d) => d.status === 'executed')
    const exec = executed[executed.length - 1]
    if (!exec) { ElMessage.warning('暂无已执行的处置记录，无法发起核验'); return }
    const res = (await verifyCase(queriedCaseId.value, exec.exec_id)).data
    if (res.consistency_check) {
      verifyResult.value = res.kb_application || ''
      ElMessage.success(`核验一致：${routeLabel('passed')}，复盘已提入库申请（pending）`)
    } else {
      ElMessage.error(`核验不一致：${routeLabel('rollback')}，案件已转人工复核（P0）`)
    }
    await query()   // 刷新状态与审计链（归档留痕立即可见）
  } catch (e) { ElMessage.error(friendlyError(e, '核验失败')) } finally { verifying.value = false }
}
</script>

<style scoped>
.result { margin-top: 16px; }
.verify-bar { margin-bottom: 16px; }
.verify-btn { margin-left: 12px; }
.action { margin-left: 8px; }
.detail { color: var(--tg-text-sub); font-size: 13px; margin-top: 4px; }
.detail.trace { font-family: Consolas, monospace; }
</style>
