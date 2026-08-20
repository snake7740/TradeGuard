"""AA-MCP-02 外部数据服务 MCP 模拟（02 §5；04 §10.1 唯一允许的数据源模拟）
征信 / 舆情 / 投诉三类外部源的确定性模拟 + 防腐层字段翻译；query_reason 强制（BA-BR-10，E-REASON-REQUIRED）。
调用链路本身（HTTP→MCP→存储）与生产一致，仅响应内容为合成。
"""
import asyncio
import hashlib
import json
import os
import random
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tradeguard-external-mock", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8102")))  # nosec B104 —— 容器内必须绑全部接口，宿主暴露面由 compose 端口映射控制


def _seed(subject: str) -> random.Random:
    return random.Random(int(hashlib.sha256(subject.encode()).hexdigest()[:8], 16))  # nosec B311 —— mock 确定性回放种子，非安全用途


def _require_reason(query_reason: str | None) -> dict | None:
    if not query_reason or not query_reason.strip():
        return {"code": "E-REASON-REQUIRED", "message": "外部源查询必须携带查询事由（BA-BR-10）"}
    return None


@mcp.tool()
async def query_credit(subject_id: str, query_reason: str | None = None) -> str:
    """API-M-07：征信报告模拟（防腐层翻译为统一结构；工具名对齐契约 query_credit）"""
    if err := _require_reason(query_reason):
        return json.dumps(err, ensure_ascii=False)
    rnd = _seed("credit:" + subject_id)
    score = rnd.randint(350, 850)
    return json.dumps({
        "source": "credit-mock", "subject_id": subject_id,
        "credit_score": score,
        "risk_band": "high" if score < 520 else ("mid" if score < 680 else "low"),
        "overdue_count_12m": rnd.randint(0, 6),
        "query_reason": query_reason,
        "degraded": False,
    }, ensure_ascii=False)


@mcp.tool()
async def query_sentiment(subject_id: str, query_reason: str | None = None) -> str:
    """API-M-08：舆情监测模拟（命中即负面条目，供信号聚合）"""
    if err := _require_reason(query_reason):
        return json.dumps(err, ensure_ascii=False)
    rnd = _seed("sentiment:" + subject_id)
    hit = rnd.random() < 0.3
    return json.dumps({
        "source": "sentiment-mock", "subject_id": subject_id,
        "hits": [{
            "title": "网友曝料：某账户疑似参与跑分",
            "sentiment": "negative", "confidence": round(rnd.uniform(0.4, 0.9), 2),
        }] if hit else [],
        "query_reason": query_reason,
        "degraded": False,
    }, ensure_ascii=False)


@mcp.tool()
async def query_complaint(subject_id: str, query_reason: str | None = None) -> str:
    """API-M-09：客服投诉记录模拟（持卡人否认交易 = Chargeback 前置信号，BA-BP-02；工具名对齐契约 query_complaint）"""
    if err := _require_reason(query_reason):
        return json.dumps(err, ensure_ascii=False)
    rnd = _seed("complaint:" + subject_id)
    n = rnd.randint(0, 2)
    return json.dumps({
        "source": "complaint-mock", "subject_id": subject_id,
        "items": [{
            "type": "deny_transaction",
            "content": "持卡人否认该笔交易",
            "channel": rnd.choice(["phone", "app", "counter"]),
        } for _ in range(n)],
        "query_reason": query_reason,
        "degraded": False,
    }, ensure_ascii=False)


# ---------- F1 pyod_* 异常检测工具族（US-E12，docs/14 §1.4） ----------
# optional extras：pyod/numpy 未安装即白名单拒绝（E-TOOL-UNAVAILABLE），
# 主链路无感；输出仅建议分（advisory=true），不进入裁决（与 DA-INV-07 同精神）

def _pyod_detect(algo: str, values: list[float], contamination: float) -> dict[str, Any]:
    """pyod 三检测器统一入口：金额序列 → 异常索引+分数；依赖缺失抛出供上层白名单拒绝"""
    import numpy as np  # pyright: ignore[reportMissingImports] —— pyod 传递依赖，同属 optional extras
    x = np.asarray(values, dtype=float).reshape(-1, 1)
    if algo == "iforest":
        from pyod.models.iforest import IForest  # pyright: ignore[reportMissingImports]
        model = IForest(contamination=contamination, random_state=42)
    elif algo == "lof":
        from pyod.models.lof import LOF  # pyright: ignore[reportMissingImports]
        model = LOF(contamination=contamination)
    else:
        from pyod.models.ecod import ECOD  # pyright: ignore[reportMissingImports]
        model = ECOD(contamination=contamination)
    model.fit(x)
    labels = model.labels_.tolist()
    scores = [round(float(s), 4) for s in model.decision_scores_]
    return {"anomaly_indices": [i for i, v in enumerate(labels) if v == 1],
            "scores": scores, "advisory": True}


async def _pyod_tool(algo: str, values: list[float], query_reason: str | None,
                     contamination: float) -> str:
    if err := _require_reason(query_reason):
        return json.dumps(err, ensure_ascii=False)
    if len(values or []) < 5:
        return json.dumps({"code": "E-BAD-INPUT",
                           "message": "pyod 检测需 ≥5 个样本点"}, ensure_ascii=False)
    contamination = min(max(contamination, 0.01), 0.5)
    try:
        out = await asyncio.to_thread(_pyod_detect, algo, values, contamination)
    except ImportError:
        # 白名单拒绝：optional extras 未安装，工具不可用（主链路无感，14 §1.4）
        return json.dumps({"code": "E-TOOL-UNAVAILABLE",
                           "message": "pyod/numpy 未安装（optional extras），"
                                      "工具白名单拒绝，不影响主链路"}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 —— 检测异常降级，不抛裸错
        return json.dumps({"code": "E-TOOL-ERROR", "message": str(e)[:200]},
                          ensure_ascii=False)
    return json.dumps({"source": f"pyod-{algo}", "n": len(values),
                       "query_reason": query_reason, "degraded": False, **out},
                      ensure_ascii=False)


@mcp.tool()
async def pyod_iforest(values: list[float], query_reason: str | None = None,
                       contamination: float = 0.1) -> str:
    """F1 隔离森林检测（仅建议输出）：金额序列离群点，供调查研判参考"""
    return await _pyod_tool("iforest", values, query_reason, contamination)


@mcp.tool()
async def pyod_lof(values: list[float], query_reason: str | None = None,
                   contamination: float = 0.1) -> str:
    """F1 局部离群因子检测（仅建议输出）：局部密度异常，适合小额高频簇识别"""
    return await _pyod_tool("lof", values, query_reason, contamination)


@mcp.tool()
async def pyod_ecod(values: list[float], query_reason: str | None = None,
                    contamination: float = 0.1) -> str:
    """F1 经验累积分布检测（仅建议输出）：尾部概率异常，适合大额单笔识别"""
    return await _pyod_tool("ecod", values, query_reason, contamination)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
