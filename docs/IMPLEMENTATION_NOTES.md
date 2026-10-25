# 二改执行记录与调整

按任务书 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 顺序推进。每个阶段通过检查后
单独提交；未执行的在线验证明确标记 NOT_RUN。保留上游 MIT LICENSE。

## 阶段 0

- 仓库已经有专用 `.venv`，故复用并执行 editable dev 安装，没有新建第二套环境。
- 原测试 3 个 Windows GBK 解码失败，最小修复 fixture 读取的 UTF-8 编码。
- 基线、调用链、依赖版本和待改点见 `BASELINE.md`。
- 初始依赖未作大版本升级；未读取/覆盖 `.env`、实际密钥和运行数据。

完整阶段结果见后续 `STAGE_RESULTS.md`，只填写已运行结果。

## 阶段 1

- 复用现有 ChatOpenAI，不增加依赖、不升级 LangGraph/LangChain。
- Qwen 使用 DASHSCOPE_MODEL，不继承 LLM_MODEL，避免切换 provider 后误用型号。
- 显式 function_calling 是本地已安装 SDK 可测试的首选结构化路径；
  只有明确能力不兼容才切到普通 JSON。详见 `QWEN.md`。
- 关闭 SDK 自带重试，复用项目已有 retry/circuit breaker 并只重试瞬态错误。
  输出修复次数在两种格式之间共享，配置最大 2。
- 保留程序端 FindingList() 的兼容性，同时拒绝模型省略 findings 字段导致的
  默认空列表（包含 SDK 已转成 Pydantic 实例的场景）。
- 新增真实 SDK + MockTransport 回归和共享 Runner/Graph 端到端离线验证。
- 在线请求单独通过 smoke 的 --live 开启，本阶段未使用真实凭证。
- README 先标明来源、当前阶段及上游历史性能数据边界；完整架构和评测改写
  留到对应阶段，不提前声称 Correctness/Judge/动态路由已实现。

## 阶段 2–7 的最小调整

- 未建立第二套 graph/adapter：本地与 GitHub 输入复用 `_execute_review`；保留四个
  上游 Agent/静态工具，新增 Correctness、Risk Planner 与 Judge。
- 受审源码只读，不运行仓库测试脚本；静态工具通过隔离的只读路径使用。
  额外取证限制轮数/次数/字符数。大 diff 选完整 hunk 并记录被省略覆盖。
- 不引入 SQLite Checkpointer/Redis/HITL。人工检查 JSON + 独立 publish CLI 是
  最小可审计方案；GitHub marker/SHA 校验不提供跨进程原子性。
- 因没有真人签核和付费凭证，阶段 6 按任务书允许的 20 例首版推进，gold 标注
  明示待签核，质量指标留 null。A 用单 general Reviewer 近似基线，而非假定上游
  有不存在的完整单 Agent 接口。结果中保留每个失败运行。
- 三个离线 demo 的候选由 ScriptedDemoModel 明确给出，验证的是正式流水线的路由、
  Judge 与报告；真实模型能力要另行 `--live` 验证。
