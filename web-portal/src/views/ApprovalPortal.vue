<template>
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">审批门户</div>
        <div class="page-desc">高风险处置审批队列（BA-BR-02 审批门控）；创建超过 30 分钟未决策标红（BA-BR-13）。批准 / 驳回走 API-W-09。</div>
      </div>
    </div>
    <div class="page-body">
    <el-table :data="rows" v-loading="loading" stripe>
      <el-table-column prop="approval_id" label="工单号" width="220" />
      <el-table-column prop="case_id" label="事件编号" width="200" />
      <el-table-column prop="decision" label="状态" width="110" />
      <el-table-column label="等待时长" width="140">
        <template #default="{ row }">
          <el-tag :type="overdue(row) ? 'danger' : 'info'">{{ minutes(row) }} 分钟</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" show-overflow-tooltip />
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" @click="openDecide(row, 'approve')">批准</el-button>
          <el-button link type="danger" @click="openDecide(row, 'reject')">驳回</el-button>
          <el-button link type="primary" @click="openEvidence(row)">查看证据链</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!loading && rows.length === 0" description="暂无待审批工单" />
    </div>

    <!-- 审批决策弹窗：API-W-09（SC-02/03），opinion 前端校验 ≥5 字符 -->
    <el-dialog v-model="decideVisible"
      :title="`${form.decision === 'approve' ? '批准' : '驳回'}工单 · ${form.approvalId}`" width="460px">
      <el-form label-width="60px">
        <el-form-item label="意见">
          <el-input v-model="form.opinion" type="textarea" :rows="3"
            placeholder="审批意见（不少于 5 个字符）" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="decideVisible = false">取消</el-button>
        <el-button :type="form.decision === 'approve' ? 'success' : 'danger'"
          :loading="form.submitting" @click="submitDecide">
          确认{{ form.decision === 'approve' ? '批准' : '驳回' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 证据链抽屉：API-W-06，按 case_id 展示证据时间线 -->
    <el-drawer v-model="evidenceVisible" :title="`证据链 · ${evidenceCaseId}`" size="40%">
      <div v-loading="evidenceLoading">
        <el-timeline v-if="evidence.length">
          <el-timeline-item v-for="(e, i) in evidence" :key="i" :timestamp="e.created_at || e.timestamp">
            <b>{{ e.evidence_type || e.type || '证据' }}</b> · {{ e.summary || e.content || e.description || pretty(e) }}
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无证据" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getApprovals, decideApproval, getEvidence } from '../api'

const rows = ref([])
const loading = ref(false)
const minutes = (row) => Math.floor((Date.now() - new Date(row.created_at).getTime()) / 60000)
const overdue = (row) => minutes(row) >= 30   // BA-BR-13
const pretty = (o) => Object.entries(o).filter(([k]) => !['created_at', 'timestamp'].includes(k))
  .map(([k, v]) => `${k}=${v}`).join(' ')

async function load() {
  loading.value = true
  try { rows.value = (await getApprovals()).data.items }
  catch (e) { ElMessage.error('查询失败：' + e.message) }
  finally { loading.value = false }
}

// ---- 批准/驳回 ----
const decideVisible = ref(false)
const form = reactive({ approvalId: '', decision: 'approve', opinion: '', submitting: false })

function openDecide(row, decision) {
  Object.assign(form, { approvalId: row.approval_id, decision, opinion: '', submitting: false })
  decideVisible.value = true
}

async function submitDecide() {
  if (form.opinion.trim().length < 5) { ElMessage.warning('审批意见不少于 5 个字符'); return }
  form.submitting = true
  try {
    await decideApproval(form.approvalId, { decision: form.decision, opinion: form.opinion.trim() })
    ElMessage.success(form.decision === 'approve' ? '已批准' : '已驳回')
    decideVisible.value = false
    load()
  } catch (e) { ElMessage.error('决策失败：' + e.message) } finally { form.submitting = false }
}

// ---- 证据链抽屉 ----
const evidenceVisible = ref(false)
const evidenceLoading = ref(false)
const evidenceCaseId = ref('')
const evidence = ref([])

async function openEvidence(row) {
  evidenceCaseId.value = row.case_id
  evidenceVisible.value = true
  evidenceLoading.value = true
  try {
    const d = (await getEvidence(row.case_id)).data
    evidence.value = Array.isArray(d) ? d : d?.items || []
  } catch (e) { ElMessage.error('证据链加载失败：' + e.message); evidence.value = [] }
  finally { evidenceLoading.value = false }
}

onMounted(load)
</script>

<style scoped>
</style>
