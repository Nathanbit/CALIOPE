# Consolidation decision — imported MacroFlow v0.9.5

**Decision:** admit as a quarantined sidecar prototype; reject as a replacement
for the canonical Pine model.

## External review closed

The bounded run `mf-v095-consolidation-20261019` received all eight contractual
observations and advanced to `evidence_merge`. Its consolidated report is
`AI_CONSOLIDATED_REVIEW_2026-10-19.md`; the content-addressed response index is
`AI_REVIEW_EVIDENCE_INDEX_2026-10-19.json`.

The deterministic decision was then ingested as `reject`. The final state is
`archived_rejected`, with decision hash
`2B66F60414A5F1B66D85D41352D3AE0EBF0B57E18AB9FEA8BA9CB291797CC76F`.

The synthesis confirms this decision: reject promotion, preserve quarantine and
revise. It also records two corrections to model output: the runner already has
hard budgets, and live, auto-approval, automatic publishing and automatic
trading are already disabled.

## Evidence

- ZIP SHA-256: `7630C65966E1EB1311F9F6D49B9E434FBF4AEBC1680A760FF65BC9FA6599D3E9`.
- Python syntax check: passed with the bundled Python runtime.
- Full pytest suite: not run because the available runtime lacks the declared
  third-party dependencies; no packages were installed implicitly.
- Canonical Pine: 2,200 lines, 136 inputs, 19 confirmed
  `request.security()` calls, SHA-256
  `63CFF54E2A36A3A3B5DB2AAA812D0F1BCF70D751D579D566DFB18A26DEA4816C`.
- ZIP Pine: patch fragment with no executable entries and no model parity.

## Accepted components

- Paper broker abstraction.
- Risk guard, kill switch and journal concepts.
- Alert deduplication and version registry concepts.
- Walk-forward, bootstrap and Monte Carlo modules as research candidates.
- Dashboard as a local research surface.

## Rejected until repaired

- Pine replacement or parameter injection.
- Promotion using concatenated metrics from different fold-specific models.
- Reuse of old OOS windows as new evidence.
- Synthetic-data promotion.
- Automatic approval, unlimited recursion and automatic restart.
- MT5 execution or webhook-driven live orders.

## Quarantine hardening already applied

The copy in `CALIOPE/labs/macroflow_v0_9_5_import` now defaults to paper mode,
manual approval, two iterations, one-hour cadence and no automatic restart.
The optimizer and Pine generation are disabled until parity. MT5 requires a
second explicit live-trading flag plus a non-default secret. Test alerts cannot
run against MT5. Upload and webhook bodies are capped.

## Required next gates

1. Produce a bar-by-bar export from TradingView and an equivalent Python trace.
2. Reconcile every WATCH transition, fill, stop, target, partial exit and cost.
3. Choose one owner for exits and version the webhook contract.
4. Reserve a blind, single-use holdout for the exact artifact being promoted.
5. Run the complete offline suite and adversarial bridge tests.
6. Require an explicit human approval record before any activation.

External AI reviews are advisory evidence only. Caliope must preserve
disagreements and apply deterministic gates; a model majority cannot promote a
candidate.
