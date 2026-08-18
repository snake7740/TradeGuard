#!/usr/bin/env python3
"""Skill 打包自包含校验器（p4-skill-pack，维度3）。

校验 skills/AA-SK-*.md 的 YAML frontmatter 与仓库实际状态的一致性，
防止「文档 / 代码 / 测试」三方漂移：

  1. frontmatter 必填键齐全（name/version/description/agent/entrypoint/
     depends-mcp/depends-tables/tests/test-cases/degradation-paths）；
  2. name 与文件名 stem 一致（注册名即文件名，杜绝检索歧义）；
  3. entrypoint / tests 指向的文件存在（仓库根相对路径）；
  4. test-cases 声明值与 tests 文件内 `def test_` 实际计数一致
     （质量指标可量化、可复核，防文档夸大）。

frontmatter 采用扁平 `key: value` 单行键值（不引入 PyYAML 依赖，
与 skills/README.md 打包规范同步维护）。

用法：
    python scripts/skill_pack_validate.py           # 校验 + 质量汇总表
    python scripts/skill_pack_validate.py --quiet   # 仅错误（CI 用）

退出码：0 = 全部通过；1 = 存在错误（CI 拦截）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills"

REQUIRED_KEYS = (
    "name",
    "version",
    "description",
    "agent",
    "entrypoint",
    "depends-mcp",
    "depends-tables",
    "tests",
    "test-cases",
    "degradation-paths",
)
# depth-limit 为可选键：仅递归规划类技能（AA-SK-02）需要声明递归边界。

FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$", re.M)
DEF_TEST_RE = re.compile(r"^(?:async )?def test_", re.M)


def parse_frontmatter(path: Path) -> dict[str, str]:
    """解析扁平 frontmatter，返回键值映射；无 frontmatter 返回空 dict。"""
    m = FM_RE.match(path.read_text(encoding="utf-8"))
    if not m:
        return {}
    return {k: v.strip() for k, v in KEY_RE.findall(m.group(1))}


def validate_skill(path: Path) -> tuple[dict[str, str], list[str], int]:
    """校验单个 skill 文件，返回 (frontmatter, 错误列表, 实际测试用例数)。"""
    errors: list[str] = []
    fm = parse_frontmatter(path)
    if not fm:
        return {}, [f"{path.name}: 缺少 YAML frontmatter（首行须为 ---）"], 0

    for key in REQUIRED_KEYS:
        if not fm.get(key):
            errors.append(f"{path.name}: 缺少必填键 {key}")

    if fm.get("name") and fm["name"] != path.stem:
        errors.append(f"{path.name}: name={fm['name']} 与文件名 stem={path.stem} 不一致")

    entry = fm.get("entrypoint")
    if entry and not (REPO_ROOT / entry).is_file():
        errors.append(f"{path.name}: entrypoint 文件不存在: {entry}")

    actual = 0
    for t in (s.strip() for s in (fm.get("tests") or "").split(",")):
        if not t:
            continue
        tp = REPO_ROOT / t
        if not tp.is_file():
            errors.append(f"{path.name}: tests 文件不存在: {t}")
            continue
        actual += len(DEF_TEST_RE.findall(tp.read_text(encoding="utf-8")))

    declared = fm.get("test-cases")
    if declared is not None:
        try:
            if int(declared) != actual:
                errors.append(
                    f"{path.name}: test-cases 声明 {declared} 与实际 def test_ 计数 {actual} 不一致")
        except ValueError:
            errors.append(f"{path.name}: test-cases 非整数: {declared}")
    return fm, errors, actual


def main() -> int:
    ap = argparse.ArgumentParser(description="Skill 打包自包含校验")
    ap.add_argument("--quiet", action="store_true", help="仅输出错误（CI 用）")
    args = ap.parse_args()

    skill_files = sorted(SKILL_DIR.glob("AA-SK-*.md"))
    if not skill_files:
        print("错误：skills/ 下未找到 AA-SK-*.md", file=sys.stderr)
        return 1

    all_errors: list[str] = []
    rows: list[tuple[str, str, str, int, int, int]] = []
    for path in skill_files:
        fm, errors, actual = validate_skill(path)
        all_errors.extend(errors)
        test_files = [s for s in (fm.get("tests") or "").split(",") if s.strip()]
        ok_files = sum(1 for t in test_files if (REPO_ROOT / t.strip()).is_file())
        rows.append((path.stem, fm.get("version", "?"),
                     fm.get("test-cases", "-"), actual, ok_files, len(test_files)))

    if not args.quiet:
        print(f"{'skill':<40}{'ver':<8}{'声明':>5}{'实际':>5}  tests")
        print("-" * 76)
        for stem, ver, dec, act, okf, tot in rows:
            print(f"{stem:<40}{ver:<8}{dec:>5}{act:>5}  {okf}/{tot}")
        print("-" * 76)
        print(f"共 {len(rows)} 个 skill，错误 {len(all_errors)} 个")

    for e in all_errors:
        print(f"错误 {e}", file=sys.stderr)
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
