"""AA-AG-01 调查规划-反思内核（Manager 规划循环，R-47）

定位（02 §3.3 人机边界不变：LLM 只规划与复盘，不决策状态转移）：
  plan    —— 依据案件上下文（信号/图谱边/KB 提示/同案记忆）生成调查计划：
              假设优先序 + 外部源选择性查询（与 AG-02 全量三源互补：调查阶段
              按假设深查，豁免源必须给理由并留痕，审计可回放"为什么没查 X"）。
  execute —— 按计划调用外部源（AA-MCP-02，query_reason 逐项携带 BA-BR-10），
              单源失败降级记录不阻断。
  reflect —— 对比计划与执行结果（假设验证否/证据缺口），结论落证据链与
              agent_memory，形成「规划→执行→反思」可回放闭环。

降级保底：LLM 不可用/失败/输出非法 → 确定性 rule 版（信号特征驱动的
源选择），系统行为与既有基线一致（拿掉 LLM 仍是完整工作流，只是少了
选择性）；裁决权始终在状态机与人工门控。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tradeguard.planner")

SOURCE_WHITELIST = ("credit", "sentiment", "complaint")
VELOCITY_1H_COUNT = 10  # 与 investigation.VELOCITY_1H_COUNT 同源（BA-BR-14）

# B3 并行假设编排（BA-BR-18，docs/14 US-E9）：假设→首选深查源映射，
# 用于假设覆盖核算——未覆盖假设必须留痕「为什么没查 X」
HYPOTHESIS_SOURCES = {
    "跑分": ("credit", "complaint"),
    "盗卡": ("credit", "complaint"),
    "团伙盗刷": ("complaint", "sentiment"),
}


@dataclass
class SourceQuery:
    """计划中的单条外部源查询（source 白名单三源）"""

    source: str
    reason: str
    priority: int = 1


@dataclass
class InvestigationPlan:
    """调查计划（LLM 版或规则版，结构同构）"""

    source: str  # "llm" | "rule"
    hypotheses: list[dict[str, Any]] = field(default_factory=list)  # [{pattern,rationale,priority}]
    queries: list[SourceQuery] = field(default_factory=list)
    kb_queries: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)  # 豁免留痕 [{source,reason}]
    rationale: str = ""


def hypothesis_skipped(plan: InvestigationPlan,
                       findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """B3 假设覆盖核算（BA-BR-18）：未被任何成功深查源覆盖的假设，留痕
    「为什么没查 X」；已覆盖/待定假设不产生豁免项。"""
    executed = {f["source"] for f in findings if f.get("ok")}
    out: list[dict[str, Any]] = []
    for h in plan.hypotheses:
        pattern = str(h.get("pattern", "待定"))
        if pattern == "待定":
            continue
        wanted = HYPOTHESIS_SOURCES.get(pattern)
        if wanted is None:  # 未知手法无首选源映射：以全部计划源成功视为覆盖
            continue
        covered = [s for s in wanted if s in executed]
        if not covered:
            out.append({
                "hypothesis": pattern,
                "reason": f"假设[{pattern}]首选深查源 {'/'.join(wanted)} 均未成功"
                          f"执行（降级/异常/未入计划），未深查留痕（BA-BR-18）",
            })
    return out


@dataclass
class Reflection:
    """执行后反思（对比计划 vs 结果 vs 假设结论）"""

    source: str  # "llm" | "rule" | "skipped"
    verdict: str  # "sufficient" | "gaps"
    gaps: list[str] = field(default_factory=list)
    summary: str = ""


def _signal_features(signals: list[dict[str, Any]], edge_types: set[str]) -> dict[str, Any]:
    """提取规划用信号特征摘要（确定性，LLM/rule 两版共用输入）"""
    feats: dict[str, Any] = {"velocity_high": False, "large_amount": False,
                   "same_device": "SAME_DEVICE" in edge_types, "types": sorted({
                       s.get("type", "") for s in signals if s.get("type")})}
    for s in signals:
        vj = s.get("velocity_json")
        if isinstance(vj, str):
            vj = json.loads(vj or "{}")
        v1 = (vj or {}).get("velocity_1h") or {}
        if v1.get("count", 0) >= VELOCITY_1H_COUNT:
            feats["velocity_high"] = True
        if s.get("type") == "large_amount_burst":
            feats["large_amount"] = True
    return feats


# ---------------------------------------------------------------- 规则 baseline

def rule_plan(signals: list[dict[str, Any]], edge_types: set[str]) -> InvestigationPlan:
    """确定性规划（LLM 不可用时的下限，也是 LLM 输出的校验回退）。

    选择性策略（保守：仅在有明确特征时豁免无关源，特征组合不识别则全查）：
      高频小额（跑分特征）   → credit（信用/流水核验）+ complaint（否认交易线索）
      同设备关联（团伙特征） → complaint + sentiment（团伙舆情）
      单卡大额（盗卡特征）   → credit + complaint
      无特征                 → 三源全查（不豁免任何源）
    """
    f = _signal_features(signals, edge_types)
    queries: list[SourceQuery] = []
    skipped: list[dict[str, Any]] = []
    hypotheses: list[dict[str, Any]] = []

    if f["velocity_high"]:
        hypotheses.append({"pattern": "跑分", "priority": 1,
                           "rationale": "近1h高频小额信号（BA-BR-14 特征）"})
    if f["large_amount"]:
        hypotheses.append({"pattern": "盗卡", "priority": 1,
                           "rationale": "单卡突发大额信号（BA-BR-01 同源线）"})
    if f["same_device"]:
        hypotheses.append({"pattern": "团伙盗刷", "priority": 1,
                           "rationale": "图谱 SAME_DEVICE 边（多账户同设备）"})

    wanted: set[str] = set()
    if f["velocity_high"]:
        wanted |= {"credit", "complaint"}
    if f["same_device"]:
        wanted |= {"complaint", "sentiment"}
    if f["large_amount"]:
        wanted |= {"credit", "complaint"}
    if not wanted:
        wanted = set(SOURCE_WHITELIST)  # 无特征 → 保守全查

    for src in SOURCE_WHITELIST:
        if src in wanted:
            queries.append(SourceQuery(
                source=src, priority=1,
                reason=f"AA-AG-01 规划：特征 {[k for k, v in f.items() if v is True] or ['无（保守全查）']}"
                       f" 指向需核验 {src} 源（调查阶段选择性深查）"))
        else:
            skipped.append({"source": src,
                            "reason": f"当前信号特征与 {src} 源无因果，计划豁免"
                                      f"（特征：{[k for k, v in f.items() if v is True]}）"})
    top = hypotheses[0]["pattern"] if hypotheses else "待定"
    return InvestigationPlan(
        source="rule",
        hypotheses=hypotheses or [{"pattern": "待定", "priority": 1,
                                   "rationale": "无显著信号特征，定性交人工"}],
        queries=queries,
        kb_queries=[f"{top} 手法特征", f"{top} 处置先例"],
        skipped=skipped,
        rationale="规则规划：信号特征驱动源选择，无特征保守全查",
    )


# ---------------------------------------------------------------- LLM 版规划

_PLAN_PROMPT = """你是交易风控调查规划员（AG-01）。根据案件信号特征规划调查动作。
可选外部源（白名单）：credit=征信/流水核验、sentiment=舆情、complaint=客诉否认线索。
约束：每个非白名单 source 丢弃；至少规划一个源；豁免任何源必须给理由（审计要回放）。

