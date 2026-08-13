<template>
  <div>
    <el-row justify="space-between" align="middle" class="bar">
      <el-select v-model="status" placeholder="全部状态" clearable style="width:220px" @change="load">
        <el-option v-for="s in statuses" :key="s" :value="s" :label="s" />
      </el-select>
      <el-button type="primary" @click="triggerDemo">触发演示事件（API-W-01）</el-button>
    </el-row>
    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="case_id" label="事件编号" width="210" />
      <el-table-column prop="subject_ref" label="主体" show-overflow-tooltip />
      <el-table-column prop="risk_score" label="风险分" width="90" sortable>
        <template #default="{ row }">
          <el-tag :type="row.risk_score >= 70 ? 'danger' : row.risk_score >= 40 ? 'warning' : 'success'">
            {{ row.risk_score }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="170" />
      <el-table-column prop="current_agent" label="当前 Agent" width="120" />
      <el-table-column prop="created_at" label="创建时间" width="200" />
    </el-table>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getCases, postAlert } from '../api'

const statuses = ['REGISTERED', 'AGGREGATING', 'INVESTIGATING', 'PENDING_APPROVAL', 'APPROVED',
  'REJECTED', 'MANUAL_REVIEW', 'DISPOSING', 'DISPOSED', 'VERIFIED', 'ROLLBACK', 'ARCHIVED']
const rows = ref([])
const status = ref(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    rows.value = (await getCases({ status: status.value || undefined })).data.items
  } catch (e) { ElMessage.error('查询失败：' + e.message) } finally { loading.value = false }
}

async function triggerDemo() {
  const subject = 'acct-' + Math.random().toString(16).slice(2, 10)
  const { data } = await postAlert({ subject_ref: subject, source_type: 'demo_script', severity: 75 })
  ElMessage.success(`立案成功：${data.case_id}`)
  load()
}

onMounted(load)
</script>

<style scoped>.bar { margin-bottom: 12px; }</style>
