# Minishop 完整仓库评测与四组消融实验

这是针对扩充后 `hellolc666/code-review-demo` 仓库的新评测产物。评测数据在调用模型前冻结，使用仓库全部 Python 源码、测试、README 和独立的 PR 形式 diff；未包含与运行逻辑无关的 `.gitignore`。标签由 Codex 模拟人工编写和核对，**不是已获真人确认的标注数据**。

- `DATASET.md`：20 个样例逐项说明，开发集和正式测试集划分。
- `PROTOCOL.md`：冻结的四组实验设置和评分规则。
- `data/cases.jsonl`：各样例的全部 Python 源码、测试、README、diff 和评测侧 gold 标签。
- `data/labels_simulated.json`：方便人工复核的标签清单。
- `data/patches/`：20 个可以独立应用到基线的补丁。
- `base/`：固定版本的正常仓库快照。
- `data/probes.json`、`label_validation.json`：评测侧行为断言及执行证据，绝不传给模型。
- `results/metadata.json`：模型、预算、工具版本、源代码指纹。
- `results/runs.jsonl`：每次真实审查的完整报告，包含失败和 partial。
- `scoring/`：运行完成后生成的打乱组名的报告、模拟人工匹配及解盲映射。
- `results/evaluation.json`、`REPORT.md`：评分完成后生成的指标与分析。
- `offline-preflight/`：仅测试运行框架的 Mock 结果，不用于计算模型效果。

## 复现

以下命令使用当前机器已存在的 Reviewer 虚拟环境。真实评测读取 Reviewer 根目录原有 `.env`；评测目录不保存密钥。

```powershell
$python = 'D:\desktop\AgentProjects\multi-agent-code-reviewer\.venv\Scripts\python.exe'
Set-Location 'D:\desktop\AgentProjects\multi-agent-code-reviewer\experiments\minishop-v1'
$env:PYTHONPATH = 'D:\desktop\AgentProjects\multi-agent-code-reviewer'
$env:MINISHOP_TOOL_VENV = 'C:\Users\dafd\Documents\Codex\2026-10-24\new-chat\work\eval-tools-venv\Scripts'
$env:MINISHOP_REPO = 'C:\Users\dafd\Documents\Codex\2026-10-24\new-chat\work\code-review-demo'
& $python validate_dataset.py
& $python run_ablation.py --live
```

`run_ablation.py` 具有续跑功能：已保存的 64 条结果不会覆盖，即使该结果失败。若要做新一轮实验，请复制为新的实验目录，保留原记录。模型必须为 `qwen-max`，Ruff 使用 Reviewer 环境，Semgrep 使用 `MINISHOP_TOOL_VENV` 指定的独立虚拟环境。`build_dataset.py` 仅用于构造新数据版本；原仓库在本机的路径可由 `MINISHOP_REPO` 指定，当前冻结数据不需要重新构造。

以下命令用于新的实验轮次。当前目录已经含有完整的盲评文件和最终指标，请直接查阅 `REPORT.md`：

```powershell
& $python score_ablation.py export
# 阅读 scoring/blind_predictions.jsonl，核对冻结的标签和代码；
# 将每条语义判断、理由保存到 scoring/decisions_simulated.json 后再解盲。
& $python score_ablation.py score
& $python render_report.py
```

导出步骤禁止覆盖现有盲评轮次。没有填写语义判断前不能计算质量指标。相同文件或相同行号不会被自动当成正确检出。

## 阅读结果时的边界

这是一轮单仓库、合成变更、小样本评测。它能检验当前实现的漏报、误报和运行稳定性，不能证明工业级泛化能力。Test Mapping 的输出是静态关联；Judge 由大模型检查静态证据，本身不动态执行 Bug。评测侧执行断言是独立的标签核验步骤。

A/B 的 findings 没有经过 Judge；C/D 的 findings 经过 Judge。本次评测目标是变更引入的缺陷，不把一般文档建议或笼统的测试补充建议记为真正例。费用没有配置可核验价格，保持未知，不从请求次数推算费用。