信号特征：{features}
图谱边类型：{edges}
知识库提示：{kb_hints}
先验假设（规则初判）：{prior}

只返回 JSON：
{{"hypotheses":[{{"pattern":"手法","priority":1,"rationale":"依据"}}],
  "queries":[{{"source":"credit|sentiment|complaint","reason":"查询事由","priority":1}}],
  "kb_queries":["检索词"],
  "skipped":[{{"source":"源","reason":"豁免理由"}}],
  "rationale":"整体规划理由"}}"""


def _parse_plan_json(raw: str, fallback_feats: dict[str, Any]) -> InvestigationPlan:
    """LLM 输出解析 + 白名单校验；结构非法/空查询 → 抛 ValueError 由上层降级"""
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0) if m else raw)

    queries = []
    for q in data.get("queries", []):
        src = str(q.get("source", ""))
        if src in SOURCE_WHITELIST:
            queries.append(SourceQuery(
                source=src, priority=int(q.get("priority", 1)),
                reason=str(q.get("reason", "AA-AG-01 LLM 规划查询"))[:200]))
    if not queries:  # 空计划不合规：保守回退全查（不豁免任何源）
        queries = [SourceQuery(s, priority=1, reason="AA-AG-01 LLM 计划为空，保守全查")
                   for s in SOURCE_WHITELIST]
    skipped = [{"source": str(s.get("source", "")), "reason": str(s.get("reason", ""))[:200]}
               for s in data.get("skipped", [])
               if str(s.get("source", "")) in SOURCE_WHITELIST]
    hypotheses = [{"pattern": str(h.get("pattern", "待定")),
                   "priority": int(h.get("priority", 1)),
                   "rationale": str(h.get("rationale", ""))[:300]}
                  for h in data.get("hypotheses", [])][:5]
    return InvestigationPlan(
        source="llm", hypotheses=hypotheses, queries=queries,
        kb_queries=[str(k)[:100] for k in data.get("kb_queries", [])][:5],
        skipped=skipped,
        rationale=str(data.get("rationale", ""))[:500] or "LLM 规划（无理由字段）",
    )


async def make_plan(signals: list[dict[str, Any]], edge_types: set[str],
                    kb_hints: str = "", client=None) -> InvestigationPlan:
    """规划入口：LLM 优先（可用时），任何失败降级规则版（行为下限不变）"""
    if client is None:
        from app.core.llm_adapters import LlmClient
        client = LlmClient()
    if not getattr(client, "available", False):
        return rule_plan(signals, edge_types)
    try:
        prior = rule_plan(signals, edge_types)
        raw = await client.chat(
            [{"role": "system", "content": "你是严谨的风控调查规划员，只输出 JSON。"},
             {"role": "user", "content": _PLAN_PROMPT.format(
                 features=json.dumps(_signal_features(signals, edge_types), ensure_ascii=False),
                 edges=json.dumps(sorted(edge_types), ensure_ascii=False),
                 kb_hints=kb_hints or "无",
                 prior=json.dumps(prior.hypotheses, ensure_ascii=False))}],
            temperature=0.1,
        )
        return _parse_plan_json(raw, _signal_features(signals, edge_types))
    except Exception:  # noqa: BLE001 —— LLM 规划失败降级规则，调查链路不受阻
        logger.warning("LLM 调查规划失败，降级规则规划")
        return rule_plan(signals, edge_types)


# ---------------------------------------------------------------- 计划执行

async def execute_plan(plan: InvestigationPlan, external, subject: str) -> list[dict[str, Any]]:
    """按计划并行执行外部源查询（B3 asyncio.gather，US-E9）；单源失败记录
    degraded 不阻断其余分支（与 AG-02 降级同构，14 §1.4）。结果按 priority
    序稳定输出（并行不改结果顺序，反思/证据链可回放）。

    external 为 None（旧装配/测试未注入）时返回显式未执行标记，反思按 gaps 记录。
    """
    if external is None:
        return [{"source": q.source, "ok": False, "degraded": True,
                 "summary": "external 通道未装配，计划查询未执行"} for q in plan.queries]
    method = {"credit": external.query_credit_report,
              "sentiment": external.query_sentiment,
              "complaint": external.query_complaints}

    async def _one(q: SourceQuery) -> dict[str, Any]:
        try:
            payload = await method[q.source](subject, q.reason)
            if isinstance(payload, str):  # mock 通道偶发 json 字符串
                payload = json.loads(payload)
            degraded = bool(payload.get("degraded")) or bool(payload.get("code"))
            return {"source": q.source, "ok": not degraded,
                    "degraded": degraded,
                    "summary": json.dumps(payload, ensure_ascii=False)[:400]}
        except Exception:  # noqa: BLE001 —— 单源失败不阻断其余并行分支
            return {"source": q.source, "ok": False, "degraded": True,
                    "summary": f"{q.source} 源查询异常（降级记录）"}

    ordered = sorted(plan.queries, key=lambda x: x.priority)
    return list(await asyncio.gather(*(_one(q) for q in ordered)))


# ---------------------------------------------------------------- 反思（规则/LLM）

def rule_reflect(plan: InvestigationPlan, findings: list[dict[str, Any]],
                 hypothesis_pattern: str) -> Reflection:
    """确定性反思下限：计划项全部成功且假设非待定 → sufficient；否则列缺口"""
    gaps: list[str] = []
    degraded = [f["source"] for f in findings if f.get("degraded")]
    if degraded:
        gaps.append(f"计划源降级：{','.join(degraded)}")
    executed = {f["source"] for f in findings if not f.get("degraded")}
    missing = [q.source for q in plan.queries if q.source not in executed]
    if missing:
        gaps.append(f"计划源未覆盖：{','.join(missing)}")
    if hypothesis_pattern == "待定":
        gaps.append("假设未定性（交人工复核，02 §3.3）")
    return Reflection(
        source="rule",
        verdict="sufficient" if not gaps else "gaps",
        gaps=gaps,
        summary=f"规则反思：计划 {len(plan.queries)} 项，执行成功 {len(executed)} 项，"
                f"假设[{hypothesis_pattern}]",
    )


_REFLECT_PROMPT = """你是风控调查复盘员（AG-01）。对比调查计划与实际执行结果，判断证据是否充分。
计划假设：{hypotheses}
计划查询源：{planned}
实际执行：{findings}
最终假设结论：{conclusion}

