# 数据与第三方许可核对

项目目前没有 LICENSE。新样本的生成方式见 [data/README.md](../data/README.md)，没有复制旧 CSV，也没有引入第三方数据集。本文只记录核对结果，不授予任何许可。

依赖的版本和许可文件指纹见 [evidence/dependency-licenses.json](evidence/dependency-licenses.json)，来自全新环境中的 distribution metadata 和 wheel 内的 LICENSE / NOTICE。主要的直接依赖：

| 依赖 | 版本 | 许可 |
|---|---|---|
| Pandas | 2.2.3 | BSD-3-Clause |
| pytest | 8.4.2 | MIT |
| Ruff | 0.12.0 | MIT |
| PyYAML | 6.0.2 | MIT |
| PyMySQL（可选） | 1.1.2 | MIT |

传递依赖也要看：NumPy 的 wheel 带有多个第三方许可；python-dateutil 的 LICENSE 保留了历史条款，不能只看 metadata 里的一个标签；tzdata 同时包含包装代码和时区数据的声明；cryptography 及其相关依赖各有各的许可。完整清单以上面的证据文件为准。

仓库不包含依赖的 wheel、虚拟环境、MySQL 二进制或基础镜像，也没有发现额外内嵌的第三方代码。如果以后要分发 Docker 镜像或打包依赖，需要保留上游的 LICENSE 和 NOTICE，并另外检查基础镜像和 wheel 里本地库的条款。

这份核对不是法律意见，也不是依赖漏洞扫描。来源归因见 [PROVENANCE.md](PROVENANCE.md)，待决定事项见 [RELEASE_DECISIONS.md](RELEASE_DECISIONS.md)。项目采用 MIT 或 Apache-2.0 也不能重新授权第三方内容。
