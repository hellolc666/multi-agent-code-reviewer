# snapshot_quantity：预期与标注

库存快照暴露内部可变字典，调用者能绕过库存校验修改真实库存 + 小计忽略商品数量，多个同类商品只收取一个的价格 + Equivalent aggregation after unchanged input validation; missing and repeated SKUs retain behavior.

标签由 AI 模拟人工标注，非独立真人复核。此文件放在 repo-after 外，不提供给审查模型。

预期独立行为缺陷：2 条；目标状态 completed，待复核 0 条。

## inventory_snapshot_alias_g1

- 位置：minishop/inventory.py:14
- 根因：库存快照暴露内部可变字典，调用者能绕过库存校验修改真实库存
- 触发条件：snapshot()['book']=0 改变原库存；快照也随后续补货变化
- 参考严重程度：medium（不作为语义匹配条件）

```python
        return self._stock
```

## pricing_quantity_g1

- 位置：minishop/pricing.py:43
- 根因：小计忽略商品数量，多个同类商品只收取一个的价格
- 触发条件：单价2000分、数量2，应得到4000分小计
- 参考严重程度：medium（不作为语义匹配条件）

```python
    subtotal = sum(catalog.purchasable(line.sku).price_cents for line in lines)
```

独立动态断言见 ../../data/probes.json；这些断言仅验证标签，不由 Judge 执行。
