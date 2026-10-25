# 测试与验证

Windows PowerShell，项目根目录：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check --no-cache reviewer tests scripts evals
git diff --check
.\.venv\Scripts\python.exe scripts/smoke_qwen.py
```

默认单元/集成测试使用 Mock/Fake SDK 与临时 checkout，不读取密钥或调用付费 API。
覆盖 provider 与 JSON fallback、基础可靠性、路径/符号链接/二进制/超长 diff、
跨文件上下文与工具限额、路由白名单及 Send、Judge 三态、共享并发预算、取消与
超时、脱敏、发布阻止/head SHA/幂等、数据 SHA、gold 隔离、人工匹配数学、
离线 A/B/C/D 和三条演示。Windows 上需要符号链接权限的 1 项和 3 项真实网络
测试可能 skip；准确数目以 STAGE_RESULTS.md 的实际执行记录为准。

真实模型/工具须单独显式执行，不作为 CI 的默认门槛：

```powershell
# 配好 Qwen 环境后
.\.venv\Scripts\python.exe scripts/smoke_qwen.py --live
.\.venv\Scripts\python.exe -m evals.run --live --split test --output evals/results/live-new.json
# GitHub PR 需另提供 URL/授权；默认只写本地 JSON
.\.venv\Scripts\reviewer.exe review https://github.com/owner/repo/pull/42 --live --output pr-report.json
```

本机 Semgrep 未运行，离线测试仅模拟结果。Dockerfile 固定 Semgrep 版本，但容器
构建尚未验证。LangGraph 当前依赖发出 `allowed_objects` 将改变默认值的 pending
deprecation 警告；未因该警告升级上游依赖大版本。
