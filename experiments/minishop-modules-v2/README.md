# 多文件、多函数评测与模块消融

本轮基于 `code-review-demo` 固定提交 `4353804fcf686c04a0ae4345bac223815d82f6b2`。
每例是独立的完整仓库快照加 unified diff，不是已发布的 GitHub PR。
标签由 Codex 模拟人工设计，未经独立真人复核；不能写成“专家人工标注”。
这批组合复用了 minishop-v1 已知变异，其中优惠券条件曾作为开发样例；
因此它是复杂变更回归与消融探索集，不是未见过的独立测试集。
不与旧 20 例的历史模型结果合并，不从本次输出反向修改预期标签。

## 样例与预期

|ID|改动|预期独立缺陷|不应报告的正常改动|
|---|---|---|---|
|atomic_coupon|库存、优惠券、订单三个文件|2：批量预留部分扣库存；恰好达到优惠券门槛被拒绝|订单提取 quantities 局部变量|
|cart_shipping|购物车、计价、库存三个文件|2：重复添加覆盖旧数量；恰好达到免邮门槛仍收费|补货提取中间变量|
|order_catalog|订单、目录、计价三个文件|2：订单预校验直接扣真实库存；下架商品被允许购买|计算折后净价提取中间变量|
|snapshot_quantity|库存、计价、购物车三个文件|2：快照暴露可变库存；计价忽略购买数量|setdefault 后累加购物车数量|
|clean_checkout|购物车、计价、订单三个文件|0|等价重构，保持验证及扣库存顺序|
|clean_catalog_stock|目录、库存、购物车与新增测试|0|等价条件、补货重构、新增总件数方法及测试|

精确行号、根因、触发条件和严重程度在 `data/labels_simulated.json`。
例如 atomic_coupon：book 库存2、pen库存0，批量预留各1件失败后 book 应仍为2；
小计2000分、优惠券门槛2000分、折扣10%时，应得到200分优惠。
其余触发断言见 `data/probes.json`，每个根因单独验证，避免一个失败遮盖另一个。

前端使用 `examples/<ID>/repo-after` 作为代码目录，上传同级 `change.diff`。
不要将标签或 probes 放入模型审查输入。
预期 completed、缺陷例2条独立发现、干净例0条，均无待复核；这是目标，不是保证。

## 实验组

|组|设置|比较目的|
|---|---|---|
|A|单 General Reviewer，无静态工具、主动检索和 Judge|通用单 Agent 基线|
|B|固定五 Reviewer + 静态分析，无主动检索、无 Judge|多专家架构|
|C|B + Judge|与 B 比较 Judge 作用|
|D|动态规则路由 + 静态分析 + bind_tools 证据检索 + Judge|完整方案|
|D_no_judge|仅关闭 D 的 Judge|Judge 单因素对照|
|D_no_evidence|仅关闭 D 的主动证据工具|主动检索单因素对照|
|D_no_routing|固定五 Reviewer，其余同 D|路由选择的影响|
|D_no_static|仅关闭 Ruff、Semgrep 和 Quality AST 复杂度|静态分析信号的影响|

所有组保留 diff 解析、路径安全、输入校验；启用 Judge 的组保留证据锚点和 schema 约束。
“无证据工具”仍提供变更文件上下文，Judge 固定的证据检查也保留，并非完全无代码上下文。
无 Judge 的 A/B/D_no_judge 输出候选，不伪装成已确认；按最终 findings 同口径评分。
动态路由是现有规则路由，不是模型路由；不会为了让静态工具显得有用而额外选择 Security。
该集没有安全漏洞标签，不能据此评价安全审查召回率或 Semgrep 漏洞检测能力。

## 控制变量与指标

- 同一模型、temperature=0、相同预算、相同仓库与 diff，串行运行，按样例轮换组别顺序。
- 从根目录 `.env` 加载配置，但仅在实验进程内禁用模型 fallback，防止组间模型混杂。
- 保存数据 SHA256、实现文件 SHA256、原始报告、耗时、token 用量、失败记录；不写密钥。
- 先打乱并隐藏组别导出发现，再显式记录模拟人工语义匹配理由；隐藏组别不保证完全盲法，文字可能暴露架构。
- TP：根因语义正确、文件一致且位置误差不超过2行。一个标签只匹配一次；重复报告计 FP。
- 这是对已植入行为缺陷标签的闭集评分。未匹配的风格、文档或测试建议计入基准 FP，并不自动说明建议本身错误；结果不能当作所有审查建议的人工接受率。发现标签之外的实际行为缺陷应另记争议，不事后修改本轮标签。
- Precision=TP/(TP+FP)，Recall=TP/(TP+FN)，F1=2TP/(2TP+FP+FN)。空分母保留 null。
- 同时报告每变更误报数、干净样例误报率、语义命中的定位准确率、完成率、P50/P95耗时、请求数和 token。
- failed/partial 不剔除；缺失发现仍计 FN。未知 token 或价格不猜测，不计算虚构费用。
- 每组6例、仅1次运行，样本小且同源；不作显著性或通用能力结论。正式结论需要扩充独立仓库、真人复核并重复运行。
- A→B 涉及多个变化，C→D 同时涉及路由与检索，只能作架构比较；D 与 D_no_* 才是单模块对照。

## 复现

从项目根目录运行，先确保 `.venv/Scripts` 和已安装 Semgrep 的目录在 PATH。
静态工具按项目当前规则运行；Semgrep `p/python` 为远端规则集，未内容锁定，跨日期复现有局限。

```powershell
.\.venv\Scripts\python.exe -m evals.module_ablation build
.\.venv\Scripts\python.exe -m evals.module_ablation validate
.\.venv\Scripts\python.exe -m evals.module_ablation run --live --output experiments/minishop-modules-v2/live-new
.\.venv\Scripts\python.exe -m evals.module_scoring export experiments/minishop-modules-v2/live-new
```

读取 `scoring/blind.json`，参考标签填写 `decisions_template.json`，另存
`decisions_simulated.json`，每条填写 gold_id 或 null 及具体理由，然后：

```powershell
.\.venv\Scripts\python.exe -m evals.module_scoring score experiments/minishop-modules-v2/live-new
```

既有结果目录不可覆盖。未完成所有计划运行时拒绝评分，避免只选成功运行。
不加 `--live` 只做 Mock 管线检查，评分脚本拒绝将 Mock 输出当作模型质量指标。
动态行为断言是评测侧标签验证，Judge 本身不运行这些测试，Test Mapping 仍是静态关联。
