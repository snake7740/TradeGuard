# TradeGuard · Transaction Risk-Control Hub

A multi-agent system for credit-card / payment fraud detection and automated disposition.

**[中文文档](./README.md)** · OpenAPI contract: [`docs/openapi/tradeguard-openapi.yaml`](./docs/openapi/tradeguard-openapi.yaml)

## The Problem

Transaction fraud has shifted from single-transaction attacks to industrialized rings — card testing, account takeover, and money-mule laundering are invisible to rule engines that only see one transaction at a time. Signals are scattered across streams, credit bureaus, public opinion, and complaints; analysts drown in alerts; dispositions lack audit trails.

TradeGuard closes the loop with five stages driven by cooperating agents:

```
Signal Aggregation → Root-Cause Investigation → Disposition Execution → Verification & Audit → Knowledge Sedimentation
```

Low-risk cases are auto-disposed with zero human touch; high-risk cases require evidence-based investigation and human approval; **every disposition leaves a compliant audit trail**.

## Architecture

| Layer | Component | Role |
| --- | --- | --- |
| Multi-agent | AgentTeams (Manager + 4 Workers) | Task decomposition / context passing / cooperative execution |
| Backend | FastAPI (`web-api`) | 12-state case machine + 5 deterministic skill cores, 22 REST paths |
| Business MCP | `mcp-core` (12 tools) | The **only** disposition execution channel (approval gate + idempotency) |
| External MCP | `mcp-external-mock` (3 tools) | Credit / public-opinion / complaints (deterministic simulation) |
| Frontend | Vue 3 + Element Plus | 5 pages × 4 roles human-in-the-loop console |
| Storage | PostgreSQL (pgvector) | Business + vector + audit in one |
| Events | RocketMQ | Best-effort event-driven fan-out |
| Config / Gateway | Nacos + Higress | Hot-reloaded thresholds + unified AI gateway |
| Observability | AgentScope Studio | OTLP trace visualization |
| LLM | DashScope (Qwen) | Semantic RAG + hypothesis ranking (**optional, degrades gracefully without a key**) |

### Human-machine boundary (by design)

The 12-state machine guards every human-only transition (`REVIEW_CONFIRMED`, `APPROVAL_*`, …): agents physically cannot approve a disposition or archive a case — the API returns `409 E-HUMAN-ONLY` if an `agent:` operator attempts it. The four console roles are: risk on-call, risk approver, compliance auditor, and strategy admin.

## Quick Start (Windows + Docker Desktop)

```powershell
git clone <repo-url> TradeGuard
cd TradeGuard
.venv\Scripts\python scripts\start_all.py
```

The script bootstraps credentials (random strong secrets when missing), brings up the full stack, probes every service, restores/creates demo data, rebuilds gateway routes, and runs an end-to-end smoke check — exit 0 means all green.

- Console: <http://localhost:8300> (switch the 4 roles in the top bar)
- OpenAPI docs: <http://localhost:8200/docs>

The **core loop runs deterministically without any LLM key**. Configure `secrets/dashscope.env` (template `*.example` committed, real file gitignored) only for semantic RAG, LLM hypothesis ranking, or the 5-agent cooperative mode.

### Demo scenarios

- **D1 low-risk auto-release**: zero human touch, EventWorker drives to DISPOSED.
- **D2 investigate → freeze with human approval**: high risk → agent investigation → human submits disposition request (`API-W-23`) → approver decides → execution → verification → archive.
- **D3 false-positive rollback**: fault injection → verification mismatch → inverse disposition → manual appeal.

## Security Baseline (R-37)

- `.env` / `secrets/` never committed; only `CHANGE_ME` templates in-repo
- All host ports bound to `127.0.0.1` only
- Every `/api` call requires `Authorization: Bearer <TG_API_TOKEN>` (only `/api/health` exempt; portal nginx injects the token transparently)
- Constant-time token comparison; audit logging on every write, including denials

## Repository Map

| Looking for | Where |
| --- | --- |
| Full design docs (4A architecture, Chinese) | [`docs/00-总则.md`](./docs/00-总则.md) → 01–09 |
| Backend code | `services/web-api/app/` (entry `main.py`) |
| Disposition execution | `services/mcp-core/server.py` |
| Frontend pages | `web-portal/src/views/` |
| Database schema | `db/init/01-schema.sql` |
| One-click startup | `scripts/start_all.py` |
| Demo playbook | `scripts/demo_playbook.py` |

## Testing

```powershell
cd services\web-api
..\..\.venv\Scripts\python -m pytest tests -q
```

155+ tests across 19 files drive the real stack (PostgreSQL on 5433 + live MCP servers), including four-role business-flow integration tests (`test_multi_role_flow.py`: fraud-confirmation chain, false-positive release, human-only boundary guards, investigation→approval handoff).

## License

[Apache-2.0](./LICENSE)
