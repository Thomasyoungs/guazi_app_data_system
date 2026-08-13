# Feishu Pricing Execution Standard V3.3

Source sync date: 2026-06-27

Active rule sources:

- Page contract: V1.50
- Scoring rule: V1.11
- Pricing rule: V3.3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT
- Reference selection rule: V3_3_BOUNDARY_PREVIOUS_REFERENCE_RECOLLECT
- Competition coefficient: V1.2.6

## Reference Selection

S10 reference cards must be collected in canonical low-to-high order.

The first complete and trustworthy reference whose `reference_score >= target_score` is the boundary reference. The boundary reference is evidence only. It must not be used as the final reference for S16 pricing.

The final reference candidate is always:

```text
final_reference_candidate_index = boundary_reference_index - 1
```

If the candidate is complete, trustworthy, and has `reference_score < target_score`, it becomes the final reference.

If the candidate was previously marked `LOW_SCORE_SKIPPED_INCOMPLETE` or otherwise incomplete, runtime must recollect that same reference through S11/S12/S13/S14 before pricing.

If recollection fails, the task enters manual review. Runtime must not price from the boundary reference.

If the boundary reference is the first reference, the task enters manual review with:

```text
FIRST_BOUNDARY_HAS_NO_PREVIOUS_REFERENCE
```

If no boundary reference is found after all same-source references, the task enters manual review with:

```text
NO_BOUNDARY_REFERENCE_FOUND_NEEDS_MANUAL_PRICING
```

## S14 Low-Score Skip

After every deterministic S14 repair item, runtime computes:

```text
reference_score_upper_bound = partial_confirmed_score + remaining_max_possible_score
```

If:

```text
reference_score_upper_bound < target_score
```

then runtime may stop collecting the current S14 item sequence, mark the reference:

```text
LOW_SCORE_SKIPPED_INCOMPLETE
```

and return reliably to S10 to continue the next reference.

The skipped reference is not eligible for boundary, pre-boundary, or pricing until it is recollected completely.

## Feishu Business Reply

After a user confirms a target task, the business-group reply must be:

```text
【定价已开始】FSxxxx
系统已开始自动定价，请等待结果。
```

Business replies must not use stale queue language or internal runtime names.

When `NEEDS_REVIEW` has a clean pricing chain, the business group must receive the system pricing chain for supervisor confirmation. If the pricing chain is missing or contaminated, the system must not fabricate a price.


V1.50 alignment note: S13 all-zero exit, reference_history writes, continuation and all-references-exhausted decisions require physical UI transition proof.
