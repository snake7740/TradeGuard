// 前端契约层：API-W-01~15 全量声明，函数名与 openapi.yaml operationId 逐一对齐。
// 三端真实调用链（04 §10.1）：前端零内置静态数据，一律实时请求 web-api。
// 契约纪律：先改 docs/openapi/tradeguard-openapi.yaml → 后端实现 → 本文件跟进。
import axios from 'axios'

export const http = axios.create({ baseURL: '/api', timeout: 15000 })

// 统一错误信封解包：后端 4xx/5xx 一律 {detail:{code,message}}
http.interceptors.response.use(
  (res) => res,
  (err) => Promise.reject(Object.assign(err, { code: err.response?.data?.detail?.code,
    message: err.response?.data?.detail?.message || err.message })),
)

// ---- observability ----
export const getHealth = () => http.get('/health')                                     // API-W-15

// ---- alerts / cases ----
export const postAlert = (body) => http.post('/alerts', body)                          // API-W-01
export const getCases = (params) => http.get('/cases', { params })                     // API-W-02
export const getCase = (caseId) => http.get(`/cases/${caseId}`)                        // API-W-03
export const getSignals = (caseId) => http.get(`/cases/${caseId}/signals`)             // API-W-04
export const getGraph = (caseId, hops = 2) => http.get(`/cases/${caseId}/graph`, { params: { hops } }) // API-W-05
export const getEvidence = (caseId) => http.get(`/cases/${caseId}/evidence`)           // API-W-06
export const postReview = (caseId, body) => http.post(`/cases/${caseId}/review`, body) // API-W-07（SC-10）

// ---- approvals ----
export const getApprovals = (decision = 'pending') => http.get('/approvals', { params: { decision } }) // API-W-08
export const decideApproval = (approvalId, body) => http.post(`/approvals/${approvalId}/decide`, body) // API-W-09（SC-02/03）

// ---- audit ----
export const getAuditTrail = (caseId) => http.get(`/audit/${caseId}`)                  // API-W-10（SC-08）

// ---- knowledge base ----
export const getKbApplications = (status = 'pending') => http.get('/kb/applications', { params: { status } }) // API-W-11
export const publishKbDocument = (docId, body) => http.post(`/kb/applications/${docId}/publish`, body)        // API-W-12
export const rejectKbDocument = (docId, body) => http.post(`/kb/applications/${docId}/reject`, body)          // API-W-13

// ---- events ----
// SSE 实时事件流（API-W-14）：后端 Sprint 0 走进程内总线，Sprint 1 切 RocketMQ，前端协议不变
export function openEventStream(onMessage) {
  const es = new EventSource('/api/events/stream')
  es.onmessage = (e) => onMessage(JSON.parse(e.data))
  return es
}
