<template>
  <div>
    <el-alert type="info" :closable="false" class="bar"
      title="AgentScope Studio（OTel tracing）入口与 SSE 实时事件流（API-W-14）。" />
    <el-row :gutter="12">
      <el-col :span="12">
        <el-card header="观测入口">
          <el-link href="http://localhost:3000" target="_blank" type="primary">AgentScope Studio →</el-link>
          <el-divider />
          <el-link href="http://localhost:8001" target="_blank" type="primary">Higress 网关控制台 →</el-link>
          <el-divider />
          <el-link href="http://localhost:8848/nacos" target="_blank" type="primary">Nacos 控制台 →</el-link>
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
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { openEventStream } from '../api'

const messages = ref([])
let es
onMounted(() => {
  es = openEventStream((d) => {
    messages.value.unshift({ ...d, time: new Date().toLocaleTimeString() })
    if (messages.value.length > 20) messages.value.pop()
  })
})
onUnmounted(() => es && es.close())
</script>

<style scoped>.bar { margin-bottom: 12px; }</style>
