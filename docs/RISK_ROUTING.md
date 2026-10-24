# 风险路由与 Correctness

Planner 输出 Pydantic ReviewPlan：risk、change_summary、affected_files、
selected_agents、focus_points、routing_reasons、budget、mode。
默认 rules；环境 REVIEWER_PLANNER_MODE=hybrid 或共享 runner 的
options={"planner_mode":"hybrid"} 可开启模型补充。

确定性底座：Python 触发 Correctness/Quality；非测试源文件触发 Test Mapping；
存在公开 API 时选择 Docs；认证/权限、SQL/事务、文件/反序列化、网络/并发
风险才增加 Security。纯 README 不派发，纯测试不做源代码测试映射。
大变更提高风险、记录覆盖；规则使用文件/代码静态信号，是启发式而非完整语义证明。
Hybrid 不能移除规则底座；非法 Agent JSON 被拒绝并记录 planner_errors，回退规则。
最多五个 Reviewer，预算不足优先 Security（有风险时）/Correctness，记录省略维度。

Correctness 使用同一 FindingList 和证据工具，审查逻辑、边界、异常、状态一致性、
资源、并发及 N+1；要求触发条件和精确代码片段，不执行目标测试。
保留原四节点；五个分支仍用 LangGraph Send 与 add reducer 合并结果。
report.review_plan 保留路由理由，未派发报告 status=skipped。

测试：tests/test_risk_plan.py、test_planner.py、test_graph_builder.py。
可通过同一输入分别 options max_reviewers/--only 比较调用量；真实差异需阶段 6
同预算实验验证，本阶段不宣称动态路由已经提高准确率。
