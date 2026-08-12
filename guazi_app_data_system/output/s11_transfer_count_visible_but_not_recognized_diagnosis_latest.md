# S11 transfer count visible but not recognized diagnosis

Final classification: `S11_TRANSFER_COUNT_XML_NODE_EXISTS_BUT_REGEX_MISSED`

## Evidence Pair
- Screenshot: `artifacts\screenshots\s10_to_s11_pre_dump_2_20260517_160126.png`
- XML: `artifacts\debug\s10_to_s11_20260517_160126_compressed.xml`
- Same fresh pair: yes, same S10_TO_S11 wait round; screenshot first, XML dump after it.
- Recognized page: `S11`, by `S11_TOP_ONE_THIRD_IMAGE_ONLY_STANDARD`.

## Screenshot Check
- The screenshot shows `??0? / ??1?` in the vehicle condition row. The user mentioned `??0?`, but this specific reference #1 evidence shows `??1?`.
- The text is in the middle S11 vehicle-condition card and is not obscured.

## XML Check
- `??1?` count: 0
- `??0?` count: 0
- Candidate nodes: 0

## Parser Check
- Function: `_extract_transfer_count` at `scripts/runtime_s10_to_s16_mainline.py:6170-6172`.
- It reads `snapshot.visible_blob`.
- Current regex is mojibake: `???(?:???)?\s*(\d+)`.
- XML text is normal Unicode: `??1?`.
- Therefore the candidate exists, but the regex misses it.

## Conclusion
This is not a page-contract problem and not a screenshot/XML mismatch. It is a fixed-script XML parsing/regex encoding issue in the S11 transfer-count parser.

No code was modified, no real-device run was performed, and `result.json` was not overwritten.
