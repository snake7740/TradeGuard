#!/usr/bin/env python3
"""Skill 包安装器与发布清单生成器（新智基座维度3闭合项：生态 install 可消费）。

把 skills/AA-SK-*.md 自包含技能包从"仓库内文档"升级为"可被外部生态 install
消费"的发布形态，闭合「未发布公共 registry（不能被 install 消费）」扣分项：

  1. manifest  —— 生成 skills/RELEASE-MANIFEST.json 发布清单（包清单/版本/
     sha256/测试数/发布时点与 commit），即 registry 的索引元数据；
  2. install   —— 按清单把技能包安装到目标目录（默认 ~/.tradeguard-skills），
     逐包校验 sha256 完整性 + frontmatter 零漂移（复用 skill_pack_validate 规则），
     坏包拒装并留痕，输出安装回执；
  3. verify    —— 对已安装目录复核 sha256 与 frontmatter（安装后可审计）。

设计约束：纯标准库（与 skill_pack_validate.py 同纪律，不引入 PyYAML/pip 依赖）；
发布通道 = git 仓库 + 清单文件，第三方 `git clone` 后一条命令 install，
不依赖中心化 registry 账号——工程级闭合，公共 registry 上架为后续运营动作。

用法：
    python scripts/skill_install.py manifest              # 生成/刷新发布清单
    python scripts/skill_install.py install               # 安装到 ~/.tradeguard-skills
    python scripts/skill_install.py install --target D:\\x  # 指定目标目录
    python scripts/skill_install.py verify --target D:\\x  # 复核已安装目录

退出码：0 = 成功；1 = 校验/安装失败。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills"
MANIFEST_PATH = SKILL_DIR / "RELEASE-MANIFEST.json"
DEFAULT_TARGET = Path.home() / ".tradeguard-skills"


def _load_validator() -> Any:
    """显式路径加载既有校验器，避免规则双写漂移（单一事实源在 skill_pack_validate）。"""
    spec = importlib.util.spec_from_file_location(
        "skill_pack_validate", REPO_ROOT / "scripts" / "skill_pack_validate.py")
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_validator = _load_validator()
validate_skill = getattr(_validator, "validate_skill", None) if _validator else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=REPO_ROOT, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def cmd_manifest() -> int:
    """生成发布清单：包清单 + sha256 + 版本 + 测试数（发布时点快照）。"""
    packs = []
    errors = 0
    for path in sorted(SKILL_DIR.glob("AA-SK-*.md")):
        if validate_skill is not None:
            fm, errs, actual = validate_skill(path)
            if errs:
                for e in errs:
                    print(f"错误 {e}", file=sys.stderr)
                errors += 1
                continue
        else:  # pragma: no cover
            fm, actual = {}, 0
        packs.append({
            "name": fm.get("name", path.stem),
            "version": fm.get("version", "?"),
            "file": path.name,
            "sha256": _sha256(path),
            "test_cases": actual,
            "agent": fm.get("agent", "?"),
        })
    if errors:
        print(f"存在 {errors} 个漂移包，拒绝生成清单（先修复 frontmatter）", file=sys.stderr)
        return 1
    manifest = {
        "registry": "tradeguard-skills",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "release_commit": _git_head(),
        "install_command": "python scripts/skill_install.py install",
        "packs": packs,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    print(f"清单已生成：{MANIFEST_PATH}（{len(packs)} 个技能包，commit {manifest['release_commit']}）")
    return 0


def _load_manifest() -> dict[str, Any] | None:
    if not MANIFEST_PATH.is_file():
        print("错误：无发布清单，先运行 `python scripts/skill_install.py manifest`",
              file=sys.stderr)
        return None
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _check_pack(src: Path, expected_sha: str) -> list[str]:
    """单包安装前校验：sha256 完整性 + frontmatter 零漂移。"""
    errors = []
    if _sha256(src) != expected_sha:
        errors.append(f"{src.name}: sha256 与发布清单不一致（包已变动或被篡改，拒装）")
    if validate_skill is not None:
        _, errs, _ = validate_skill(src)
        errors.extend(errs)
    return errors


def cmd_install(target: Path) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1
    target.mkdir(parents=True, exist_ok=True)
    installed, rejected = [], []
    for pack in manifest["packs"]:
        src = SKILL_DIR / pack["file"]
        if not src.is_file():
            rejected.append((pack["file"], "源文件缺失"))
            continue
        errs = _check_pack(src, pack["sha256"])
        if errs:
            rejected.append((pack["file"], "；".join(errs)))
            continue
        (target / pack["file"]).write_bytes(src.read_bytes())
        installed.append(pack)
    # 安装回执（可审计：装了什么版本、sha、从哪个发布 commit）
    receipt = {
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "release_commit": manifest.get("release_commit"),
        "target": str(target),
        "packs": [{"name": p["name"], "version": p["version"], "sha256": p["sha256"]}
                  for p in installed],
        "rejected": [{"file": f, "reason": r} for f, r in rejected],
    }
    (target / "INSTALL-RECEIPT.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"安装 {len(installed)}/{len(manifest['packs'])} 个技能包 → {target}")
    for f, r in rejected:
        print(f"拒装 {f}: {r}", file=sys.stderr)
    print(f"安装回执：{target / 'INSTALL-RECEIPT.json'}")
    return 1 if rejected else 0


def cmd_verify(target: Path) -> int:
    manifest = _load_manifest()
    if manifest is None:
        return 1
    bad = 0
    for pack in manifest["packs"]:
        installed = target / pack["file"]
        if not installed.is_file():
            print(f"缺失 {pack['file']}", file=sys.stderr)
            bad += 1
            continue
        if _sha256(installed) != pack["sha256"]:
            print(f"篡改/过期 {pack['file']}（sha256 与发布清单不符）", file=sys.stderr)
            bad += 1
    print(f"复核：{len(manifest['packs']) - bad}/{len(manifest['packs'])} 个包完整"
          f"（目标 {target}）")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TradeGuard skill 包安装器/发布清单")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest", help="生成/刷新 skills/RELEASE-MANIFEST.json")
    p_ins = sub.add_parser("install", help="按清单安装技能包")
    p_ins.add_argument("--target", type=Path, default=DEFAULT_TARGET,
                       help=f"目标目录（默认 {DEFAULT_TARGET}）")
    p_ver = sub.add_parser("verify", help="复核已安装目录完整性")
    p_ver.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    args = ap.parse_args()

    if args.cmd == "manifest":
        return cmd_manifest()
    if args.cmd == "install":
        return cmd_install(args.target)
    return cmd_verify(args.target)


if __name__ == "__main__":
    sys.exit(main())
