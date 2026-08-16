# -*- coding: utf-8 -*-
"""Docker 命名卷导出（R-37/G）：把运行时实际存储导出到仓库内 db/export/，
克隆/复制项目后即可完整启动（start_all.py 空库时自动恢复，见 restore_from_export）。

命名卷与导出形态（compose 仅两个命名卷）：
  pg-data     → pg_dump --data-only（流式 gzip）→ db/export/tradeguard-data.sql.gz
                不含 schema：db/init/*.sql 幂等迁移在新卷首次启动时建结构，
                数据恢复补齐内容——新卷旧卷双写一致（与 06-closedloop-fix 同纪律）。
  higress-data → docker cp higress:/data → tar.gz → db/export/higress-data.tar.gz
                路由本就由 scripts/higress_routes.py 幂等重建，此导出为离线快照取证用；
                打包时剔除 data/secrets/ 与 *.key/*.pem 等私钥素材（R-37 复审收口）。

安全闸门：导出落盘后按密钥模式扫描（LLM key / 明文与 base64 封装的私钥块 /
GitHub/AWS 凭据特征），命中即删除导出件并报错——凭据绝不随导出件入库
（R-37 凭据治理红线；base64 特征防 k8s Secret 封装绕过）。

用法：.venv\\Scripts\\python.exe scripts\\volume_export.py [--skip-higress]
"""
from __future__ import annotations

import argparse
import gzip
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = ROOT / "db" / "export"
PG_DUMP_OUT = EXPORT_DIR / "tradeguard-data.sql.gz"
HIGRESS_OUT = EXPORT_DIR / "higress-data.tar.gz"

# 密钥模式（脱敏审计同口径）：LLM key / PEM 私钥块 / Nacos 互信值明文
# R-37 复审收口：补 base64 封装特征（k8s Secret 的 tls.key 是 base64(PEM)，
# 明文 PEM 头扫描看不见它；'-----BEGIN' 的 base64 前缀恒为 LS0tLS1CRUdJTi），
# 并扩 GitHub/AWS 常见凭据面——导出件绝不携带任何可用于重建身份的素材。
KEY_PATTERNS = [
    (re.compile(rb"sk-[A-Za-z0-9_\-]{16,}"), "LLM API Key（sk-*）"),
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PEM 私钥块"),
    (re.compile(rb"LS0tLS1CRUdJTi"), "base64 封装的 PEM 私钥块（k8s Secret 形态）"),
    (re.compile(rb"AGENTTEAMS_LLM_API_KEY=sk-"), "AgentTeams LLM Key 赋值"),
    (re.compile(rb"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "GitHub 访问令牌"),
    (re.compile(rb"\bAKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
]

for _s in (sys.stdout, sys.stderr):          # Windows GBK 控制台兼容
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def sh(args, timeout=600, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       cwd=ROOT, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"命令失败: {' '.join(args)}\n{(r.stdout + r.stderr)[-500:]}")
    return r


def scan_export(path: Path) -> None:
    """导出件密钥扫描：命中即删除并报错（凭据绝不入库，R-37 红线）"""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        data = f.read()
    for pat, name in KEY_PATTERNS:
        if pat.search(data):
            path.unlink(missing_ok=True)
            raise RuntimeError(f"❌ 导出件 {path.name} 命中密钥模式【{name}】，已删除导出件——"
                               "请先从库中清理对应敏感值再导出")
    print(f"  密钥扫描：{path.name} 干净（{len(data) / 1048576:.1f} MB 解压后）")


def export_pg() -> None:
    """pg-data 卷 → data-only SQL 流式 gzip（10 万级行约 1~2 分钟）"""
    print("■ 导出 pg-data →", PG_DUMP_OUT.relative_to(ROOT))
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["docker", "compose", "exec", "-T", "postgres", "pg_dump",
         "-U", "postgres", "-d", "tradeguard",
         "--data-only", "--no-owner", "--no-privileges"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
    rows_hint = 0
    with gzip.open(PG_DUMP_OUT, "wb") as gz:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(1 << 20)
            if not chunk:
                break
            rows_hint += chunk.count(b"\n")
            gz.write(chunk)
    err = proc.communicate(timeout=60)[1].decode("utf-8", "replace")
    if proc.returncode != 0:
        PG_DUMP_OUT.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump 失败：{err[-500:]}")
    print(f"  pg_dump 完成：约 {rows_hint} 行，压缩后 "
          f"{PG_DUMP_OUT.stat().st_size / 1048576:.1f} MB")
    scan_export(PG_DUMP_OUT)


def _higress_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """R-37 复审收口：higress /data 快照剔除敏感子树与私钥素材。
    data/secrets/ 存网关自签 TLS 证书私钥（k8s Secret base64 封装），
    以及任何 *.key/*.pem/*.crt 私钥/证书原件与备份件——一律不进入随库分发的
    导出件；路由配置（data/ 下非敏感文件仓内容）不受影响，且路由本就由
    scripts/higress_routes.py 幂等重建。"""
    parts = Path(ti.name).parts
    if "secrets" in parts:
        return None
    if ti.name.lower().endswith((".key", ".pem", ".crt", ".p12", ".pfx", ".bak")):
        return None
    return ti


def export_higress() -> None:
    """higress-data 卷 → /data 目录 tar.gz（路由文件仓快照）。
    经 compose 服务名操作（容器名带 -1 后缀会随 compose 版本漂移，服务名稳定）。"""
    print("■ 导出 higress-data →", HIGRESS_OUT.relative_to(ROOT))
    if subprocess.run(["docker", "compose", "exec", "-T", "higress", "test", "-d", "/data"],
                      capture_output=True).returncode != 0:
        raise RuntimeError("higress 服务不可用或无 /data，无法导出（先 start_all 起栈）")
    with tempfile.TemporaryDirectory() as tmp:
        sh(["docker", "compose", "cp", "higress:/data", tmp])
        src = Path(tmp) / "data"
        with tarfile.open(HIGRESS_OUT, "w:gz") as tar:
            tar.add(src, arcname="data", filter=_higress_filter)
    print(f"  higress /data 快照完成：压缩后 {HIGRESS_OUT.stat().st_size / 1024:.0f} KB")
    scan_export(HIGRESS_OUT)


def main() -> int:
    ap = argparse.ArgumentParser(description="Docker 命名卷导出到 db/export/（R-37/G）")
    ap.add_argument("--skip-higress", action="store_true", help="仅导出 pg-data")
    args = ap.parse_args()
    try:
        export_pg()
        if not args.skip_higress:
            export_higress()
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"❌ 导出失败：{e}", file=sys.stderr)
        return 1
    print("RESULT: OK —— 克隆后经 scripts/start_all.py 自动恢复（空库 → 读 db/export）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
