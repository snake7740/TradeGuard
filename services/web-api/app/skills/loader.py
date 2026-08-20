# -*- coding: utf-8 -*-
"""Skill 运行时装载器（API-W-24 消费侧，AA-SK 自包含打包的基座消费能力）

定位：把 skills/AA-SK-*.md 的 frontmatter（12 键，skills/README.md §打包规范）
变成基座运行时可发现、可校验、可分派的 skill 注册表——skill 包从
「文档自包含」升级为「Agent 基座可装载」，第三方 Agent/门户可经
GET /api/skills 枚举元数据与降级路径，无需翻源码。

解析纪律（与 scripts/skill_pack_validate.py 同源规则，CI 门禁保零漂移）：
  - 扁平 YAML 子集（键: 值 行），不引入 YAML 依赖；
  - depends-mcp / depends-tables / degradation-paths 逗号分隔 → list；
  - entrypoint 形如 services/web-api/app/skills/<mod>.py → 映射为
    app.skills.<mod> 动态导入（sys.path 含 services/web-api，运行与测试
    环境均已保证）；导入成功即 loadable=True。

降级保底：坏包（frontmatter 缺键 / entrypoint 不可导入 / skills 目录缺失）
不抛出不阻断——loadable=False + error 留痕，与各包 degradation-paths
声明精神一致；每请求实时装载，frontmatter 变更免重启可见（热加载同精神）。
"""

from __future__ import annotations

import importlib
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("tradeguard.skills.loader")

REQUIRED_KEYS = (
    "name", "version", "description", "agent", "entrypoint",
    "depends-mcp", "depends-tables", "tests", "test-cases",
    "degradation-paths",
)  # 与 skill_pack_validate.py 必填键同源（depth-limit 可选）
LIST_KEYS = ("depends-mcp", "depends-tables", "degradation-paths")

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$", re.M)

# loader.py 位于 <repo>/services/web-api/app/skills/ → parents[4] 即仓库根；
# 容器内路径深度不足（/srv/app/skills/）且 TG_SKILLS_DIR 已显式设置，越界回落 /srv
_res = Path(__file__).resolve().parents
REPO_ROOT = _res[4] if len(_res) > 4 else Path("/srv")
_ENTRYPOINT_PREFIX = "services/web-api/"


def skills_dir() -> Path:
    """skill 包目录（TG_SKILLS_DIR 可覆盖；默认仓库根 skills/）"""
    return Path(os.getenv("TG_SKILLS_DIR", str(REPO_ROOT / "skills")))


@dataclass
class SkillSpec:
    """单个 skill 包的装载结果（frontmatter 元数据 + 导入校验）"""

    name: str = ""
    version: str = ""
    description: str = ""
    agent: str = ""
    entrypoint: str = ""
    depends_mcp: list[str] = field(default_factory=list)
    depends_tables: list[str] = field(default_factory=list)
    tests: str = ""
    test_cases: int = 0
    degradation_paths: list[str] = field(default_factory=list)
    loadable: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        """API 输出形状（docs/openapi SkillSpec 同构）"""
        return {
            "name": self.name, "version": self.version,
            "description": self.description, "agent": self.agent,
            "entrypoint": self.entrypoint,
            "depends_mcp": self.depends_mcp,
            "depends_tables": self.depends_tables,
            "tests": self.tests, "test_cases": self.test_cases,
            "degradation_paths": self.degradation_paths,
            "loadable": self.loadable, "error": self.error,
        }


def parse_frontmatter(md_path: Path) -> dict[str, str]:
    """扁平 frontmatter 解析（YAML 子集，与 CI 校验器同规则）"""
    m = _FM_RE.match(md_path.read_text(encoding="utf-8"))
    if m is None:
        raise ValueError(f"缺少 frontmatter 块（--- ... ---）：{md_path.name}")
    return {k: v.strip() for k, v in _KEY_RE.findall(m.group(1))}


def _entrypoint_module(entrypoint: str) -> str:
    """entrypoint 路径 → 模块名（services/web-api/ 前缀剥离 + 点号化）"""
    if not entrypoint.startswith(_ENTRYPOINT_PREFIX):
        raise ValueError(f"不支持的 entrypoint 形态：{entrypoint}")
    rel = entrypoint[len(_ENTRYPOINT_PREFIX):]
    if not rel.endswith(".py"):
        raise ValueError(f"entrypoint 非 .py 文件：{entrypoint}")
    return rel[:-3].replace("/", ".").replace("\\", ".")


def load_skill(md_path: Path) -> SkillSpec:
    """装载单个 skill 包：frontmatter 校验 + entrypoint 导入（坏包留痕不抛出）"""
    spec = SkillSpec(name=md_path.stem)
    try:
        fm = parse_frontmatter(md_path)
        missing = [k for k in REQUIRED_KEYS if not fm.get(k)]
        if missing:
            raise ValueError(f"frontmatter 缺键/空值：{missing}")
        spec.name = fm["name"]
        spec.version = fm["version"]
        spec.description = fm["description"]
        spec.agent = fm["agent"]
        spec.entrypoint = fm["entrypoint"]
        spec.depends_mcp = [s.strip() for s in fm["depends-mcp"].split(",") if s.strip()]
        spec.depends_tables = [s.strip() for s in fm["depends-tables"].split(",") if s.strip()]
        spec.tests = fm["tests"]
        spec.test_cases = int(fm["test-cases"])
        spec.degradation_paths = [s.strip() for s in fm["degradation-paths"].split(",") if s.strip()]
        importlib.import_module(_entrypoint_module(spec.entrypoint))
        spec.loadable = True
    except Exception as exc:  # noqa: BLE001 —— 坏包留痕降级，装载器不阻断路由
        spec.error = f"{type(exc).__name__}: {exc}"
        logger.warning("skill 包 %s 不可装载：%s", md_path.name, spec.error)
    return spec


def load_all() -> list[SkillSpec]:
    """装载 skills 目录全部 AA-SK 包（按名排序；目录缺失返回空并留痕）"""
    d = skills_dir()
    if not d.is_dir():
        logger.info("skills 目录不存在（%s），注册表为空", d)
        return []
    return [load_skill(p) for p in sorted(d.glob("AA-SK-*.md"))]
