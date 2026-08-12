# S03 Brand Search V2 Leapmotor Patch

## Final Status

DIAGNOSE_AND_PATCH_S03_BRAND_SEARCH_V2_FOR_LEAPMOTOR_DONE

## Target

- fingerprint: ??|C10|2026?|210???|?|2026.02
- online device: `6TGYYHPZCETCSK6L`
- second stage: not run

## Diagnosis

- alphabet_index_detected: True
- detected_letters: A, B, C, D, E, F, G, H, I, J, K, L, M, N, O, P, Q, R, S, T, W, X, Y, Z
- L_letter_bounds: (1155, 1417, 1220, 1453)
- new_energy_tab_detected: True
- new_energy_tab_bounds: (858, 152, 1155, 250)
- target_brand_aliases: 零跑, 零跑汽车, LEAPMOTOR, Leapmotor
- alias_match_seen_on_failed_tail_page: False
- classification: S03_ALPHABET_INDEX_NOT_USED, S03_NEW_ENERGY_TAB_NOT_USED, S03_BRAND_ALIAS_INSUFFICIENT, S03_LINEAR_SCROLL_ONLY_STRATEGY_INSUFFICIENT
- evidence screenshot: `artifacts/screenshots/s03_brand_scroll_8_20260510_130858.png`
- evidence XML: `artifacts/debug/s03_brand_scroll_8_20260510_130858.xml`

Root cause: old S03 search did not construct Leapmotor aliases, did not use the visible new-energy tab, and did not map ??/???? to right-side letter L before falling back to linear scroll.

## Patch

Modified file:

- `scripts/runtime_s01_to_s10_mainline.py`

Implemented `S03_BRAND_SEARCH_V2`:

1. target aliases: ?? / ???? / LEAPMOTOR / Leapmotor
2. try `?????` tab when present
3. try right-side `L` alphabet index
4. only then use linear scroll fallback
5. persist attempted tabs, attempted letters, visible brand names by step, matched alias, clicked brand bounds
6. short fresh-poll after brand click avoids treating the first empty WebView frame as final S04 evidence

No changes were made to the second-stage script, pricing, config files, page-contract DOCX, or baseline files.

## Verification

- py_compile: passed
- first-stage script: ran on `ANDROID_SERIAL=6TGYYHPZCETCSK6L`
- S03 attempted_tabs: `[{"tab": "只看新能源", "bounds": [858, 152, 1155, 250], "clicked": true}]`
- S03 attempted_letters: `[{"letter": "L", "found": true, "bounds": [1155, 1417, 1220, 1453]}]`
- matched_alias: 零跑汽车
- clicked_brand: 零跑汽车
- clicked_brand_bounds: [0, 529, 1155, 685]
- first-stage final status: S07_RIGHT_AGE_SLIDER_MOVE_NO_EFFECT
- result JSON raw XML large fields removed: true
- first-stage final error: Exact target age tick not found before view-result.

S03 is fixed: the run found and clicked `????`, then continued past S04/S05 into S07. The remaining failure is unrelated to S03: target_age=0 right age slider did not move/effectively confirm.

## Next Step

Only suggested next step: diagnose/fix S07 target_age=0 right-slider behavior. Do not start second stage until first stage reaches S10_READY.
