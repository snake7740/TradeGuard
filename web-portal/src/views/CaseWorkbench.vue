<template>
  <!-- 案件工作台（API-W-02~07/17~19/21~22 消费方，01 §6 风控值班员旅程） -->
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">案件工作台</div>
        <div class="page-desc">值班员专属工作台：立案 → 信号聚合 → 调查取证 → 提请处置。选择案件查看证据链与关联图谱，并按流程推进；复核与审批由审批官在「复核审批工作台」处理。</div>
      </div>
      <div class="page-actions">
        <!-- API-W-01 立案：severity 三档对应三类处置路径（06 §2 SC-01/02/10）；工作台专属值班员 -->
        <el-dropdown trigger="click" @command="triggerDemo">
          <el-button type="primary">新建演示案件<el-icon class="el-icon--right"><arrow-down /></el-icon></el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item v-for="(m, sev) in SEVERITY_META" :key="sev" :command="sev">
                <div><b>{{ m.label }}</b></div>
                <div class="hint">{{ m.desc }}</div>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
    <div class="page-body">
      <el-row align="middle" class="toolbar">
        <el-space>
          <el-select v-model="status" placeholder="全部状态" clearable
            style="width:200px" @change="reload">
            <el-option v-for="s in statuses" :key="s" :value="s" :label="STATUS_META[s].label" />
          </el-select>
          <span class="hint">风险分 ≥</span>
          <el-input-number v-model="riskMin" :min="0" :max="100" :step="10"
            controls-position="right" style="width:120px" @change="reload" />
          <el-tooltip content="案件变化实时刷新（SSE 事件驱动）" placement="top">
            <el-tag size="small" effect="plain" round type="success">实时</el-tag>
          </el-tooltip>
        </el-space>
      </el-row>
    <el-table :data="rows" v-loading="loading" stripe
      empty-text="暂无案件。点击右上角「新建演示案件」生成一笔案件">
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
      <el-table-column prop="current_agent" label="当前处理方" width="110" />
      <el-table-column label="处理时效" width="150">
        <!-- BA-BR-13：待审批显示 30 分钟决策倒计时；已归档显示总耗时；其余显示已耗时 -->
        <template #default="{ row }">
          <el-tag v-if="row.status === 'PENDING_APPROVAL'" :type="remainMs(row) <= 0 ? 'danger' : 'warning'">
            {{ remainMs(row) <= 0 ? '超时 ' + fmt(-remainMs(row)) : '剩余 ' + fmt(remainMs(row)) }}
          </el-tag>
          <el-tag v-else-if="row.status === 'ARCHIVED'" type="success">结案耗时 {{ fmtSpan(row) }}</el-tag>
          <el-tag v-else type="info">已耗时 {{ fmt(elapsedMs(row)) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="立案时间" width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="80" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>
      <el-pagination class="tg-pager" background layout="total, sizes, prev, pager, next"
        :total="total" v-model:current-page="page" v-model:page-size="size"
        :page-sizes="[10, 20, 50]" @current-change="load" @size-change="reload" />
    </div>

    <!-- 详情抽屉：基本信息 / 信号 / 证据链 / 关联图谱 / 处置凭证 / 审计时间线 六源聚合（API-W-03~06/10/22） -->
    <el-drawer v-model="detailVisible" :title="`案件详情 · ${detail.caseId}`" size="55%">
      <div v-loading="detailLoading">
        <!-- 闭环进度：五阶段步骤条，由案件状态映射（状态含义悬停可见） -->
        <el-card shadow="never" class="stage-card">
          <el-steps :active="stageIdx" :process-status="stageStatus" finish-status="success" align-center>
            <el-step v-for="s in STAGES" :key="s" :title="s" />
          </el-steps>
          <div class="stage-note">
            <el-tag :type="statusMeta(detail.info.status).tag" effect="light" size="small">
              {{ statusLabel(detail.info.status) }}
            </el-tag>
            <span class="hint">{{ statusMeta(detail.info.status).desc }}</span>
          </div>
        </el-card>

        <el-descriptions title="基本信息" :column="2" border size="small" class="mt12">
          <el-descriptions-item v-for="k in infoKeys" :key="k" :label="INFO_LABELS[k] || k">
            <template v-if="k === 'status'">{{ statusLabel(detail.info.status) }}</template>
            <template v-else>{{ fmtInfo(k, detail.info[k]) }}</template>
          </el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">风险信号</el-divider>
        <el-table :data="detail.signals" size="small" stripe max-height="220" empty-text="暂无风险信号">
          <el-table-column prop="signal_id" label="信号编号" width="180" show-overflow-tooltip />
          <el-table-column prop="source" label="来源" width="100" />
          <el-table-column prop="type" label="类型" width="140" show-overflow-tooltip />
          <el-table-column prop="confidence" label="置信度" width="90" />
          <el-table-column label="降级" width="70">
            <template #default="{ row }">
              <el-tag v-if="row.degraded" type="warning" size="small">数据源降级</el-tag>
              <span v-else class="hint">-</span>
            </template>
          </el-table-column>
          <el-table-column prop="ts" label="时间" show-overflow-tooltip />
        </el-table>

        <el-divider content-position="left">证据链</el-divider>
        <el-timeline v-if="detail.evidence.length">
          <el-timeline-item v-for="(e, i) in detail.evidence" :key="i" :timestamp="e.ts">
            <b>{{ e.claim }}</b>
            <div class="hint">依据：{{ e.source_ref }} · 置信度 {{ e.confidence }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无证据。调查取证完成后，证据将在此固化展示" :image-size="40" />

        <el-divider content-position="left">关联图谱</el-divider>
        <el-row :gutter="12">
          <el-col :span="10">
            <el-table :data="detail.graph.nodes" size="small" stripe max-height="200" empty-text="无关联节点">
              <el-table-column prop="id" label="节点" show-overflow-tooltip />
              <el-table-column prop="type" label="类型" width="90" />
              <el-table-column label="名单标记" width="90">
                <template #default="{ row }">
                  <!-- 后端对无名单账户返回 'none'（truthy），必须显式排除才不恒红 -->
                  <el-tag :type="flagged(row) ? 'danger' : 'success'" size="small">
                    {{ flagged(row) ? row.risk_flag : '正常' }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-col>
          <el-col :span="14">
            <el-table :data="detail.graph.links" size="small" stripe max-height="200" empty-text="无关联关系">
              <el-table-column prop="source" label="源节点" show-overflow-tooltip />
              <el-table-column label="关系" width="110">
                <template #default="{ row }">{{ EDGE_LABELS[row.relation] || row.relation }}</template>
              </el-table-column>
              <el-table-column prop="target" label="目标节点" show-overflow-tooltip />
            </el-table>
          </el-col>
        </el-row>

        <el-divider content-position="left">处置凭证</el-divider>
        <el-table :data="detail.dispositions" size="small" stripe max-height="200"
          empty-text="暂无处置记录。处置动作执行后将在此生成执行凭证（幂等键 + 审批引用）">
          <el-table-column prop="exec_id" label="凭证号" width="170" show-overflow-tooltip />
          <el-table-column label="动作" width="100">
            <template #default="{ row }">{{ actionLabel(row.action) }}</template>
          </el-table-column>
          <el-table-column label="金额" width="100">
            <template #default="{ row }">{{ row.amount != null ? '¥' + row.amount : '-' }}</template>
          </el-table-column>
          <el-table-column label="执行状态" width="110">
            <template #default="{ row }">
              <el-tag :type="(DISP_STATUS_META[row.status] || {}).tag || 'info'" size="small" effect="light">
                {{ (DISP_STATUS_META[row.status] || {}).label || row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="审批引用" width="170" show-overflow-tooltip>
            <template #default="{ row }">{{ row.approval_ref || '-' }}</template>
          </el-table-column>
          <el-table-column prop="idempotency_key" label="幂等键" show-overflow-tooltip />
          <el-table-column prop="ts" label="时间" width="170" show-overflow-tooltip />
        </el-table>

        <el-divider content-position="left">审计时间线</el-divider>
        <el-timeline v-if="detail.audit.length">
          <el-timeline-item v-for="(a, i) in detail.audit" :key="i" :timestamp="a.ts">
            <b>{{ a.actor }}</b> · {{ auditActionLabel(a.action) }}
            <div class="hint">{{ a.basis }}</div>
            <div v-if="a.trace_id" class="hint trace">trace：{{ a.trace_id }}</div>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无审计记录" :image-size="40" />

        <!-- 流程指引 + 流水线推进（API-W-17/18/19）：每个状态都有明确的下一步 -->
        <el-divider content-position="left">流程指引</el-divider>
        <el-alert type="info" :closable="false" show-icon class="next-step">
          {{ NEXT_STEP[detail.info.status] || '暂无流程指引' }}
        </el-alert>
        <el-space class="mt12">
          <el-button v-if="canAggregate" type="primary" :loading="pipe.busy"
            @click="runPipeline('aggregate')">推进聚合</el-button>
          <el-button v-if="canInvestigate" type="primary" :loading="pipe.busy"
            @click="runPipeline('investigate')">启动调查</el-button>
          <el-button v-if="canVerify" type="primary" :loading="pipe.busy"
            @click="runPipeline('verify')">触发核验</el-button>
          <!-- API-W-23（SC-02）：调查完成后人工提请处置，高风险经 E-DISP-AUTH 门控建单转审批 -->
          <el-button v-if="canSubmitDisposition" type="primary" :loading="disp.submitting"
            @click="openDisposition">提请处置审批</el-button>
        </el-space>
      </div>
    </el-drawer>

    <!-- 处置提交弹窗（API-W-23，SC-02）：调查完成后提请处置，高风险经门控建审批单 -->
    <el-dialog v-model="dispVisible" :title="`提请处置 · ${disp.caseId}`" width="520px">
      <el-form label-width="80px">
        <el-form-item label="处置动作">
          <el-radio-group v-model="disp.action" class="review-radios">
            <el-radio value="freeze">
              账户冻结
              <span class="hint">冻结涉事账户资金流出（高风险默认）</span>
            </el-radio>
            <el-radio value="block">
              交易拦截
              <span class="hint">拦截后续交易指令</span>
            </el-radio>
            <el-radio value="reduce">
              额度下调
              <span class="hint">下调交易/取现额度（需填调整后额度）</span>
            </el-radio>
            <el-radio value="release">
              解除管控
              <span class="hint">解除既有管控措施</span>
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="disp.action === 'reduce'" label="调整后额度">
          <el-input-number v-model="disp.amount" :min="0" :max="10000000" :step="1000"
            controls-position="right" style="width:220px" placeholder="下调后的额度（元）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dispVisible = false">取消</el-button>
        <el-button type="primary" :loading="disp.submitting" @click="submitDispositionAction">提交审批</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowDown } from '@element-plus/icons-vue'
import { getCases, postAlert, getCase, getSignals, getEvidence, getGraph, getAuditTrail,
  aggregateCase, investigateCase, submitDisposition, verifyCase, getDispositions,
  getDemoSubjects, openEventStream } from '../api'
import { STAGES, STATUS_META, NEXT_STEP, SEVERITY_META, EDGE_LABELS, INFO_LABELS,
  DISP_STATUS_META, statusLabel, statusMeta, routeLabel, actionLabel, auditActionLabel,
  friendlyError } from '../labels'

const statuses = Object.keys(STATUS_META)
const rows = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const status = ref(null)
const riskMin = ref(null)
const loading = ref(false)

// 时效计算基准：每秒刷新一次，驱动倒计时/已耗时实时更新
const now = ref(Date.now())
let tick
const elapsedMs = (row) => now.value - new Date(row.created_at).getTime()
const remainMs = (row) => 30 * 60000 - elapsedMs(row)   // BA-BR-13：30 分钟审批时限
const fmt = (ms) => {
  const m = Math.floor(ms / 60000), s = Math.floor((ms % 60000) / 1000)
  return m >= 60 ? `${Math.floor(m / 60)}时${m % 60}分` : `${m}分${s}秒`
}
// 已归档案件显示结案总耗时（立案→归档，取 updated_at 近似归档时刻）
const fmtSpan = (row) => fmt(Math.max(0, new Date(row.updated_at || row.created_at) - new Date(row.created_at)))
const asList = (d) => (Array.isArray(d) ? d : d?.items || [])
// 图谱节点名单标记：后端对无名单账户返回字符串 'none'（truthy），必须显式排除
const flagged = (row) => !!row.risk_flag && row.risk_flag !== 'none'

async function load() {
  loading.value = true
  try {
    const { data } = await getCases({   // API-W-02 分页契约 {total, items}
      status: status.value || undefined,
      risk_min: riskMin.value ?? undefined,
      page: page.value, size: size.value,
    })
    rows.value = data.items
    total.value = data.total
  } catch (e) { ElMessage.error(friendlyError(e, '查询失败')) } finally { loading.value = false }
}
function reload() { page.value = 1; load() }

// ---- 新建演示案件（API-W-01，severity 三档三类路径） ----
async function triggerDemo(severity = 'high') {
  // API-W-21：取无未结案件的真实主体（随机字符串不在三源底表内，聚合走降级路径失真）。
  // severity 软过滤（BUG-01/R-46）：high→black/block 名单主体（必走调查→审批链），
  // low→none 干净主体（低分自动放行），否则"高风险"按钮与实际路径语义脱钩
  let subject
  try {
    const { data } = await getDemoSubjects(10, severity)
    subject = data.items?.[0]?.subject_ref
  } catch { subject = null }
  if (!subject) { ElMessage.warning('暂无可用演示主体：账户表为空或全部账户均有未结案件'); return }
  try {
    const { data } = await postAlert({ subject_ref: subject, source_type: 'demo_script', severity })
    ElMessage.success(`立案成功：${data.case_id}，${SEVERITY_META[severity].label}路径已启动`)
    load()
  } catch (e) { ElMessage.error(friendlyError(e, '立案失败')) }
}

// ---- 详情抽屉 ----
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = reactive({ caseId: '', info: {}, signals: [], evidence: [], graph: { nodes: [], links: [] }, dispositions: [], audit: [] })

// 基本信息按业务顺序展示已知字段，未知字段追加在后（不隐藏数据）
const infoKeys = computed(() => {
  const known = Object.keys(INFO_LABELS).filter((k) => k in (detail.info || {}))
  const rest = Object.keys(detail.info || {}).filter((k) => !(k in INFO_LABELS))
  return [...known, ...rest]
})
const fmtInfo = (k, v) => {
  if (k === 'context_json' && typeof v === 'object') return JSON.stringify(v)
  return v ?? '-'
}
// 闭环进度：状态 → 阶段下标；驳回/回滚属异常分支，步骤条标红
const stageIdx = computed(() => statusMeta(detail.info.status).stage)
const stageStatus = computed(() => ['REJECTED', 'ROLLBACK'].includes(detail.info.status) ? 'error' : 'process')

async function openDetail(row) {
  detail.caseId = row.case_id
  detailVisible.value = true
  detailLoading.value = true
  // 六源并发拉取，单接口失败不阻塞其余区块
  const [info, signals, evidence, graph, dispositions, audit] = await Promise.allSettled([
    getCase(row.case_id), getSignals(row.case_id), getEvidence(row.case_id),
    getGraph(row.case_id), getDispositions(row.case_id), getAuditTrail(row.case_id),
  ])
  detail.info = info.status === 'fulfilled' ? info.value.data : { 提示: '基本信息加载失败，请重试' }
  detail.signals = signals.status === 'fulfilled' ? asList(signals.value.data) : []
  detail.evidence = evidence.status === 'fulfilled' ? asList(evidence.value.data) : []
  detail.graph = graph.status === 'fulfilled' ? graph.value.data : { nodes: [], links: [] }
  detail.dispositions = dispositions.status === 'fulfilled' ? asList(dispositions.value.data) : []
  detail.audit = audit.status === 'fulfilled' ? asList(audit.value.data) : []
  detailLoading.value = false
}

// ---- 流水线推进（API-W-17/18/19）：按案件状态给出唯一合法的下一步动作 ----
const pipe = reactive({ busy: false })
const canAggregate = computed(() => ['REGISTERED', 'AGGREGATING'].includes(detail.info.status))
const canInvestigate = computed(() => detail.info.status === 'INVESTIGATING')
const canVerify = computed(() => detail.info.status === 'DISPOSED')
const canSubmitDisposition = computed(() => detail.info.status === 'PENDING_APPROVAL')

async function refreshDetail() {
  try {
    const { data } = await getCase(detail.caseId)
    detail.info = data
  } catch { /* 案件刷新失败不阻断，列表重载兜底 */ }
  load()
}

async function runPipeline(step) {
  pipe.busy = true
  try {
    let res
    if (step === 'aggregate') res = await aggregateCase(detail.caseId)
    else if (step === 'investigate') res = await investigateCase(detail.caseId)
    else {
      // 核验需 exec_id（API-W-22）：取最近一条执行成功的处置记录
      const { data } = await getDispositions(detail.caseId)
      const executed = (data.items || []).filter((d) => d.status === 'executed')
      const exec = executed[executed.length - 1]
      if (!exec) { ElMessage.warning('暂无已执行的处置记录，无法发起核验'); return }
      res = await verifyCase(detail.caseId, exec.exec_id)
    }
    const route = res.data?.route || res.data?.status || ''
    ElMessage.success(route ? `已推进：${routeLabel(route)}` : '流水线已推进')
    await refreshDetail()
  } catch (e) { ElMessage.error(friendlyError(e, '推进失败')) }
  finally { pipe.busy = false }
}

// ---- 处置提交（API-W-23，SC-02）：调查完成后人工提请，高风险经门控建单转审批 ----
const dispVisible = ref(false)
const disp = reactive({ caseId: '', action: 'freeze', amount: null, submitting: false })

function openDisposition() {
  Object.assign(disp, { caseId: detail.caseId, action: 'freeze', amount: null, submitting: false })
  dispVisible.value = true
}

async function submitDispositionAction() {
  disp.submitting = true
  try {
    const body = { action: disp.action }
    if (disp.action === 'reduce' && disp.amount != null) body.amount = disp.amount
    const { data } = await submitDisposition(disp.caseId, body)
    if (data.route === 'approval_required') {
      ElMessage.success(`已${data.duplicate ? '存在待决工单' : '创建审批工单'} ${data.approval_id}，等待审批官决策`)
    } else if (data.route === 'refused_mid_risk') {
      ElMessage.warning('中风险段禁止无凭证自动处置（BA-BR-01），请走人工复核通道')
    } else {
      ElMessage.success(`已推进：${routeLabel(data.route)}`)
    }
    dispVisible.value = false
    await refreshDetail()
  } catch (e) { ElMessage.error(friendlyError(e, '处置提交失败')) } finally { disp.submitting = false }
}

// ---- 实时更新：SSE 事件驱动列表刷新（防抖合并，避免事件风暴下频繁请求） ----
let es, debounce
function onEvent() {
  clearTimeout(debounce)
  debounce = setTimeout(() => {
    // BUG-03/R-46：详情抽屉打开时刷案件基本信息（状态/评分实时可见），关闭时刷列表
    if (detailVisible.value) refreshDetail()
    else load()
  }, 1200)
}

onMounted(() => {
  load(); tick = setInterval(() => { now.value = Date.now() }, 1000)
  es = openEventStream(onEvent)
})
onUnmounted(() => { clearInterval(tick); clearTimeout(debounce); es && es.close() })
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
.mt12 { margin-top: 12px; }
.stage-card { border: none; background: var(--el-color-primary-light-9); }
.stage-card :deep(.el-card__body) { padding: 14px 12px 8px; }
.stage-note { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.next-step :deep(.el-alert__content) { font-size: 13px; }
.trace { font-family: Consolas, monospace; }
.review-radios { display: flex; flex-direction: column; gap: 10px; }
.review-radios .hint { margin-left: 10px; }
</style>
