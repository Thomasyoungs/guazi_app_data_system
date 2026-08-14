# Runtime Rule Coverage Score

Patch: `PAGE_CONTRACT_RUNTIME_COVERAGE_MATRIX_AND_EXECUTION_COVERAGE_ENFORCEMENT_PATCH`

## Current Score

- Coverage score: `78.89`
- Check status: `RUNTIME_RULE_COVERAGE_CHECK_PASSED`
- Not covered clauses: `0`
- Needs source rule clauses: `1`

## Coverage Status Counts

- `FULLY_CONTRACT_DRIVEN`: 4
- `CONTRACT_GUARDED_BUT_CODE_DRIVEN`: 11
- `FALLBACK_BOUND_BY_CONTRACT`: 2
- `NEEDS_SOURCE_RULE`: 1
- `NOT_COVERED`: 0

## Score Scale

- 100: Fully contract-driven. Source clause, action plan, runtime trace, and tests are complete.
- 80: Contract guarded, but part of the runtime action is still code-driven.
- 60: Contract bounded fallback. Fallback is allowed only by clause and is budget-limited.
- 40: Partial guard exists but trace/test coverage is incomplete.
- 20: Source rule is needed before runtime action can be enabled.
- 0: Not covered.

## Remaining Gap

`REFERENCE_EARLY_EXIT_MAX_POSSIBLE_SCORE_CONTRACT` is intentionally marked as `NEEDS_SOURCE_RULE`.
Runtime early exit remains forbidden until a desktop rule source clause explicitly authorizes it.
