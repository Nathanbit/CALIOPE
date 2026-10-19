# MacroFlow v0.9.5 import — quarantine status

**Status:** research-only, blocked from promotion and live execution.

This directory preserves the useful Python sidecar delivered in
`macroflow_v0.9.5.zip` while keeping it isolated from the canonical Pine model.
The original ZIP SHA-256 is:

`7630C65966E1EB1311F9F6D49B9E434FBF4AEBC1680A760FF65BC9FA6599D3E9`

## Safety changes applied during import

- Paper broker remains the default.
- MT5 requires both `MF_BROKER=mt5` and `MF_ALLOW_LIVE_TRADING=1`, plus a
  non-default webhook secret.
- Auto-approval is off and requires an explicit opt-in.
- The experimental optimizer is disabled by default.
- The recursive loop defaults to two iterations, one hour apart.
- A persisted loop does not resume automatically.
- Test alerts are rejected outside the paper broker.
- Webhook and CSV upload sizes are capped.
- Pine generation is disabled until Python↔Pine parity passes.

## Blocking findings

1. The bundled Pine file is a patch fragment, not the complete strategy.
2. The Python reference omits most of MacroFlow: three pillars, WATCH state,
   3/3 MTF, grades, closed context, pivot stops, partial exits and plan metrics.
3. It assumes next-open execution while the canonical Pine uses
   `process_orders_on_close=true`.
4. Walk-forward metrics concatenate different fold-specific parameter sets,
   but the registry promotes only the final fold's parameters. The reported
   metrics therefore do not belong to the promoted artifact.
5. Historical OOS windows are reused after new data arrives.
6. The bridge schema conflicts with the current `macroflow.order.v1` and
   `macroflow.alert.v4` payloads, and ownership of partial exits is ambiguous.

## Canonical files

- `pine/MacroFlow_v0.9.4.pine`: original patch fragment from the ZIP.
- `pine/MacroFlow_v0.9.4_CANONICAL.pine`: complete current baseline copied for
  parity work only. It is not wired into the optimizer.

## Exit criteria

- Bar-by-bar parity on signals, WATCH transitions, fills, stops, targets,
  partial exits, costs and plan P&L.
- One executable artifact per set of reported OOS metrics.
- A blind holdout that is consumed once and never used to redesign the model.
- Versioned webhook contract with a single owner for exits.
- Full offline test suite passing.
- Explicit human approval before model activation, publication, paper trading
  or any future live adapter.
