# 阶段验收记录

## 阶段 0：PASS（2026-10-24）

- 原状态：分支 `feature/multi-agent-v2`，SHA
  `1114e3d431b482d19239d71dec50f4c1715110bb`，工作区干净。
- 改动：`docs/BASELINE.md` 调用链/版本/基线；`docs/IMPLEMENTATION_NOTES.md`
  执行策略；`tests/test_ast_inspector.py` 三处显式 UTF-8 读取。
- `python -m pip install -e ".[dev]"`：PASS。
- `python -m pip check`：PASS。
- 原始 `python -m pytest -q`：FAIL，276 passed / 3 failed / 3 skipped，12.49s。
- 修复后 `python -m pytest -q`：PASS，279 passed / 3 skipped，11.74s。
- `python -m ruff check reviewer tests`：PASS（修改前、后）。
- 全部命令使用项目 `.\.venv\Scripts\python.exe`。
- 真实 Qwen/GitHub/Semgrep：NOT_RUN。Semgrep 不在 PATH，测试 Mock 其结果。
- 一个 LangGraph pending deprecation warning，保留依赖兼容性，未升级大版本。
- 通过后提交；提交 SHA 用 `git log --oneline` 查看，避免在提交内自引用。
- 遗留：见 BASELINE；下一阶段复用 base/config 接入 Qwen 和有界 JSON fallback。

## 阶段 1：PASS（离线验收，2026-10-24）

- 阶段 0 提交：`7a629177b5bb5c0be7b1bc4eaebeedd24a83b010`。
- 修改 `reviewer/config.py`：qwen provider、必填 DashScope 字段、HTTPS 端点
  校验、共享格式修复配置、配置 repr 隐藏所有密钥。
- 修改 `reviewer/agents/base.py:get_llm/analyze_with_schema`：复用 ChatOpenAI；
  Pydantic 验证、能力不兼容白名单降级、共享最多 2 次格式修复、瞬态重试。
- 更新 `.env.example`、README、IMPLEMENTATION_NOTES；新增 `docs/QWEN.md`、
  `scripts/smoke_qwen.py`、`tests/test_qwen.py`，扩展 provider 常量测试。
- 针对性检查（首次）：`python -m pytest -q tests/test_qwen.py tests/test_base_agent.py tests/test_config.py`
  PASS，92 passed，13.71s（随后补入两个端到端/默认值回归用例）。
- 最终 `python -m pytest -q`：PASS，**339 passed / 3 skipped**，13.86s；
  比阶段 0 新增 **60** 个测试用例，测试范围见 QWEN.md。
- `python -m ruff check reviewer tests scripts/smoke_qwen.py`：PASS。
- `python -m pip check`：PASS。
- `python scripts/smoke_qwen.py`：PASS，退出码 0；输出
  `{"status":"NOT_RUN","live":false,"reason":"requires --live"}`。
- `.\.venv\Scripts\reviewer.exe review --help`：PASS，退出码 0，原命令可用。
  Windows 控制台有部分原有 Unicode 符号显示问题，不影响 JSON 文件输出。
- `git diff --check`：PASS。
- 命令均使用项目 `.\.venv\Scripts\python.exe`；单独 smoke 不读取 `.env`。
- 真实 Qwen：**NOT_RUN**。没有真实 API 成功证据，不声称准确率/成本/性能。
  有凭证时复现：`python scripts/smoke_qwen.py --live`（上述项目解释器）。
- 真实 GitHub/Semgrep：NOT_RUN；没有发表评论或推送远程。
- 遗留：上游 Agent 静态种子状态、API 发布保护、共享预算需后续阶段解决；
  LangGraph pending deprecation warning 保留。下一阶段是本地 Diff 与上下文沙箱。
- 分支 `feature/multi-agent-v2`，阶段通过后创建本地提交；完整 SHA 见 git log。

## 阶段 2：PASS（离线验收，2026-10-24）

- 新增 `models/inputs.py`、`tools/context.py`、`tools/diff_input.py`、
  `agents/evidence.py`、`offline.py`、`tests/test_diff_context.py` 和 `LOCAL_DIFF.md`。
- runner 的 GitHub/本地输入共享 `_execute_review`；CLI 新增 review-diff/--live；
  graph/report 记录输入指纹、覆盖、证据与预算事件。Security 接入有限证据循环。
- 收紧 GitHub/test_dirs 静态读取边界与 Ruff 隔离配置，mock 不调用模型/工具子进程。
- 首次准备副本测试因 Windows 默认 pytest 临时目录权限冲突出现 setup ERROR；
  换用任务专用 --basetemp 后完整测试 PASS：**355 passed / 4 skipped**，14.37s。
  新增 17 个用例，其中真实 symlink 创建因 Windows 权限跳过；3 个上游 live 跳过。
- `python -m ruff check reviewer tests`：PASS。
- 本阶段完整测试包括本地 CLI、文件指纹、跨文件引用、路径逃逸、超长/二进制/非 UTF-8、
  rename/delete/binary、坏 hunk、证据重复请求预算，以及全部旧回归。
- 原 SDK e2e fixture 增补 EvidenceTurn 响应；tracing 测试显式 live（仍 Mock graph）。
- 真实 Qwen/GitHub/Semgrep：NOT_RUN；没有发布和远程推送。独立阶段提交后继续阶段 3。
