# 冻结样例与 A/B/C/D 对照

首版提供 20 个独立原创样例：8 个逻辑、4 个安全、4 个测试回归风险、4 个 clean。
MIT 授权。每例都有完整新版本文件、真实 unified diff、固定 split、来源、目标新增行、
缺陷/触发条件/严重性/证据；clean 记录逐项检查理由。`build_dataset.py` 保留作者源稿。
这些标注经过 Codex 静态检查，**尚未获得人工签核**；不能声称已完成人工确认评测。
没有借用公开 PR，也没有重复模板扩充到 50 条。后续可扩充至任务书建议的 40–60 条。

`data/schema.json` / `schema.py` 校验每行 JSONL。`data/manifest.json` 冻结字节 SHA-256，
10 dev / 10 test，编号与划分固定。修改数据须重新 freeze 并产生新版本，不可看测试结果后
偷偷改 gold。作者命令 `python -m evals.build_dataset` 不在 runner 中自动调用。

`Case.reviewer_input()` 仅投影 diff + repo_files。runner 将这些文件写进独立临时 checkout，
gold/来源/标注文字/数据集目录都不放进去；Planner/Reviewer/Judge 只获得统一审查输入。
上下文工具无法访问 evaluator 的标签文件。所有受审样例均不执行。

|配置|流程|固定条件|
|---|---|---|
|A|一个 general Reviewer，无工具循环/Judge|复用 Correctness 节点、通用审查 prompt；不是上游的精确重放|
|B|五 Reviewer + 原静态工具，无 Judge/证据循环|固定五维，不按风险缩减|
|C|B + Judge|可观察 Judge 的拒绝/误否定|
|D|规则路由 + 有界证据工具 + Judge|完整方案，未调参的 rules 模式|

同次 live 实验强制 Qwen、同一已配置模型、temperature=0、相同每 PR 总预算和输入。
各配置实际用量可以不同。A 无专用静态工具，因此并非完全等价基线；B/C 保留上游工具，
消融体现整体系统差异，不据此单独推断并行机制的因果贡献。Semgrep 缺失会记失败，
不从统计中删除。模型服务别名可能变更，应同时保存实际 response model/version 信息。

```powershell
# 项目根目录；默认完全离线（不读取 .env，不需要 key）
.\.venv\Scripts\python.exe -m evals.run --split all --output evals/results/offline-new.json
.\.venv\Scripts\python.exe -m evals.run --split test --limit 3 --repeats 3 --output evals/results/repeat-new.json
# 配置 QWEN.md 所列环境变量后显式在线，保留所有错误
.\.venv\Scripts\python.exe -m evals.run --live --split test --output evals/results/live-new.json
```

输出文件拒绝覆盖；旁边 `.runs.jsonl` 每次运行即时保存，进程中断保留已完成记录。
报告含模型/温度/prompt 版本/数据 SHA/预算/时间、逐次完整审查 JSON 和四组汇总。
P50 用中位数，P95 用 nearest-rank；completed/全部运行作为完成率，skipped 单列。
LLM requests 包括重试和修复。任一 usage 不可得时 token/cost=null，不用字符数伪造。
Python API `evaluate(prices={"input":...,"output":...})` 可传每百万 token 单价，币种自行注明。
3-run 报告给每例 exact finding-set 一致性与时延方差；这不是多次模型准确率验证。

离线报告显式 `not_live_benchmark`，质量指标全部 null。**当前不能回答多 Agent 是否
优于单 Agent、Judge 减少了多少误报、真实路由节约多少费用。** 不把空 Mock 结果的
一致性/低时延当质量或性能收益；已提交的 results 仅证明评测流水线可重现。

人工验收时先独立确认 gold，再看每条预测和代码，按同一缺陷语义写一对一匹配。
只有同文件且新增行相差 ≤2 的语义匹配才计 TP；未匹配预测计 FP，未命中 gold 计 FN。
重复报告同一缺陷额外计 FP。定位准确率=位置对齐的语义匹配/全部语义匹配。
零分母指标为 null。失败运行仍在分母，缺少的预测仍漏报，禁止挑选最佳轮次。
已确认不存在其他缺陷时，未匹配候选才可当 FP；发现标注遗漏需新 corpus 版本并完整重跑。

匹配文件由审阅者填写（示意格式，**不是已批准标签**）：

```json
{
  "dataset_sha256": "从 manifest 复制",
  "gold_confirmed": true,
  "reviewed_by": "实际人工审阅者",
  "runs": {
    "logic_empty_average/A/1": [{"prediction_index": 0, "gold_id": "logic_empty_average_g1"}]
  }
}
```

`runs` 必须覆盖一组所有运行；无命中也显式填空数组。未齐全则该组质量统计保持 pending。
`prediction_index` 对应该份 report.findings 的零基下标。给旧报告补匹配无需再次付费：

```powershell
.\.venv\Scripts\python.exe -m evals.score_report --report evals/results/live-new.json --matches reviewed-matches.json --output evals/results/scored-new.json
```

工具不会伪造人的身份或签核；live provider 字段也不是防伪签名，应保留原始运行产物。
