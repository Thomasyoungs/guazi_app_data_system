# S13 Repair Item Clickable Container Binding Diagnosis

## Status

`S13_REPAIR_ITEM_CLICKABLE_CONTAINER_BINDING_DIAGNOSIS_DONE`

This was a read-only diagnosis. No code was modified, no real-device run was started, and `result.json` was not overwritten.

## Evidence

- screenshot: `artifacts/screenshots/s13_repair_item_reposition_车头_1_20260518_111841.png`
- XML: `artifacts/debug/s13_repair_item_reposition_车头_1_20260518_111841.xml`
- parsed extract: `output/s13_repair_item_clickable_container_binding_bounds_extract.json`

The page is still in S13. The `车头` history repair count remains bound as `2`, and context was not lost after reposition.

## What The Script Selected

After reposition, the script-selected candidate was:

- text: `前保险杠`
- class: `android.widget.TextView`
- bounds: `[152,2154,312,2216]`
- clickable: `false`
- enabled: `true`

Its parent chain includes:

- parent level 1: bounds `[78,2024,1144,2513]`, clickable=`false`
- parent level 2: bounds `[26,1885,1196,2513]`, clickable=`true`

That clickable parent contains lower-list texts:

- `车头：检测通过10`
- `日间行车灯`
- `机舱盖铰链（左）`
- `前保险杠`
- `发动机舱盖漆面`
- `发动机舱盖`
- `机舱盖铰链（右）`
- `机舱盖锁`
- `前雾灯`
- `中网`
- `前风挡玻璃`

This is the lower `检测通过10` list, not the `历史修复2` entry area.

## Actual Clickable History-Repair Entries

XML does expose clickable containers in the `车头深度检测 / 历史修复2` block:

1. bounds `[81,1608,611,1716]`
   - class: `android.view.View`
   - clickable: `true`
   - enabled: `true`
   - descendant text: `左前大灯`

2. bounds `[611,1608,1140,1716]`
   - class: `android.view.View`
   - clickable: `true`
   - enabled: `true`
   - descendant text: `右前大灯`

These are safe, region-local, and belong to the `车头` history-repair block.

## Why Binding Failed

The current binding path is too narrow:

- `_s13_repair_item_candidates` scans text labels after the region header.
- `_s13_repair_item_label_matches` relies on `S14_ALLOWED_PARTS`.
- `S14_ALLOWED_PARTS` does not include `大灯`.
- Therefore `左前大灯` / `右前大灯` history-repair clickable entries were skipped.
- The script then considered lower detection-pass list items like `前保险杠`.

So this is not simply “no clickable container exposed”. A valid clickable container exists, but the binder missed it.

## Classification

`S13_REPAIR_ITEM_CLICKABLE_CONTAINER_EXISTS_BUT_BINDER_MISSED`

Also applicable:

`S13_REPAIR_ITEM_CONTAINER_IS_SIBLING_OR_REGION_BLOCK`

## Answer

The current final refusal to click `前保险杠` was safe. But the upstream binding selected the wrong candidate set. The next fix should target S13 clickable container binding: enumerate clickable containers inside the count>0 region block and inspect descendant text, including labels outside the old `S14_ALLOWED_PARTS` list such as `大灯`.
