<template>
  <div class="page">
    <div class="page-head">
      <div>
        <div class="page-title">可观测面板</div>
        <div class="page-desc">AgentScope Studio（OTel tracing）入口与 SSE 实时事件流（API-W-14）；外链可达性自动探测，不可达时先检查对应容器是否已启动。</div>
      </div>
    </div>
    <el-row :gutter="12">
      <el-col :span="12">
        <el-card header="观测入口">
          <div v-for="l in extLinks" :key="l.href" class="ext-link">
            <div class="ext-row">
              <el-link :href="l.href" target="_blank" type="primary">{{ l.name }} →</el-link>
              <el-tag size="small" effect="plain" round
                :type="l.state === 'ok' ? 'success' : l.state === 'fail' ? 'danger' : 'info'">
                {{ l.state === 'ok' ? '可达' : l.state === 'fail' ? '不可达' : '探测中' }}
              </el-tag>
            </div>
            <div class="hint">{{ l.desc }} · {{ l.href }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card header="实时事件流（SSE）">
          <el-timeline>
            <el-timeline-item v-for="(m, i) in messages" :key="i" :timestamp="m.time">
              {{ m.tag }} · {{ m.case_id }}
            </el-timeline-item>
          </el-timeline>
          <el-empty v-if="messages.length === 0" description="等待 case-events 事件（US-E7-03 完整接入）" />
        </el-card>
      </el-col>
    </el-row>
    <!-- Trace 回放：GET /api/observability/traces，按 case_id 查询 span 列表 -->
    <el-card header="Trace 回放">
      <el-space class="toolbar">
        <el-input v-model="traceCaseId" placeholder="输入 case_id" clearable
          style="width:260px" @keyup.enter="loadTraces" />
        <el-input-number v-model="traceLimit" :min="1" :max="200" controls-position="right" style="width:110px" />
        <el-button type="primary" :loading="traceLoading" @click="loadTraces">查询 Trace</el-button>
      </el-space>
      <el-table :data="spans" v-loading="traceLoading" size="small" stripe max-height="320">
        <el-table-column prop="trace_id" label="trace_id" width="220" show-overflow-tooltip />
        <el-table-column prop="span_id" label="span_id" width="160" show-overflow-tooltip />
        <el-table-column label="span 名称" show-overflow-tooltip>
          <template #default="{ row }">{{ row.name || row.span_name || row.operation || '-' }}</template>
        </el-table-column>
        <el-table-column label="开始时间" width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.start_time || row.timestamp || row.created_at || '-' }}</template>
        </el-table-column>
        <el-table-column label="耗时(ms)" width="100">
          <template #default="{ row }">{{ row.duration_ms ?? row.duration ?? '-' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!traceLoading && traceQueried && spans.length === 0" description="该事件暂无 trace 记录" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { openEventStream, getTraces } from '../api'

// ---- 外链可达性探测：no-cors fetch，服务端有响应即视为可达（连接拒绝/超时=不可达） ----
const extLinks = reactive([
  { name: 'AgentScope Studio', desc: 'Agent 调用链回放（OTel）', href: 'http://localhost:3000', state: 'pending' },
  { name: 'Higress 网关控制台', desc: 'MCP/LLM 流量网关', href: 'http://localhost:8001', state: 'pending' },
  { name: 'Nacos 控制台', desc: '服务注册与动态配置', href: 'http://localhost:8848/nacos', state: 'pending' },
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
const traceCaseId = ref('')
const traceLimit = ref(50)
const traceLoading = ref(false)
const traceQueried = ref(false)
const spans = ref([])

async function loadTraces() {
  const id = traceCaseId.value.trim()
  if (!id) { ElMessage.warning('请输入 case_id'); return }
  traceLoading.value = true
  try {
    const d = (await getTraces(id, traceLimit.value)).data
    spans.value = Array.isArray(d) ? d : d?.items || d?.spans || []
    traceQueried.value = true
  } catch (e) { ElMessage.error('Trace 查询失败：' + e.message) } finally { traceLoading.value = false }
}
</script>

<style scoped>
.ext-link { padding: 8px 0; }
.ext-link + .ext-link { border-top: 1px solid var(--tg-border); }
.ext-row { display: flex; align-items: center; justify-content: space-between; }
.ext-link .hint { margin-top: 3px; }
.toolbar { margin-bottom: 12px; }
</style>
