# PATCH_ONLY_S13_REPAIR_ITEM_CLICK_GUARD_AND_CLICK_AUDIT

Final status: PATCH_ONLY_S13_REPAIR_ITEM_CLICK_GUARD_AND_CLICK_AUDIT_DONE

## Scope

- Modified code: yes, only `scripts/runtime_s10_to_s16_mainline.py`.
- Modified first-stage script: no.
- Modified S10 canonical order: no.
- Modified S11/S12 logic: no.
- Modified S14 image-horizontal-swipe collection body: no.
- Modified pricing/config/page-contract docs: no.

## Patch Summary

S13 to S14 repair-item entry now uses a guarded repair-item selector instead of the previous broad “legal repair item” text search.

Added:

- forbidden click texts for live-room / explanation / bottom action areas.
- repair-item candidate extraction bound to current S13 region.
- safe content-region calculation that excludes bottom fixed action areas.
- nearest clickable repair-item parent selection only when the parent does not contain or overlap forbidden areas.
- persistent click audit fields before and after S13 to S14 tap.
- after-click live-room detection; live-room pages are never treated as S14.
- `current_reference_excluded_from_history=true` for S13 to S14 click failures where the reference has not completed S14/S15.

New/strengthened stop codes:

- `S13_REPAIR_ITEM_CLICK_TARGET_NOT_CONFIRMED`
- `S13_REPAIR_ITEM_CLICK_TARGET_OVERLAPS_FORBIDDEN_AREA`
- `S13_TO_S14_LIVE_ROOM_ENTERED_AFTER_CLICK`
- `S13_TO_S14_INTERMEDIATE_PAGE_UNCONFIRMED`

## Offline Validation

`py_compile scripts/runtime_s10_to_s16_mainline.py`: passed.

Click-before XML:

- XML: `artifacts/debug/s13_region_车尾_20260509_155339.xml`
- current_region: `车尾`
- history_repair_count: `1`
- selected_repair_item_text: `后备箱盖铰链`
- selected_click_bounds: `[81, 1956, 611, 2063]`
- click_strategy: `nearest_clickable_repair_item_parent_bounds`
- safe_click_region: true
- forbidden areas detected: `微信咨询`, `实车讲解`, `联系卖家/电话`, `讲价`
- forbidden_nearby_texts for selected target: none

Click-after XML:

- XML: `artifacts/debug/s13_to_s14_车尾_20260509_155344.xml`
- live_room_signals_detected: true
- live-room signals include: `等待看车`, `马上为您实车讲解`, `演示清单`, `带看车辆`, `商家讲解车况`, `瓜子认证商家`
- recognized_as_s14_allowed_for_after_xml: false

## Real-Device Verification

First stage rerun:

- status: `S10_READY`
- target_fingerprint: `大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07`
- S07_FILTER_DONE/COLOR_FILTER_DONE/AGE_FILTER_DONE/SORT_DONE/S10_READY: true
- log: `output/tuang_s13_guard_first_20260509_161420.log`

Second stage rerun:

- status: `REFERENCE_CARD_TITLE_MISMATCH`
- target_fingerprint: `大众|途昂|2017款|330TSI 两驱豪华版|白|2018.07`
- expected_title: `大众 途昂 2017款 330TSI 两驱豪华版`
- actual_title: `大众ID.3 2024款 出众版`
- screenshot: `artifacts/screenshots/s10_s16_start_20260509_161743.png`
- XML: `artifacts/debug/s10_s16_start_20260509_161743.xml`
- log: `output/tuang_s13_guard_second_20260509_161742.log`

The real-device run did not reach S13 because the existing S10 title binding gate correctly refused to click a non-target live card. This is a separate pre-S13 blocker and was not patched in this S13-only round.

## Verification Conclusion

- S13 repair item safe target selection is patched.
- S13 click audit fields are persisted in `current_reference.s13_to_s14_click_audits`.
- Live-room / real-car-explanation pages are explicitly detected after click and cannot be recognized as S14.
- Incomplete S13 to S14 failures are excluded from effective `reference_history`.
- Real-device S13 path was not exercised because the run stopped earlier at `REFERENCE_CARD_TITLE_MISMATCH`.

