"""AA-MCP-02 外部数据服务 MCP 模拟（02 §5；04 §10.1 唯一允许的数据源模拟）
征信 / 舆情 / 投诉三类外部源的确定性模拟 + 防腐层字段翻译；query_reason 强制（BA-BR-10，E-REASON-REQUIRED）。
调用链路本身（HTTP→MCP→存储）与生产一致，仅响应内容为合成。
"""
import hashlib
import json
import os
import random

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tradeguard-external-mock", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8102")))


def _seed(subject: str) -> random.Random:
    return random.Random(int(hashlib.sha256(subject.encode()).hexdigest()[:8], 16))


def _require_reason(query_reason: str | None) -> dict | None:
    if not query_reason or not query_reason.strip():
        return {"code": "E-REASON-REQUIRED", "message": "外部源查询必须携带查询事由（BA-BR-10）"}
    return None


@mcp.tool()
async def query_credit_report(subject_id: str, query_reason: str | None = None) -> str:
    """API-M-07：征信报告模拟（防腐层翻译为统一结构）"""
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
async def query_complaints(subject_id: str, query_reason: str | None = None) -> str:
    """API-M-09：客服投诉记录模拟（持卡人否认交易 = Chargeback 前置信号，BA-BP-02）"""
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


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
