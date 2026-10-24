# 预算、失败与发布

`ReviewBudget` 是每次运行独立的 Pydantic 配置；通过 runner 的
`options={"budget": {...}}` 或 HTTP 请求的 `options.budget` 传入。
默认最多 5 Reviewer、30 次模型请求、3 个并发调用、每 Reviewer 6 次上下文工具、
2 轮取证、每次工具输出 8192 字符、模型输入 64000 字符、单次请求 30 秒、总计 180 秒。
格式修复、能力降级和瞬态重试也消耗请求预算。计数有共享锁，并发受 Semaphore 限制。
总时限包含 GitHub 获取；单次 clone/fetch/checkout 有 30 秒上限，取消会终止子进程。
只读文件操作有大小/数量限制；短同步读取不支持抢占式取消。

`completed/partial/failed/skipped` 表达执行完整性，不表达缺陷是否存在。
节点失败保留其他节点结果；总超时保留已完成节点快照和未验证候选，标记复核不完整。
外部任务取消向调用方传播 `CancelledError`。旧节点的静态种子回退仍可保留证据，
但 wrapper 会把被吞掉的模型/静态工具失败恢复为 `agents_failed`。
省略的 hunk、工具错误、预算耗尽和未运行的维度均可见；部分报告不能发布。

每份 JSON 带 run_id/trace_id、节点/工具耗时、逐次模型请求和瞬态重试事件。
Trace 只包含类型、状态、计数和真实 usage；没有完整 Prompt、源码或异常原文。
拿不到任意请求的 usage 时总 token/cost 为 null；单次已知 usage 仍保留。
成本仅按显式提供的 `price_per_million_tokens={"input":...,"output":...}` 计算，
币种由提供价格者负责，缺省不估价。共享 runner 关闭会上传原始消息的 LangSmith tracing。
报告本身含必要代码证据，是本地审查产物，需按仓库数据权限保存。

GitHub 写操作默认关闭。HTTP API 对 `post_comments=true` 返回 403。
人工查看 JSON 后，可先预览，再单独显式发布：

```powershell
.\.venv\Scripts\reviewer.exe publish --report report.json
.\.venv\Scripts\reviewer.exe publish --report report.json --post-comments
```

仅 GitHub 来源、live、completed、Judge 完整、confirmed 的报告可发布。
写入前校验 head SHA；注释内保存 PR URL + head SHA + review_version 的哈希标识。
顺序重复发布返回 already_published；`--allow-update --post-comments` 显式更新原评论。
发布结果和阻止原因可观察。选择人工 JSON + 单独发布，未引入 Checkpointer/Redis。
JSON 是可信操作者的本地产物，不是防篡改签名；不能把 publish 暴露成匿名服务。

GitHub 没有原子“检查 head 并写 issue comment”接口。最后一次检查后的 head 变动及
多个发布进程同时首次写入仍有竞态；这是原型限制，不宣称跨进程 exactly-once。
同步 GitHub 写入线程启动后，Python 取消不能撤回已发送请求；重试前必须查幂等标识。
默认只发布一条汇总评论，`can_comment=false` 不可用于行内评论锚点。

测试：`python -m pytest -q tests/test_harness_publication.py tests/test_qwen.py`。
真实 Qwen / GitHub 写入 / Windows Semgrep：NOT_RUN；离线使用 Fake SDK / GitHub。
