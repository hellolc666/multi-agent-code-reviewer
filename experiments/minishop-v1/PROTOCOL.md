# Minishop A/B/C/D evaluation v1

This experiment uses all Python source and tests plus README from `code-review-demo` at commit
`4353804fcf686c04a0ae4345bac223815d82f6b2`, with one independently authored change
per case. It does not reuse the old snippet corpus. Inputs are local PR-shaped
unified diffs plus all post-change Python source, tests and README, not new GitHub PRs.
The repository's `.gitignore` is excluded because it does not affect this program.

The frozen dataset contains 4 development cases and 16 test cases (10 defective,
6 clean). All four architectures run once on every test case: 64 live reviews.
No test-result-driven changes to labels, prompts, code, budgets or sample selection.
Failed/partial runs remain in every denominator. Resume skips only finished keys,
including failed keys; it never replaces an unfavorable result.

|Group|Configuration|
|---|---|
|A|One General Reviewer, no retrieval loop, no Judge|
|B|Five reviewers, real Semgrep/Ruff/AST, no retrieval loop, no Judge|
|C|Same reviewer/tool configuration as B, plus Judge|
|D|Rule-based dynamic routing, bounded evidence tools, static tools and Judge|

All groups use the existing `evals.run.run_case` and `analyze_with_schema` path,
the same `qwen-max` alias, temperature 0 and default ReviewBudget. Runs are
sequential, with group order rotated ABCD/BCDA/CDAB/DABC across cases. Code hashes,
dependency/tool versions and exact budgets are recorded before model calls.
The Semgrep CLI is version-pinned; its default `p/python` registry rule content is
not pinned. A is the existing shared-adapter general baseline, not a recreation
of a separate upstream system. Its input prompt contains the diff, without tools
for retrieving additional source. This input-access difference is part of the
architecture comparison and limits causal attribution to the number of agents.

Labels are authored and reviewed by Codex as simulated human annotations, not
real human signoff. External assertions validate baseline behavior and mutation
effects. For clean cases, the baseline tests and case probes must all pass.
These assertions and labels are never copied into model checkout directories;
normal repository tests are available to the reviewer as ordinary code context.
No dynamic bug execution takes place inside Reviewer or Judge.

Scoring is defined before live results:

- Read every final finding and require the same defect, supported consequence
  and feasible trigger as a gold label. Same line alone is not a match.
- Match one prediction to at most one gold defect. Different symptoms of the
  same root cause share one gold label; additional reports count as duplicates.
- A semantic match counts as TP only in the same file and within two lines of
  the gold added line. Unmatched predictions are FP; unmatched gold is FN.
- Unsupported speculation, pre-existing conditions, and style-only findings
  are FP for this change-related defect benchmark.
- A/B are scored on their unjudged report.findings; C/D on final findings after
  Judge. A/B candidate statuses are not turned into confirmed statuses.
- Precision=TP/(TP+FP), Recall=TP/(TP+FN), F1=2TP/(2TP+FP+FN).
- Also report false positives per case, clean-case false-alarm rate, full
  completion/partial/failure rates, latency, requests and available token usage.
- Missing usage or prices stays null; no invented token counts, cost or savings.
- Predictions are shuffled with seed 20261027 and group labels hidden during
  simulated semantic matching. Mapping back to groups happens after matching.

This is a single-repository, small synthetic mutation evaluation. There are no
security-specific gold defects because minishop has no authentication/network
feature. No statistical significance or general superiority claim is supported
by one run of 16 test cases. The dataset author and simulated matcher are both
Codex; independent human review remains outstanding.
