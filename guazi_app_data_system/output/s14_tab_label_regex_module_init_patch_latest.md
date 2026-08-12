# S14 tab label regex module-init patch

Final status: `S14_TAB_LABEL_RE_REGEX_PATCHED`

## Modified files
- `scripts/runtime_s10_to_s16_mainline.py`

## Patch scope
Only `S14_TAB_LABEL_RE` was changed, replacing the malformed character class with a valid ASCII/Chinese-parentheses pattern.

## Validation
- `py_compile`: passed
- module import: `IMPORT_OK`
- S14 tab label regex positive/negative samples: passed

No first-stage script, pricing, config, baseline, scoring, S10, S11, or S14 collection logic was modified.
