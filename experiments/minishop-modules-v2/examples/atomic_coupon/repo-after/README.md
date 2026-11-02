# Code Review Demo

一个用于测试 Pull Request 代码审查的小型 Python 商店。无需第三方依赖或 API Key。

项目包含商品目录、购物车、库存、优惠券定价、单商品结账和多商品订单。订单会先在
临时库存上校验全部商品，再一次性预留真实库存，避免后续商品失败时留下部分扣减。

```text
minishop/
  catalog.py    商品信息与上下架
  cart.py       购物车与同 SKU 合并
  inventory.py  库存查询、补货和批量预留
  pricing.py    优惠券、运费及金额计算
  checkout.py   单商品结账规则
  orders.py     多商品订单编排
tests/          单元测试和跨模块流程测试
```

运行全部测试：

```powershell
python -m unittest discover -s tests -v
```

这个仓库只有演示数据，不包含真实用户数据或密钥。审查 PR 时，可以结合差异、
周围模块及测试用例判断改动对结账流程的影响。
