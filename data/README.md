# demo-v1 虚构订单

本公开工程的 `raw/orders.csv` **在本轮新建**，完全由 `scripts/create_demo_data.py` 的固定规则生成，不读取旧教学 CSV、外部数据集、网络或个人数据。订单、用户、商品和城市均使用明确 synthetic / DEMO 标识。日期是测试值，不表示真实交易时间。

来源元数据见 [orders.synthetic.json](raw/orders.synthetic.json)，包含生成器版本、空外部输入列表、SHA-256 和预期指标。新样本内容由所有者在 Codex 辅助下为本项目制作；尚未代表所有者授予数据再分发许可，公开时需明确是否与代码同一许可证。

```bash
.venv/bin/python -m scripts.create_demo_data --verify
.venv/bin/python -m scripts.create_demo_data --output .local/recreated-demo.csv
```

生成器拒绝覆盖已有 CSV 或元数据。ETL 对输入只读；可在第二个路径重建并逐字节比较。

| 契约 | 值 |
| --- | --- |
| 输入字段 | order_id、user_id、product_name、quantity、unit_price、order_time、city、status |
| 原始／接收／隔离／重复副本 | 30 / 26 / 3 / 1 |
| paid 订单／销售额 | 11 / 6600 分（66.00 元） |
| 城市缺失／非法日期 | 2 / 1，均警告且保留有效订单 |
| 异常原始行／问题记录 | 7 / 7；其他数据可能一行多问题，两者不应混用 |
| 新样本日期无效但保留的 paid 订单 | SYN011，11.00 元 |

迁移说明：私有工程旧样本的 paid 销售额为 1223.70 元，本公开样本为 66.00 元。两者输入 SHA 和标识不同；数量契约相同是有意保留可解释的边界，而非复制旧数据。只更新快照中依赖样本的断言，质量和金额规则不变。MySQL 历史实测使用旧样本，不能套用为新样本已验收。

`scripts/generate_orders.py` 是另一套带种子的 synthetic 性能生成器，输出规模更大、有匹配元数据，不覆盖本样本。数据许可决定见[发布事项](../docs/RELEASE_DECISIONS.md)。
