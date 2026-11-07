# 验证记录

这里记录实际跑过的命令和结果。没有执行过的内容不会写成通过。结构化的数据在 [evidence/validation.json](evidence/validation.json)。

## 当前状态

最新提交 `09ca67c` 的 [GitHub Actions](https://github.com/wuyi060531-del/ecommerce-order-pipeline/actions/runs/42326079715) 四项任务全部成功：

| 任务 | 内容 |
|---|---|
| Python 3.10 / SQLite | Ruff、发布检查（`scripts.check_release`）、129 项测试、SQLite 演示 |
| Python 3.12 / SQLite | 同上 |
| MySQL 8.4 真实集成 | 建表、事务、幂等、服务重启后对账 |
| Docker 构建与 SQLite 冒烟 | 镜像构建，只读输入的 SQLite 容器运行 |

这次提交的改动：把 `scripts.check_release` 加进了 CI；修正了两个“模式保护”测试（原来传了不存在的参数，在参数解析阶段就退出了，没有真正测到保护逻辑）；统一了文档里的状态表述。

本地回归（2026-11-07）：

- `python -m pytest tests/v1 -q`：129 passed，没有失败、错误或跳过，1 条第三方 DeprecationWarning。
- 两个模式保护测试现在用合法参数，断言能看到“另一种运行模式”的提示，且原产物字节不变。在临时副本里关掉保护逻辑后，这两项都会失败，说明测试确实有约束力。正式代码没有改。
- `python -m scripts.demo_sqlite`：通过，30 = 26 + 3 + 1，paid 6600 分，三张表 26 / 1 / 7。重跑、SQL、十份产物哈希和输入保护都一致。
- Ruff、`pip check`、YAML 解析、CI 只读权限、checkout 凭据配置、11 个 `run` 块的 `bash -n` 检查通过。

## 已知限制

- Compose 服务和 Windows 没有验证过。
- 本地 Docker 构建曾因 Docker Hub 的鉴权请求超时而失败，镜像构建是在 GitHub CI 里通过的。
- 2026-10-31 在 MySQL 8.4.13 上有一次真实验收，但针对的是拆分前的代码和旧数据，见 [mysql-historical.json](evidence/mysql-historical.json)。现在的 MySQL 验证来自上面的 CI 任务。
- 没有专业的熵扫描，也没有法律保证。

## 首次封存时的记录（2026-11-06）

来源版本 `7f738f8`，Python 3.12.14，全新虚拟环境，`pip check` 无冲突。

- 129 passed，22.83 秒。原来属于教学工具的 39 项没有带过来，新增 4 项检查独立运行、来源和文件边界。
- `scripts.create_demo_data --verify` 通过，样本可以逐字节重建；原规则对新样本与提取后的公开规则，质量指标一致。
- CLI 缺文件、缺字段都退出 2，不产生成功产物。
- 独立根提交 `84fc0fa` 上传后，远端 56 个文件的 Git blob 哈希与本地逐一相等，没有原仓库的父历史。首轮 [Actions](https://github.com/wuyi060531-del/ecommerce-order-pipeline/actions/runs/42222552697) 四项成功。

## 性能观测

环境：Apple M1 / 8 GiB、macOS 26.6.2、Python 3.12.14、Pandas 2.2.3、NumPy 2.5.4、SQLite。种子 20261030，三档依次运行，每档用新进程和新的空库，各测一次，含导出，不含解释器启动。

| synthetic 输入行数 | 完整 ETL 秒数 | 进程峰值 MiB |
| ---: | ---: | ---: |
| 1,000 | 0.2889 | 71.39 |
| 10,000 | 0.4686 | 106.03 |
| 50,000 | 2.2668 | 214.44 |

```bash
.venv/bin/python -m scripts.benchmark --sizes 1000 10000 50000 --label public-rc1 --output .local/benchmarks
```

单档超时 120 秒，512 MiB 是升级到下一档前的内存门槛，不是系统强制限额。峰值取自 worker 的 `resource.RUSAGE_SELF.ru_maxrss`，不含 MySQL 服务的内存。每档只测了一次，没有控制缓存、温度和调度，所以这些数字只说明这台机器上的大致量级，不是生产吞吐或 MySQL 性能，也没有优化前后的对比。详细数据见 [benchmark-rc1.json](evidence/benchmark-rc1.json)。

## 发布范围检查

对最终文件清单做了独立的安全检查，没有发现未审阅的密钥、token、私人路径、邮箱、手机号、证件号或带密码的 URL。少量环境变量引用和测试用的假凭据，是按精确行的 SHA 逐条审阅的，不是整文件忽略。`scripts.check_release` 会检查完整文件集合、哈希和所有本地 Markdown 链接。
