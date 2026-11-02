# 本轮结果的解释与限制

48 次真实调用流程已经全部结束：24 completed、20 partial、4 failed。
使用 qwen3.5-27b；没有切换备用模型。逐条核对 244 条输出并保存模拟人工匹配理由。
6 个样例包含8个行为缺陷标签和2个干净对照，来自同一仓库及已知变异的组合。
它是探索性回归评测，不能作为独立人工标注大样本测试集或论文显著性证据。

## 可以观察到的现象

- D 的8个预设缺陷均被检出，闭集 Precision/Recall/F1 均为1，但只有2/6次完整完成。
  因此不能据此称“系统准确率100%且稳定可用”。4次 partial 仍可能有未完成的维度。
- 无 Judge 的组产生大量 docstring、静态测试关联缺口和重复根因报告。
  D_no_judge 8 TP/95 FP，D 8 TP/0 FP。Judge 对最终输出筛选有明显影响，
  但这里 FP 定义是“未匹配植入行为缺陷”，不等于每条文档或测试建议都是事实错误。
- C 和 D_no_evidence 在 atomic_coupon 上各漏一个预设缺陷；D 检出两个。
  这是一个样例中的观察，不足以证明检索在所有代码库提升召回。
- D_no_routing 比 D 请求更多（86 对65），且完成率更低（1/6 对2/6）。
  但 order_catalog 还遇到整轮超时，不能将全部差异归因于路由策略。
- D_no_static 与 D 的召回差值来自 order_catalog 的连接失败，
  不能将这2个漏检直接归功于静态工具。其余缺陷样例的召回没有观察到差异。
  该集没有安全漏洞标签，动态路由未选 Security；不能评价 Semgrep 的漏洞召回增益。
- A 与 B 的 order_catalog 没有有效结果，保留为2 FN；不能将这部分解释为模型看不懂代码。

## 可靠性问题与外部干扰

1. `search_symbol` / `search_references` 返回 `invalid_symbol`，导致若干 otherwise 完成的报告为 partial。
   需要核对工具参数契约与模型实际传入格式，不能通过隐藏错误或强行标 completed 修饰指标。
2. Test Mapping 多次60秒超时；Docs 多次90秒超时，部分之前发生 FindingList 格式修复。
3. 中途出现 APIConnectionError；order_catalog/B 最外层记录 PermissionError，无法恢复完整用量。
4. 末尾请求收到 DashScope `Arrearage`（账号余额/欠费状态异常），
   最后一组出现 BadRequestError；clean_catalog_stock/D 的 Judge 也记录 BadRequestError。
   不继续发起在线请求，不修改密钥、Base URL 或 .env。
5. token 用量汇总不完整，结果保留 null；没有推算或伪造成本。

上述因素使本轮模块差异带有明显混杂，不能用于确定模块的因果收益。
保留本轮作为故障诊断和回归基线。网络/账号恢复、工具参数契约及超时问题修复后，
应按相同冻结数据另建结果目录完整重跑，报告本轮与新轮各自结果，不挑选最好的一轮。
正式评估还需要新增独立仓库、真人复核标签，并在预先约定的重复次数下运行。

## 可核查材料

- `REPORT.md`：各组指标和 D 减去单模块移除组的差值。
- `evaluation.json`：TP/FP/FN、定位、完成率、耗时、请求数、逐样例分数和评分哈希。
- `runs.jsonl`：全部48次原始报告，包含失败和 partial。
- `scoring/blind.json` 与 `scoring/decisions_simulated.json`：244条发现及逐条匹配理由。
- `metadata.json` 与 `environment.json`：数据、模型、预算、源文件指纹、工具版本。
- `../validation.json`：每个组件在正常版和变更版的独立行为断言结果。

评测侧动态断言不代表 Judge 执行了测试；Judge 仍为静态证据加模型复核。
