# 本地 Diff 与证据工具

```powershell
# checkout 应为补丁新版本；不执行 checkout 中的脚本或测试。
git -c core.quotepath=false diff HEAD~1 HEAD --output example.patch
.\.venv\Scripts\reviewer.exe review-diff --diff example.patch --repo-path . --output report.json
# 默认 mock，无付费请求，无 Semgrep/Ruff 子进程。真实调用须显式 --live：
.\.venv\Scripts\reviewer.exe review-diff --diff example.patch --repo-path . --live --output report.json
# GitHub 入口也默认 mock，真实模型/工具使用 --live。
.\.venv\Scripts\reviewer.exe review https://github.com/OWNER/REPO/pull/42 --live --output report.json
```

本地和 GitHub 均经过 `ReviewInput → _execute_review → 同一 LangGraph`。
报告记录 mode、diff SHA-256、head SHA（GitHub）、文件清单、覆盖 hunk 与省略原因。
本地输入限制 4 MiB；模型 diff 字符预算默认 24000，优先 auth/sql/permission/transaction
路径，按完整 hunk 裁剪，不截断 hunk 内行。新增行与上下文有明确 RIGHT 行号，删除行无
RIGHT 行号。保留 rename/delete/binary 元数据。暂不接受无 git header 的传统 unified diff；
Git 八进制转义路径请使用 `core.quotepath=false` 重新导出，避免默默错误解码。

`CodeContext` 仅读仓库根目录内 UTF-8 文本，拒绝绝对路径、..、Windows drive/ADS、
symlink/junction（含仓库内的链接）、.git/.env/依赖目录。单文件最多 1 MiB，单次读范围
最多 500 行，输出最多 8192 字符；搜索最多 200 个 Python 文件、20 条结果，并标注限制。
只提供 read_file、search_symbol、search_references、get_changed_hunks。AST 不导入模块。
未知工具、缺失文件、错误路径均返回结构化错误，不执行 pytest/shell/目标源码。
仓库必须在审查时保持只读不变；不是针对并发恶意宿主文件替换的 OS 安全隔离。

Security 开启证据循环：默认最多 2 轮（硬上限 3）、6 次工具调用、16000 字符证据。
相同请求、调用或字符/轮次预算耗尽立即停止并记录 budget_exhausted。
未来 Correctness 复用相同模块。模型消息把全部审查数据 JSON 编码为 untrusted 输入，
系统消息固定工具/角色/发布边界；模型无法通过工具接口取得执行或发布权限。

离线 mock 用于验证流程，不能用于准确率、Recall 或成本宣称。真实 API 验证仍 NOT_RUN。
测试：`python -m pytest -q tests/test_diff_context.py`，使用项目 .venv 解释器。
