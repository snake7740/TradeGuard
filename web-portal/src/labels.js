// ============================================================
// 业务语汇层：状态/事件/动作/错误码 → 风控业务语言的唯一翻译层。
// 原则：讲人话且专业——用金融风控领域通用术语（立案/信号聚合/调查研判/
// 审批单/处置/核验/结案归档），不出现内部追溯编号（API-W/BA-BR/SC/DA-T
// 只留在代码注释与文档里），也不做低幼化解释。
// 术语口径：risk_case 统一称「案件」（与立案/结案语义自洽）；approval_record
// 称「审批单」（金融审批语境，不用 ITSM 的「工单」）；kb_document 称「知识条目」；
// disposition_record 称「处置记录」（「凭证」系会计用语）；「事件」仅指领域事件。
// ============================================================

/** 五阶段风控闭环（01 §1 业务主链路），详情页进度条与状态归属共用 */
export const STAGES = ['信号聚合', '根因定位', '处置执行', '核验审计', '知识沉淀']

/** 案件 12 态：label 中文标签 / tag 色彩 / stage 闭环阶段归属 / desc 业务含义 */
export const STATUS_META = {
  REGISTERED:       { label: '已立案', tag: 'info', stage: 0, desc: '案件已登记立案，等待信号聚合承接' },
  AGGREGATING:      { label: '信号聚合中', tag: 'info', stage: 0, desc: '正在汇聚多源风险信号并计算风险评分' },
  INVESTIGATING:    { label: '调查取证中', tag: 'info', stage: 1, desc: '正在研判欺诈手法、固化证据链' },
  PENDING_APPROVAL: { label: '待审批', tag: 'warning', stage: 2, desc: '高风险处置等待审批官决策（30 分钟时限）' },
  APPROVED:         { label: '已批准', tag: 'success', stage: 2, desc: '审批通过，系统正在自动执行处置' },
  REJECTED:         { label: '已驳回', tag: 'danger', stage: 2, desc: '审批驳回，自动通道已禁用，转人工复核' },
  DISPOSING:        { label: '处置执行中', tag: 'info', stage: 2, desc: '正在执行冻结/拦截/降额/放行等处置动作' },
  DISPOSED:         { label: '已处置', tag: 'success', stage: 2, desc: '处置动作已执行，等待结果核验' },
  MANUAL_REVIEW:    { label: '人工复核中', tag: 'warning', stage: 3, desc: '等待人工复核给出最终结论' },
  VERIFIED:         { label: '已核验', tag: 'success', stage: 3, desc: '执行结果核验一致，等待结案归档' },
  ROLLBACK:         { label: '处置撤销中', tag: 'danger', stage: 3, desc: '核验不一致，正在执行撤销（反向处置），完成后转人工复核' },
  ARCHIVED:         { label: '已归档', tag: 'success', stage: 4, desc: '案件结案归档，复盘知识进入沉淀流程' },
}
export const statusLabel = (s) => STATUS_META[s]?.label || s
export const statusMeta = (s) => STATUS_META[s] || { label: s, tag: 'info', stage: 0, desc: '' }

/** 状态 → 下一步指引（详情抽屉"流程指引"，覆盖全部 12 态） */
export const NEXT_STEP = {
  REGISTERED: '案件已立案，系统将自动承接信号聚合；如需人工推动，可点击下方"推进聚合"。',
  AGGREGATING: '信号聚合完成后按风险分级裁决：低风险走自动放行通道，其余转入调查取证。',
  INVESTIGATING: '点击"启动调查"：经关联图谱与外部数据源固化证据链，调查完成后生成处置建议。',
  PENDING_APPROVAL: '等待审批官在「审批门户」决策——批准后系统自动执行处置，驳回则转人工复核。',
  APPROVED: '审批已批准，系统正在自动执行处置，无需人工干预。',
  REJECTED: '审批已驳回且自动通道禁用。案件转入人工复核，请在复核队列中给出结论。',
  DISPOSING: '处置动作执行中，请稍候；执行结果将生成处置记录。',
  DISPOSED: '处置已执行。点击"触发核验"比对执行结果与处置意图，确认无误后归档。',
  MANUAL_REVIEW: '在列表点击"复核"给出结论：排除归档 / 确认处置 / 升级审批。',
  VERIFIED: '核验通过，案件将结案归档，复盘知识转入知识库入库申请。',
  ROLLBACK: '撤销处置已执行（原处置被纠正），等待人工复核确认最终定性。',
  ARCHIVED: '案件已归档，流程结束。可通过「审计查询」回放本案全部操作留痕。',
}

