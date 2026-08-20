<template>
  <!-- 策略工作台（风控策略管理员专属工作台，A0 角色工作台分化；API-W-11~13 消费方，01 §6 风控策略管理员旅程，SC-05；SSE 事件驱动实时刷新） -->
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">策略工作台</div>
        <div class="page-desc">策略管理员专属工作台：案件结案后，Agent 复盘产出的欺诈手法知识会以「入库申请」形式进入本队列。知识发布仅限人工审核——确认内容准确后发布入库，供后续案件调查检索引用。</div>
      </div>
      <div class="page-actions">
        <el-tooltip content="入库申请随 SSE 领域事件实时刷新" placement="top">
          <el-tag size="small" effect="plain" round type="success">实时</el-tag>
        </el-tooltip>
      </div>
    </div>
    <div class="page-body">
    <el-row justify="space-between" align="middle" class="toolbar">
      <el-select v-model="status" style="width:200px" @change="load">
        <el-option v-for="s in statusOptions" :key="s.value" :value="s.value" :label="s.label" />
      </el-select>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </el-row>
    <el-table :data="rows" v-loading="loading" stripe
      :empty-text="emptyText">
      <el-table-column prop="doc_id" label="条目编号" width="220" />
      <el-table-column prop="title" label="标题" show-overflow-tooltip />
      <el-table-column prop="status" label="审核状态" width="120">
        <template #default="{ row }">
          <el-tag :type="(KB_STATUS_META[row.status] || {}).tag || 'info'" effect="light">
            {{ (KB_STATUS_META[row.status] || {}).label || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="ts" label="申请时间" width="200" show-overflow-tooltip />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'pending'">
            <el-button link type="success" @click="confirmPublish(row)">审核发布</el-button>
            <el-button link type="danger" @click="confirmReject(row)">驳回</el-button>
          </template>
          <span v-else class="hint">已完成审核</span>
        </template>
      </el-table-column>
    </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getKbApplications, publishKbDocument, rejectKbDocument, openEventStream } from '../api'
import { KB_STATUS_META, friendlyError } from '../labels'

const statusOptions = [
  { value: 'pending', label: '待审核' },
  { value: 'published', label: '已发布' },
  { value: 'rejected', label: '已驳回' },
]
const status = ref('pending')
const rows = ref([])
const loading = ref(false)

const emptyText = computed(() => status.value === 'pending'
  ? '暂无待审核的入库申请。案件结案归档后，Agent 复盘生成的知识申请将出现在这里'
  : '该状态下暂无知识条目')

async function load() {
  loading.value = true
  try {
    const d = (await getKbApplications(status.value)).data
    rows.value = Array.isArray(d) ? d : d?.items || []
  } catch (e) { ElMessage.error(friendlyError(e, '查询失败')) } finally { loading.value = false }
}

// DA-INV-06：发布/驳回均属人工决策，需二次确认
async function confirmPublish(row) {
  try {
    await ElMessageBox.confirm(`确认发布知识条目「${row.title || row.doc_id}」？发布后立即向调查检索开放。`,
      '审核发布', { type: 'warning', confirmButtonText: '确认发布', cancelButtonText: '取消' })
  } catch { return }
  try {
    await publishKbDocument(row.doc_id, {})
    ElMessage.success('知识已发布入库，后续调查可检索引用')
    load()
  } catch (e) { ElMessage.error(friendlyError(e, '发布失败')) }
}

async function confirmReject(row) {
  try {
    await ElMessageBox.confirm(`确认驳回知识条目「${row.title || row.doc_id}」的入库申请？驳回后该知识不进入检索库。`,
      '驳回申请', { type: 'warning', confirmButtonText: '确认驳回', cancelButtonText: '取消' })
  } catch { return }
  try {
    await rejectKbDocument(row.doc_id, {})
    ElMessage.success('已驳回该入库申请')
    load()
  } catch (e) { ElMessage.error(friendlyError(e, '驳回失败')) }
}

// ---- 实时更新：SSE 事件驱动刷新（案件归档生成入库申请等事件到达后防抖重载） ----
let es, debounce
function onEvent() {
  clearTimeout(debounce)
  debounce = setTimeout(load, 1200)
}
onMounted(() => {
  load()
  es = openEventStream(onEvent)
})
onUnmounted(() => { clearTimeout(debounce); es && es.close() })
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
</style>
