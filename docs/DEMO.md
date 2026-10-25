# 三段可复现的离线演示

`scripts/demo.py` 构造三个原始小 diff，写出受审新版本文件与 patch，然后调用
正式 `run_review_diff`、Risk Planner、Reviewer、Judge、Aggregator。FindingList 与
JudgeDecision 来自明确编排的 `ScriptedDemoModel`，**展示链路和证据格式，不是
模型发现缺陷的准确率实验**。脚本无需 key/GitHub，不执行样例源码。

```powershell
.\.venv\Scripts\python.exe scripts/demo.py clean --output examples/demo-clean.json
.\.venv\Scripts\python.exe scripts/demo.py logic --output examples/demo-logic.json
.\.venv\Scripts\python.exe scripts/demo.py security --output examples/demo-security.json
```

clean 修复 `index == len(items)` 越界检查，预期 0 条 confirmed。logic 将分页向上取整
错误改成向下取整，`total=11,size=10` 时丢失最后一页，预期 1 条 confirmed。
security 将参数化查询改为 f-string SQL，姓名带引号可改变查询语义，预期 1 条
confirmed。每个命令另外生成 `.diff` 和 `demo-<name>-repo/`，输出 JSON 有
`demo_model: scripted_mock`；`execution_mode=mock`、`publication.status=dry_run`。
命令拒绝覆盖既有产物；如需重跑请换输出名/目录。可运行
`python -m pytest -q tests/test_demos.py` 验证上述字段。

真实模型演示是可选的独立过程：先配置 Qwen，使用 `reviewer review-diff --live`
查看结果。真实 GitHub 写评论须再经过 JSON 预览和显式 `publish --post-comments`。
