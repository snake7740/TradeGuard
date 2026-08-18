"""AA-SK-01 确定性聚合内核（skills/AA-SK-01-signal-aggregation.md 的可执行实现）

纯函数层（单测目标，06 §3）：
  compute_velocity / velocity_bonus / build_tx_signal     —— BA-BR-14 频次特征
  normalize_*                                              —— 防腐层翻译（AA-MCP-02 → DA-T-04）
  dedupe_signals                                           —— 降噪合并（同 source+type 1h 窗口）
  score_signals                                            —— 加权评分（权重 02 §4，封顶 100）
  triage                                                   —— 分级裁决（BA-BR-01 边界）

编排层 AggregationService（US-E3-03/04，SC-01/SC-11 载体）：
  立案→聚合→落库（经 AA-MCP-01 record_case_signals，tg_app 写角色 DA-INV-05）
  →裁决路由：noise 降噪 ARCHIVED / auto_release 自动放行 DISPOSED（BA-CAP-05）
  / investigate 转调查 INVESTIGATING / all_fail 转人工（E-AGG-ALL-FAIL）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

from ..core.state_machine import CaseEvent, status_zh
from ..core.tracing import skill_span
from .mcp_adapters import remember

# ---------- 规则常量（可追溯：BA-BR-01/05/14，02 §4 AA-SK-01） ----------

SOURCE_WEIGHTS: dict[str, float] = {
    "tx": 0.4,
    "credit": 0.2,
    "complaint": 0.25,
    "sentiment": 0.15,
    "internal": 0.25,
}
VELOCITY_BONUS = 30  # BA-BR-14：1h≥10 笔或 24h≥50 笔 +30 分
VELOCITY_1H_THRESHOLD = 10  # BA-BR-14 阈值（正式值经 Nacos 热更新，TA-C-05）
VELOCITY_24H_THRESHOLD = 50
HIGH_FREQ_DAYS = 7  # BA-BR-05：同主体近 7 天风险事件窗口（可配置 br-05-window-days）
HIGH_FREQ_COUNT = 3  # BA-BR-05：窗口内 ≥3 次追加高频异常信号（可配置 br-05-case-count）
AUTO_SCORE_MAX = 40  # BA-BR-01：风险分 <40 可自动处置
AUTO_AMOUNT_MAX = 5000  # BA-BR-01：单笔涉案 <5000 元可自动处置
BLACK_FLAG_SCORE = 75  # BA-BR-04：黑名单主体立案即高风险（≥BA-BR-02 审批线 70，SC-04）
EXTERNAL_SOURCES = ("credit", "sentiment", "complaint")

ZERO_VELOCITY = {
    "velocity_1h": {"count": 0, "amount": 0.0},
    "velocity_24h": {"count": 0, "amount": 0.0},
}


class AggregationStateError(Exception):
    """案件当前状态不允许聚合（非 REGISTERED/AGGREGATING）"""

    code = "E-BAD-STATE"


# ---------- velocity 频次统计（BA-BR-14） ----------


def compute_velocity(txs: list[dict], now: datetime) -> dict:
    """近 1h/24h 交易笔数与金额（形状对齐 openapi VelocityFeature）"""
    c1 = c24 = 0
    a1 = a24 = 0.0
    for t in txs:
        age = now - t["ts"]
        amount = float(t["amount"])
        if age <= timedelta(hours=1):
            c1 += 1
            a1 += amount
        if age <= timedelta(hours=24):
            c24 += 1
            a24 += amount
    return {
        "velocity_1h": {"count": c1, "amount": a1},
        "velocity_24h": {"count": c24, "amount": a24},
    }


def velocity_bonus(
    velocity: dict,
    *,
    t1h: int = VELOCITY_1H_THRESHOLD,
    t24h: int = VELOCITY_24H_THRESHOLD,
    bonus: int = VELOCITY_BONUS,
) -> int:
    """BA-BR-14：1h≥t1h 笔或 24h≥t24h 笔 → +bonus 分
    （SC-06 闭环修复 D1：阈值可经关键字参数注入热值，缺省即代码常量）"""
    if (
        velocity["velocity_1h"]["count"] >= t1h
        or velocity["velocity_24h"]["count"] >= t24h
    ):
        return bonus
    return 0


# ---------- 四源信号构建（DA-T-04 Schema，query_reason 必填 BA-BR-10） ----------


def _signal(
    source: str,
    type_: str,
    confidence: float,
    query_reason: str,
    now: datetime,
    raw_ref: str | None = None,
    velocity_json: dict | None = None,
) -> dict:
    # 确定性 signal_id（阶段2，R-41）：同 source+type+raw_ref+query_reason → 同 id，
    # 使信号落库幂等（EventWorker 重试不重复落），对齐 DA-T-04 signal_id varchar(40)
    seed = f"{source}|{type_}|{raw_ref or ''}|{query_reason}"
    signal_id = hashlib.md5(seed.encode("utf-8"),
                            usedforsecurity=False).hexdigest()  # 幂等键非安全哈希
    return {
        "signal_id": signal_id,
        "source": source,
        "type": type_,
        "confidence": round(float(confidence), 2),
        "raw_ref": raw_ref,
        "query_reason": query_reason,
        "degraded": False,
        "velocity_json": velocity_json,
        "ts": now,
    }


def build_tx_signal(
    case_id: str,
    velocity: dict,
    query_reason: str,
    now: datetime,
    *,
    t1h: int = VELOCITY_1H_THRESHOLD,
    t24h: int = VELOCITY_24H_THRESHOLD,
    bonus: int = VELOCITY_BONUS,
) -> dict | None:
    """tx 源仅在 velocity 突破时产出信号（低频正常流水不放大评分）；
    velocity_json 为 tx 源必填（DA-T-04，SC-11 断言载体）。"""
    if velocity_bonus(velocity, t1h=t1h, t24h=t24h, bonus=bonus) == 0:
        return None
    c1 = velocity["velocity_1h"]["count"]
    c24 = velocity["velocity_24h"]["count"]
    confidence = min(1.0, max(c1 / 20, c24 / 100))
    return _signal(
        "tx",
        "tx_velocity_burst",
        confidence,
        query_reason,
        now,
        raw_ref=f"case={case_id}:tx-cluster",
        velocity_json=velocity,
    )


def normalize_credit_report(
    payload: dict, case_id: str, query_reason: str, now: datetime
) -> list[dict]:
    """防腐层：征信模拟载荷 → 标准信号（high 段 0.8 / mid 段 0.45 / low 无信号）"""
    if payload.get("degraded") or payload.get("code") or "risk_band" not in payload:
        return []
    band = payload["risk_band"]
    if band == "high":
        return [
            _signal(
                "credit",
                "credit_band_high",
                0.8,
                query_reason,
                now,
                raw_ref=f"credit:{payload.get('credit_score')}",
            )
        ]
    if band == "mid":
        return [
            _signal(
                "credit",
                "credit_band_mid",
                0.45,
                query_reason,
                now,
                raw_ref=f"credit:{payload.get('credit_score')}",
            )
        ]
    return []


def normalize_sentiment(
    payload: dict, case_id: str, query_reason: str, now: datetime
) -> list[dict]:
    """防腐层：舆情命中条目 → 负面舆情信号（confidence 透传）"""
    if payload.get("degraded") or payload.get("code"):
        return []
    return [
        _signal(
            "sentiment",
            "sentiment_negative",
            h.get("confidence", 0.5),
            query_reason,
            now,
            raw_ref=f"sentiment:{i}",
        )
        for i, h in enumerate(payload.get("hits", []))
        if h.get("sentiment") == "negative"
    ]


def normalize_complaints(
    payload: dict, case_id: str, query_reason: str, now: datetime
) -> list[dict]:
    """防腐层：投诉条目 → 否认交易信号（Chargeback 前置，BA-BP-02；固定置信 0.9）"""
    if payload.get("degraded") or payload.get("code"):
        return []
    return [
        _signal(
            "complaint",
            "complaint_deny_transaction",
            0.9,
            query_reason,
            now,
            raw_ref=f"complaint:{i}",
        )
        for i, item in enumerate(payload.get("items", []))
        if item.get("type") == "deny_transaction"
    ]


# ---------- 降噪合并（AA-SK-01 步骤 3） ----------


def dedupe_signals(signals: list[dict]) -> list[dict]:
    """同 (source, type, 1h 窗口) 重复信号合并为 1 条：confidence 取 max，count 累计入 raw_ref"""
    buckets: dict[tuple, dict] = {}
    counts: dict[tuple, int] = {}
    for s in signals:
        key = (
            s["source"],
            s["type"],
            s["ts"].replace(minute=0, second=0, microsecond=0),
        )
        counts[key] = counts.get(key, 0) + 1
        cur = buckets.get(key)
        if cur is None or s["confidence"] > cur["confidence"]:
            buckets[key] = dict(s)
    out = []
    for key, s in buckets.items():
        if counts[key] > 1:
            s["raw_ref"] = f"{s.get('raw_ref') or ''}|merged×{counts[key]}"
        out.append(s)
    return sorted(out, key=lambda s: s["ts"])


# ---------- 加权评分（AA-SK-01 步骤 5） ----------


def score_signals(
    signals: list[dict],
    velocity: dict,
    *,
    t1h: int = VELOCITY_1H_THRESHOLD,
    t24h: int = VELOCITY_24H_THRESHOLD,
    bonus: int = VELOCITY_BONUS,
) -> int:
    """基础分 = Σ(confidence × 源权重) × 100 + velocity 加分，封顶 100"""
    base = (
        sum(s["confidence"] * SOURCE_WEIGHTS.get(s["source"], 0.0) for s in signals)
        * 100
    )
    return min(
        100,
        int(
            math.floor(
                base + velocity_bonus(velocity, t1h=t1h, t24h=t24h, bonus=bonus) + 0.5
            )
        ),
    )


# ---------- 分级裁决（US-E3-04，BA-BR-01 边界） ----------


def triage(
    risk_score: int,
    amount: float,
    signals: list[dict],
    *,
    auto_score_max: int = AUTO_SCORE_MAX,
    auto_amount_max: int = AUTO_AMOUNT_MAX,
) -> str:
    """裁决路由：零信号降噪 / 低风险小额自动放行（BA-CAP-05）/ 其余转调查
    （SC-06 闭环修复 D1：两条边界可经关键字参数注入热值，缺省即代码常量）"""
    if not signals:
        return "noise"
    if risk_score < auto_score_max and amount < auto_amount_max:
        return "auto_release"
    return "investigate"


# ---------- 编排层（US-E3-03/04，SC-01/SC-11 载体） ----------


class AggregationService:
    """AA-SK-01 聚合流水线编排：采集→标准化→降噪→评分→落库→裁决。

    依赖全部注入（端口/适配器）：pool（tg_web 读路径与状态迁移）、
    cases（CaseRepository 状态机写模板）、external（AA-MCP-02）、core（AA-MCP-01）。
    """

    ACTOR_AGG = "agent:AA-AG-02"  # 信号聚合 Agent（02 §3）
    ACTOR_DISP = "AA-AG-04"  # 处置执行 Agent（SC-01 审计操作者）

    def __init__(
        self,
        pool,
        cases,
        external,
        core,
        retry: int = 2,
        timeout: float = 5.0,
        config=None,
        scorer=None,
    ):
        self.pool = pool
        self.cases = cases
        self.external = external
        self.core = core
        self.retry = retry  # AA-SK-01 失败处理：单源超时重试 2 次→降级
        self.timeout = timeout
        self.config = config  # ConfigService（BA-BR-05 阈值热加载，可缺省用常量）
        if scorer is None:  # 阶段3 接线（R-42）：规则 baseline，可注入模型版
            from app.core.scoring import RuleRiskScorer

            scorer = RuleRiskScorer()
        self.scorer = scorer

    def _cfg_int(self, key: str, default: int) -> int:
        """从 ConfigService 读整型阈值，未配置/非法值回退代码常量"""
        if self.config is None:
            return default
        try:
            return int(self.config.values[key])
        except (KeyError, TypeError, ValueError):
            return default

    def _thresholds(self) -> dict:
        """SC-06 阈值热值快照（D1）：每次聚合实时读取，变更不重启生效。
        键与 db/init/01-schema.sql 种子、scripts/nacos_register.py THRESHOLDS 同源。"""
        return {
            "t1h": self._cfg_int("br-14-velocity-1h-count", VELOCITY_1H_THRESHOLD),
            "t24h": self._cfg_int("br-14-velocity-24h-count", VELOCITY_24H_THRESHOLD),
            "bonus": self._cfg_int("br-14-velocity-bonus", VELOCITY_BONUS),
            "auto_score_max": self._cfg_int("br-01-mid-review-score", AUTO_SCORE_MAX),
            "auto_amount_max": self._cfg_int(
                "br-01-auto-amount-limit", AUTO_AMOUNT_MAX
            ),
            "black_flag": self._cfg_int("br-04-black-flag-score", BLACK_FLAG_SCORE),
        }

    async def run(self, case_id: str) -> dict:
        async with skill_span("AA-SK-01", "AA-AG-02", case_id):
            result = await self._run(case_id)
        await remember(
            self.core,
            case_id,
            "AA-AG-02",
            "aggregation",
            {
                "route": result["route"],
                "risk_score": result["risk_score"],
                "signals_count": result["signals_count"],
                "degraded_sources": result["degraded_sources"],
            },
        )
        return result

    async def _run(self, case_id: str) -> dict:
        case = await self.pool.fetchrow(
            "SELECT case_id, subject_ref, status, version, context_json FROM risk_case WHERE case_id=$1",
            case_id,
        )
        if not case:
            raise LookupError(case_id)
        version = case["version"]
        if case["status"] == "REGISTERED":
            out = await self.cases.transition(
                case_id, CaseEvent.AGGREGATION_STARTED, self.ACTOR_AGG, version
            )
            version = out["version"]
        elif case["status"] != "AGGREGATING":
            raise AggregationStateError(
                f"案件当前处于「{status_zh(case['status'])}」状态，不支持信号聚合，请刷新页面查看最新进展"
            )

        reason = f"case={case_id} 风险信号聚合（BA-BR-10 查询事由）"
        now = datetime.now(timezone.utc)
        degraded: list[str] = []
        th = self._thresholds()  # SC-06 热值（D1）：本轮聚合内一致

        # 1. 采集（单源失败降级不中断，AA-SK-01 失败处理）
        try:
            txs = await self._fetch_tx(case["subject_ref"])
        except Exception:
            txs, degraded = [], degraded + ["tx"]
        raw: dict[str, dict] = {}
        for name, method in (
            ("credit", self.external.query_credit_report),
            ("sentiment", self.external.query_sentiment),
            ("complaint", self.external.query_complaints),
        ):
            try:
                raw[name] = await self._fetch_external(
                    method, case["subject_ref"], reason
                )
            except Exception:
                degraded.append(name)

        # 2-3. 标准化 + 降噪合并
        velocity = compute_velocity(txs, now)
        signals: list[dict] = []
        if "tx" not in degraded:
            tx_sig = build_tx_signal(
                case_id,
                velocity,
                reason,
                now,
                t1h=th["t1h"],
                t24h=th["t24h"],
                bonus=th["bonus"],
            )
            if tx_sig:
                signals.append(tx_sig)
        for name, normalize in (
            ("credit", normalize_credit_report),
            ("sentiment", normalize_sentiment),
            ("complaint", normalize_complaints),
        ):
            if name in degraded:
                continue
            signals.extend(normalize(raw[name], case_id, reason, now))
        signals = dedupe_signals(signals)

        # 全源失败 → E-AGG-ALL-FAIL 转人工（不推进状态）
        if len(degraded) == 4 and not signals:
            await self.pool.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, $2, 'signals.all_fail', $3, 'E-AGG-ALL-FAIL 全源失败转人工（AA-SK-01）',
                           (SELECT trace_id FROM risk_case WHERE case_id=$4))""",
                uuid.uuid4().hex,
                self.ACTOR_AGG,
                case_id,
                case_id,
            )
            return {
                "case_id": case_id,
                "status": "AGGREGATING",
                "route": "all_fail",
                "risk_score": 0,
                "velocity": ZERO_VELOCITY,
                "signals": [],
                "signals_count": 0,
                "degraded_sources": degraded,
                "exec_id": None,
            }

        # 4. BA-BR-05：同主体近 N 天风险事件 ≥M 次（排除当前案件）→ 追加高频异常信号
        window_days = self._cfg_int("br-05-window-days", HIGH_FREQ_DAYS)
        count_threshold = self._cfg_int("br-05-case-count", HIGH_FREQ_COUNT)
        hist = await self.pool.fetchval(
            """SELECT count(*) FROM risk_case
               WHERE subject_ref=$1 AND case_id<>$2
                 AND created_at >= now() - make_interval(days=>$3)""",
            case["subject_ref"],
            case_id,
            window_days,
        )
        if hist >= count_threshold:
            signals.append(
                _signal(
                    "internal",
                    "high_freq_case",
                    min(1.0, hist / (count_threshold * 2)),
                    f"case={case_id} BA-BR-05 同主体近 {window_days} 天风险事件 {hist} 次≥{count_threshold}",
                    now,
                    raw_ref=f"risk_case:{case['subject_ref'][:16]}:recent={hist}",
                )
            )

        # 5-6. 评分 + 落库（经 AA-MCP-01，tg_app 写角色 DA-INV-05）
        _scored = self.scorer.score(
            signals, velocity, t1h=th["t1h"], t24h=th["t24h"], bonus=th["bonus"]
        )
        score = _scored["score"]
        # BA-BR-04（SC-04）：黑名单主体立案即高风险，处置建议拦截，
        # 无论金额均经 BA-BR-02 审批门控入人工通道（评分垫高至 ≥审批线）
        black = await self.pool.fetchval(
            "SELECT list_flag FROM account WHERE account_hash=$1", case["subject_ref"]
        )
        recommended_action = None
        if black == "black":
            score = max(score, th["black_flag"])
            recommended_action = "block"
            await self.pool.execute(
                """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                   VALUES ($1, $2, 'signals.black_flag', $3,
                           'list_flag=black 立案即高风险，处置建议=block（BA-BR-04，SC-04）',
                           (SELECT trace_id FROM risk_case WHERE case_id=$4))""",
                uuid.uuid4().hex,
                self.ACTOR_AGG,
                case_id,
                case_id,
            )
        # internal 源信号仅参与评分与响应，不落 DA-T-04（risk_signal.source CHECK 仅允许
        # tx/credit/sentiment/complaint，01-schema.sql；历史频次可由 risk_case 复算）
        await self.core.record_case_signals(
            case_id, score, [s for s in signals if s["source"] != "internal"]
        )

        # 7. 裁决路由
        amount = velocity["velocity_24h"]["amount"]
        route = triage(
            score,
            amount,
            signals,
            auto_score_max=th["auto_score_max"],
            auto_amount_max=th["auto_amount_max"],
        )
        # BA-BR-07：驳回回滚禁用自动通道后，同档低风险也不再自动放行，转调查
        ctx = case["context_json"]
        if isinstance(ctx, str):
            ctx = json.loads(ctx or "{}")
        if route == "auto_release" and (ctx or {}).get("auto_channel") == "disabled":
            route = "investigate"
        if recommended_action and route == "noise":
            route = "investigate"  # 黑名单主体不得降噪归档，必须入人工通道（SC-04）
        if route == "noise":
            out = await self.cases.transition(
                case_id,
                CaseEvent.NOISE_DISMISSED,
                self.ACTOR_AGG,
                version,
                basis="零信号降噪放行（AA-SK-01 步骤 6）",
            )
            return self._result(
                case_id, out["status"], route, score, velocity, signals, degraded
            )
        if route == "auto_release":
            out = await self.cases.transition(
                case_id,
                CaseEvent.DISPOSITION_SUBMITTED,
                self.ACTOR_DISP,
                version,
                basis=f"risk_score={score} amount={amount:.2f} 自动通道准入（BA-BR-01）",
            )
            version = out["version"]
            try:
                exec_id = await self._auto_release(case_id, amount)
            except RuntimeError as e:
                # 失败兜底（B1）：审计 + DispositionFailed→MANUAL_REVIEW，案件不卡死 DISPOSING
                await self.pool.execute(
                    """INSERT INTO audit_log (log_id, actor, action, target, basis, trace_id)
                       VALUES ($1, $2, 'disposition.failed', $3, $4,
                               (SELECT trace_id FROM risk_case WHERE case_id=$3))""",
                    uuid.uuid4().hex,
                    self.ACTOR_DISP,
                    case_id,
                    f"自动放行失败升级人工复核：{e}（BA-BR-01 失败兜底）",
                )
                out = await self.cases.transition(
                    case_id,
                    CaseEvent.DISPOSITION_FAILED,
                    self.ACTOR_DISP,
                    version,
                    basis=f"自动放行失败转人工复核：{e}",
                )
                return self._result(
                    case_id,
                    out["status"],
                    "failed_manual",
                    score,
                    velocity,
                    signals,
                    degraded,
                )
            out = await self.cases.transition(
                case_id,
                CaseEvent.DISPOSITION_EXECUTED,
                self.ACTOR_DISP,
                version,
                basis=f"risk_score={score} amount={amount:.2f} action=release 低风险自动放行（BA-CAP-05，SC-01）",
            )
            result = self._result(
                case_id, out["status"], route, score, velocity, signals, degraded
            )
            result["exec_id"] = exec_id
            return result
        out = await self.cases.transition(
            case_id,
            CaseEvent.SIGNALS_AGGREGATED,
            self.ACTOR_AGG,
            version,
            basis=f"risk_score={score} 转调查（中/高风险分段）",
        )
        result = self._result(
            case_id, out["status"], route, score, velocity, signals, degraded
        )
        if recommended_action:
            result["recommended_action"] = (
                recommended_action  # SC-04 处置建议随裁决输出
            )
        return result

    async def _fetch_tx(self, subject_ref: str) -> list[dict]:
        """AA-MCP-01 query_transactions 等价读路径（近 24h 流水）"""
        rows = await self.pool.fetch(
            """SELECT amount, ts FROM transaction
               WHERE account_hash=$1 AND ts >= now() - interval '24 hours'""",
            subject_ref,
        )
        return [{"amount": r["amount"], "ts": r["ts"]} for r in rows]

    async def _fetch_external(self, method, subject: str, reason: str) -> dict:
        """外部源调用：超时控制 + 重试（AA-SK-01：5s 超时重试 2 次→降级）"""
        last: Exception | None = None
        for _ in range(self.retry + 1):
            try:
                return await asyncio.wait_for(method(subject, reason), self.timeout)
            except Exception as e:  # noqa: BLE001 —— 单源失败一律降级，不中断主链路
                last = e
        raise last  # type: ignore[misc]

    async def _auto_release(self, case_id: str, amount: float) -> str:
        """经 AA-MCP-01 execute_disposition 落 DA-T-06（幂等键 DA-INV-03）。

        幂等重投按成功处理（复用首次凭证）；瞬时错误/网络异常重试 1 次（0.3s），
        确定性拒绝与重试耗尽 raise RuntimeError，由调用方转人工复核（B1）。
        """
        result = {"code": "E-MCP-UNAVAILABLE", "message": "mcp-core 不可达"}
        for attempt in range(2):
            try:
                result = await self.core.execute_disposition(
                    case_id=case_id,
                    action="release",
                    amount=None,
                    idempotency_key=f"{case_id}:auto-release",
                )
            except Exception as e:  # noqa: BLE001 —— 网络/会话异常 → 重试
                result = {"code": "E-MCP-UNAVAILABLE", "message": str(e)}
            else:
                if result.get("code") == "E-IDEMPOTENT-CONFLICT":
                    return result["first_result"]["exec_id"]  # 幂等重放：复用首次凭证
                if not result.get("code"):
                    return result["exec_id"]
                if result["code"] in (
                    "E-DISP-AUTH",
                    "E-DISP-SCOPE",
                    "E-EVIDENCE-MISSING",
                ):
                    raise RuntimeError(f"自动放行处置被拒：{result}")
            if attempt == 0:
                await asyncio.sleep(0.3)
        raise RuntimeError(f"自动放行处置失败：{result}")

    @staticmethod
    def _result(
        case_id: str,
        status: str,
        route: str,
        score: int,
        velocity: dict,
        signals: list[dict],
        degraded: list[str],
    ) -> dict:
        return {
            "case_id": case_id,
            "status": status,
            "route": route,
            "risk_score": score,
            "velocity": velocity,
            "signals": signals,
            "signals_count": len(signals),
            "degraded_sources": degraded,
            "exec_id": None,
        }
