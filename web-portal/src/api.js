// 前端契约层：API-W-01~15/17~19/21~22 全量声明，函数名与 openapi.yaml operationId 逐一对齐。
// 用户可见文案统一经 labels.js 业务语汇层翻译，本文件不承载展示文案。
// 三端真实调用链（04 §10.1）：前端零内置静态数据，一律实时请求 web-api。
// 契约纪律：先改 docs/openapi/tradeguard-openapi.yaml → 后端实现 → 本文件跟进。
import axios from 'axios'
import { currentRole } from './role'

export const http = axios.create({ baseURL: '/api', timeout: 15000 })

// 统一操作者标识：X-Operator 携带当前角色名（中文经 encodeURIComponent 编码以满足 HTTP 头字符集，后端 unquote 还原）
http.interceptors.request.use((config) => {
  config.headers['X-Operator'] = encodeURIComponent(currentRole())
  return config
})

// 统一错误信封解包：后端 4xx/5xx 一律 {detail:{code,message}}
http.interceptors.response.use(
  (res) => res,
  (err) => Promise.reject(Object.assign(err, { code: err.response?.data?.detail?.code,
    message: err.response?.data?.detail?.message || err.message })),
)

// ---- observability ----
export const getHealth = () => http.get('/health')                                     // API-W-15

// ---- alerts / cases ----
export const postAlert = (body) => http.post('/alerts', body)                          // API-W-01（severity: low|medium|high，202）
// API-W-02：分页契约 {total, items}；入参 status/risk_min/page/size
export const getCases = ({ status, risk_min, page = 1, size = 20 } = {}) =>
  http.get('/cases', { params: { status, risk_min, page, size } })
export const getCase = (caseId) => http.get(`/cases/${caseId}`)                        // API-W-03
export const getSignals = (caseId) => http.get(`/cases/${caseId}/signals`)             // API-W-04
export const getGraph = (caseId, hops = 2) => http.get(`/cases/${caseId}/graph`, { params: { hops } }) // API-W-05
export const getEvidence = (caseId) => http.get(`/cases/${caseId}/evidence`)           // API-W-06
export const postReview = (caseId, body) => http.post(`/cases/${caseId}/review`, body) // API-W-07（SC-10）body:{conclusion:release|block|escalate, opinion≥5字符}

// ---- pipeline actions（04 §10.1 三端真实调用链）----
export const aggregateCase = (caseId) => http.post(`/cases/${caseId}/aggregate`)        // API-W-17
export const investigateCase = (caseId) => http.post(`/cases/${caseId}/investigate`)    // API-W-18
export const verifyCase = (caseId, execId) => http.post(`/cases/${caseId}/verify`, { exec_id: execId }) // API-W-19
export const getDispositions = (caseId) => http.get(`/cases/${caseId}/dispositions`)    // API-W-22（核验取 exec_id）
export const getDemoSubjects = (limit = 10) => http.get('/demo/subjects', { params: { limit } }) // API-W-21

// ---- config / observability ----
export const getThresholds = () => http.get('/config/thresholds')
export const getTraces = (caseId, limit = 50) =>
  http.get('/observability/traces', { params: { case_id: caseId, limit } })

// ---- approvals ----
export const getApprovals = (decision = 'pending') => http.get('/approvals', { params: { decision } }) // API-W-08
export const decideApproval = (approvalId, body) => http.post(`/approvals/${approvalId}/decide`, body) // API-W-09（SC-02/03）body:{decision:approve|reject, opinion≥5字符}

// ---- audit ----
export const getAuditTrail = (caseId) => http.get(`/audit/${caseId}`)                  // API-W-10（SC-08）

// ---- knowledge base ----
export const getKbApplications = (status = 'pending') => http.get('/kb/applications', { params: { status } }) // API-W-11
export const publishKbDocument = (docId, body) => http.post(`/kb/applications/${docId}/publish`, body)        // API-W-12
export const rejectKbDocument = (docId, body) => http.post(`/kb/applications/${docId}/reject`, body)          // API-W-13

// ---- events ----
// SSE 实时事件流（API-W-14）：后端进程内总线必达 + RocketMQ 尽力而为，前端协议不变
export function openEventStream(onMessage) {
  const es = new EventSource('/api/events/stream')
  es.onmessage = (e) => onMessage(JSON.parse(e.data))
  return es
}
