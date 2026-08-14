<template>
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">事件工作台</div>
        <div class="page-desc">事件全生命周期跟踪（API-W-02）；详情抽屉聚合信号 / 证据 / 图谱 / 审计五源（API-W-03~06/10）；人工复核走 API-W-07（SC-10）。</div>
      </div>
      <div class="page-actions">
        <el-button type="primary" @click="triggerDemo">触发演示事件（API-W-01）</el-button>
      </div>
    </div>
    <div class="page-body">
      <el-tabs v-model="tab" @tab-change="onTab">
        <el-tab-pane label="全部事件" name="all" />
        <el-tab-pane label="人工复核队列（MANUAL_REVIEW）" name="review" />
      </el-tabs>
      <el-row align="middle" class="toolbar">
        <el-space>
          <el-select v-if="tab === 'all'" v-model="status" placeholder="全部状态" clearable
            style="width:200px" @change="reload">
            <el-option v-for="s in statuses" :key="s" :value="s" :label="s" />
          </el-select>
          <span class="hint">风险分 ≥</span>
          <el-input-number v-model="riskMin" :min="0" :max="100" :step="10"
            controls-position="right" style="width:120px" @change="reload" />
        </el-space>
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
      <el-table-column prop="status" label="状态" width="160" />
      <el-table-column prop="current_agent" label="当前 Agent" width="110" />
      <el-table-column label="时效" width="150">
        <!-- BA-BR-13：PENDING_APPROVAL 显示 30 分钟审批倒计时，超时标红；其余状态显示已耗时 -->
        <template #default="{ row }">
          <el-tag v-if="row.status === 'PENDING_APPROVAL'" :type="remainMs(row) <= 0 ? 'danger' : 'warning'">
            {{ remainMs(row) <= 0 ? '超时 ' + fmt(-remainMs(row)) : '剩余 ' + fmt(remainMs(row)) }}
          </el-tag>
          <el-tag v-else type="info">已耗时 {{ fmt(elapsedMs(row)) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180" show-overflow-tooltip />
      <el-table-column label="操作" :width="tab === 'review' ? 150 : 80" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          <el-button v-if="tab === 'review'" link type="warning" @click="openReview(row)">复核</el-button>
        </template>
      </el-table-column>
    </el-table>
      <el-pagination class="tg-pager" background layout="total, sizes, prev, pager, next"
        :total="total" v-model:current-page="page" v-model:page-size="size"
        :page-sizes="[10, 20, 50]" @current-change="load" @size-change="reload" />
    </div>

    <!-- 详情抽屉：API-W-03/04/05/06/10 五源聚合展示 -->
    <el-drawer v-model="detailVisible" :title="`事件详情 · ${detail.caseId}`" size="55%">
      <div v-loading="detailLoading">
        <el-descriptions title="基本信息" :column="2" border size="small">
          <el-descriptions-item v-for="(v, k) in detail.info" :key="k" :label="k">{{ v }}</el-descriptions-item>
        </el-descriptions>

        <el-divider content-position="left">信号列表（API-W-04）</el-divider>
        <el-table :data="detail.signals" size="small" stripe max-height="220">
          <el-table-column prop="signal_id" label="信号编号" width="180" show-overflow-tooltip />
          <el-table-column prop="source_type" label="来源" width="120" />
          <el-table-column prop="severity" label="严重度" width="90" />
          <el-table-column prop="created_at" label="时间" show-overflow-tooltip />
        </el-table>
        <el-empty v-if="detail.signals.length === 0" description="暂无信号" :image-size="40" />

        <el-divider content-position="left">证据链（API-W-06）</el-divider>
        <el-timeline v-if="detail.evidence.length">
          <el-timeline-item v-for="(e, i) in detail.evidence" :key="i" :timestamp="e.created_at || e.timestamp">
            <b>{{ e.evidence_type || e.type || '证据' }}</b> · {{ e.summary || e.content || e.description || pretty(e) }}
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无证据" :image-size="40" />

        <el-divider content-position="left">关联图谱（API-W-05，start={{ detail.graph.start }}，hops={{ detail.graph.hops }}）</el-divider>
        <el-row :gutter="12">
          <el-col :span="10">
            <el-table :data="detail.graph.nodes" size="small" stripe max-height="200">
              <el-table-column prop="id" label="节点" show-overflow-tooltip />
              <el-table-column prop="type" label="类型" width="90" />
              <el-table-column label="风险" width="70">
                <template #default="{ row }">
                  <el-tag :type="row.risk_flag ? 'danger' : 'success'" size="small">
                    {{ row.risk_flag ? '风险' : '正常' }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-col>
          <el-col :span="14">
            <el-table :data="detail.graph.links" size="small" stripe max-height="200">
              <el-table-column prop="source" label="源节点" show-overflow-tooltip />
              <el-table-column prop="relation" label="关系" width="110" />
              <el-table-column prop="target" label="目标节点" show-overflow-tooltip />
            </el-table>
          </el-col>
        </el-row>

        <el-divider content-position="left">审计时间线（API-W-10）</el-divider>
        <el-timeline v-if="detail.audit.length">
          <el-timeline-item v-for="(a, i) in detail.audit" :key="i" :timestamp="a.created_at || a.timestamp">
            <b>{{ a.actor || a.operator || '-' }}</b> · {{ a.action }}
            <span class="hint">{{ a.detail || a.details || '' }}</span>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="暂无审计记录" :image-size="40" />
      </div>
    </el-drawer>

    <!-- 人工复核弹窗：API-W-07（SC-10，BA-BR-01 中风险分段） -->
    <el-dialog v-model="reviewVisible" :title="`人工复核 · ${review.caseId}`" width="480px">
      <el-form label-width="60px">
        <el-form-item label="结论">
          <el-radio-group v-model="review.conclusion">
            <el-radio value="release">排除归档</el-radio>
            <el-radio value="block">确认处置</el-radio>
            <el-radio value="escalate">升级审批</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="意见">
          <el-input v-model="review.opinion" type="textarea" :rows="3"
            placeholder="复核意见（不少于 5 个字符）" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button type="primary" :loading="review.submitting" @click="submitReview">提交复核</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getCases, postAlert, getCase, getSignals, getEvidence, getGraph, getAuditTrail, postReview } from '../api'

const statuses = ['REGISTERED', 'AGGREGATING', 'INVESTIGATING', 'PENDING_APPROVAL', 'APPROVED',
  'REJECTED', 'MANUAL_REVIEW', 'DISPOSING', 'DISPOSED', 'VERIFIED', 'ROLLBACK', 'ARCHIVED']
const tab = ref('all')
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
const asList = (d) => (Array.isArray(d) ? d : d?.items || [])
const pretty = (o) => Object.entries(o).filter(([k]) => !['created_at', 'timestamp'].includes(k))
  .map(([k, v]) => `${k}=${v}`).join(' ')

async function load() {
  loading.value = true
  try {
    // API-W-02 新分页契约：{total, items}
    const { data } = await getCases({
      status: tab.value === 'review' ? 'MANUAL_REVIEW' : (status.value || undefined),
      risk_min: riskMin.value ?? undefined,
      page: page.value, size: size.value,
    })
    rows.value = data.items
    total.value = data.total
  } catch (e) { ElMessage.error('查询失败：' + e.message) } finally { loading.value = false }
}
function reload() { page.value = 1; load() }
function onTab() { reload() }

async function triggerDemo() {
  const subject = 'acct-' + Math.random().toString(16).slice(2, 10)
  // severity 契约：字符串枚举 low|medium|high（响应 202）
  const { data } = await postAlert({ subject_ref: subject, source_type: 'demo_script', severity: 'high' })
  ElMessage.success(`立案成功：${data.case_id}`)
  load()
}

// ---- 详情抽屉 ----
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = reactive({ caseId: '', info: {}, signals: [], evidence: [], graph: { nodes: [], links: [] }, audit: [] })

async function openDetail(row) {
  detail.caseId = row.case_id
  detailVisible.value = true
  detailLoading.value = true
  // 五源并发拉取，单接口失败不阻塞其余区块
  const [info, signals, evidence, graph, audit] = await Promise.allSettled([
    getCase(row.case_id), getSignals(row.case_id), getEvidence(row.case_id),
    getGraph(row.case_id), getAuditTrail(row.case_id),
  ])
  detail.info = info.status === 'fulfilled' ? info.value.data : { 提示: '基本信息加载失败' }
  detail.signals = signals.status === 'fulfilled' ? asList(signals.value.data) : []
  detail.evidence = evidence.status === 'fulfilled' ? asList(evidence.value.data) : []
  detail.graph = graph.status === 'fulfilled' ? graph.value.data : { nodes: [], links: [] }
  detail.audit = audit.status === 'fulfilled' ? asList(audit.value.data) : []
  detailLoading.value = false
}

// ---- 人工复核 ----
const reviewVisible = ref(false)
const review = reactive({ caseId: '', conclusion: 'release', opinion: '', submitting: false })

function openReview(row) {
  Object.assign(review, { caseId: row.case_id, conclusion: 'release', opinion: '', submitting: false })
  reviewVisible.value = true
}

async function submitReview() {
  if (review.opinion.trim().length < 5) { ElMessage.warning('复核意见不少于 5 个字符'); return }
  review.submitting = true
  try {
    await postReview(review.caseId, { conclusion: review.conclusion, opinion: review.opinion.trim() })
    ElMessage.success('复核已提交')
    reviewVisible.value = false
    load()
  } catch (e) { ElMessage.error('复核失败：' + e.message) } finally { review.submitting = false }
}

onMounted(() => { load(); tick = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => clearInterval(tick))
</script>

<style scoped>
.toolbar { margin-bottom: 12px; }
</style>
