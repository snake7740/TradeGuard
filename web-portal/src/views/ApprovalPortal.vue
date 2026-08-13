<template>
  <div>
    <el-alert type="info" :closable="false" class="bar"
      title="高风险处置审批队列（BA-BR-02 审批门控）；创建超过 30 分钟未决策标红（BA-BR-13）。写路径 API-W-09 于 US-E5 实现。" />
    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="approval_id" label="工单号" width="220" />
      <el-table-column prop="case_id" label="事件编号" width="200" />
      <el-table-column prop="decision" label="状态" width="110" />
      <el-table-column label="等待时长" width="140">
        <template #default="{ row }">
          <el-tag :type="overdue(row) ? 'danger' : 'info'">{{ minutes(row) }} 分钟</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" />
    </el-table>
    <el-empty v-if="!loading && rows.length === 0" description="暂无待审批工单" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getApprovals } from '../api'

const rows = ref([])
const loading = ref(false)
const minutes = (row) => Math.floor((Date.now() - new Date(row.created_at).getTime()) / 60000)
const overdue = (row) => minutes(row) >= 30   // BA-BR-13

async function load() {
  loading.value = true
  try { rows.value = (await getApprovals()).data.items }
  catch (e) { ElMessage.error('查询失败：' + e.message) }
  finally { loading.value = false }
}
onMounted(load)
</script>

<style scoped>.bar { margin-bottom: 12px; }</style>
