# 公开候选独立验收

## GitHub Private 上架实测（2026-11-06）

独立根提交 `84fc0fa` 已上传至所有者的 Private 仓库；远端 56 文件的 Git blob 哈希与本地逐一相等，无原学习仓库父历史。GitHub README HTML 渲染响应正常；未声称进行浏览器视觉检查。

[首轮 Actions 实际运行](https://github.com/wuyi060531-del/ecommerce-order-pipeline/actions/runs/42222552697)四项全部成功：Python 3.10 为 129 passed（5.55 秒），Python 3.12 为 129 passed（5.25 秒、1 warning）；MySQL 8.4 建表、事务、幂等及服务重启对账通过；Docker 镜像构建与只读输入的 SQLite 容器运行通过。该远端结果补齐下述封存时的 MySQL／Docker／CI 缺口，不改变历史本地结果或性能数字。

以下是首次封存时的本地独立验收记录。LICENSE 仍待决定，Private 不等于已开源；Public 需要另行批准。


2026-11-06；来源版本 `7f738f8`，公开目录 public-rc1。新建 Python 3.12.14 虚拟环境，安装 requirements-dev / requirements-mysql 并使用 3.12 约束成功，pip check 无冲突。没有复用原虚拟环境或数据库。

## 实际运行

- `.venv/bin/python -m pytest tests/v1 -q --junitxml=.local/pytest.xml`：**129 passed**，0 failures / errors / skipped，JUnit suite 22.83 秒。原教学工具的 39 项没有进入本目录；新增 4 项检验独立运行、生成来源与文件边界，不能沿用原 164 项。
- 1 条第三方 DeprecationWarning：Pandas 2.2.3 与 NumPy 2.5.4 的 timedelta 通用单位转换。没有屏蔽 warning 或修改业务去消除它，当前行为测试通过。
- `.venv/bin/python -m scripts.demo_sqlite`：0；真实 run 两次、analyze、report、check。30 = 26 + 3 + 1，paid 11 条 / 6600 分；orders / etl_runs / dq_issues = 26 / 1 / 7；十份产物哈希稳定，原输入不变。
- `.venv/bin/python -m scripts.create_demo_data --verify`：0；新样本逐字节可重建。原规则对新样本与提取后的公开规则质量指标一致，未因换数据改变规则。
- 真实 CLI 的缺文件与缺字段均退出 2，未生成成功产物；相应更多反例、SQL 精度、事务回滚、A/B/A、报告失败恢复由本目录测试覆盖。
- Ruff（src/order_pipeline、scripts、tests/v1）、YAML 解析、workflow 所有 run 块 bash -n、真实 `docker compose config --quiet` 静态配置检查通过。
- Docker 29.8.2 / OrbStack daemon 可用，已尝试 build；Docker Hub 匿名鉴权 token 请求超时，应用镜像未构建成功，未运行容器。不是无 Docker 环境，也不记为应用测试失败或容器通过。
- 本目录 live MySQL、Compose 服务、远端 Actions 未运行。2026-10-31 MySQL 8.4.13 的[历史证据摘要](evidence/mysql-historical.json)仅针对拆分前代码与旧数据，不标成本快照实测。

安装时沙箱 DNS 曾阻止下载，获准联网后成功；没有松动依赖或修改系统服务。完整结构化结果见 [validation.json](evidence/validation.json)。日志、JUnit、虚拟环境、数据库仅在本地运行区，不放进发布清单。

## 本公开目录的 synthetic 性能观测

Apple M1 / 8 GiB、macOS 26.6.2 arm64；Python 3.12.14、Pandas 2.2.3、NumPy 2.5.4、SQLite。种子 20261030，三档串行、新进程、新空库，各测一次，不含解释器启动，含导出；非 profiler 测量。

| synthetic 输入行数 | 完整 ETL 秒数 | 进程峰值 MiB |
| ---: | ---: | ---: |
| 1,000 | 0.2889 | 71.39 |
| 10,000 | 0.4686 | 106.03 |
| 50,000 | 2.2668 | 214.44 |

命令：`.venv/bin/python -m scripts.benchmark --sizes 1000 10000 50000 --label public-rc1 --output .local/benchmarks`。单档 120 秒；512 MiB 为升级门槛，非 OS 强制限额。峰值为 worker 的 resource.RUSAGE_SELF.ru_maxrss（Darwin bytes / Linux KiB，统一 MiB），不含 MySQL 服务内存。一次观测未控制缓存、温度或调度，不是生产吞吐、MySQL 性能或优化收益结论；没有本快照优化前后对照。原始聚合、各阶段、环境、输入及代码哈希见 [benchmark-rc1.json](evidence/benchmark-rc1.json)。

## 发布范围

安全复查对最终源码清单独立执行，常见密钥／token、私人路径、邮箱、手机号、证件号及带密码 URL 无未审阅命中。少量配置环境引用和测试假凭据以精确行 SHA 审阅；不是按文件整体忽略。`scripts.check_release` 检查完整文件集合、SHA 和所有本地 Markdown 链接；清单本身不自哈希，工作环境不随包发布。无专业熵扫描或法律保证。

技术上达到可供所有者最终审阅的源码公开候选，无必须修复的源码运行阻塞。剩余所有者事项是许可证及数据范围、署名和明确公开批准；Docker 网络导致的容器验证缺口、远端 CI 及本快照 live MySQL 均须继续如实披露，不构成源码演示已验证的部分。
