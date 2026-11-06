# 数据与第三方许可复核

项目尚无 LICENSE。新数据的确定生成来源见 [data/README.md](../data/README.md)，没有复制旧 CSV 或引入第三方数据集。本文件只记录核对，不代所有者授予许可。

本轮从新环境的 distribution metadata 和 wheel 实际 LICENSE / NOTICE 核对依赖；具体版本与许可文件指纹见 [evidence/dependency-licenses.json](evidence/dependency-licenses.json)。主要直接依赖为 Pandas 2.2.3（BSD-3-Clause）、pytest 8.4.2（MIT）、Ruff 0.12.0（MIT）、PyYAML 6.0.2（MIT）及可选 PyMySQL 1.1.2（MIT）。

传递依赖不能忽略：NumPy wheel 含多个第三方许可；python-dateutil 的 metadata 与 LICENSE 保留的历史条款需一并看，不能只依赖一个标签；tzdata 包装与时区数据通知并存；cryptography 及其认证相关依赖有各自许可。完整枚举以本轮新环境证据为准。

本源码候选不包含依赖 wheel、虚拟环境、MySQL 二进制或基础镜像，也未发现额外 vendored 第三方代码。若以后分发 Docker 镜像或打包依赖，应保留完整上游 LICENSE / NOTICE，并额外检查基础镜像及 wheel 附带本地库的条款。本轮不是完整法律意见或依赖漏洞扫描。

来源归因见 [PROVENANCE.md](PROVENANCE.md)。许可证候选与待决定事项见 [RELEASE_DECISIONS.md](RELEASE_DECISIONS.md)；MIT / Apache 不能重新许可第三方内容。
