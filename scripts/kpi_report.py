# -*- coding: utf-8 -*-
"""US-E7-04 离线评估脚本（BA-KPI 口径，01 §BA-KPI / 04 §7）：KPI 报告可复现

口径定义（ground truth = account.list_flag 打标，data-generator 团伙标 watch）：
  KPI-01 事件响应时效（低风险≤5分钟）：已处置/已核验/已归档案件从立案
         （risk_case.created_at）到处置完成（首条 executed 处置凭证 ts）的
         平均时长；目标线仅约束低风险案件（risk_score<40，自动通道带）；
  KPI-02 召回率（≥85%）：watch/black 主体案件中 risk_score≥40（进入风控处置带）占比；
  KPI-03 误报率（≤10%）：none 主体案件中被"最终定性为欺诈"的占比——
         定性依据 = risk_score≥40 且无反向处置回滚（:rollback 凭证）
         且未经人工复核排除欺诈（case.transition.ReviewDismissed）；
         已被回滚/申诉排除的误判不计入（核验闭环的纠错能力，AA-SK-04）；
  KPI-04 人工介入率（≤30%）：进入人工通道（建审批工单 / 停留 PENDING_APPROVAL /
         MANUAL_REVIEW / 全源失败转人工）案件占全部案件比例；
  KPI-05 留痕完整率（100%）：disposition_record 均有对应 audit_log
         action='disposition.submit' 的覆盖比例（04 §7 遍历 DA-T-06 检查 DA-T-08）。

样本构成说明：库内含自动化测试案件（source_type=TEST）与演示剧本案件
（demo_script），报告分全量与演示口径两组呈现；测试案件主体多为无账户
档案的合成哈希，KPI-02/03 仅统计可关联 ground truth 的样本。

用法：.venv\\Scripts\\python.exe scripts\\kpi_report.py
产出：docs/reports/kpi-report.md + kpi-report.json（幂等覆盖，可复现取证）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import asyncpg

DSN = os.getenv("TG_KPI_DSN", "postgresql://tg_web:tg_web_dev@localhost:5433/tradeguard")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "docs", "reports")
THRESHOLDS = {"KPI-02": (0.85, ">="), "KPI-03": (0.10, "<="),
              "KPI-04": (0.30, "<="), "KPI-05": (1.00, ">=")}
KPI01_TARGET_MIN = 5  # BA-KPI-01 目标线：低风险事件 ≤5 分钟（01 §7）


async def compute(conn) -> dict:
    out: dict = {"generated_at": datetime.now(timezone.utc).isoformat(),
                 "thresholds": {"KPI-01": f"低风险≤{KPI01_TARGET_MIN}分钟"}
                 | {k: f"{op}{v:.0%}" for k, (v, op) in THRESHOLDS.items()}}
    for scope, demo_only in (("all", False), ("demo", True)):
        kpi = {}
        # 演示口径：主体有 demo- 前缀播种交易（剧本 D1~D3 专用主体，多轮复跑稳定）
        scope_sql = ("AND rc.subject_ref IN (SELECT rtrim(account_hash) FROM transaction "
                     "WHERE tx_id LIKE 'demo-%')" if demo_only else "")
        # KPI-01 响应时效：立案→首条 executed 处置凭证（BA-KPI-01，低风险=risk_score<40）
        row = await conn.fetchrow(f"""
            SELECT count(*) AS total,
                   avg(extract(epoch FROM d.done_ts - rc.created_at))/60 AS avg_min,
                   count(*) FILTER (WHERE rc.risk_score < 40) AS low_total,
                   avg(extract(epoch FROM d.done_ts - rc.created_at))
                     FILTER (WHERE rc.risk_score < 40)/60 AS low_avg_min
            FROM risk_case rc
            JOIN LATERAL (SELECT min(dr.ts) AS done_ts FROM disposition_record dr
                          WHERE dr.case_id=rc.case_id AND dr.status IN ('executed','rolled_back')
                         ) d ON d.done_ts IS NOT NULL
            WHERE rc.status IN ('DISPOSED','VERIFIED','ARCHIVED') {scope_sql}""")
        kpi["KPI-01"] = {"total": row["total"],
                         "avg_min": float(row["avg_min"]) if row["avg_min"] is not None else None,
                         "low_total": row["low_total"],
                         "low_avg_min": (float(row["low_avg_min"])
                                         if row["low_avg_min"] is not None else None),
                         "value": (float(row["low_avg_min"])
                                   if row["low_avg_min"] is not None else None)}
        # KPI-02 召回率：watch/black 主体进入风控处置带（risk_score≥40）
        row = await conn.fetchrow(f"""
            SELECT count(*) FILTER (WHERE rc.risk_score>=40) AS hit, count(*) AS total
            FROM risk_case rc JOIN account a ON a.account_hash=rc.subject_ref
            WHERE a.list_flag IN ('watch','black') {scope_sql}""")
        kpi["KPI-02"] = {"hit": row["hit"], "total": row["total"],
                         "value": (row["hit"] / row["total"]) if row["total"] else None}
        # KPI-03 误报率：none 主体被最终定性欺诈（未回滚且未申诉排除）
        row = await conn.fetchrow(f"""
            SELECT count(*) FILTER (WHERE rc.risk_score>=40
                   AND NOT EXISTS (SELECT 1 FROM disposition_record dr
                                   WHERE dr.case_id=rc.case_id
                                     AND dr.idempotency_key LIKE '%:rollback')
                   AND NOT EXISTS (SELECT 1 FROM audit_log al
                                   WHERE al.target=rc.case_id
                                     AND al.action='case.transition.ReviewDismissed')
                   ) AS hit, count(*) AS total
            FROM risk_case rc JOIN account a ON a.account_hash=rc.subject_ref
            WHERE a.list_flag='none' {scope_sql}""")
        kpi["KPI-03"] = {"hit": row["hit"], "total": row["total"],
                         "value": (row["hit"] / row["total"]) if row["total"] else None}
        # KPI-04 人工介入率：进入人工通道的案件占比
        row = await conn.fetchrow(f"""
            SELECT count(*) FILTER (WHERE EXISTS
                     (SELECT 1 FROM approval_record ap WHERE ap.case_id=rc.case_id)
                   OR rc.status IN ('PENDING_APPROVAL','MANUAL_REVIEW')
                   OR EXISTS (SELECT 1 FROM audit_log al WHERE al.target=rc.case_id
                              AND al.action='signals.all_fail')) AS hit,
                   count(*) AS total
            FROM risk_case rc WHERE 1=1 {scope_sql}""")
        kpi["KPI-04"] = {"hit": row["hit"], "total": row["total"],
                         "value": (row["hit"] / row["total"]) if row["total"] else None}
        # KPI-05 留痕完整率：DA-T-06 凭证均有 DA-T-08 disposition.submit 审计
        row = await conn.fetchrow(f"""
            SELECT count(*) FILTER (WHERE EXISTS
                     (SELECT 1 FROM audit_log al WHERE al.target=dr.case_id
                       AND al.action='disposition.submit')) AS hit,
                   count(*) AS total
            FROM disposition_record dr
            JOIN risk_case rc ON rc.case_id=dr.case_id
            WHERE 1=1 {scope_sql}""")
        kpi["KPI-05"] = {"hit": row["hit"], "total": row["total"],
                         "value": (row["hit"] / row["total"]) if row["total"] else None}
        out[scope] = kpi
    return out


def _verdict(kpi_id: str, value: float | None) -> str:
    if value is None:
        return "N/A（无样本）"
    if kpi_id == "KPI-01":
        return "达标" if value <= KPI01_TARGET_MIN else "未达标"
    limit, op = THRESHOLDS[kpi_id]
    ok = value >= limit if op == ">=" else value <= limit
    return "达标" if ok else "未达标"


def render_md(report: dict) -> str:
    # 复现命令用当前解释器相对路径（不硬编码 .venv/Scripts/python.exe，跨环境可复现）
    interp = os.path.relpath(sys.executable, os.path.join(os.path.dirname(__file__), ".."))
    lines = ["# TradeGuard KPI 评估报告（US-E7-04 离线评估，可复现）", "",
             f"- 生成时间：{report['generated_at']}",
             f"- 复现命令：`{interp.replace(os.sep, '/')} scripts/kpi_report.py`",
             "- Ground truth：`account.list_flag`（watch=团伙观察名单，black=确认欺诈，none=正常）",
             "", "## 阈值与结论（全量 / 演示两口径分别独立判定，不互相掩盖）", "",
             "| KPI | 定义 | 阈值 | 全量口径 | 全量结论 | 演示口径 | 演示结论 |",
             "|---|---|---|---|---|---|---|"]
    names = {"KPI-02": "欺诈召回率", "KPI-03": "误报率（最终定性口径）",
             "KPI-04": "人工介入率", "KPI-05": "处置留痕完整率"}

    def _fmt_kpi01(k: dict) -> str:
        if k["total"] == 0:
            return "N/A"
        low = (f"低风险 {k['low_avg_min']:.1f} 分（{k['low_total']}例）"
               if k["low_avg_min"] is not None else "低风险 N/A（0例）")
        return f"{low}／全部 {k['avg_min']:.1f} 分（{k['total']}例）"

    a, d = report["all"]["KPI-01"], report["demo"]["KPI-01"]
    lines.append(f"| KPI-01 | 事件响应时效（立案→处置完成） | {report['thresholds']['KPI-01']} "
                 f"| {_fmt_kpi01(a)} | {_verdict('KPI-01', a['value'])} "
                 f"| {_fmt_kpi01(d)} | {_verdict('KPI-01', d['value'])} |")
    for k in ("KPI-02", "KPI-03", "KPI-04", "KPI-05"):
        a, d = report["all"][k], report["demo"][k]
        fa = f"{a['value']:.1%}（{a['hit']}/{a['total']}）" if a["value"] is not None else "N/A"
        fd = f"{d['value']:.1%}（{d['hit']}/{d['total']}）" if d["value"] is not None else "N/A"
        lines.append(f"| {k} | {names[k]} | {report['thresholds'][k]} | {fa} "
                     f"| {_verdict(k, a['value'])} | {fd} | {_verdict(k, d['value'])} |")
    lines += ["", "## 口径说明", "",
              "- KPI-01 统计已处置闭环案件（DISPOSED/VERIFIED/ARCHIVED 且有 executed 处置凭证），"
              "时长 = risk_case.created_at → 首条处置凭证 ts；目标线仅约束低风险案件"
              "（risk_score<40 自动通道带），高风险案件含人工审批等待不设目标线。",
              "- KPI-02/03 仅统计可关联账户档案（ground truth）的案件；自动化测试案件"
              "多为无档案合成哈希，不计入。",
              "- KPI-03 的误报以\"最终定性\"计：核验回滚（:rollback）或人工复核排除"
              "（ReviewDismissed）的误判视为被闭环纠错，不计入误报（AA-SK-04 纠错能力）。",
              "- KPI-04 人工通道 = 建过审批工单 / 停留 PENDING_APPROVAL / MANUAL_REVIEW / "
              "全源失败转人工（signals.all_fail）。",
              "- KPI-05 遍历 DA-T-06（disposition_record）逐条检查 DA-T-08（audit_log）"
              "对应 disposition.submit 记录（04 §7 验收口径）。",
              "- 演示口径：主体带 demo- 前缀播种交易（剧本 D1/D2/D3 专用主体），复跑可复现。",
              "- 全量口径含 Sprint 自动化测试案件（source=TEST，pytest 运行残留，多为",
              "PENDING_APPROVAL 中间态），会抬升 KPI-03/04 全量数值；决赛验收以演示口径为准。",
              "- KPI-04 演示口径结构性偏高：三剧本中 D2/D3 本身即人机协同审批/申诉场景",
              "（2/3 必入人工通道），自动通道能力由 SC-01 与测试矩阵 169 例覆盖。", ""]
    return "\n".join(lines)


async def main():
    conn = await asyncpg.connect(DSN)
    try:
        report = await compute(conn)
    finally:
        await conn.close()
    os.makedirs(OUT_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, "kpi-report.md")
    json_path = os.path.join(OUT_DIR, "kpi-report.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_md(report))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(render_md(report))
    print(f"■ 报告已落盘：{os.path.abspath(md_path)} / {os.path.abspath(json_path)}")


if __name__ == "__main__":
    asyncio.run(main())
