<template>
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">审计查询</div>
        <div class="page-desc">按 case_id 回放完整审计链（SC-08 界面载体，BA-BR-09 全链路留痕）。数据来源 DA-T-08，API-W-10。</div>
      </div>
    </div>
    <div class="page-body">
    <el-row align="middle">
      <el-space>
        <el-input v-model="caseId" placeholder="输入事件编号 case_id" clearable
          style="width:280px" @keyup.enter="query" />
        <el-button type="primary" :loading="loading" @click="query">查询审计链</el-button>
      </el-space>
    </el-row>
    <el-card v-if="queried" class="result" :header="`审计时间线 · ${queriedCaseId}`">
      <el-timeline v-if="records.length">
        <el-timeline-item v-for="(a, i) in records" :key="i"
          :timestamp="a.created_at || a.timestamp" placement="top">
          <b>{{ a.actor || a.operator || '-' }}</b>
          <el-tag size="small" class="action">{{ a.action }}</el-tag>
          <div class="detail">{{ a.detail || a.details || a.reason || '' }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="该事件暂无审计记录" />
    </el-card>
    <el-empty v-else description="输入 case_id 后查询审计链" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getAuditTrail } from '../api'

const caseId = ref('')
const queriedCaseId = ref('')
const queried = ref(false)
const loading = ref(false)
const records = ref([])

async function query() {
  const id = caseId.value.trim()
  if (!id) { ElMessage.warning('请输入 case_id'); return }
  loading.value = true
  try {
    const d = (await getAuditTrail(id)).data
    records.value = Array.isArray(d) ? d : d?.items || []
    queriedCaseId.value = id
    queried.value = true
  } catch (e) { ElMessage.error('查询失败：' + e.message) } finally { loading.value = false }
}
</script>

<style scoped>
.result { margin-top: 16px; }
.action { margin-left: 8px; }
.detail { color: var(--tg-text-sub); font-size: 13px; margin-top: 4px; }
</style>
