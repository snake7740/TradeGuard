# -*- coding: utf-8 -*-
"""离线评估（阶段3，R-45）：混淆矩阵 + P/R/F1 + 时间窗口漂移

ground truth = account.list_flag（watch=团伙观察 / black=确认欺诈 / none=正常）
系统最终定性欺诈 = risk_score≥40 且未回滚（:rollback）且未申诉排除（ReviewDismissed）

与 kpi_report.py 的分工：kpi_report 是「KPI 达标判定」（5 项阈值），
本脚本是「模型质量评估」（混淆矩阵 + 漂移），两者互补，构成离线评估闭环。
"""
import asyncio
import os

import asyncpg

DSN = os.getenv("TG_EVAL_DSN",
                "postgresql://postgres:tradeguard_dev@localhost:5433/tradeguard")

# 最终定性欺诈判定（与 kpi_report KPI-03 误报口径同源）
FRAUD_VERDICT = """
    rc.risk_score>=40
    AND NOT EXISTS (SELECT 1 FROM disposition_record dr
                    WHERE dr.case_id=rc.case_id
                      AND dr.idempotency_key LIKE '%:rollback')
    AND NOT EXISTS (SELECT 1 FROM audit_log al
                    WHERE al.target=rc.case_id
                      AND al.action='case.transition.ReviewDismissed')
"""


async def confusion_matrix(conn) -> dict:
    """混淆矩阵：真欺诈=watch/black，正常=none，判定欺诈=最终定性口径"""
    row = await conn.fetchrow(f"""
        SELECT
          count(*) FILTER (WHERE a.list_flag IN ('watch','black') AND {FRAUD_VERDICT}) AS tp,
          count(*) FILTER (WHERE a.list_flag='none' AND {FRAUD_VERDICT}) AS fp,
          count(*) FILTER (WHERE a.list_flag IN ('watch','black')
                           AND NOT ({FRAUD_VERDICT})) AS fn,
          count(*) FILTER (WHERE a.list_flag='none'
                           AND NOT ({FRAUD_VERDICT})) AS tn
        FROM risk_case rc JOIN account a ON a.account_hash=rc.subject_ref
    """)
    tp, fp, fn, tn = row["tp"], row["fp"], row["fn"], row["tn"]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if (precision and recall) else None)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1}


async def drift_by_month(conn) -> list[dict]:
    """时间窗口漂移：按月分桶，观察 recall/fpr 随时间变化（模型/规则漂移监控）"""
    rows = await conn.fetch(f"""
        SELECT date_trunc('month', rc.created_at)::date AS month,
               count(*) FILTER (WHERE a.list_flag IN ('watch','black')
                                AND {FRAUD_VERDICT}) AS tp,
               count(*) FILTER (WHERE a.list_flag IN ('watch','black')) AS pos,
               count(*) FILTER (WHERE a.list_flag='none' AND {FRAUD_VERDICT}) AS fp,
               count(*) FILTER (WHERE a.list_flag='none') AS neg
        FROM risk_case rc JOIN account a ON a.account_hash=rc.subject_ref
        GROUP BY 1 ORDER BY 1
    """)
    out = []
    for r in rows:
        recall = r["tp"] / r["pos"] if r["pos"] else None
        fpr = r["fp"] / r["neg"] if r["neg"] else None
        out.append({"month": str(r["month"]), "recall": recall, "fpr": fpr,
                    "pos": r["pos"], "neg": r["neg"]})
    return out


async def main():
    conn = await asyncpg.connect(DSN)
    try:
        cm = await confusion_matrix(conn)
        drift = await drift_by_month(conn)
        print("=== 混淆矩阵（ground truth: account.list_flag）===")
        print(f"TP={cm['tp']} FP={cm['fp']} FN={cm['fn']} TN={cm['tn']}")
        print(f"precision={cm['precision']} recall={cm['recall']} f1={cm['f1']}")
        print("\n=== 时间窗口漂移（按月，recall/fpr 监控）===")
        for d in drift:
            print(f"{d['month']}: recall={d['recall']} fpr={d['fpr']} "
                  f"(pos={d['pos']} neg={d['neg']})")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
