# 运行与验证边界

在仓库根目录执行。推荐 Python 3.12；本轮真实环境见[验收](VALIDATION.md)。requirements 直接依赖固定，`constraints-demo-py312.txt` 固定本轮 3.12 的解析；不把该约束用于其他 Python 版本，也不宣称它是跨平台 hash lock。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c constraints-demo-py312.txt
.venv/bin/python -m pip check
.venv/bin/python -m scripts.demo_sqlite
.venv/bin/python -m pytest tests/v1 -q
```

Windows 的解释器路径为 `.venv\Scripts\python.exe`，本轮未测试 Windows；性能工具使用 resource，主要适用于 macOS / Linux。

## CLI

```bash
.venv/bin/python -m src.order_pipeline run
.venv/bin/python -m src.order_pipeline analyze
.venv/bin/python -m src.order_pipeline report
.venv/bin/python -m src.order_pipeline check
```

默认输出 `data/processed/v1/`，SQLite 库 `data/processed/orders.sqlite3`；演示工具使用自己的新目录和新库，适合首次运行。check 使用独立 check 目录，发现隔离行时退出 **1**，成功的 run/analyze/report 退出 0，输入／配置错误退出 2。report 从 manifest 校验过的 CSV/JSON 重建，不重新读取 CSV 或数据库。不同模式使用不同输出目录；工具拒绝覆盖未知个人文件或输入。

## MySQL

目标 MySQL 8.0.19+；先准备独立数据库与账号，程序不会创建数据库或安装服务：

```bash
.venv/bin/python -m pip install -r requirements-mysql.txt -c constraints-demo-py312.txt
export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=3306
export MYSQL_DATABASE=ecommerce_pipeline
export MYSQL_USER=pipeline_user
read -s MYSQL_PASSWORD
export MYSQL_PASSWORD
.venv/bin/python -m src.order_pipeline run --backend mysql
unset MYSQL_PASSWORD
```

在 read 后输入密码回车。程序只读环境变量，不自动加载 `.env`；模板是 `.env.example`。TLS 可用 MYSQL_SSL_CA。验证脚本要求 MYSQL_INTEGRATION=1、数据库名以 `_test` / `_smoke` 结尾、三张业务表为空且独占。它会写测试数据并按自己精确的键清理，不能指向业务库。本快照仅保留可执行脚本，未重新跑 live MySQL。

## Docker 与 CI

已准备非 root 进程、只读原始数据挂载和独立输出 volume。Docker daemon 可用，本轮 build 因 Docker Hub 匿名 token 请求超时失败，未成功构建或运行应用容器；以下是网络恢复后的复现命令：

```bash
docker build -t ecommerce-order-pipeline:local .
docker run --rm --memory=1g --read-only --tmpfs /tmp:size=128m \
  --mount type=bind,source="$PWD/data/raw",target=/data/raw,readonly \
  --mount type=volume,source=ecommerce-pipeline-output,target=/data/processed \
  ecommerce-order-pipeline:local
```

Compose 使用隔离 MySQL 与临时密码环境变量，不要复用真实凭据。CI 提供 Python 3.10 / 3.12 正式测试和演示、MySQL 8.4 service 验收及正常重启对比、Docker SQLite 冒烟；它们不会发布镜像或部署。当前无远程 CI 运行结果。

## 发布清单

`.venv`、`.git`、日志、数据库、缓存和 `.local` 为本机运行区，不是公开文件。`SHA256SUMS.json` 列出实际发布文件（清单本身除外），`scripts.check_release` 验证文件集合、哈希、内部链接及敏感内容；精确行哈希白名单只覆盖人工审阅过的环境引用／测试常量。重新编辑公开源码后应重新验收并封存清单，不能直接把变更当成已审核版本。
