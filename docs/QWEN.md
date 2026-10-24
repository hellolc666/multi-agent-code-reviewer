# 阶段 1：Qwen 兼容接入

所有 Reviewer 和摘要继续复用 `reviewer.agents.base.get_llm` /
`analyze_with_schema`，没有新增第二套 Model Adapter、Runner 或 Graph。

## 配置

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:LLM_PROVIDER = "qwen"
$env:DASHSCOPE_API_KEY = "<your-key>"
$env:DASHSCOPE_BASE_URL = "<your-workspace-openai-compatible-https-url>"
$env:DASHSCOPE_MODEL = "<your-enabled-model>"
$env:REVIEWER_JSON_REPAIR_RETRIES = "2"
$env:LANGCHAIN_TRACING_V2 = "false"
```

上面的值都是占位符，替换后再运行。不要把真实值写入 Git。
三个 DASHSCOPE 字段必填；URL 必须是 HTTPS，不接受 userinfo、query 或 fragment，
避免误把密钥拼入 URL。模型和地域/工作空间都不硬编码。Qwen 不会误用
OPENAI_API_KEY、OPENAI_BASE_URL 或遗留 LLM_MODEL。显式 Python 参数
`get_llm(provider="qwen", model="...")` 可以覆盖 provider 和 DASHSCOPE_MODEL。

`.env.example` 只提供空密钥/端点/型号字段，CLI 保留现有 dotenv 加载行为。
smoke 脚本只读进程环境变量，不自动打开 `.env`。原 Anthropic/OpenAI 的
provider、model 参数、默认型号和环境变量接口保留，测试也继续覆盖。
`config.snapshot()` / `QwenConfig` 的 repr 隐藏密钥；不要把 dataclass
转成字典后整份打印，敏感值仍须供调用方构建客户端。

## 结构化输出策略

1. Qwen 使用现有 `langchain_openai.ChatOpenAI`，显式 base_url / api_key / model。
2. 先调用 `with_structured_output(schema, method="function_calling")`。
   显式选择方法避免已安装 LangChain 内部 json_schema 自动切换掩盖兼容路径。
3. 只有绑定的 NotImplementedError，或 400/422 响应明确说明 tools /
   tool_choice / function_call / response_format / json_schema 不受支持，
   才进入普通 Chat JSON 模式。普通模式不再发送 tools 或 response_format，
   在系统消息中提供 JSON schema，调用 Pydantic `model_validate_json`。
4. 所有原生结果也经过 Pydantic 校验。原生返回 None、非法枚举、越界 confidence、
   非法 JSON、缺失 findings 均不视为成功。只有显式 `{"findings": []}` 才代表
   模型输出空发现；程序构造 `FindingList()` 的原有接口保留。
5. 原生/普通模式**共享**最多 2 次格式修复（环境可设 0、1、2）。
   从原生切到普通模式最多一次，因此每次 analyze 最多 4 次逻辑请求。
   不把原始非法响应或验证输入写入日志/下一次 Prompt；只发送通用修复要求。
6. 鉴权、权限、模型不存在、无关 400 不触发降级或重试；超时/连接错误/
   429/5xx 复用现有 async_retry（最多 3 次）和 circuit breaker。
   SDK 内层重试关闭，避免叠加。取消立即传播。
7. 最坏情况每次 analyze 为 12 次 HTTP 尝试（4 逻辑请求 × 3 瞬态尝试）；
   Agent 现有超时仍生效。全 review 的共享请求预算在阶段 5 才统一实现。
   摘要现有的外层重试暂时保留，不能把每次 analyze 的上限误称为整个 PR 上限。

格式重试/降级事件使用 `structured_output_repair` / `structured_output_fallback`
日志，记录 schema 与剩余修复数，不记录完整 Prompt、源码或密钥。
最终非法输出抛出 `StructuredOutputError`，不会制造空列表假成功。
底层操作错误仍向上传递。**四个上游 Agent 的静态种子 fallback/成功状态语义
尚未重构**；本阶段只保证共享模型层不会伪造成功，全局 partial 和来源标记
按阶段 5 处理，参见 BASELINE。

参考官方文档（本实现仍以已安装 SDK 的本地离线测试为准）：

- [阿里云结构化输出](https://help.aliyun.com/en/model-studio/qwen-structured-output)
- [阿里云错误码](https://help.aliyun.com/en/model-studio/error-code)
- [LangChain with_structured_output](https://reference.langchain.com/python/langchain-openai/chat_models/base/BaseChatOpenAI/with_structured_output)

端点/模型的能力会不同；没有实际在线测试前，不声称指定端点支持此调用方式。
若账户/型号需要特殊推理模式参数，本阶段不擅自猜测或强加 enable_thinking 值；
服务端拒绝会显式报错，后续有真实凭证再按账户能力做最小适配。

## 离线测试（默认）

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/test_qwen.py tests/test_base_agent.py tests/test_config.py
.\.venv\Scripts\python.exe -m ruff check reviewer tests scripts/smoke_qwen.py
.\.venv\Scripts\python.exe scripts/smoke_qwen.py
```

新测试包括配置和坏环境变量、旧 Provider 回归、Pydantic 解析、修复上限、
能力错误/鉴权错误分类、429/5xx/超时、取消、脱敏、真实 LangChain/OpenAI SDK
的 MockTransport 工具调用与普通 JSON 路径，以及 fixture + 共享 Runner +
LangGraph + 四 Reviewer 的离线端到端测试。所有外部服务由 Mock 替换。

## 真实冒烟（单独显式开启）

```powershell
.\.venv\Scripts\python.exe scripts/smoke_qwen.py --live
# Optional explicit local diff, UTF-8, <= 32 KiB:
.\.venv\Scripts\python.exe scripts/smoke_qwen.py --live --diff .\example.patch
```

无 --live：输出 `NOT_RUN`，不构造客户端，不访问网络。
有 --live 但缺配置/请求失败：输出 `FAIL`，退出码 1；错误只显示类型。
成功必须两次实际模型请求分别生成并验证 FindingList 和摘要，输出模型名、
实测时长和验证后的数据；有格式/网络修复时请求次数可能更多。
`usage=null` 表示本阶段 wrapper 尚未收集真实 token，绝不伪造 token 或成本。
原生格式被拒绝时日志可以看到实际降级。

这只验证格式与接入，不是准确率评测；报告中的候选未经过 Judge。
本轮验收没有执行 --live，真实 Qwen 为 **NOT_RUN**，也没有 GitHub 写操作。
