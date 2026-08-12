# S11 transfer count Unicode regex patch

Final status: `S11_TRANSFER_COUNT_UNICODE_REGEX_PATCHED`

## Modified files
- `scripts/runtime_s10_to_s16_mainline.py`

## What changed
- Replaced mojibake transfer-count regex with Unicode patterns.
- Supported compact, spaced, reverse, and ???? formats.
- Added raw XML node text/content-desc evidence scanning.
- Added S11 current_reference fields for transfer_count_text, parsed_transfer_count, listing_id, mileage_age_text, and insurance_claim_text.
- FIELD_MISSING remains only when no transfer count can be parsed from XML/text/content-desc evidence.

## Validation
- `py_compile`: passed
- module import: `IMPORT_OK`
- A_compact_zero: passed
- B_compact_one: passed
- C_spaced_zero: passed
- D_reverse_zero: passed
- E_count_one: passed
- F_missing_candidate: passed
- content_desc_supported: passed
- G_s11_fresh_pair_stale_xml_unaffected: passed
- H_s10_s14_metal_deduct_unaffected: passed
