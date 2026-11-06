# 发布前由所有者决定

已按所有者授权上传 GitHub Private，远端 CI 四项通过；实际结果见 [VALIDATION.md](VALIDATION.md)。尚未添加项目 LICENSE，也未转为 Public。

推荐候选：**MIT**。本项目是实习作品与可复现示例，MIT 文本简洁，适合允许他人研究和复用，同时要求保留版权与许可声明。它不是自动授予的许可，本轮不填写所有者姓名或替其决定授权。

备选 Apache-2.0：如果更重视明确的贡献者专利授权，可选它；相应变更说明和适用 NOTICE 等义务更多。也可以暂不授予开源许可，只保留展示意图；这不能称“已经开源”。依据：[MIT 条款](https://choosealicense.com/licenses/mit/)、[Apache-2.0 条款](https://www.apache.org/licenses/LICENSE-2.0)、[未选择许可的含义](https://choosealicense.com/no-permission/)。

所有者还须确认：公开署名与 GitHub noreply 邮箱；是否将本轮新生成的 demo-v1、生成器与报告纳入同一授权；接受 AI 辅助归因文字；最终审阅公开文件清单并明确批准转为 Public。Private 创建与本次推送已获授权，不代表授权 Public。

旧样本来源不明的阻碍已通过新生成样本消除，没有第三方数据下载或旧数据再分发。新样本授权仍由所有者决定。第三方依赖各自保留许可证，不能用项目 MIT / Apache 覆盖它们，见[许可核对](THIRD_PARTY.md)。

首次本地 Docker build 受 Docker Hub 网络超时阻碍；本次远端 Actions 已实际通过 Docker 构建／运行、MySQL 集成和 Python 3.10/3.12 验证。Compose 服务和 Windows 未由这些结果验证，不提供全平台或生产部署保证。