/** 领域事件 21 名（03 §9.2 事件目录）：实时事件流与审计时间线展示用 */
export const EVENT_LABELS = {
  CaseRegistered: '案件立案', AggregationStarted: '聚合启动', SignalsAggregated: '信号聚合完成',
  NoiseDismissed: '降噪放行', InvestigationRequested: '发起调查', InvestigationCompleted: '调查完成',
  ApprovalApproved: '审批批准', ApprovalRejected: '审批驳回',
  DispositionSubmitted: '处置提交', DispositionExecuted: '处置执行完成', DispositionFailed: '处置失败转人工',
  RollbackToReview: '驳回退回复核', VerificationPassed: '核验通过', VerificationFailed: '核验不一致',
  RollbackExecuted: '处置撤销完成', RollbackEscalated: '撤销受阻升级转人工',
  ReviewConfirmed: '复核确认欺诈', ReviewDismissed: '复核排除欺诈', CaseArchived: '结案归档',
  ApprovalEscalated: '审批超时升级', VerificationOverdue: '核验超时提醒',
}
export const eventLabel = (e) => EVENT_LABELS[e] || e

/** 处置动作（DA-T-06 action 枚举） */
export const ACTION_LABELS = { freeze: '账户冻结', block: '交易拦截', reduce: '额度下调', release: '解除管控' }
export const actionLabel = (a) => ACTION_LABELS[a] || a

/** 裁决/执行路由 → 业务结论 */
export const ROUTE_LABELS = {
  noise: '降噪放行（低风险误报归档）', auto_release: '低风险自动放行',
  investigate: '转入调查取证', all_fail: '数据源全部失败，转人工处理',
  executed: '处置已执行', approval_required: '转入审批流程',
  refused_mid_risk: '中风险不允许该自动处置', idempotent_hit: '重复提交，已按首次执行结果返回',
  failed_manual: '处置失败，转人工复核', passed: '核验一致，案件归档', rollback: '核验不一致，已撤销处置',
}
export const routeLabel = (r) => ROUTE_LABELS[r] || r

/** 审批决策 / 知识库状态 */
export const DECISION_META = {
  pending: { label: '待决策', tag: 'warning' }, approved: { label: '已批准', tag: 'success' },
  rejected: { label: '已驳回', tag: 'danger' },
}
export const KB_STATUS_META = {
  pending: { label: '待审核', tag: 'warning' }, published: { label: '已发布', tag: 'success' },
  rejected: { label: '已驳回', tag: 'danger' },
}

/** 严重等级（API-W-01 severity 枚举） */
export const SEVERITY_META = {
  low: { label: '低风险', desc: '聚合后走自动放行通道，全自动闭环' },
  medium: { label: '中风险', desc: '转调查取证，处置建议需审批决策' },
  high: { label: '高风险', desc: '调查→审批→处置→核验全链路，含人工审批环节' },
}

/** 图谱关系（03-umodel-fallback 四类边，03 §3 语义模型同源） */
export const EDGE_LABELS = {
  SAME_PAYEE: '同收款方', SAME_DEVICE: '同设备', SAME_IPSEG: '同 IP 网段', SAME_CONTACT: '同联系方式',
}

/** 动态阈值键含义（SC-06，与 db/init/01-schema.sql 种子、scripts/nacos_register.py THRESHOLDS 三处同源） */
export const THRESHOLD_LABELS = {
  'br-01-auto-block-score': '高风险线（自动处置评分线）',
  'br-01-mid-review-score': '中风险线下限（以下转人工复核分段）',
  'br-01-auto-amount-limit': '自动处置金额上限（元）',
  'br-05-window-days': '高频异常观察窗（天）',
  'br-05-case-count': '窗口内立案次数阈值',
  'br-06-fraud-link-bonus': '关联网络命中黑名单加分',
  'br-08-verification-timeout-min': '核验时限（分钟）',
  'br-13-approval-timeout-min': '审批超时升级阈值（分钟）',
  'br-14-velocity-1h-count': '1 小时交易频次阈值',
  'br-14-velocity-24h-count': '24 小时交易频次阈值',
  'br-14-velocity-bonus': '频次命中加分',
}

