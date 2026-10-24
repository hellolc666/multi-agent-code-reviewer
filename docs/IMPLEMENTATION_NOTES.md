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
