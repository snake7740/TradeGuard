# -*- coding: utf-8 -*-
"""案件审计叙事生成（BA-BR-27，API-W-30，docs/13 D2 闭合，docs/14 v1.7 US-E18）

业务定位：STR/SAR 可疑报告叙事是调查员最耗时的行政环节（行业对标 docs/09 v1.3
最佳实践 #4）——本模块以案件证据链为唯一素材自动起草叙事，人工审校定稿。

构造性防幻觉（与 kb/ask BA-BR-23 同哲学，升级为引用对齐机制）：
1. 规则轨：叙事逐句由 DB 行装配，每个论断自带引用 token（SIG/EV/DSP/APR + 行 id
   前 8 位），引用集恒为素材集子集——构造上不可能产生无据论断；
2. 校验门 verify_citations：对任何注入的叙事产出（含未来 LLM 轨）逐 token 回查
   素材全集，未对齐即拒绝（E-NARRATIVE-GROUNDING），双轨降级同 R-49 先例——
   LLM 轨预留注入点，规则轨为确定性保底；
3. 产物为 DRAFT 工作稿：生成行为留痕（audit narrative.generated），内容不直接
   替代人工上报文书（出站定稿须人工，同 DA-INV-06 知识发布人审门精神）。

纯函数层（单测目标，06 §3）：compose_narrative / verify_citations。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

# 引用 token 词法：前缀 ∈ {SIG(信号) EV(证据) DSP(处置) APR(审批)} + id 前 8 位
_TOKEN_RE = re.compile(r"\[(SIG|EV|DSP|APR):([0-9a-f]{8})\]")
TOKEN_PREFIXES = {"SIG", "EV", "DSP", "APR"}


class NarrativeGroundingError(Exception):
    """叙事含未对齐引用（E-NARRATIVE-GROUNDING，BA-BR-27 引用对齐门禁）"""

    code = "E-NARRATIVE-GROUNDING"


def _token(prefix: str, row_id: str) -> str:
    return f"[{prefix}:{row_id[:8]}]"


def compose_narrative(case: dict, signals: list[dict], evidence: list[dict],
                      dispositions: list[dict], approvals: list[dict]) -> dict:
    """规则轨装配：案件五段叙事（概况/信号/证据/处置/审批），每句挂引用 token。
    素材为空时如实声明"无记录"而非编造（负结果不粉饰，同 kb/ask 无先例声明）。"""
    cid = case["case_id"]
    sections: list[dict] = []

    # 一、案件概况（案件行本身为素材根引用）
    sections.append({
        "heading": "案件概况",
        "text": (f"案件 {cid} 于 {case.get('created_at')} 立案，主体 {case.get('subject_ref')}，"
                 f"当前风险分 {case.get('risk_score')}，状态 {case.get('status')}。"),
        "citations": [],
    })

    # 二、风险信号
    if signals:
        parts = [f"{s.get('source')}/{s.get('type')}（置信 {s.get('confidence')}，"
                 f"{_token('SIG', s['signal_id'])}）" for s in signals]
        sections.append({"heading": "风险信号", "text": f"共 {len(signals)} 条：" + "；".join(parts) + "。",
                         "citations": [_token('SIG', s['signal_id']) for s in signals]})
    else:
        sections.append({"heading": "风险信号", "text": "无风险信号记录。", "citations": []})

    # 三、证据链
    if evidence:
        parts = [f"「{e.get('claim')}」（来源 {e.get('source_ref')}，{_token('EV', e['evidence_id'])}）"
                 for e in evidence]
        sections.append({"heading": "证据链", "text": f"共 {len(evidence)} 项：" + "；".join(parts) + "。",
                         "citations": [_token('EV', e['evidence_id']) for e in evidence]})
    else:
        sections.append({"heading": "证据链", "text": "无证据记录。", "citations": []})

    # 四、处置记录
    if dispositions:
        parts = [f"动作 {d.get('action')}（{_token('DSP', d['exec_id'])}）" for d in dispositions]
        sections.append({"heading": "处置记录", "text": f"共 {len(dispositions)} 次：" + "；".join(parts) + "。",
                         "citations": [_token('DSP', d['exec_id']) for d in dispositions]})
    else:
        sections.append({"heading": "处置记录", "text": "尚无处置记录。", "citations": []})

    # 五、审批记录
    if approvals:
        parts = [f"{a.get('decision')}（{_token('APR', a['approval_id'])}）" for a in approvals]
        sections.append({"heading": "审批记录", "text": f"共 {len(approvals)} 单：" + "；".join(parts) + "。",
                         "citations": [_token('APR', a['approval_id']) for a in approvals]})
    else:
        sections.append({"heading": "审批记录", "text": "尚无审批工单。", "citations": []})

    citations = [t for s in sections for t in s["citations"]]
    return {"case_id": cid, "sections": sections, "citations": citations}


def citation_universe(signals: list[dict], evidence: list[dict],
                      dispositions: list[dict], approvals: list[dict]) -> set[str]:
    """素材引用全集：任何合法叙事 token 必须落在此集合内"""
    u: set[str] = set()
    u |= {_token('SIG', s['signal_id']) for s in signals}
    u |= {_token('EV', e['evidence_id']) for e in evidence}
    u |= {_token('DSP', d['exec_id']) for d in dispositions}
    u |= {_token('APR', a['approval_id']) for a in approvals}
    return u


def verify_citations(text: str, universe: set[str]) -> list[str]:
    """引用对齐门禁：抽取文本全部 token，返回未对齐清单（空 = 通过）。
    对规则轨产物构造性恒通过；对任何注入产出（LLM 轨）强制回查。"""
    found = {f"[{m.group(1)}:{m.group(2)}]" for m in _TOKEN_RE.finditer(text)}
    return sorted(found - universe)


async def build_case_narrative(pool, case_id: str, actor: str,
                               generator=None) -> dict:
    """编排：取素材 → 装配（或注入 generator 产出后过校验门）→ 生成留痕。
    generator 注入点（LLM 轨预留）：callable(case, signals, evidence,
    dispositions, approvals) -> str；产出未过 verify_citations 一律降级规则轨。"""
    case = await pool.fetchrow(
        "SELECT case_id, subject_ref, status, risk_score, created_at FROM risk_case WHERE case_id=$1",
        case_id)
    if not case:
        raise LookupError(case_id)
    case = dict(case)
    signals = [dict(r) for r in await pool.fetch(
        "SELECT signal_id, source, type, confidence FROM risk_signal WHERE case_id=$1 ORDER BY ts", case_id)]
    evidence = [dict(r) for r in await pool.fetch(
        "SELECT evidence_id, claim, source_ref FROM case_evidence WHERE case_id=$1 ORDER BY ts", case_id)]
    dispositions = [dict(r) for r in await pool.fetch(
        "SELECT exec_id, action FROM disposition_record WHERE case_id=$1 ORDER BY ts", case_id)]
    approvals = [dict(r) for r in await pool.fetch(
        "SELECT approval_id, decision FROM approval_record WHERE case_id=$1 ORDER BY created_at", case_id)]

    narrative = compose_narrative(case, signals, evidence, dispositions, approvals)
    universe = citation_universe(signals, evidence, dispositions, approvals)
    track = "rule"
    if generator is not None:  # LLM 轨：产出过引用对齐门禁，未对齐降级规则轨（同 R-49 先例）
        try:
            draft = generator(case, signals, evidence, dispositions, approvals)
            if not verify_citations(draft, universe):
                narrative = {"case_id": case_id, "sections":
                             [{"heading": "AI 起草", "text": draft,
                               "citations": sorted(_TOKEN_RE.findall(draft) and
                                                   [f"[{a}:{b}]" for a, b in _TOKEN_RE.findall(draft)])}],
                             "citations": [f"[{a}:{b}]" for a, b in _TOKEN_RE.findall(draft)]}
                track = "llm"
        except Exception:  # noqa: BLE001 —— LLM 轨失败一律降级规则轨（确定性保底）
            pass

    await pool.execute(
        """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
           VALUES ($1, $2, 'narrative.generated', $3, $4,
                   (SELECT trace_id FROM risk_case WHERE case_id=$5))""",
        uuid.uuid4().hex, actor, case_id,
        f"叙事草稿生成 track={track} 段落={len(narrative['sections'])} "
        f"引用={len(narrative['citations'])}（BA-BR-27 引用对齐，DRAFT 待人工审校）"[:300],
        case_id)
    narrative["status"] = "DRAFT"
    narrative["track"] = track
    narrative["generated_at"] = datetime.now(timezone.utc).isoformat()
    return narrative
