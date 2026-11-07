# 发布前待决定事项

目前仓库是 Private，还没有 LICENSE，也没有转为 Public。

## 许可证

首选 **MIT**。这是一个实习作品和可复现的示例，MIT 文本简短，允许别人学习和复用，只要求保留版权声明。

备选 Apache-2.0：对贡献者的专利授权更明确，但需要附带变更说明和 NOTICE，义务更多。也可以暂不授予任何许可，这种情况不能说“已开源”。

参考：[MIT](https://choosealicense.com/licenses/mit/) · [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) · [不选许可意味着什么](https://choosealicense.com/no-permission/)

## 转为 Public 前要确认

- 公开署名和 GitHub noreply 邮箱。
- demo-v1 样本、生成器和报告是否采用与代码相同的许可证。
- 最终的公开文件清单。
- 同步修改 README 和相关文档中“尚未公开”的表述。

## 已处理

- 旧样本来源不明的问题，已用新生成的样本解决：没有下载第三方数据，也没有再分发旧数据。
- 第三方依赖各自保留自己的许可证，项目许可证不能覆盖它们，见[许可核对](THIRD_PARTY.md)。
- 远端 CI 已覆盖 Python 3.10 / 3.12、MySQL 8.4 集成，以及 Docker 构建和 SQLite 冒烟测试。Compose 服务和 Windows 没有验证过，所以不提供全平台或生产部署的保证。
