# Geely Yuanjing V1.24 deterministic sequence and old-stop precedence patch

Status: `GEELY_YUANJING_V1_24_DETERMINISTIC_SEQUENCE_OLD_STOP_PATCHED`  
Created at: 2026-05-12T17:29:45

## Modified Files
- `scripts/runtime_s01_to_s10_mainline.py`
- `scripts/runtime_s10_to_s16_mainline.py`

## Implemented
- Added deterministic action precondition gates before contract actions execute.
- S05 now requires the left target year tab to be clicked and confirmed before right-side trim selection.
- S10 reference-card clicks require reliable S10, complete card binding, and deterministic title match.
- S11 missing official full-report entry now excludes the current reference and returns to reliable S10 instead of failing the whole chain.
- Evidence pairing checks are enabled; foreign target terms become warnings and are not used for current-page decisions.

## Validation
- py_compile: both fixed scripts passed.
- Offline validation: `True`.
- Live first stage: `S10_READY`.
- Live second stage: `FULL_CHAIN_PRICED_DONE`.
- Reference 2 exclusion: `OFFICIAL_REPORT_NOT_AVAILABLE`.
- Final reference: index `4` with score `97.0`.

## Result Quality
- raw XML / nodes / visible_blob / page_source fields: none found.
- Baselines overwritten: no.
