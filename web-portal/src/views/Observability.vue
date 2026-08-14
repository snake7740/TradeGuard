<template>
  <!-- 可观测面板（API-W-14/20 消费方，运维与开发排障视图，US-E7-03/04） -->
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">可观测面板</div>
        <div class="page-desc">系统运行状态观测：实时事件流展示案件在风控闭环中的流转，Trace 回放展示各技能环节的执行耗时，外部组件入口供运维排障。本页面面向运维与开发人员。</div>
      </div>
    </div>
    <el-row :gutter="12">
      <el-col :span="12">
        <el-card header="外部组件入口">
          <div v-for="l in extLinks" :key="l.href" class="ext-link">
            <div class="ext-row">
              <el-link :href="l.href" target="_blank" type="primary">{{ l.name }} →</el-link>
              <el-tag size="small" effect="plain" round
                :type="l.state === 'ok' ? 'success' : l.state === 'fail' ? 'danger' : 'info'">
                {{ l.state === 'ok' ? '可达' : l.state === 'fail' ? '不可达' : '探测中' }}
              </el-tag>
            </div>
            <div class="hint">{{ l.desc }} · {{ l.href }}<span v-if="l.state === 'fail'">（不可达时请确认对应容器已启动：docker compose ps）</span></div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card header="实时事件流">
          <el-timeline v-if="messages.length">
            <el-timeline-item v-for="(m, i) in messages" :key="i" :timestamp="m.time">
              {{ eventLabel(m.event) }}
              <span class="hint">· {{ m.case_id }}</span>
            </el-timeline-item>
          </el-timeline>
          <el-empty v-else description="暂无事件。新建演示案件或推进案件后，领域事件将实时出现在这里" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>
    <!-- Trace 回放：GET /api/observability/traces，按案件编号查询技能执行 span -->
    <el-card header="技能执行 Trace 回放">
      <el-space class="toolbar">
        <el-input v-model="traceCaseId" placeholder="输入案件编号" clearable
          style="width:260px" @keyup.enter="loadTraces" />
        <el-input-number v-model="traceLimit" :min="1" :max="200" controls-position="right" style="width:110px" />
        <el-button type="primary" :loading="traceLoading" @click="loadTraces">查询 Trace</el-button>
        <span class="hint">展示该案件各环节（聚合/调查/处置/核验）的执行耗时与调用链</span>
      </el-space>
      <el-table :data="spans" v-loading="traceLoading" size="small" stripe max-height="320"
        empty-text="暂无 Trace 数据">
        <el-table-column prop="trace_id" label="追踪标识" width="220" show-overflow-tooltip />
        <el-table-column prop="span_id" label="片段标识" width="160" show-overflow-tooltip />
        <el-table-column label="环节名称" show-overflow-tooltip>
          <template #default="{ row }">{{ row.name || row.span_name || row.operation || '-' }}</template>
        </el-table-column>
        <el-table-column label="开始时间" width="200" show-overflow-tooltip>
          <!-- 留痕 span 用 start_ts（epoch 秒，core/tracing.py），换算本地时间展示 -->
          <template #default="{ row }">{{ fmtTs(row.start_ts) }}</template>
        </el-table-column>
        <el-table-column label="耗时(ms)" width="100">
          <template #default="{ row }">{{ row.duration_ms ?? row.duration ?? '-' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!traceLoading && traceQueried && spans.length === 0" description="该案件暂无 Trace 记录。技能环节执行后将自动埋点留痕" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { openEventStream, getTraces } from '../api'
import { eventLabel, friendlyError } from '../labels'

// ---- 外链可达性探测：no-cors fetch，服务端有响应即视为可达（连接拒绝/超时=不可达） ----
// 外链文案如实标注数据连通状态：Studio/Nacos 已真实打通；Higress 已承载门户业务流量（04 §5）
const extLinks = reactive([
  { name: 'AgentScope Studio', desc: '技能 span 经 OTLP 直推上报，按案件分组可视化回放调用链（已打通）', href: 'http://localhost:3000', state: 'pending' },
  { name: 'Higress 网关入口', desc: '门户 /api 业务流量经此入口真实转发至 web-api（已承载，04 §5）', href: 'http://localhost:8180/api/health', state: 'pending' },
  { name: 'Higress 网关控制台', desc: '控制台 UI（已部署；首次初始化未完成，路由经文件仓下发，见 04 §5）', href: 'http://localhost:8001', state: 'pending' },
  { name: 'Nacos 控制台', desc: '服务注册 + 动态阈值配置，web-api 5s 快照热加载（已打通）', href: 'http://localhost:8848/nacos', state: 'pending' },
])
async function probe(l) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 4000)
  try { await fetch(l.href, { mode: 'no-cors', signal: ctrl.signal }); l.state = 'ok' }
  catch { l.state = 'fail' } finally { clearTimeout(timer) }
}

const messages = ref([])
let es
onMounted(() => {
  extLinks.forEach(probe)
  es = openEventStream((d) => {
    messages.value.unshift({ ...d, time: new Date().toLocaleTimeString() })
    if (messages.value.length > 20) messages.value.pop()
  })
})
onUnmounted(() => es && es.close())

// ---- Trace 回放 ----
const fmtTs = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : '-')
const traceCaseId = ref('')
const traceLimit = ref(50)
const traceLoading = ref(false)
const traceQueried = ref(false)
const spans = ref([])

async function loadTraces() {
  const id = traceCaseId.value.trim()
  if (!id) { ElMessage.warning('请输入案件编号'); return }
  traceLoading.value = true
  try {
    const d = (await getTraces(id, traceLimit.value)).data
    spans.value = Array.isArray(d) ? d : d?.items || d?.spans || []
    traceQueried.value = true
  } catch (e) { ElMessage.error(friendlyError(e, 'Trace 查询失败')) } finally { traceLoading.value = false }
}
</script>

<style scoped>
.ext-link { padding: 8px 0; }
.ext-link + .ext-link { border-top: 1px solid var(--tg-border); }
.ext-row { display: flex; align-items: center; justify-content: space-between; }
.ext-link .hint { margin-top: 3px; }
.toolbar { margin-bottom: 12px; }
</style>
