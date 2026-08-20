<template>
  <!-- 复核审批工作台（风控审批官专属工作台，A0 角色工作台分化）：
       人工复核队列（API-W-07，SC-10）+ 审批工单（API-W-08/09，SC-02/03/09）；SSE 事件驱动实时刷新 -->
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">复核审批工作台</div>
        <div class="page-desc">审批官专属工作台：对待人工复核案件作出复核结论（排除归档/确认处置/升级审批），并对高风险处置审批单批准或驳回——批准后系统将立即执行所请求的处置动作，驳回则案件退回人工复核。创建超过 30 分钟未决策的审批单将自动升级并标红。</div>
      </div>
      <div class="page-actions">
        <el-tooltip content="复核队列与待决审批单随 SSE 领域事件实时刷新" placement="top">
          <el-tag size="small" effect="plain" round type="success">实时</el-tag>
        </el-tooltip>
        <el-button :loading="loading" @click="loadAll">刷新队列</el-button>
      </div>
    </div>
    <div class="page-body">
      <el-tabs v-model="tab">
        <el-tab-pane label="人工复核队列" name="review" />
        <el-tab-pane label="审批工单" name="approval" />
      </el-tabs>

      <!-- 人工复核队列：审批驳回/处置失败/升级进入的案件，复核为审批官专属职责（网关 RBAC：review 仅审批官） -->
      <el-table v-if="tab === 'review'" :data="reviewRows" v-loading="loading" stripe
        empty-text="当前没有需要人工复核的案件。审批驳回、处置失败或处置撤销的案件会进入此队列">
        <el-table-column prop="case_id" label="案件编号" width="210" />
        <el-table-column prop="subject_ref" label="涉事主体" show-overflow-tooltip />
        <el-table-column prop="risk_score" label="风险评分" width="100" sortable>
          <template #default="{ row }">
            <el-tag :type="row.risk_score >= 70 ? 'danger' : row.risk_score >= 40 ? 'warning' : 'success'">
              {{ row.risk_score }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="130">
          <template #default="{ row }">
            <el-tooltip :content="statusMeta(row.status).desc" placement="top">
              <el-tag :type="statusMeta(row.status).tag" effect="light">{{ statusLabel(row.status) }}</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="立案时间" width="180" show-overflow-tooltip />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="warning" @click="openReview(row)">复核</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 审批工单：高风险处置的人工决策队列（API-W-08） -->
      <el-table v-else :data="rows" v-loading="loading" stripe
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
            <!-- C1 控辩互审记录（BA-BR-19，US-E10）：仅建议不裁决，最终决策仍由审批官作出 -->
            <el-button v-if="row.debate_json" link type="warning" @click="openDebate(row)">控辩</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 人工复核弹窗（API-W-07，SC-10）：三种结论均明示后果 -->
    <el-dialog v-model="reviewVisible" :title="`人工复核 · ${review.caseId}`" width="520px">
      <el-form label-width="70px">
        <el-form-item label="复核结论">
          <el-radio-group v-model="review.conclusion" class="review-radios">
            <el-radio value="release">
              排除归档
              <span class="hint">认定为误报，案件结案归档</span>
            </el-radio>
            <el-radio value="block">
              确认处置
              <span class="hint">确认欺诈，创建审批单执行管控</span>
            </el-radio>
            <el-radio value="escalate">
              升级审批
              <span class="hint">证据不足或金额重大，升级人工审批</span>
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="复核意见">
          <el-input v-model="review.opinion" type="textarea" :rows="3"
            placeholder="请填写复核依据，例如：经核实交易为本人操作，设备与常用地址一致（不少于 5 个字符）"
            maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button type="primary" :loading="review.submitting" @click="submitReview">提交复核</el-button>
      </template>
    </el-dialog>

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

    <!-- 控辩互审抽屉（C1，BA-BR-19/DA-INV-09，US-E10）：控/辩/裁三段建议性记录，
         只增不改；裁决权仍在审批官 -->
    <el-drawer v-model="debateVisible" :title="`控辩互审记录 · ${debateApprovalId}`" size="40%">
      <template v-if="debate">
        <el-alert type="info" :closable="false" show-icon class="decide-tip"
          title="以下为 AG-01 控辩互审的建议性结论，不替代人工裁决；最终批准/驳回仍由您作出（BA-BR-19）。" />
        <div class="debate-block">
          <div class="debate-title">控方（主张从严）</div>
          <ul><li v-for="(p, i) in debate.prosecution" :key="'p' + i">{{ p }}</li></ul>
        </div>
        <div class="debate-block">
          <div class="debate-title">辩方（主张从轻/保护）</div>
          <ul><li v-for="(p, i) in debate.defense" :key="'d' + i">{{ p }}</li></ul>
        </div>
        <div class="debate-block">
          <div class="debate-title">裁判倾向</div>
          <el-tag :type="debate.verdict === 'pass' ? 'success' : debate.verdict === 'concerns' ? 'warning' : 'danger'">
            {{ debate.adjudication || debate.verdict }}
          </el-tag>
          <div v-if="debate.summary" class="hint debate-summary">{{ debate.summary }}</div>
        </div>
      </template>
      <el-empty v-else description="该审批单暂无控辩记录" />
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getApprovals, decideApproval, getEvidence, getCases, postReview, openEventStream } from '../api'
import { DECISION_META, actionLabel, statusLabel, statusMeta, friendlyError } from '../labels'

