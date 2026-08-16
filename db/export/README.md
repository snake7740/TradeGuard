# db/export —— Docker 命名卷数据导出（克隆即完整启动）

> **这是什么 / 给谁看**：数据库卷的导出快照——让克隆后「无需重新合成数据」即可完整启动。
> 面向**部署 / 运维**。零基础请先读[根 README](../../README.md) 的「快速开始」；本目录由 `scripts/start_all.py` 自动消费，一般无需手工操作。

本目录存放 compose 命名卷的**数据导出件**，使克隆/复制仓库后无需重新合成数据
即可完整启动：`scripts/start_all.py` 检测到空库时优先从本目录恢复（
`restore_from_export`），导出缺失才回退 `data-generator` 合成路径。

## 导出件清单

| 文件 | 来源卷 | 形态 | 恢复方式 |
| --- | --- | --- | --- |
| `tradeguard-data.sql.gz` | `pg-data` | `pg_dump --data-only` 流式 gzip | start_all 空库时 TRUNCATE public 全表后 psql 管道恢复 |
| `higress-data.tar.gz` | `higress-data` | higress 容器 `/data` 目录 tar.gz | 离线快照取证件；路由由 `scripts/higress_routes.py` 幂等重建（无需手工恢复） |

## 再生成（数据演进后刷新导出件）

```bash
.venv/Scripts/python scripts/volume_export.py        # 需 compose 栈在运行
```

脚本内置密钥扫描闸门：导出件检出 LLM key / 私钥块等模式即删除导出件并报错，
凭据绝不随导出件入库（R-37 凭据治理）。

## 纪律

- 导出件**仅含数据、不含 schema**：结构由 `db/init/*.sql` 幂等迁移在新卷首次
  启动时建立（新卷旧卷双写一致）。
- 导出件随代码库提交分发；其中全部为合成数据（PaySim 式分布），无真实客户信息。
- 恢复失败时 start_all 会显式报错，不会静默回落造成"空库假启动"。
