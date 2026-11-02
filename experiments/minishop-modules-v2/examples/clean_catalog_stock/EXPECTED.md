# clean_catalog_stock：预期与标注

Product has no custom falsy behavior; None and inactive checks are equivalent. + Names the new stock total without changing positive-quantity validation or missing-SKU behavior. + Adds a total-unit-count method and an empty/multi-SKU test; no existing contract changes.

标签由 AI 模拟人工标注，非独立真人复核。此文件放在 repo-after 外，不提供给审查模型。

预期独立行为缺陷：0 条；目标状态 completed，待复核 0 条。

这是干净对照，应避免将等价重构或有测试支撑的新增方法报告为行为缺陷。

独立动态断言见 ../../data/probes.json；这些断言仅验证标签，不由 Judge 执行。
