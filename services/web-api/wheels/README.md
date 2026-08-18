# Vendored Wheels

## rocketmq_client_python-0.5.0rc2-py3-none-manylinux1_x86_64.whl

**为什么 vendor**：`rocketmq-client-python==0.5.0rc2` 的 manylinux 轮子是 cp312
唯一可装版本线（内置 librocketmq 二进制，与 RocketMQ 5.x broker 走 remoting 协议
兼容），但该轮子已从 PyPI 索引消失；后续全部版本（0.5.0rc3+ / 2.0.0）均为
sdist-only，安装需要 C++ 构建工具链。为保证供应链可复现（`docker build` 与
CI 均需离线解析该 pin），将其 vendored 入库。

**来源**：从本项目既有镜像（`python:3.12-slim` + PyPI 0.5.0rc2 manylinux 轮子
安装产物）的 `site-packages` 导出包体与 `dist-info` 重打为 wheel。WHEEL 标签由
原始双标签（py2/py3）裁剪为 py3 单标签，包内容未做任何修改。

**许可证**：rocketmq-client-python 以 Apache License 2.0 发布
（https://github.com/apache/rocketmq-client-python ）。依 Apache-2.0 第 4 条，
本目录随仓库重分发该二进制产物，许可证全文见仓库根 [LICENSE](../../LICENSE)；
上游版权与许可声明保留在 wheel 内 `dist-info/METADATA`。

**使用**：`pip install --find-links=<本目录> -r services/web-api/requirements.txt`

**退出策略**：上游发布含 manylinux 轮子的新版本（或项目改用 gRPC 协议的
rocketmq 2.x + 构建工具链）后，删除本目录并恢复纯 PyPI 解析。
