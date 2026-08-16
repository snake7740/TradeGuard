"""LLM / Embedding 端口适配器（阶段 1，R-40）

baseline（无外部依赖，可单测）：
- HashEmbeddingProvider：封装 knowledge.hash_embedding（字符三元组哈希 → 1024 维）
- RuleHypothesisRanker：封装 investigation.match_hypothesis（规则兜底假设匹配）

LLM 实现（可选，需 DashScope Key，经 Higress 凭据透传，04 §4.1/§5）：
- LlmClient：httpx 直调 DashScope OpenAI 兼容接口（chat.completions / embeddings）
- DashScopeEmbeddingProvider / LlmHypothesisRanker：无 Key 或调用失败时降级 baseline，
  人机边界不变（LLM 只建议、不做决策，02 §3.3）。
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger("tradeguard.llm")


def _project_root() -> str:
    """app/core/llm_adapters.py → 项目根（向上 4 层）"""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
    )


def _load_dashscope_creds() -> tuple[str | None, str | None]:
    """加载 DashScope 凭据（阶段1 修复：真实 Key 在 secrets/dashscope.env 文件里，非环境变量）。

    优先级：环境变量（CI/容器注入）→ secrets/dashscope.env（本地真实凭据）。
    base_url 用 OpenAI 兼容 endpoint（AGENTTEAMS_OPENAI_BASE_URL=/compatible-mode/v1，
    实测 HTTP 200）；DASHSCOPE_BASE_URL（/api/v1 百炼原生）不兼容 /chat/completions。
    """
    api_key = os.getenv("DASHSCOPE_API_KEY")
    base_url = os.getenv("AGENTTEAMS_OPENAI_BASE_URL") or os.getenv(
        "DASHSCOPE_BASE_URL"
    )
    secrets_file = os.path.join(_project_root(), "secrets", "dashscope.env")
    if os.path.exists(secrets_file):
        try:
            vals: dict[str, str] = {}
            with open(secrets_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        vals[k.strip()] = v.strip()
            api_key = api_key or vals.get("DASHSCOPE_API_KEY")
            base_url = (
                base_url
                or vals.get("AGENTTEAMS_OPENAI_BASE_URL")
                or vals.get("DASHSCOPE_BASE_URL")
            )
        except OSError:  # 无 secrets 文件/不可读 → 回落 None 降级 baseline
            pass
    return api_key, base_url


# 凭据经环境变量注入或 secrets/dashscope.env 加载，绝不落代码；未配置即 None → 降级 baseline。
DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL = _load_dashscope_creds()
DASHSCOPE_EMBED_MODEL = os.getenv("DASHSCOPE_EMBED_MODEL", "text-embedding-v3")


class HashEmbeddingProvider:
    """哈希版向量化（baseline）：EmbeddingProvider 端口实例（确定性、无外部依赖）"""

    async def embed(self, text: str) -> list[float]:
        from app.skills.knowledge import hash_embedding

        return hash_embedding(text)


class RuleHypothesisRanker:
    """规则版假设排序（baseline）：HypothesisRanker 端口实例"""

    async def rank(
        self,
        signals: list[dict],
        graph_edge_types: set[str],
        kb_hints: list[str] | None = None,
    ) -> dict:
        from app.skills.investigation import match_hypothesis

        pattern, basis = match_hypothesis(signals, graph_edge_types)
        return {
            "pattern": pattern,
            "basis": basis,
            "confidence": 1.0 if pattern != "待定" else 0.0,
            "source": "rule",
        }


class LlmClient:
    """DashScope OpenAI 兼容接口最小客户端（httpx，无新增重依赖）"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        chat_model: str = "qwen3.8-max",
    ):
        self.api_key = api_key if api_key is not None else DASHSCOPE_API_KEY
        # 安全默认值：无凭据时 base_url 不落空（避免 None.rstrip 崩溃），available=False 降级 baseline
        self.base_url = (
            base_url
            or DASHSCOPE_BASE_URL
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.chat_model = chat_model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.chat_model,
                    "messages": messages,
                    "temperature": temperature,
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async def embed(self, text: str) -> list[float]:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": DASHSCOPE_EMBED_MODEL, "input": text},
            )
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]


class DashScopeEmbeddingProvider:
    """语义向量化（LLM 版）：DashScope text-embedding-v3；无 Key/失败降级哈希"""

    def __init__(
        self,
        client: LlmClient | None = None,
        fallback: HashEmbeddingProvider | None = None,
    ):
        self.client = client or LlmClient()
        self.fallback = fallback or HashEmbeddingProvider()

    async def embed(self, text: str) -> list[float]:
        if not self.client.available:
            return await self.fallback.embed(text)
        try:
            return await self.client.embed(text)
        except Exception:  # noqa: BLE001 —— 语义 embedding 失败降级哈希，不阻断检索
            logger.warning("DashScope embedding 失败，降级哈希")
            return await self.fallback.embed(text)


class LlmHypothesisRanker:
    """LLM 版假设排序：生成假设 + 可审计推理链；无 Key/失败降级规则"""

    def __init__(
        self,
        client: LlmClient | None = None,
        fallback: RuleHypothesisRanker | None = None,
    ):
        self.client = client or LlmClient()
        self.fallback = fallback or RuleHypothesisRanker()

    async def rank(
        self,
        signals: list[dict],
        graph_edge_types: set[str],
        kb_hints: list[str] | None = None,
    ) -> dict:
        if not self.client.available:
            return await self.fallback.rank(signals, graph_edge_types, kb_hints)
        try:
            return await self._llm_rank(signals, graph_edge_types, kb_hints)
        except Exception:  # noqa: BLE001 —— LLM 失败降级规则，人机边界不变
            logger.warning("LLM 假设排序失败，降级规则")
            return await self.fallback.rank(signals, graph_edge_types, kb_hints)

    async def _llm_rank(self, signals, graph_edge_types, kb_hints) -> dict:
        prompt = (
            "你是金融交易风控调查员。根据风险信号与关联图谱，判断最可能的欺诈手法"
            "（跑分/盗卡/团伙盗刷/待定），给出可审计依据与置信度。只返回 JSON。\n"
            "信号：" + json.dumps(signals, ensure_ascii=False) + "\n"
            "图谱边：" + json.dumps(sorted(graph_edge_types), ensure_ascii=False) + "\n"
            "知识库提示：" + (kb_hints or "无") + "\n"
            '返回格式：{"pattern":"手法","basis":"依据","confidence":0.0~1.0}'
        )
        raw = await self.client.chat(
            [
                {"role": "system", "content": "你是严谨的风控调查员，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )
        # 容忍 markdown 代码块包裹：用正则提取首个 JSON 对象
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            raw = m.group(0)
        data = json.loads(raw)
        return {
            "pattern": str(data.get("pattern", "待定")),
            "basis": str(data.get("basis", "")),
            "confidence": float(data.get("confidence", 0.0)),
            "source": "llm",
            "reasoning": str(data.get("reasoning", data.get("basis", ""))),
        }


__all__ = [
    "HashEmbeddingProvider",
    "RuleHypothesisRanker",
    "LlmClient",
    "DashScopeEmbeddingProvider",
    "LlmHypothesisRanker",
]
