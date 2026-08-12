# S10-S16 Clean Module Boundary

This package is a Phase 1 boundary layer for S12/S13 cleanup. It must not
change pricing behavior, page contracts, V3.3 reference selection, or real
device execution order.

Module responsibility rules:

1. `page_proofs` only judges page evidence. It must not click, scroll, send,
   mutate task state, or advance the runtime state machine.
2. `field_extractors` only extracts or validates fields. It must not advance
   state, send feedback, or decide final pricing.
3. `transition_gates` only decides whether a state transition is allowed. It
   must not collect fields or perform page actions.
4. Executors may click, swipe, return, and fresh-capture only. They must not
   infer business outcomes.
5. `feedback_mapper` only maps issue/stop codes to known message templates.
   It must not reverse-infer root cause from missing pricing fields.
6. `runtime_s10_to_s16_mainline.py` should stay as orchestration and
   compatibility wrappers.
7. Raw exceptions must be converted to explicit `issue_code` / `stop_code`.
8. Every new stop code must have a regression test.
9. Historical success paths must be recorded in the baseline matrix.
10. New patches must not add complex S12/S13 if/else branches directly to the
    main runtime file when a clean module can own the pure logic.

