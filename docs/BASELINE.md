# 阶段 0：原项目基线

检查日期：2026-10-24（Asia/Shanghai）。本仓库是
[Om Solanki 的 multi-agent-code-reviewer](https://github.com/OmkumarSolanki/multi-agent-code-reviewer)
的二次开发，保留原 MIT LICENSE 与版权。定位是可审计的实习展示原型。

## 版本与环境

- 初始分支：`feature/multi-agent-v2`，初始工作区干净。
- 初始 SHA：`1114e3d431b482d19239d71dec50f4c1715110bb`。
- Windows PowerShell；复用项目已有 `.venv`，Python 3.12.7，base 为 `D:\Anaconda`。
- 没有升级 LangGraph/LangChain 大版本；`pip install -e ".[dev]"` 成功。
- 实际依赖：LangGraph 0.6.11、LangChain 0.3.30、langchain-core 0.3.86、
  langchain-openai 0.3.35、langchain-anthropic 0.3.22、Pydantic 2.14.1、
  openai 2.54.0、anthropic 0.125.0、FastAPI 0.162.0、Typer 0.27.3、
  pytest 8.4.2、pytest-asyncio 0.26.0、Ruff 0.17.2。

## 已核对的入口与调用链

```text
reviewer.cli:app (Typer) / reviewer.api:app (FastAPI)
  -> reviewer.runner.run_review
    -> github_client.fetch_pr (metadata + diff + file list + temporary clone)
    -> seed ReviewState (repo_path, changed_files, diff, trace_id, started_at)
    -> build_review_graph / optional --only forced planner
      -> fetch_pr_node (pass through; fetching already happened)
      -> plan_review_node (Python extensions + test paths)
      -> dispatch_specialists (LangGraph Send)
        -> security: Semgrep + FindingList LLM
        -> quality: Ruff + AST complexity + FindingList LLM
        -> test_mapping: AST references + FindingList LLM
        -> docs: public entity docstrings + FindingList LLM
      -> aggregate_findings_node (deduplicate, sort, _SummaryResponse LLM)
      -> ReviewReport JSON
    -> optional _post_pr_comment (default false)
```

并行分支的 findings、agents_succeeded、agents_failed 使用 `Annotated[..., add]`
reducer。空派发直接进入 aggregator，说明不适用。模型构建统一在
`reviewer/agents/base.py:get_llm`，结构化输出入口为 `analyze_with_schema`。
已有 retry/circuit breaker，所有工具均为静态分析，不执行目标项目测试。

上述四节点、Send、基础 planner、聚合、CLI/API/GitHub、重试熔断、报告均为
**上游原有能力**，不作为本次新增成果。

## 基线执行记录

命令均在仓库根目录执行：

| 命令 | 结果 |
|---|---|
| `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` | PASS，editable 安装成功 |
| `.\.venv\Scripts\python.exe -m pip check` | PASS，No broken requirements found |
| `.\.venv\Scripts\python.exe -m pytest -q`（未修改源码） | FAIL，276 passed / 3 failed / 3 skipped |
| `.\.venv\Scripts\python.exe -m ruff check reviewer tests` | PASS，All checks passed |
| 真实 Semgrep | NOT_RUN，PATH 未发现 semgrep；离线测试使用 Mock |
| 真实 GitHub / Qwen | NOT_RUN，本阶段未请求外部审查、未发评论、未调用付费模型 |
| 编码修复后同一 pytest 命令 | PASS，279 passed / 3 skipped / 1 warning，11.74s |
| 编码修复后同一 Ruff 命令 | PASS，All checks passed |

三个原有失败均为 `tests/test_ast_inspector.py` 未指定编码读取包含 UTF-8
标点的 fixture，在 Windows GBK locale 下触发 UnicodeDecodeError。
本阶段最小修复是三处 `read_text(encoding="utf-8")`，保留断言与 fixture。
另有一个 LangGraph `allowed_objects` 默认值的 pending deprecation 警告。

离线图示例（使用现有 fixture / fake LLM，完全不需要 API key）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_graph_builder.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_cli.py tests/test_runner.py
.\.venv\Scripts\reviewer.exe review --help
```

真实 CLI 示例（会请求模型；默认不发布评论）：

```powershell
.\.venv\Scripts\reviewer.exe review https://github.com/OWNER/REPO/pull/42 --output report.json
```

前置条件：配置有效 provider/model/key、可访问模型和 GitHub、git 可用；
私有仓库需要 GITHUB_TOKEN，Semgrep 在 Windows 不可用时需 Docker/WSL2
或接受工具缺失。阶段 1 前 Qwen 尚不支持。不要把上游 README 的性能、
成本、测试数量当成本机测量结果。

## 已发现的待改点（不是已完成功能）

1. Qwen、本地 diff、上下文 sandbox、工具循环、Correctness Reviewer、Risk Plan、
   Judge、共享预算、可信评测尚未实现。
2. 原 LLM wrapper 对全部 Exception 重试，没有区分鉴权、能力不兼容和输出错误。
3. 四个 Agent 在 LLM 失败时退回静态种子仍标成功，工具失败也只记录日志；
   需在阶段 5 中明确 partial 状态和来源。阶段 0 不声称已解决。
4. API 接受 post_comments，没有服务端发布保护；clone 没有固定 SHA，
   发布没有 SHA 检查与幂等性；后续阶段 5 处理，当前不运行发布。
5. GitHub 文件内容读取、test_dirs、symlink 的边界需在阶段 2 统一限制。
6. README 中固定测试数量、线上效果与成本属于上游说明，二改交付时需要改写。
