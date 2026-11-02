# order_catalog：预期与标注

预校验直接扣真实库存，成功订单重复扣减，后续失败也无法回滚 + 已下架商品通过 purchasable 校验，可继续被订单购买 + Introduces a name for an unchanged integer expression; coupon and shipping behavior is preserved.

标签由 AI 模拟人工标注，非独立真人复核。此文件放在 repo-after 外，不提供给审查模型。

预期独立行为缺陷：2 条；目标状态 completed，待复核 0 条。

## orders_live_staging_g1

- 位置：minishop/orders.py:29
- 根因：预校验直接扣真实库存，成功订单重复扣减，后续失败也无法回滚
- 触发条件：库存3购买1，checkout 和 reserve_many 各扣1，剩1而非2；后续缺货也会留下前项扣减
- 参考严重程度：medium（不作为语义匹配条件）

```python
        staged = self._inventory
```

## catalog_inactive_g1

- 位置：minishop/catalog.py:30
- 根因：已下架商品通过 purchasable 校验，可继续被订单购买
- 触发条件：active=False 的商品传给 Catalog.purchasable 或 OrderService.place
- 参考严重程度：medium（不作为语义匹配条件）

```python
        if product is None:
```

独立动态断言见 ../../data/probes.json；这些断言仅验证标签，不由 Judge 执行。
