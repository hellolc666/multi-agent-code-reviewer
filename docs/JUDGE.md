# 独立 Judge 与证据验证

图：Planner → Send(选中 Reviewer) → Judge → Aggregator。
报告保留 raw_findings（原始候选）和 verified_findings（分类、理由、检查项、候选 ID、
来源 Agent、can_comment、verification_incomplete）。默认 findings 只列 confirmed。
options={"judge_enabled":False} 用于消融：findings 是未验证候选，明确 candidate 状态，
不会默认升级为 confirmed；完整分类仍记录 judge_disabled。

第一层检查：路径属于本次 diff、安全路径、RIGHT 行范围、代码与 checkout 一致、
精确片段确实位于锚点、低置信度。不存在路径/伪造行/伪造片段 rejected；缺片段/
低置信度/缺 checkout 证据 uncertain。同 path/line/规范化标题的重复缺陷合并，
保留所有 candidate_ids/source_agents；不同标题不强行合并，避免同一行多个真实缺陷被吞。

受影响的 hunk 上下文行必须有 trigger_condition；即使 confirmed 也不允许作为新增行
inline anchor（can_comment=false）。只有已确认且在新增行的记录 can_comment=true。
这仍是静态审查：checkout 在审查期间必须不可变，没有执行目标源码/测试。

第二层是独立 Judge Prompt，输入仅候选、核对过的代码/hunk、有限静态/测试引用与
同锚点其他候选。明确要求反证、缺陷因果和可复现触发条件；confirmed 必须同时提供
evidence_support 与 reproducible_trigger，风格建议 rejected。低证据 uncertain。
evaluator/gold_labels 不在输入投影中；不允许拿标准答案替代验证。

Judge 错误保留 raw，标 uncertain + verification_incomplete；不同候选在同一锚点
分类冲突记录 verification_conflicts。摘要消费分类结果，没有 confirmed 时明确写
“No confirmed findings”及 uncertain/rejected 数，不把待复核说成没有 Bug。

`tests/test_judge.py` 覆盖有效/伪造路径与行、片段、合并、Judge 不可用、低置信度、
上下文锚点、禁用对照、干净 PR、误否定保留和冲突；没有真实 Judge 准确率结论。
