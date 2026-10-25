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

## 阶段 3：PASS（2026-10-24）

- 新增 Correctness（逻辑/边界/状态/资源），复用 FindingList 与证据工具。
- 新增 Pydantic ReviewPlan；规则风险底座 + 可选 hybrid；路由白名单、理由、预算和
  被省略维度进入报告；README/测试/认证/SQL/普通函数/大文件路由有区别。
- 保留原四节点与 LangGraph Send/add reducer，新增第五节点。报告增加 skipped 状态。
- 新增 test_risk_plan.py 9 个用例；既有五节点图、原模型映射回归相应更新。
- `python -m pytest -q --tb=short -p no:cacheprovider`：PASS，**364 passed / 4 skipped**，13.78s。
- `python -m ruff check reviewer tests` / `git diff --check`：PASS。
- 初次回归发现旧测试两处固定四节点断言，已更新并完整重跑。
- Qwen/GitHub/Semgrep 在线：NOT_RUN；仍有上游 LangGraph 警告。接下来实现 Judge。

## 阶段 4：PASS（2026-10-24）

- 新增 judge.py、verification.py，图在五 Reviewer 汇合后运行独立 Judge。
- 确定性路径/RIGHT 行/真实片段验证 + LLM 反证；保留 raw/confirmed/rejected/uncertain，
  重复来源、上下文锚点发布限制、冲突、失败时 verification_incomplete。
- Aggregator 默认只展示 confirmed；没有 confirmed 时摘要明确列出待复核/驳回，
  judge_enabled=false 可保留未验证候选用于消融，不伪造确认。
- 新增 test_judge.py 14 个用例；包含误否定可观测性和 gold 不进入 Judge 投影。
- `python -m pytest -q --tb=short -p no:cacheprovider`：PASS，**378 passed / 4 skipped**，14.40s。
- `python -m ruff check reviewer tests`：PASS；文档 JUDGE.md 说明接口及静态验证限制。
- Qwen/GitHub 在线：NOT_RUN；下一阶段为统一预算、失败治理、追踪与受控发布。

## 阶段 5：PASS（离线验收，2026-10-24）

- 新增 reliability/harness.py、publication.py、HARNESS.md 和预算/发布回归测试。
- 所有模型尝试共享请求预算与 Semaphore；真实 token 未提供时为 null。
  总时限含输入获取，失败保留已完成结果；取消传播；静态种子与模型失败分开记录。
- 新增 publish 预览/显式发布，API 禁止直接发布；检查 head SHA 与远端幂等标识。
  Git clone 凭证移出参数，固定 head SHA，隔离 git hooks/config。
- 最终实际项目测试：`python -m pytest -q -p no:cacheprovider`，
  **394 passed / 4 skipped**，14.77s；新增 16 个用例。
- 初次准备测试修复 report 预算变量名；项目复跑发现旧 CLI 断言期待异常原文，
  更新为验证脱敏后全量通过。Ruff 普通权限缓存写入失败，改用 --no-cache。
- `python -m ruff check --no-cache reviewer tests` / `git diff --check`：PASS。
- 真实 Qwen/GitHub/Semgrep：NOT_RUN。GitHub 跨进程首次发布和 head 校验后变动
  非原子，详见 HARNESS.md；无伪造 exactly-once 保证。下一阶段为冻结评测集与消融。

## 阶段 6：PASS（离线工程验收；人工/在线验收待完成）

- 新增 evals/ 的 20 个独立原创样例、JSONL/schema、固定 10/10 划分、SHA manifest、
  A/B/C/D runner、人工语义匹配计分、旧报告重算、逐次失败保存与 3-run 一致性。
- 8 逻辑 / 4 安全 / 4 测试回归 / 4 clean；标签经过 Codex 静态检查，
  标记 curated_pending_human_signoff；**没有伪造人工签核**。
- 数据 SHA：`4a7461fb965c440d813b01c633ebcb489810421710d163968ca8d6ea4134b7b1`。
- 准备阶段的严格新增行校验发现 3 个上下文锚点，改为明确新增语句后 freeze。
- `python -m evals.run --split all --output evals/results/offline-v1.json`：
  PASS，80 次运行；`--split test --limit 3 --repeats 3`：PASS，36 次。
  运行时间/请求数仅为离线流程记录，质量指标为 null、not_live_benchmark。
- 实际项目 `python -m pytest -q -p no:cacheprovider`：**401 passed / 4 skipped**，18.07s。
  新增 7 个评测测试；SHA 篡改、gold 隔离、位置与语义分离、重复匹配、失败保存均覆盖。
- `python -m ruff check --no-cache reviewer tests evals` / `git diff --check`：PASS。
- 真实 Qwen 消融、人工签核：NOT_RUN；因此未证明多 Agent/Judge 优势或真实成本收益。
  工程入口完整，后续外部验收命令见 evals/README.md。阶段 5 SHA：ddc740b。

## 阶段 7：PASS（离线工程验收，2026-10-25）

- README 重写来源/许可/上游能力与本次扩展对照、Windows 配置、架构图、本地/PR
  命令与性能数据边界；原上游截图和数字不作为本次证明。
- 新增 ARCHITECTURE.md、DECISIONS.md、TESTING.md、DEMO.md；更新实现说明、
  CLI 帮助、API 脱敏、Docker Compose Qwen 变量；保留原 Dockerfile。
- `scripts/demo.py` 调用共享 runner，生成三份 patch/checkout/JSON：
  clean 0、logic 1、security 1 条 confirmed。候选由 ScriptedDemoModel 预设，
  不是模型检测准确率证据。`tests/test_demos.py` 3 个测试 PASS。
- 原仓库 `pytest -q -p no:cacheprovider`：**404 passed / 4 skipped**，
  15.97s；`ruff check --no-cache reviewer tests evals scripts`：PASS；
  `pip check`：PASS；`scripts/smoke_qwen.py`：PASS，返回 NOT_RUN/live=false。
- 原仓库 `git diff --check`：PASS；阶段 6 提交 SHA 为 `74ad817`，
  阶段 7 SHA 见 git log；未向远程推送。
- 真实 Qwen/GitHub/Semgrep、人工标签签核、Docker build：NOT_RUN。
  一个上游 LangGraph pending deprecation warning 和 GitHub 非原子评论竞态仍存在。
