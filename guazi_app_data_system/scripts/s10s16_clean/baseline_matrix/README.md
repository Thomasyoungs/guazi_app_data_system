# S12/S13 Baseline Matrix

Phase 1 records the required regression matrix. Tests must map each new fix
to at least one baseline so later patches do not pile unbounded logic into the
runtime mainline.

Required baselines:

1. `buick_historical_s13_success`
   - Historical Buick S13 success must still prove region tabs or
     `s13_history_table_detected`.
2. `fs20260703_0005_weak_s12_body_proof`
   - `SECTION_ALREADY_REACHED` / body text alone must not enter S13 without
     S13 region/history proof.
3. `fs20260704_0001_s12_claim_malformed_extent`
   - Malformed S12 claim recovery extent must not raise raw `IndexError`.
4. `v149_physical_transition`
   - S13 all-zero return flow must keep `physical_ui_transition_proof`.
5. `v33_boundary_previous_recollect`
   - `LOW_SCORE_SKIPPED_INCOMPLETE`, boundary previous recollect, and terminal
     success semantics must stay intact.
6. `cross_task_isolation`
   - Stale NIO ES6 / 140156 results must not contaminate a new task.
7. `global_popup_guard`
   - Popup capture/wait-loop guard must remain wired across S10-S16.