// 审批官默认直面人工复核队列（复核为审批官专属职责）
const tab = ref('review')
const rows = ref([])
const reviewRows = ref([])
const loading = ref(false)
const minutes = (row) => Math.floor((Date.now() - new Date(row.created_at).getTime()) / 60000)

async function loadAll() {
  loading.value = true
  try {
    const [ap, rv] = await Promise.all([
      getApprovals(),
      getCases({ status: 'MANUAL_REVIEW', page: 1, size: 50 }),
    ])
    rows.value = ap.data.items
    reviewRows.value = rv.data.items
  } catch (e) { ElMessage.error(friendlyError(e, '查询失败')) }
  finally { loading.value = false }
}

// ---- 人工复核（API-W-07，SC-10） ----
const reviewVisible = ref(false)
const review = reactive({ caseId: '', conclusion: 'release', opinion: '', submitting: false })

function openReview(row) {
  Object.assign(review, { caseId: row.case_id, conclusion: 'release', opinion: '', submitting: false })
  reviewVisible.value = true
}

async function submitReview() {
  if (review.opinion.trim().length < 5) { ElMessage.warning('请填写不少于 5 个字符的复核依据'); return }
  review.submitting = true
  try {
    await postReview(review.caseId, { conclusion: review.conclusion, opinion: review.opinion.trim() })
    ElMessage.success('复核结论已提交，案件已按结论流转')
    reviewVisible.value = false
    loadAll()
  } catch (e) { ElMessage.error(friendlyError(e, '复核失败')) } finally { review.submitting = false }
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
    loadAll()
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

// ---- 控辩互审抽屉（C1，US-E10）：debate_json 随审批单返回（DA-INV-09 只增） ----
const debateVisible = ref(false)
const debateApprovalId = ref('')
const debate = ref(null)

function openDebate(row) {
  debateApprovalId.value = row.approval_id
  debate.value = typeof row.debate_json === 'string'
    ? (() => { try { return JSON.parse(row.debate_json) } catch { return null } })()
    : row.debate_json
  debateVisible.value = true
}

// ---- 实时更新：SSE 事件驱动队列刷新（建单/决策/升级/复核事件到达后防抖重载） ----
let es, debounce
function onEvent() {
  clearTimeout(debounce)
  debounce = setTimeout(loadAll, 1200)
}
onMounted(() => {
  loadAll()
  es = openEventStream(onEvent)
})
onUnmounted(() => { clearTimeout(debounce); es && es.close() })
</script>

<style scoped>
.decide-tip :deep(.el-alert__content) { font-size: 13px; }
.mt12 { margin-top: 12px; }
.page-actions { display: flex; align-items: center; gap: 10px; }
.review-radios { display: flex; flex-direction: column; gap: 10px; }
.review-radios .hint { margin-left: 10px; }
.debate-block { margin-top: 16px; }
.debate-title { font-weight: 600; margin-bottom: 6px; }
.debate-block ul { margin: 0; padding-left: 20px; }
.debate-summary { margin-top: 8px; }
</style>
