# Reference Early Exit Rule Source Proposal

Patch: `PAGE_CONTRACT_RUNTIME_COVERAGE_MATRIX_AND_EXECUTION_COVERAGE_ENFORCEMENT_PATCH`

## Current Decision

Reference early exit remains disabled.

The runtime must continue V3 boundary confirmation until the desktop source rule explicitly authorizes an early-exit condition. Without that source clause, runtime code cannot infer that later references are unnecessary.

## Proposed Clause To Review

Proposed rule clause id:

`REFERENCE_EARLY_EXIT_MAX_POSSIBLE_SCORE_CONTRACT`

Draft behavior:

If all remaining same-source reference cars can be proven unable to change the V3 boundary decision, the runtime may stop collecting later references only after recording:

- target score
- collected trusted reference scores
- remaining reference count
- maximum possible remaining reference score or a source-backed proof that no higher boundary can appear
- source rule clause id
- action plan id
- early exit reason

## Required Before Runtime Enablement

- Desktop rule source must include the clause.
- `config/page_contract_runtime_coverage.yaml` must change this clause from `NEEDS_SOURCE_RULE` to a covered status.
- Runtime trace must include `early_exit_rule_clause_id`.
- Tests must prove early exit fails without the source rule and passes only with the source rule.

## Current Enforcement

Until the above is complete:

- `early_exit_allowed=true` without `early_exit_rule_clause_id` fails runtime coverage checks.
- Runtime private early exit is listed as a forbidden action under `S15_V3_BOUNDARY_CONFIRMATION`.
