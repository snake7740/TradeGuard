#!/usr/bin/env python3
"""公开数据集回放：ULB creditcard 验证 AA-SK-01 聚合内核行业效度（p3-dataset）。

数据集：Credit Card Fraud Detection（ULB & Worldline，2013-09 欧洲持卡人两天
284,807 笔 / 492 欺诈 / 0.172%；PCA 匿名特征 V1-V28 + Time + Amount + Class）。
来源：Zenodo DOI 10.5281/zenodo.7395559（md5 e90efcb83d69faf99fcab8b0255024de，
CC-BY-4.0 存档声明；原始 Kaggle mlg-ulb/creditcardfraud，DbCL 许可）。

回放设计（诚实映射，Class 标签仅用于事后对照，绝不进入任何评分输入）：
  A 组·逐笔金额模式：Amount → 全局经验分位 → tx 源 amount_spike 信号
     （confidence=分位）→ score_signals 加权评分（权重 0.4，封顶 100）。
     velocity 诚实置零（数据集无主体 ID，主体级频次不可计算）。
  B 组·时间邻接簇模式（card-testing 近似）：欺诈手法之一 card testing 呈
     时间聚集，以 30 秒邻接聚类近似「主体会话」；簇笔数即 velocity_1h，
     经内核 velocity_bonus（BA-BR-14 阈值 10 笔）触发 tx_velocity_burst。
     先在数据画像中验证「欺诈时间聚集」假设是否成立，再报告簇级指标——
     假设不成立则如实报告负结果。

指标：PR-AUC（AP）、BR-01 工作点（score≥40 或 amount≥5000 告警）的
     precision/recall/告警量；基线：随机告警（precision=欺诈率）、
     固定金额阈值。

诚实边界（写入报告，防夸大）：
  1. V1-V28 为 PCA 匿名特征，本项目信号面中的图维度（device/payee/ipseg）
     与外部源（征信/舆情/投诉）在数据集中不存在，不参与回放；
  2. 无主体 ID → A 组 velocity 置零、B 组以时间邻接簇近似主体；
  3. 本回放验证「骨架可消费公开数据分布 + 可映射维度指标可解释」，
     不对标 Kaggle 竞赛 SOTA（其直接消费 V1-V28 全量特征）；
  4. 内核直调（纯函数层），不经 PG/MCP 全链路——全链路已由 110 例测试
     与 device-guard 第二场景覆盖（docs/10-场景扩展映射-device-guard.md）。

用法：
    python scripts/dataset_replay.py [--csv db/dataset/creditcard.csv]
                                     [--out docs/reports/dataset-replay.md]
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "web-api"))

from app.skills.aggregation import (  # noqa: E402
    AUTO_AMOUNT_MAX,
    AUTO_SCORE_MAX,
    VELOCITY_BONUS,
    VELOCITY_1H_THRESHOLD,
    score_signals,
    triage,
    velocity_bonus,
)

EPOCH = datetime(2013, 9, 1, tzinfo=timezone.utc)  # ULB 采集期 2013-09（README）
CLUSTER_GAP = timedelta(seconds=30)  # B 组时间邻接簇间隔
TX_WEIGHT_CAP = 100 * 0.4  # tx 源权重 0.4 → 单信号评分理论上限（诚实标注用）


def load_rows(path: Path) -> list[tuple[float, float, int]]:
    """读 CSV → [(Time 秒, Amount, Class)]，按 Time 升序（流式顺序回放）。"""
    rows: list[tuple[float, float, int]] = []
    with path.open(encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append((float(rec["Time"]), float(rec["Amount"]), int(rec["Class"])))
    rows.sort(key=lambda r: r[0])
    return rows


class Quantiler:
    """全局 Amount 经验分位（读入后一次性构建，查询 O(log n)）。"""

    def __init__(self, amounts: list[float]):
        self.sorted_ = sorted(amounts)

    def __call__(self, amount: float) -> float:
        return bisect_left(self.sorted_, amount) / len(self.sorted_)


def average_precision(pairs: list[tuple[float, int]]) -> float:
    """AP（PR 曲线下面积）：按分数降序，并列分数按组边界处理。"""
    pairs_sorted = sorted(pairs, key=lambda p: -p[0])
    total_pos = sum(lbl for _, lbl in pairs_sorted)
    if total_pos == 0:
        return 0.0
    hits = 0
    ap = 0.0
    i = 0
    n = len(pairs_sorted)
    while i < n:
        j = i
        group_pos = 0
        while j < n and pairs_sorted[j][0] == pairs_sorted[i][0]:
            group_pos += pairs_sorted[j][1]
            j += 1
        hits += group_pos
        ap += group_pos * hits / j  # 组边界处的 precision × 组内 recall 增量
        i = j
    return ap / total_pos


def nearest_gap_stats(rows: list[tuple[float, float, int]]) -> dict:
    """时间邻接间隔画像：欺诈笔 vs 全体（B 组 card-testing 假设的证据）。"""
    fraud_times = [t for t, _, k in rows if k == 1]
    all_times = [t for t, _, _ in rows]

    def median_gap(times: list[float]) -> float:
        gaps = [b - a for a, b in zip(times, times[1:]) if b - a > 0]
        return statistics.median(gaps) if gaps else 0.0

    return {
        "全体中位邻接间隔_s": median_gap(all_times),
        "欺诈中位邻接间隔_s": median_gap(fraud_times),
    }


def replay_a(
    rows: list[tuple[float, float, int]], q: Quantiler
) -> dict:
    """A 组：逐笔金额分位 → tx amount_spike 信号 → 内核评分（velocity 置零）。"""
    zero_velocity = {
        "velocity_1h": {"count": 0, "amount": 0.0},
        "velocity_24h": {"count": 0, "amount": 0.0},
    }
    pairs: list[tuple[float, int]] = []
    alerts = tp = 0
    for _, amount, klass in rows:
        signal = [{"source": "tx", "type": "amount_spike", "confidence": q(amount)}]
        score = score_signals(signal, zero_velocity)
        pairs.append((score, klass))
        # BR-01 工作点：内核 triage 裁决 investigate 即告警（进人工审查队列）
        if triage(score, amount, signal,
                  auto_score_max=AUTO_SCORE_MAX, auto_amount_max=AUTO_AMOUNT_MAX
                  ) == "investigate":
            alerts += 1
            tp += klass
    return {
        "ap": average_precision(pairs),
        "alerts": alerts,
        "precision": tp / alerts if alerts else 0.0,
        "recall": tp / sum(k for _, _, k in rows),
    }


def replay_b(
    rows: list[tuple[float, float, int]], q: Quantiler
) -> dict:
    """B 组：30s 邻接簇近似主体会话 → 簇 velocity → BA-BR-14 突破加分。"""
    clusters: list[list[tuple[float, float, int]]] = []
    cur: list[tuple[float, float, int]] = []
    for row in rows:
        if cur and timedelta(seconds=row[0] - cur[0][0]) > CLUSTER_GAP:
            clusters.append(cur)
            cur = []
        cur.append(row)
    if cur:
        clusters.append(cur)

    pairs: list[tuple[float, int]] = []
    burst_clusters = 0
    fraud_in_burst_n = normal_total = normal_in_burst_n = fraud_total = 0
    for c in clusters:
        count = len(c)
        amount_sum = sum(a for _, a, _ in c)
        velocity = {
            "velocity_1h": {"count": count, "amount": amount_sum},
            "velocity_24h": {"count": count, "amount": amount_sum},
        }
        burst = velocity_bonus(velocity, t1h=VELOCITY_1H_THRESHOLD) > 0
        signals = []
        if burst:
            burst_clusters += 1
            signals.append({
                "source": "tx", "type": "tx_velocity_burst",
                "confidence": min(1.0, count / 20),
            })
        signals.append({
            "source": "tx", "type": "amount_spike",
            "confidence": q(max(a for _, a, _ in c)),
        })
        score = score_signals(signals, velocity)
        label = 1 if any(k == 1 for _, _, k in c) else 0
        for _, _, k in c:  # 笔级归入统计（对照：正常笔 vs 欺诈笔落入突破簇比例）
            if k:
                fraud_total += 1
                fraud_in_burst_n += burst
            else:
                normal_total += 1
                normal_in_burst_n += burst
        pairs.append((score, label))
    n_fraud_cluster = sum(1 for p in pairs if p[1] == 1)
    fraud_in_burst = fraud_in_burst_n / fraud_total if fraud_total else 0.0
    normal_in_burst = normal_in_burst_n / normal_total if normal_total else 0.0
    return {
        "clusters": len(clusters),
        "burst_clusters": burst_clusters,
        "ap": average_precision(pairs),
        "cluster_fraud_rate": n_fraud_cluster / len(clusters) if clusters else 0.0,
        "fraud_in_burst": fraud_in_burst,
        "normal_in_burst": normal_in_burst,
        "burst_lift": fraud_in_burst / normal_in_burst if normal_in_burst else 0.0,
    }


def amount_threshold_baseline(
    rows: list[tuple[float, float, int]], threshold: float
) -> tuple[float, float]:
    tp = fp = 0
    fraud = 0
    for _, amount, klass in rows:
        fraud += klass
        if amount >= threshold:
            if klass:
                tp += 1
            else:
                fp += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / fraud if fraud else 0.0
    return prec, rec


def render_md(meta: dict, a: dict, b: dict, gaps: dict, baseline: dict) -> str:
    lines = [
        "# 公开数据集回放验证（p3-dataset）",
        "",
        "## 数据集与出处",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        "| 数据集 | Credit Card Fraud Detection（ULB & Worldline） |",
        "| 存档 | Zenodo DOI [10.5281/zenodo.7395559](https://doi.org/10.5281/zenodo.7395559)（CC-BY-4.0 存档声明） |",
        "| 原始来源 | Kaggle [mlg-ulb/creditcardfraud](https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud)（DbCL） |",
        "| 规模 | 284,807 笔 / 492 欺诈（0.172%）/ 2013-09 两天 / PCA 特征 V1-V28 |",
        "| 完整性 | md5 e90efcb83d69faf99fcab8b0255024de |",
        f"| 本次实读 | {meta['rows']} 笔 / 欺诈 {meta['frauds']}（{meta['rate']:.3%}） |",
        "",
        "## 回放口径（诚实标注）",
        "",
        "1. **无标签泄漏**：Class 标签仅用于事后对照，评分输入只有 Amount 与 Time；",
        "2. **内核直调**：回放调用 AA-SK-01 确定性内核纯函数层"
        "（`score_signals` / `velocity_bonus` / `triage`，权重与 BA-BR-14 阈值原值），"
        "不经 PG/MCP 全链路（全链路由 110 例测试与 device-guard 场景覆盖）；",
        "3. **维度缺口（诚实）**：V1-V28 为 PCA 匿名特征，本项目图维度"
        "（device/payee/ipseg）与外部源（征信/舆情/投诉）在数据集中不存在，不参与；",
        "4. **主体缺口（诚实）**：无主体 ID → A 组 velocity 置零"
        f"（tx 源权重 0.4 → 单信号评分上限 {TX_WEIGHT_CAP:.0f}），"
        "B 组以 30 秒时间邻接簇近似主体会话（card-testing 手法假设）；",
        "5. **结论边界**：验证「骨架可消费公开数据分布、可映射维度指标可解释」，"
        "不对标 Kaggle 竞赛 SOTA（其直接消费 V1-V28 全量特征）。",
        "",
        "## 数据画像",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 全体中位邻接间隔 | {gaps['全体中位邻接间隔_s']:.1f} 秒 |",
        f"| 欺诈中位邻接间隔 | {gaps['欺诈中位邻接间隔_s']:.1f} 秒 |",
        f"| 正常金额中位数 | {meta['amount_normal_median']:.2f} |",
        f"| 欺诈金额中位数 | {meta['amount_fraud_median']:.2f} |",
        "",
        "## 结果",
        "",
        "### A 组·逐笔金额模式（amount 分位 → tx 信号 → 内核评分）",
        "",
        "| 指标 | 值 | 说明 |",
        "| --- | --- | --- |",
        f"| PR-AUC（AP） | {a['ap']:.4f} | 按 score 降序全阈值扫描 |",
        f"| 随机基线 | {meta['rate']:.4f} | 随机告警的 precision=欺诈率 |",
        f"| BR-01 工作点告警量 | {a['alerts']} 笔 | triage=investigate（人工审查队列规模） |",
        f"| 工作点 precision | {a['precision']:.4f} | 告警中欺诈占比 |",
        f"| 工作点 recall | {a['recall']:.4f} | 欺诈被捕获比例 |",
        "",
        "### B 组·时间邻接簇模式（30s 簇 ≈ card-testing 会话）",
        "",
        "| 指标 | 值 | 说明 |",
        "| --- | --- | --- |",
        f"| 簇总数 | {b['clusters']} | 30 秒邻接聚类 |",
        f"| BR-14 突破簇 | {b['burst_clusters']} | 簇笔数 ≥ {VELOCITY_1H_THRESHOLD}（+{VELOCITY_BONUS} 分生效） |",
        f"| 簇级 PR-AUC | {b['ap']:.4f} | 簇含 ≥1 欺诈为正 |",
        f"| 含欺诈簇占比 | {b['cluster_fraud_rate']:.4f} |",
        f"| 欺诈落入突破簇比例 | {b['fraud_in_burst']:.4f} | card-testing 假设直接检验（需看对照行） |",
        f"| 正常笔落入突破簇比例（对照） | {b['normal_in_burst']:.4f} | 无此对照则上行为假命中 |",
        f"| 突破簇提升倍数（lift） | {b['burst_lift']:.2f}x | 欺诈落入率 / 正常落入率 |",
        "",
        "### 固定金额阈值基线（对照）",
        "",
        "| 阈值 | precision | recall |",
        "| --- | --- | --- |",
    ]
    for th, (prec, rec) in baseline.items():
        lines.append(f"| ≥ {th:.0f} | {prec:.4f} | {rec:.4f} |")
    lines += [
        "",
        "## 结论",
        "",
    ]
    a_lift = a["ap"] / meta["rate"] if meta["rate"] else 0.0
    if a_lift >= 2.0:
        a_verdict = (f"金额维度信号有效（AP 为随机基线的 {a_lift:.1f} 倍）；"
                     "工作点告警量给出人工审查队列的规模-收益参考")
    else:
        a_verdict = (f"金额单维信号在该数据集上接近无效（AP {a['ap']:.4f} 仅为随机基线 "
                     f"{meta['rate']:.4f} 的 {a_lift:.1f} 倍）——ULB 欺诈以小额 card-testing "
                     f"为主（欺诈金额中位 {meta['amount_fraud_median']:.2f} < "
                     f"正常 {meta['amount_normal_median']:.2f}），金额维度天然不判别，如实披露")
    if b["burst_lift"] >= 2.0:
        b_verdict = (f"BA-BR-14 velocity 规则在真实手法上命中（欺诈落入突破簇比例"
                     f"{b['fraud_in_burst']:.1%}，为正常笔的 {b['burst_lift']:.1f} 倍）")
    else:
        b_verdict = (f"主体近似失效，如实披露：全流密度过高（全体中位邻接间隔 "
                     f"{gaps['全体中位邻接间隔_s']:.1f} 秒），{b['burst_clusters']}"
                     f"/{b['clusters']}（{b['burst_clusters'] / b['clusters']:.0%}）的簇都突破"
                     f" BR-14 阈值——该阈值设计对象是「单主体」1h≥10 笔而非全市场流，"
                     f"欺诈落入率 {b['fraud_in_burst']:.1%} 与正常 {b['normal_in_burst']:.1%} "
                     f"几乎无差（lift {b['burst_lift']:.2f}x）。本数据集不具备验证主体级 "
                     f"velocity 规则的条件，需带主体 ID 数据集（如 Sparkov/PaySim，后续工作）")
    lines += [
        f"- A 组：{a_verdict}；",
        f"- B 组：{b_verdict}；",
        "- 本次回放证明：骨架可消费公开数据集（284,807 笔全量、无标签泄漏、"
        "内核原权重原阈值直调），可映射维度的业务指标（PR/告警队列规模/基线对照）"
        "可解释可复核；局限与负结果如实披露；",
        "- 完整复现：`python scripts/dataset_replay.py`（数据下载见 `db/dataset/README.md`）。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="ULB creditcard 公开数据集回放")
    ap.add_argument("--csv", default="db/dataset/creditcard.csv")
    ap.add_argument("--out", default="docs/reports/dataset-replay.md")
    args = ap.parse_args()

    csv_path = REPO_ROOT / args.csv
    if not csv_path.is_file():
        print(f"数据文件不存在：{csv_path}（下载方式见 db/dataset/README.md）",
              file=sys.stderr)
        return 1

    rows = load_rows(csv_path)
    frauds = sum(k for _, _, k in rows)
    q = Quantiler([a for _, a, _ in rows])
    meta = {
        "rows": len(rows),
        "frauds": frauds,
        "rate": frauds / len(rows),
        "amount_normal_median": statistics.median(
            [a for _, a, k in rows if k == 0]),
        "amount_fraud_median": statistics.median(
            [a for _, a, k in rows if k == 1]),
    }
    gaps = nearest_gap_stats(rows)
    a = replay_a(rows, q)
    b = replay_b(rows, q)
    baseline = {
        th: amount_threshold_baseline(rows, th) for th in (10.0, 50.0, 100.0, 500.0)
    }

    out_path = REPO_ROOT / args.out
    out_path.write_text(render_md(meta, a, b, gaps, baseline), encoding="utf-8")

    print(f"rows={meta['rows']} frauds={meta['frauds']} ({meta['rate']:.3%})")
    print(f"A: AP={a['ap']:.4f} alert@BR01={a['alerts']} "
          f"P={a['precision']:.4f} R={a['recall']:.4f}")
    print(f"B: clusters={b['clusters']} burst={b['burst_clusters']} "
          f"AP={b['ap']:.4f} fraud_in_burst={b['fraud_in_burst']:.4f} "
          f"normal_in_burst={b['normal_in_burst']:.4f} lift={b['burst_lift']:.2f}x")
    print(f"report -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
