<template>
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">知识库管理</div>
        <div class="page-desc">知识入库申请队列（DA-INV-06：发布仅人工，BA-BR-11 人工门控）。确认发布 / 驳回走 API-W-12/13，均需二次确认。</div>
      </div>
    </div>
    <div class="page-body">
    <el-row justify="space-between" align="middle" class="toolbar">
      <el-select v-model="status" style="width:200px" @change="load">
        <el-option v-for="s in statusOptions" :key="s.value" :value="s.value" :label="s.label" />
      </el-select>
      <el-button @click="load">刷新</el-button>
    </el-row>
    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="doc_id" label="文档编号" width="220" />
      <el-table-column prop="title" label="标题" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="row.status === 'pending' ? 'warning' : row.status === 'published' ? 'success' : 'danger'">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="申请时间" width="200" show-overflow-tooltip />
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <template v-if="row.status === 'pending'">
            <el-button link type="success" @click="confirmPublish(row)">确认发布</el-button>
            <el-button link type="danger" @click="confirmReject(row)">驳回</el-button>
          </template>
          <span v-else class="hint">已终态</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && rows.length === 0" description="暂无入库申请" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getKbApplications, publishKbDocument, rejectKbDocument } from '../api'

const statusOptions = [
  { value: 'pending', label: '待确认（pending）' },
  { value: 'published', label: '已发布（published）' },
  { value: 'rejected', label: '已驳回（rejected）' },
]
const status = ref('pending')
const rows = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const d = (await getKbApplications(status.value)).data
    rows.value = Array.isArray(d) ? d : d?.items || []
  } catch (e) { ElMessage.error('查询失败：' + e.message) } finally { loading.value = false }
}

// BA-BR-11：发布/驳回均需人工二次确认
async function confirmPublish(row) {
  try {
    await ElMessageBox.confirm(`确认发布知识文档「${row.title || row.doc_id}」？发布后立即生效。`,
      '确认发布', { type: 'warning', confirmButtonText: '确认发布', cancelButtonText: '取消' })
  } catch { return }
  try {
    await publishKbDocument(row.doc_id, {})
    ElMessage.success('已发布')
    load()
  } catch (e) { ElMessage.error('发布失败：' + e.message) }
}

async function confirmReject(row) {
  try {
    await ElMessageBox.confirm(`确认驳回知识文档「${row.title || row.doc_id}」的入库申请？`,
      '确认驳回', { type: 'warning', confirmButtonText: '确认驳回', cancelButtonText: '取消' })
  } catch { return }
  try {
    await rejectKbDocument(row.doc_id, {})
    ElMessage.success('已驳回')
    load()
  } catch (e) { ElMessage.error('驳回失败：' + e.message) }
}

onMounted(load)
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
</style>
