# 扩充版 minishop 评测集说明

基线仓库：https://github.com/hellolc666/code-review-demo，固定提交 `4353804fcf686c04a0ae4345bac223815d82f6b2`。

每个样例都是从同一正常版本独立构造的一次代码变更，包含 unified diff、全部变更后 Python 源码与测试、README 和评测侧标签；仅排除无运行逻辑的 .gitignore。不是从旧片段数据集改名，也没有为每个样例创建 GitHub PR。

共 20 例：开发集 4 例，正式测试集 16 例（10 个缺陷变更、6 个合理变更）。每个缺陷样例只有一个根因标签；同一根因的重复报告不会重复计分。

标签由 Codex 模拟人工编写，尚未经真人签字确认。标签、独立断言以及评测结论不提供给 Reviewer。正常仓库原有测试代码作为项目上下文提供给 Reviewer。

|样例|划分|预期|改动与验证内容|
|---|---|---|---|
|`dev_exact_stock`|dev|有缺陷|库存刚好等于购买数量时，结账被错误拒绝|
|`dev_coupon_minimum`|dev|有缺陷|金额恰好达到优惠券门槛时仍被拒绝|
|`dev_readme`|dev|合理改动|Clarifies test command working directory without changing behavior.|
|`dev_inventory_compare`|dev|合理改动|Equivalent stock comparison for the same arguments; baseline tests remain valid.|
|`cart_overwrite`|test|有缺陷|重复添加同一商品覆盖旧数量，订单少买少计价|
|`catalog_inactive`|test|有缺陷|已下架商品通过 purchasable 校验，可继续被订单购买|
|`inventory_snapshot_alias`|test|有缺陷|库存快照暴露内部可变字典，调用者能绕过库存校验修改真实库存|
|`inventory_partial_reserve`|test|有缺陷|批量库存预留失去原子性，后续商品缺货时前面的库存已经扣除|
|`pricing_quantity`|test|有缺陷|小计忽略商品数量，多个同类商品只收取一个的价格|
|`pricing_shipping_equal`|test|有缺陷|小计恰好达到免邮门槛时仍收取运费|
|`pricing_discount_fraction`|test|有缺陷|折扣产生小数分，破坏整数金额及向下取整约定|
|`orders_reserve_before_price`|test|有缺陷|价格或优惠券校验移到扣库存之后，失败订单也消耗库存|
|`orders_live_staging`|test|有缺陷|预校验直接扣真实库存，成功订单重复扣减，后续失败也无法回滚|
|`test_receipt_assertion`|test|有缺陷|返回值断言退化为真值判断，错误的非空收据也能通过测试|
|`clean_cart_setdefault`|test|合理改动|Equivalent aggregation after unchanged input validation; missing and repeated SKUs retain behavior.|
|`clean_catalog_guard`|test|合理改动|Product has no custom falsy behavior; None and inactive checks are equivalent.|
|`clean_pricing_named_net`|test|合理改动|Introduces a name for an unchanged integer expression; coupon and shipping behavior is preserved.|
|`clean_orders_named_quantities`|test|合理改动|Extracts the same mapping after staging; validation and mutation order are unchanged.|
|`clean_restock_named_total`|test|合理改动|Names the new stock total without changing positive-quantity validation or missing-SKU behavior.|
|`clean_cart_total_feature`|test|合理改动|Adds a total-unit-count method and an empty/multi-SKU test; no existing contract changes.|

## 标签如何验证

`validate_dataset.py` 将每个 patch 应用到独立基线，验证应用后文件与样例保存的仓库完全一致；在正常版本和修改版本分别运行评测侧行为断言。缺陷样例应在正常版本通过、在修改版本失败；合理改动同时通过断言及仓库业务测试。测试断言削弱样例通过注入错误回执验证测试是否能拦截错误。

这些动态断言用于核验评测标签，不代表 Judge 动态执行了代码。Test Mapping 仍然是静态关联分析。

## 评分

将最终报告的每条发现隐藏组名并打乱后，模拟人工核对缺陷语义、触发条件和影响。语义匹配之后，还必须满足同文件且位置误差不超过 2 行，才计 TP。每个根因最多匹配一次。未匹配报告为 FP；未检出标签为 FN。

合理改动用于衡量误报。报告保留失败与 partial 运行；不把没有完成审查解释成没有 Bug。A/B 统计未经 Judge 的最终 findings，C/D 统计 Judge 后的最终 findings。

当前数据只覆盖这个小商店的逻辑、回归和测试断言问题，没有安全漏洞标签；不能据此宣称通用安全审查能力。
