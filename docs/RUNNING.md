# 运行指南

以下命令都在仓库根目录执行。推荐 Python 3.12，`requirements*.txt` 固定了直接依赖，`constraints-demo-py312.txt` 固定了 3.12 下的完整解析结果。这个约束文件只适用于 Python 3.12，也不是跨平台的哈希锁。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt -c constraints-demo-py312.txt
.venv/bin/python -m pip check
.venv/bin/python -m scripts.demo_sqlite
.venv/bin/python -m pytest tests/v1 -q
```

Windows 下解释器路径是 `.venv\Scripts\python.exe`。Windows 没有测试过，性能脚本用到了 `resource`，主要适用于 macOS 和 Linux。

## 命令行

```bash
.venv/bin/python -m src.order_pipeline run
.venv/bin/python -m src.order_pipeline analyze
.venv/bin/python -m src.order_pipeline report
.venv/bin/python -m src.order_pipeline check
```

默认输出目录是 `data/processed/v1/`，SQLite 库是 `data/processed/orders.sqlite3`。首次运行建议用 `scripts.demo_sqlite`，它会使用全新的目录和数据库。

退出码：`run`、`analyze`、`report` 成功返回 0；`check` 发现被隔离的行时返回 1；输入或配置错误返回 2。`report` 从 manifest 校验过的 CSV/JSON 重建，不会重新读取 CSV 或数据库。不同命令使用不同的输出目录，程序会拒绝覆盖不认识的文件或输入文件。

## MySQL

需要 MySQL 8.0.19 及以上。先准备好独立的数据库和账号，程序不会自己建库或安装服务：

```bash
.venv/bin/python -m pip install -r requirements-mysql.txt -c constraints-demo-py312.txt
export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=3306
export MYSQL_DATABASE=ecommerce_pipeline
export MYSQL_USER=pipeline_user
read -s MYSQL_PASSWORD   # 输入密码后回车
export MYSQL_PASSWORD
.venv/bin/python -m src.order_pipeline run --backend mysql
unset MYSQL_PASSWORD
```

程序只读取环境变量，不会自动加载 `.env`，模板见 `.env.example`。需要 TLS 时设置 `MYSQL_SSL_CA`。

跑 MySQL 集成验证要求：设置 `MYSQL_INTEGRATION=1`，数据库名以 `_test` 或 `_smoke` 结尾，三张业务表为空且只给这次测试用。脚本会写入测试数据，并按精确的键清理，所以不能指向业务库。GitHub CI 在 MySQL 8.4 上跑过这套集成测试，包括正常重启后的对账。

## Docker 与 CI

镜像以非 root 运行，原始数据只读挂载，输出写入独立的 volume：

```bash
docker build -t ecommerce-order-pipeline:local .
docker run --rm --memory=1g --read-only --tmpfs /tmp:size=128m \
  --mount type=bind,source="$PWD/data/raw",target=/data/raw,readonly \
  --mount type=volume,source=ecommerce-pipeline-output,target=/data/processed \
  ecommerce-order-pipeline:local
```

Compose 配置使用独立的 MySQL 和临时密码环境变量，不要填入真实凭据。

CI 包含：Python 3.10 / 3.12 的 Ruff、发布检查、正式测试和演示；MySQL 8.4 服务的验收和重启对比；Docker 的 SQLite 冒烟测试。CI 不发布镜像，也不部署。结果见 [VALIDATION](VALIDATION.md)。

## 发布清单

`.venv`、`.git`、日志、数据库、缓存和 `.local` 是本机运行产生的，不属于公开文件。`SHA256SUMS.json` 列出所有发布文件（清单本身除外），`scripts.check_release` 会检查文件集合、哈希、内部链接和敏感内容。少数环境变量引用和测试常量，是按精确行哈希人工审阅后放进白名单的。

改动公开文件后，需要重新验收并更新这份清单，不要直接把改动当作已审阅过的版本。
