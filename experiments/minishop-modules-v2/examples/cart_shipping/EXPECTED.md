# cart_shipping：预期与标注

重复添加同一商品覆盖旧数量，订单少买少计价 + 小计恰好达到免邮门槛时仍收取运费 + Names the new stock total without changing positive-quantity validation or missing-SKU behavior.

标签由 AI 模拟人工标注，非独立真人复核。此文件放在 repo-after 外，不提供给审查模型。

预期独立行为缺陷：2 条；目标状态 completed，待复核 0 条。

## cart_overwrite_g1

- 位置：minishop/cart.py:21
- 根因：重复添加同一商品覆盖旧数量，订单少买少计价
- 触发条件：add('book',2) 后 add('book',1)，应为3却为1
- 参考严重程度：medium（不作为语义匹配条件）

```python
        self._quantities[sku] = quantity
```

## pricing_shipping_equal_g1

- 位置：minishop/pricing.py:49
- 根因：小计恰好达到免邮门槛时仍收取运费
- 触发条件：subtotal=5000、默认门槛5000，应免邮却收500分
- 参考严重程度：medium（不作为语义匹配条件）

```python
    shipping = 0 if subtotal > free_shipping_at_cents else shipping_cents
```

独立动态断言见 ../../data/probes.json；这些断言仅验证标签，不由 Judge 执行。