只返回 JSON：{{"verdict":"sufficient|gaps","gaps":["缺口描述"],"summary":"复盘一句话"}}"""


async def reflect(plan: InvestigationPlan, findings: list[dict[str, Any]],
                  hypothesis_pattern: str, client=None) -> Reflection:
    """反思入口：LLM 对比计划/执行/结论；不可用或失败降级规则版"""
    base = rule_reflect(plan, findings, hypothesis_pattern)
    if client is None:
        from app.core.llm_adapters import LlmClient
        client = LlmClient()
    if not getattr(client, "available", False):
        return base
    try:
        raw = await client.chat(
            [{"role": "system", "content": "你是严谨的调查复盘员，只输出 JSON。"},
             {"role": "user", "content": _REFLECT_PROMPT.format(
                 hypotheses=json.dumps(plan.hypotheses, ensure_ascii=False),
                 planned=json.dumps([q.source for q in plan.queries]),
                 findings=json.dumps(
                     [{k: f.get(k) for k in ("source", "ok", "degraded")}
                      for f in findings], ensure_ascii=False),
                 conclusion=hypothesis_pattern)}],
            temperature=0.1,
        )
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        verdict = str(data.get("verdict", ""))
        if verdict not in ("sufficient", "gaps"):
            raise ValueError(verdict)
        return Reflection(
            source="llm", verdict=verdict,
            gaps=[str(g)[:200] for g in data.get("gaps", [])][:5],
            summary=str(data.get("summary", ""))[:300],
        )
    except Exception:  # noqa: BLE001 —— LLM 反思失败降级规则
        logger.warning("LLM 调查反思失败，降级规则反思")
        return base


# ---------------------------------------------------------------- Agent 互审（R-47）

@dataclass
class ReviewVerdict:
    """AG-01 对 AG-04 处置建议的合规互审结论（建议性输出，无决策权，02 §3.3）"""

    source: str  # "llm" | "rule"
    verdict: str  # "pass" | "concerns" | "escalate"
    findings: list[str] = field(default_factory=list)
    summary: str = ""


REVIEW_HEAVY_ACTIONS = ("block", "freeze")  # 重处置（与 API-M-11 动作集同源）


def rule_review(action: str, amount: float | None, risk_score: int,
                evidence: list[dict[str, Any]]) -> ReviewVerdict:
    """确定性互审下限（LLM 不可用/失败时保底，可解释可回放）。

    证据充分性 + 处置恰当性分档：
      证据链为空 → escalate（无证据支撑的处置建议，审批官先核实案件背景）；
      证据 1 条 + 重处置（block/freeze）→ concerns（处置力度需人工斟酌，
                                   过度处置风险，BA-BR-03 精神）；
      证据 1 条 + 轻处置 → concerns（依据单薄，建议复核补强）；
      证据 ≥2 条 → pass（证据链足以支撑处置建议进入人工审批）。
    """
    n = len(evidence)
    if n == 0:
        verdict, findings = "escalate", [
            f"证据链为空：action={action} 处置建议无已固化证据支撑，"
            f"请审批官先核实案件背景（risk_score={risk_score}）"]
    elif n == 1 and action in REVIEW_HEAVY_ACTIONS:
        verdict, findings = "concerns", [
            f"证据仅 1 条且动作为重处置（{action}），处置力度建议人工斟酌"
            "（过度处置风险）"]
    elif n == 1:
        verdict, findings = "concerns", ["证据仅 1 条，处置依据建议人工复核补强"]
    else:
        verdict, findings = "pass", [
            f"证据链 {n} 条，处置建议（{action}）与风险分 {risk_score} 相符，"
            f"可进入人工审批"]
    return ReviewVerdict(
        source="rule", verdict=verdict, findings=findings,
        summary=f"规则互审：action={action} amount={amount} 证据 {n} 条 → {verdict}",
    )


_REVIEW_PROMPT = """你是风控合规审查员（AG-01），对处置岗（AG-04）的处置建议做独立互审。
审查维度：证据充分性（建议是否有已固化证据支撑）、处置恰当性（动作与风险是否匹配）、
过度处置风险（是否可能误伤正常客户）。你没有决策权，结论仅供人工审批官参考。

