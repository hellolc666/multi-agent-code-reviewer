# atomic_coupon：预期与标注

批量库存预留失去原子性，后续商品缺货时前面的库存已经扣除 + 金额恰好达到优惠券门槛时仍被拒绝 + Extracts the same mapping after staging; validation and mutation order are unchanged.

标签由 AI 模拟人工标注，非独立真人复核。此文件放在 repo-after 外，不提供给审查模型。

预期独立行为缺陷：2 条；目标状态 completed，待复核 0 条。

## inventory_partial_reserve_g1

- 位置：minishop/inventory.py:32
- 根因：批量库存预留失去原子性，后续商品缺货时前面的库存已经扣除
- 触发条件：stock book=2 pen=0；reserve_many({'book':1,'pen':1}) 抛错后 book 应仍为2
- 参考严重程度：medium（不作为语义匹配条件）

```python
            if self.available(sku) < quantity:
```

## dev_coupon_minimum_g1

- 位置：minishop/pricing.py:46
- 根因：金额恰好达到优惠券门槛时仍被拒绝
- 触发条件：2000 分小计配 minimum_cents=2000 的优惠券
- 参考严重程度：medium（不作为语义匹配条件）

```python
        if subtotal <= coupon.minimum_cents:
```

独立动态断言见 ../../data/probes.json；这些断言仅验证标签，不由 Judge 执行。
