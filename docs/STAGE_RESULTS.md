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