处置建议：action={action} amount={amount}
案件风险分：{risk_score}
证据链摘要（共 {evidence_count} 条）：
{evidence_digest}

只返回 JSON：{{"verdict":"pass|concerns|escalate",
  "findings":["关注点"],
  "summary":"一句话互审结论"}}
verdict 语义：pass=证据充分处置恰当；concerns=放行但有关注点；
escalate=证据缺口或过度处置风险显著，建议审批官优先核实或考虑更轻处置。"""


async def review_disposition(action: str, amount: float | None, risk_score: int,
                             evidence: list[dict[str, Any]],
                             client=None) -> ReviewVerdict:
    """互审入口：LLM 优先，不可用/失败/输出非法降级规则版（建单流程永不因互审阻断）"""
    base = rule_review(action, amount, risk_score, evidence)
    if client is None:
        from app.core.llm_adapters import LlmClient
        client = LlmClient()
    if not getattr(client, "available", False):
        return base
    try:
        digest = "\n".join(
            f"- [{e.get('source_ref', '')}] conf={e.get('confidence', '')} "
            f"{str(e.get('claim', ''))[:120]}"
            for e in evidence[:10]) or "（无证据）"
        raw = await client.chat(
            [{"role": "system", "content": "你是严谨的风控合规审查员，只输出 JSON。"},
             {"role": "user", "content": _REVIEW_PROMPT.format(
                 action=action, amount=amount, risk_score=risk_score,
                 evidence_count=len(evidence), evidence_digest=digest)}],
            temperature=0.1,
        )
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        verdict = str(data.get("verdict", ""))
        if verdict not in ("pass", "concerns", "escalate"):
            raise ValueError(verdict)
        return ReviewVerdict(
            source="llm", verdict=verdict,
            findings=[str(f)[:200] for f in data.get("findings", [])][:5],
            summary=str(data.get("summary", ""))[:300],
        )
    except Exception:  # noqa: BLE001 —— LLM 互审失败降级规则，处置链路不受阻
        logger.warning("LLM 处置互审失败，降级规则互审")
        return base


# ---------------------------------------------------------------- 控辩互审 debate（C1，BA-BR-19 / DA-INV-09，docs/14 US-E10）

@dataclass
class DebateRecord:
    """控/辩/裁三段辩论记录（建议性输出，裁决权仍在人工审批官，BA-BR-19）。
    落 approval_record.debate_json / audit_log.debate_json，只增不改（DA-INV-09）。"""

    source: str  # "llm" | "rule"
    prosecution: list[str] = field(default_factory=list)   # 控方：主张从严处置的论据
    defense: list[str] = field(default_factory=list)       # 辩方：主张从轻/保护的论据
    adjudication: str = ""                                  # 裁判倾向：从严|从轻|维持人工裁决
    verdict: str = ""    # 与互审同枚举：pass | concerns | escalate
    summary: str = ""


def rule_debate(action: str, amount: float | None, risk_score: int,
                evidence: list[dict[str, Any]]) -> DebateRecord:
    """确定性控辩下限（LLM 不可用/失败时保底，可解释可回放）。

    控方取规则互审的关注点/升级理由；辩方按证据薄弱/轻处置/中风险线生成
    对向论据；裁判倾向映射互审 verdict，最终裁决仍由审批官作出。
    """
    base = rule_review(action, amount, risk_score, evidence)
    n = len(evidence)
    prosecution = [
        f"risk_score={risk_score}，建议动作 {action}"
        f"{'（金额 ' + str(amount) + '）' if amount is not None else ''}"]
    if base.verdict != "pass":
        prosecution.extend(base.findings)
    else:
        prosecution.append(f"证据链 {n} 条支撑处置建议")
    defense: list[str] = []
    if n <= 1:
        defense.append("证据链单薄，误处置将损害客户体验，建议补强后再从严")
    if action in REVIEW_HEAVY_ACTIONS:
        defense.append(f"{action} 为重型处置，误伤成本高，建议优先考虑更轻档位")
    if risk_score < DISPATCH_FREEZE_SCORE:
        defense.append("风险分未达审批线（BA-BR-02），从严依据不充分")
    if not defense:
        defense.append("未发现显著从轻情节，辩方无保留意见")
    adj = {"escalate": "从严审视（证据缺口/过度处置风险显著）",
           "concerns": "倾向从轻（有关注点，建议人工斟酌处置力度）",
           "pass": "维持人工裁决（控辩无实质分歧，证据充分）"}[base.verdict]
    return DebateRecord(
        source="rule", prosecution=prosecution[:5], defense=defense[:5],
        adjudication=adj, verdict=base.verdict,
        summary=f"规则控辩：action={action} 证据 {n} 条，裁判倾向「{adj}」，"
                f"最终裁决仍由审批官作出（BA-BR-19）",
    )


_DEBATE_PROMPT = """你是风控控辩仲裁员（AG-01），对处置建议组织一场控辩辩论。
控方立场：主张从严处置（论证风险事实与处置必要性）；辩方立场：主张从轻/保护
（论证误处置风险与证据缺口）；你最后以裁判身份给出倾向。你没有决策权，
最终裁决由人工审批官作出（BA-BR-19）。

