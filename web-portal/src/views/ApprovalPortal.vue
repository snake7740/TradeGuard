<template>
  <!-- 审批门户（API-W-08/09 消费方，01 §6 风控审批官旅程，SC-02/03/09） -->
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">审批门户</div>
        <div class="page-desc">高风险处置必须经人工审批。请结合风险评分、涉事主体与证据链审慎决策——批准后系统将立即执行所请求的处置动作，驳回则案件退回人工复核。创建超过 30 分钟未决策的审批单将自动升级并标红。</div>
      </div>
      <div class="page-actions">
        <el-button :loading="loading" @click="load">刷新队列</el-button>
      </div>
    </div>
    <div class="page-body">
    <el-table :data="rows" v-loading="loading" stripe
      empty-text="暂无待决策的审批单。高风险处置请求（如账户冻结、交易拦截）将自动进入此队列">
      <el-table-column prop="approval_id" label="审批单号" width="200" show-overflow-tooltip />
      <el-table-column prop="case_id" label="案件编号" width="190" show-overflow-tooltip />
      <el-table-column prop="subject_ref" label="涉事主体" show-overflow-tooltip>
        <template #default="{ row }">{{ row.subject_ref || '-' }}</template>
      </el-table-column>
      <el-table-column prop="risk_score" label="风险评分" width="100" sortable>
        <template #default="{ row }">
          <el-tag v-if="row.risk_score != null"
            :type="row.risk_score >= 70 ? 'danger' : row.risk_score >= 40 ? 'warning' : 'success'">
            {{ row.risk_score }}
          </el-tag>
          <span v-else class="hint">-</span>
        </template>
      </el-table-column>
      <el-table-column label="请求处置" width="150">
        <!-- DA-T-07：requested_action / requested_amount 随建单写入，审批人据此决策 -->
        <template #default="{ row }">
          {{ actionLabel(row.requested_action) }}
          <span v-if="row.requested_amount != null" class="hint"> ¥{{ row.requested_amount }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="decision" label="决策状态" width="100">
        <template #default="{ row }">
          <el-tag :type="(DECISION_META[row.decision] || {}).tag || 'info'" effect="light">
            {{ (DECISION_META[row.decision] || {}).label || row.decision }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="等待时长" width="150">
        <template #default="{ row }">
          <!-- BA-BR-13：后端超时扫描写 escalated_at（SC-09），非空即已升级标红 -->
          <el-tooltip :content="row.escalated_at ? '超过 30 分钟未决策，已被系统自动升级提醒' : '未超时'" placement="top">
            <el-tag :type="row.escalated_at ? 'danger' : 'info'">
              {{ row.escalated_at ? '已升级 ' : '' }}{{ minutes(row) }} 分钟
            </el-tag>
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="230" fixed="right">
        <template #default="{ row }">
          <el-button link type="success" @click="openDecide(row, 'approve')">批准</el-button>
          <el-button link type="danger" @click="openDecide(row, 'reject')">驳回</el-button>
          <el-button link type="primary" @click="openEvidence(row)">证据链</el-button>
        </template>
      </el-table-column>
    </el-table>
    </div>

    <!-- 审批决策弹窗（API-W-09）：决策后果前置说明，意见 ≥5 字符 -->
    <el-dialog v-model="decideVisible"
      :title="`${form.decision === 'approve' ? '批准' : '驳回'}审批单 · ${form.approvalId}`" width="500px">
      <el-alert :type="form.decision === 'approve' ? 'warning' : 'info'" :closable="false" show-icon class="decide-tip">
        {{ form.decision === 'approve'
          ? '批准后系统将立即执行所请求的处置动作（冻结/拦截/降额），请确认证据链充分。'
          : '驳回后案件退回人工复核队列，且该案件的自动处置通道将被禁用。' }}
      </el-alert>
      <el-form label-width="70px" class="mt12">
        <el-form-item label="审批意见">
          <el-input v-model="form.opinion" type="textarea" :rows="3"
            placeholder="请填写审批依据，例如：证据链完整，团伙特征明确，同意冻结（不少于 5 个字符）"
            maxlength="500" show-word-limit />
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

    <!-- 证据链抽屉（API-W-06）：按案件编号展示证据时间线 -->
    <el-drawer v-model="evidenceVisible" :title="`证据链 · ${evidenceCaseId}`" size="40%">
      <div v-loading="evidenceLoading">
        <el-timeline v-if="evidence.length">
          <el-timeline-item v-for="(e, i) in evidence" :key="i" :timestamp="e.ts">
            <b>{{ e.claim }}</b>
            <div class="hint">依据：{{ e.source_ref }} · 置信度 {{ e.confidence }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="该案件暂无固化证据。证据不足时建议驳回并要求补充调查" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getApprovals, decideApproval, getEvidence } from '../api'
import { DECISION_META, actionLabel, friendlyError } from '../labels'

const rows = ref([])
const loading = ref(false)
const minutes = (row) => Math.floor((Date.now() - new Date(row.created_at).getTime()) / 60000)

async function load() {
  loading.value = true
  try { rows.value = (await getApprovals()).data.items }
  catch (e) { ElMessage.error(friendlyError(e, '查询失败')) }
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
  if (form.opinion.trim().length < 5) { ElMessage.warning('请填写不少于 5 个字符的审批依据'); return }
  form.submitting = true
  try {
    await decideApproval(form.approvalId, { decision: form.decision, opinion: form.opinion.trim() })
    ElMessage.success(form.decision === 'approve'
      ? '已批准，系统正在自动执行处置' : '已驳回，案件已退回人工复核队列')
    decideVisible.value = false
    load()
  } catch (e) { ElMessage.error(friendlyError(e, '决策失败')) } finally { form.submitting = false }
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
  } catch (e) { ElMessage.error(friendlyError(e, '证据链加载失败')); evidence.value = [] }
  finally { evidenceLoading.value = false }
}

onMounted(load)
</script>

<style scoped>
.decide-tip :deep(.el-alert__content) { font-size: 13px; }
.mt12 { margin-top: 12px; }
</style>
