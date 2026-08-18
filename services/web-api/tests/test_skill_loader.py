# -*- coding: utf-8 -*-
"""Skill 运行时装载器测试（API-W-24 消费侧，app/skills/loader.py）

覆盖：frontmatter 扁平解析（含缺块报错）、entrypoint→模块名映射与非法
形态、真实 5 包全量装载（loadable=True）、坏包/目录缺失留痕不抛出、
API 端点契约（列表/详情/404 信封，经 TestClient 走真实 lifespan）。
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.skills.loader import (
    _entrypoint_module,
    load_all,
    load_skill,
    parse_frontmatter,
    skills_dir,
)
from conftest import PG_DSN


def _write_pack(path, **overrides):
    """写一个最小合法 skill 包（默认 12 键齐全），overrides 可覆盖/删键"""
    fm = {
        "name": path.stem, "version": "1.5.0", "description": "测试包",
        "agent": "AA-AG-02",
        "entrypoint": "services/web-api/app/skills/aggregation.py",
        "depends-mcp": "record_case_evidence, apply_risk_bonus",
        "depends-tables": "risk_case, risk_signal",
        "tests": "services/web-api/tests/test_aggregation.py",
        "test-cases": "22",
        "degradation-paths": "外部源失败降级记录, 阈值缺失走代码缺省",
    }
    fm.update(overrides)
    fm = {k: v for k, v in fm.items() if v is not None}  # None = 删键
    lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    path.write_text(f"---\n{lines}\n---\n\n# 正文\n", encoding="utf-8")
    return path


# ---------- frontmatter 解析与 entrypoint 映射 ----------

def test_parse_frontmatter_flat_keys(tmp_path):
    md = _write_pack(tmp_path / "AA-SK-XX-demo.md")
    fm = parse_frontmatter(md)
    assert fm["name"] == "AA-SK-XX-demo"
    assert fm["test-cases"] == "22"
    assert fm["depends-mcp"] == "record_case_evidence, apply_risk_bonus"


def test_parse_frontmatter_missing_block_raises(tmp_path):
    p = tmp_path / "no-frontmatter.md"
    p.write_text("只有正文", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        parse_frontmatter(p)


def test_entrypoint_module_mapping_and_illegal_forms():
    assert _entrypoint_module(
        "services/web-api/app/skills/aggregation.py") == "app.skills.aggregation"
    for bad in ("docs/foo.md", "services/other/x.py", "services/web-api/x.txt"):
        with pytest.raises(ValueError):
            _entrypoint_module(bad)


# ---------- 真实包装载 ----------

def test_load_skill_real_pack_loadable():
    """SK-01 真实包：frontmatter 齐全 + entrypoint 可导入 → loadable=True"""
    spec = load_skill(skills_dir() / "AA-SK-01-signal-aggregation.md")
    assert spec.loadable is True, spec.error
    assert spec.agent == "AA-AG-02"
    assert spec.test_cases == 22
    assert "record_case_signals" in spec.depends_mcp
    assert "risk_case" in spec.depends_tables
    assert spec.degradation_paths            # 降级路径可被第三方消费


def test_load_all_returns_five_loadable_packs():
    """仓库 5 个 skill 包全部装载成功（零漂移 = CI 校验器的运行时镜像）"""
    specs = load_all()
    assert [s.name for s in specs] == [
        "AA-SK-01-signal-aggregation", "AA-SK-02-fraud-investigation",
        "AA-SK-03-disposition-execution", "AA-SK-04-compliance-audit",
        "AA-SK-05-knowledge-sedimentation",
    ]
    assert all(s.loadable for s in specs), [s.error for s in specs]


# ---------- 坏包/目录缺失留痕不抛出 ----------

def test_bad_pack_leaves_trace_not_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_SKILLS_DIR", str(tmp_path))
    _write_pack(tmp_path / "AA-SK-BAD-1.md", **{"depends-tables": None})  # 删键
    spec = load_skill(tmp_path / "AA-SK-BAD-1.md")
    assert spec.loadable is False
    assert "depends-tables" in spec.error          # 留痕指向缺口

    _write_pack(tmp_path / "AA-SK-BAD-2.md",
                entrypoint="services/web-api/app/skills/no_such_mod.py")
    specs = load_all()
    assert len(specs) == 2 and all(not s.loadable for s in specs)

    monkeypatch.setenv("TG_SKILLS_DIR", str(tmp_path / "nope"))
    assert load_all() == []                         # 目录缺失 → 空注册表


# ---------- API 端点契约 ----------

@pytest.fixture(scope="module")
def client():
    os.environ["PG_DSN"] = PG_DSN
    os.environ.pop("TG_API_TOKEN", None)
    from app.main import create_app
    with TestClient(create_app()) as c:
        yield c


def test_api_lists_five_skills(client):
    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 5
    by_name = {s["name"]: s for s in body["skills"]}
    assert by_name["AA-SK-03-disposition-execution"]["agent"] == "AA-AG-04"
    assert all(s["loadable"] for s in body["skills"])


def test_api_skill_detail_and_404_envelope(client):
    r = client.get("/api/skills/AA-SK-05-knowledge-sedimentation")
    assert r.status_code == 200
    assert r.json()["degradation_paths"]  # 降级路径对第三方 Agent 可见

    r = client.get("/api/skills/nope")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "E-NOT-FOUND"