处置建议：action={action} amount={amount}
案件风险分：{risk_score}
证据链摘要（共 {evidence_count} 条）：
{evidence_digest}

只返回 JSON：{{"prosecution":["控方论据"],"defense":["辩方论据"],
  "adjudication":"从严|从轻|维持人工裁决","verdict":"pass|concerns|escalate",
  "summary":"一句话裁判结论"}}"""


async def debate_disposition(action: str, amount: float | None, risk_score: int,
                             evidence: list[dict[str, Any]],
                             client=None) -> DebateRecord:
    """控辩互审入口（C1，US-E10）：LLM 三段辩论优先，不可用/失败/非法降级规则版。
    输出仅建议：不改变裁决权归属（建单/审批流程照常，BA-BR-19）。"""
    base = rule_debate(action, amount, risk_score, evidence)
    if client is None:
        from app.core.llm_adapters import LlmClient
        client = LlmClient()
    if not getattr(client, "available", False):
        return base
    try:
        digest = "\n".join(
            f"- [{e.get('source_ref', '')}] conf={e.get('confidence', '')} "
            f"{str(e.get('claim', ''))[:120]}"
            for e in evidence[:10]) or "（无证据）"
        raw = await client.chat(
            [{"role": "system", "content": "你是严谨的风控控辩仲裁员，只输出 JSON。"},
             {"role": "user", "content": _DEBATE_PROMPT.format(
                 action=action, amount=amount, risk_score=risk_score,
                 evidence_count=len(evidence), evidence_digest=digest)}],
            temperature=0.2,
        )
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        verdict = str(data.get("verdict", ""))
        if verdict not in ("pass", "concerns", "escalate"):
            raise ValueError(verdict)
        return DebateRecord(
            source="llm", verdict=verdict,
            prosecution=[str(x)[:200] for x in data.get("prosecution", [])][:5],
            defense=[str(x)[:200] for x in data.get("defense", [])][:5],
            adjudication=str(data.get("adjudication", ""))[:50] or "维持人工裁决",
            summary=str(data.get("summary", ""))[:300],
        )
    except Exception:  # noqa: BLE001 —— LLM 控辩失败降级规则，建单链路不受阻
        logger.warning("LLM 控辩互审失败，降级规则控辩")
        return base


# ---------------------------------------------------------------- 动态处置分派（R-49）

DISPATCH_ACTIONS = ("block", "freeze", "reduce")  # 与 API-M-11 自动档动作集同源
DISPATCH_BLOCK_ACCOUNTS = 3  # 图谱影响账户 ≥3 视为团伙规模，处置升档 block
DISPATCH_FREEZE_SCORE = 70   # BA-BR-02 审批线同源


def rule_dispatch(risk_score: int, impact_accounts: int, citations: int) -> str:
    """AG-03 调查结论 → AG-04 处置动作协商（确定性档位下限，R-49）。

    档位语义（建议性输出，动作落地仍经 BA-BR-02 审批门控/人工决策）：
      影响账户 ≥3（团伙规模）且 KB 引用 ≥1（手法佐证）→ block；
      risk_score ≥审批线 → freeze；
      其余中风险 → reduce。
    """
    if impact_accounts >= DISPATCH_BLOCK_ACCOUNTS and citations >= 1:
        return "block"
    if risk_score >= DISPATCH_FREEZE_SCORE:
        return "freeze"
    return "reduce"


_DISPATCH_PROMPT = """你是风控处置编排者（AG-01），依据调查岗（AG-03）结论协商处置档位。
你没有决策权：产出的动作建议仍需人工审批（BA-BR-02），仅供委托通道自动提单使用。