/** 处置记录执行状态（DA-T-06 status 枚举） */
export const DISP_STATUS_META = {
  submitted: { label: '已提交', tag: 'info' }, executed: { label: '已执行', tag: 'success' },
  failed: { label: '执行失败', tag: 'danger' }, rolled_back: { label: '已回滚', tag: 'warning' },
}

/** 审计动作 → 业务语言（case.transition.* 前缀按事件名二次翻译） */
const AUDIT_ACTION_LABELS = {
  'case.register': '案件立案', 'disposition.submit': '处置提交', 'disposition.failed': '处置失败',
  'disposition.refused_auth': '处置拒绝（审批未通过）', 'disposition.refused_evidence': '处置拒绝（证据链不完整）',
  'disposition.refused_scope': '处置拒绝（超出自动处置权限）',
  'approval.create': '创建审批单', 'approval.decide': '审批决策', 'approval.escalate': '审批超时升级',
  'signals.record': '风险信号落库', 'signals.all_fail': '数据源全部失败', 'signals.black_flag': '黑名单命中加分',
  'investigation.complete': '调查完成', 'kb.apply': '知识入库申请', 'kb.publish': '知识发布',
  'verification.overdue': '核验超时提醒', 'api.request': '接口操作留痕',
}
export function auditActionLabel(action) {
  if (action?.startsWith('case.transition.')) {
    return '状态流转 · ' + eventLabel(action.slice('case.transition.'.length))
  }
  return AUDIT_ACTION_LABELS[action] || action
}

/** 详情抽屉"基本信息"字段标签（未知字段按原名展示，不隐藏数据） */
export const INFO_LABELS = {
  case_id: '案件编号', subject_ref: '涉事主体', risk_score: '风险评分', status: '当前状态',
  severity: '严重等级', source_type: '来源渠道', current_agent: '当前处理方',
  created_at: '立案时间', updated_at: '最后更新', trace_id: '追踪标识', version: '版本号',
  context_json: '流程上下文',
}

/** 错误码 → 用户级解释（code 仍随附展示，便于运维定位；08 §6 错误码表同源） */
const ERROR_HINTS = {
  'E-NOT-FOUND': '未找到对应记录，请核对编号或刷新列表',
  'E-BAD-TRANSITION': '当前状态不允许该操作（案件可能已被其他环节推进），请刷新查看最新进展',
  'E-ALREADY-DECIDED': '该审批单已有决策结论，请刷新队列查看最新状态',
  'E-HUMAN-ONLY': '该操作属于人工决策环节，当前操作方无权执行',
  'E-ACTOR-REQUIRED': '缺少操作者身份，操作被拒绝',
  'E-HUMAN-ONLY-DB': '该状态变更须由人工确认',
  'E-DISP-AUTH': '高风险处置须经审批通过后执行，请先完成审批流程',
  'E-DISP-SCOPE': '中风险案件不允许该自动处置动作',
  'E-EVIDENCE-MISSING': '证据链不完整，处置被拒绝（先完成调查取证）',
  'E-IDEMPOTENT-CONFLICT': '检测到重复提交，已按首次执行结果处理',
  'E-REASON-REQUIRED': '外部数据查询须填写查询事由',
  'E-KB-HUMAN-GATE': '知识发布仅限人工操作',
}

/** 统一错误文案：优先给业务解释，括注错误码；无映射时回退原始 message */
export function friendlyError(e, fallbackVerb = '操作未成功') {
  const hint = ERROR_HINTS[e?.code]
  if (hint) return e.code ? `${hint}（${e.code}）` : hint
  return `${fallbackVerb}：${e?.message || '未知错误'}`
}
