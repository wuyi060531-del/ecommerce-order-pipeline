# demo-v1：虚构订单

`raw/orders.csv` 完全由 `scripts/create_demo_data.py` 按固定规则生成，不读取旧的教学 CSV、外部数据集、网络或个人数据。订单、用户、商品和城市都带有 synthetic / DEMO 标记，日期只是测试值，不代表真实交易时间。

来源信息在 [orders.synthetic.json](raw/orders.synthetic.json)：生成器版本、外部输入（为空）、SHA-256 和预期指标。样本内容由我在 Codex 辅助下制作，数据是否采用与代码相同的许可证，见[发布事项](../docs/RELEASE_DECISIONS.md)。

可以重新生成并逐字节对比：

```bash
.venv/bin/python -m scripts.create_demo_data --verify
.venv/bin/python -m scripts.create_demo_data --output .local/recreated-demo.csv
```

生成器不会覆盖已有的 CSV 或元数据，ETL 对输入文件只读。

| 项目 | 值 |
| --- | --- |
| 输入字段 | order_id、user_id、product_name、quantity、unit_price、order_time、city、status |
| 原始 / 接收 / 隔离 / 重复副本 | 30 / 26 / 3 / 1 |
| paid 订单 / 销售额 | 11 / 6600 分（66.00 元） |
| 城市缺失 / 日期非法 | 2 / 1，都是警告，订单仍然保留 |
| 异常原始行 / 问题记录 | 7 / 7（一行可以有多个问题，其他数据里这两个数可能不同） |
| 日期无效但保留的 paid 订单 | SYN011，11.00 元 |

另一个脚本 `scripts/generate_orders.py` 用固定种子生成更大规模的性能测试数据，有自己的元数据，不会覆盖这份样本。