案件风险分：{risk_score}
图谱影响账户数：{impact_accounts}
知识库引用数：{citations}

档位语义：block=团伙规模阻断；freeze=高风险冻结；reduce=中风险限额降档。
只返回 JSON：{{"action":"block|freeze|reduce","reason":"一句话理由"}}"""


async def dispatch_action(risk_score: int, impact_accounts: int, citations: int,
                          client=None) -> str:
    """动态分派入口：LLM 语义协商优先，不可用/失败/非法降级 rule 档位（R-49）。"""
    base = rule_dispatch(risk_score, impact_accounts, citations)
    if client is None:
        from app.core.llm_adapters import LlmClient
        client = LlmClient()
    if not getattr(client, "available", False):
        return base
    try:
        raw = await client.chat(
            [{"role": "system", "content": "你是严谨的风控处置编排者，只输出 JSON。"},
             {"role": "user", "content": _DISPATCH_PROMPT.format(
                 risk_score=risk_score, impact_accounts=impact_accounts,
                 citations=citations)}],
            temperature=0.1,
        )
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0) if m else raw)
        action = str(data.get("action", ""))
        if action not in DISPATCH_ACTIONS:
            raise ValueError(action)
        return action
    except Exception:  # noqa: BLE001 —— LLM 分派失败降级规则档，委托链路不受阻
        logger.warning("LLM 动态分派失败，降级规则档位 %s（R-49）", base)
        return base
