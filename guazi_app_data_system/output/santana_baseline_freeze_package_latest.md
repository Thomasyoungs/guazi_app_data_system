# BASELINE_SANTANA_2021_FULL_CHAIN_PRICED_DONE_202605

Freeze status: SANTANA_BASELINE_FREEZE_READY
Final status: READ_ONLY_SANTANA_BASELINE_FREEZE_PACKAGE_DONE
Target fingerprint: 大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05

## 1. Freeze Scope

- [OK] scripts/runtime_s01_to_s10_mainline.py
- [OK] scripts/runtime_s10_to_s16_mainline.py
- [OK] src/guazi_app_data_system/pricing.py
- [OK] config/fields.yaml
- [OK] output/result_s01_to_s10.json
- [OK] output/result_s10_to_s16.json
- [OK] output/result.json
- [OK] output/santana_full_chain_acceptance_report_latest.md
- [OK] output/santana_full_chain_acceptance_report.json
- [OK] output/s10_reference_card_completeness_gate_patch_latest.md
- [OK] output/s13_history_repair_duplicate_zero_parser_patch_report_latest.md
- Key artifacts/debug and artifacts/screenshots are retained; this package does not delete or overwrite them.

## 2. Baseline Result

- First stage: S10_READY
- Second stage: FULL_CHAIN_PRICED_DONE
- Acceptance: SANTANA_FULL_CHAIN_ACCEPTANCE_PASSED
- Target score: 91
- Final reference index: 5
- Selected reference score: 96
- Guazi price: 41600
- Guazi service fee: 2500
- Guazi net payout: 39100
- Suggested purchase price: 34472
- Old x95 payout rule: not used.

## 3. Successful Capabilities

- [OK] APP_FORCE_RESTART
- [OK] first_stage_S01_to_S10
- [OK] S07_color_age_filter
- [OK] S09_price_low_to_high
- [OK] S10_READY_gate
- [OK] reliable_S10_gate
- [OK] reference_index_loop
- [OK] canonical_reference_order
- [OK] same_price_mileage_desc
- [OK] complete_card_gate
- [OK] partial_card_not_clicked
- [OK] unique_Nth_card_binding
- [OK] S11_top_image_recognition
- [OK] report_entry_full_visible_safe_click
- [OK] S11_TO_S12_stable_wait
- [OK] S12_priority_over_S14
- [OK] S13_raw_nodes_bounds_parser
- [OK] S14_specific_repair_collection
- [OK] S15_single_reference_score
- [OK] stop_when_qualified_enter_S16
- [OK] S16_service_fee_tier_pricing
- [OK] S17_payload_output
- [OK] reference_history_preserved
- [OK] old_target_pollution_protection
- [OK] raw_xml_not_in_result_json

## 4. Reference Loop Acceptance

Rule: collect references sequentially by reference_index from reliable S10, score each car in S15, and stop immediately when a complete, non-rejected reference has reference_score >= target_score. This is not fixed 3 cars and not fixed 5 cars.

- Reference 1: 3.90万, 2021年 | 11.63万公里 | 唐山, transfer=1, claim=1, max=2000, repair_counts={"驾驶侧":2}, S14=左前门/钣金, score=85, target=91, rejected=false, qualified=false, decision=score_below_target_continue
- Reference 2: 4.01万, 2021年 | 13.71万公里 | 唐山, transfer=0, claim=3, max=3000, repair_counts={"驾驶侧":6}, S14=左前翼子板/钣金, score=82, target=91, rejected=false, qualified=false, decision=score_below_target_continue
- Reference 3: 4.03万, 2021年 | 11.74万公里 | 唐山, transfer=2, claim=1, max=1000, repair_counts={"驾驶侧":0,"车尾":0,"副驾驶":6}, S14=右后翼子板/喷漆, score=83.5, target=91, rejected=false, qualified=false, decision=score_below_target_continue
- Reference 4: 4.03万, 2021年 | 9.9万公里 | 唐山, transfer=0, claim=1, max=1000, repair_counts={"驾驶侧":2}, S14=左前翼子板/钣金, score=85, target=91, rejected=false, qualified=false, decision=score_below_target_continue
- Reference 5: 4.16万, 2021年 | 4.62万公里 | 唐山, transfer=1, claim=0, max=0, repair_counts={"驾驶侧":0,"车尾":0,"副驾驶":2}, S14=右后翼子板/钣金, score=96, target=91, rejected=false, qualified=true, decision=qualified_stop_and_price

Conclusion: references 1-4 are below target_score=91.0 and therefore continue; reference 5 reaches 96.0 and is selected as final reference, then the flow enters S16 pricing immediately.

## 5. S10 Same-Price Ordering Rule

- reference_index must be regenerated from current live reliable S10, not from old screenshots/snapshots.
- Ordering: price_yuan ascending; same price uses mileage_10k_km descending; if both equal, live_display_order ascending.
- Santana evidence: in the 4.03万 group, 11.74万公里 is before 9.9万公里. In the final 4.16万 group, 5.63万公里 is before 4.62万公里.

## 6. S10 Complete Card Gate

- A clickable reference card must have title, price, metadata, year, mileage, and city.
- Empty price or metadata blocks clicking. A bottom title fragment is not a complete card.
- Partial cards are not counted into valid reference_history. The runtime scrolls in a controlled way to complete the card, fresh dumps, reparses, and only clicks when card_complete=true.
- Patch evidence: invalid partial reference detected=true, recovered next_reference_index=5.

## 7. S13 History Repair Parser Rule

- History repair count parsing no longer uses deduplicated visible_texts.
- It uses raw XML nodes plus bounds/local-neighborhood binding. Duplicate 0 is preserved.
- Summary numbers such as 检测通过36 / 检测通过10 are not history repair counts.
- If count cannot be confirmed, the flow must stop rather than default to 0 or misread another number.

## 8. Final Pricing

- target_score=91
- final_reference_index=5
- selected_reference_score=96
- guazi_price_yuan=41600
- guazi_service_fee_yuan=2500
- guazi_net_payout_yuan=39100
- suggested_purchase_price_yuan=34472
- S17 payload: status=priced, listing=41600, purchase=34472, final_reference=5

## 9. Risks And Follow-Ups

- Sample shortage warning in payload: present; record only, not handled in this freeze.
- Target minor-damage scoring: 目标车况包含右后叶凹陷、前后杠擦伤、左后门划痕；如当前 scoring 未细分小伤，应后续规则议题化。
- Performance items recorded only, not optimized here: S14_COLLECT, S14_HORIZONTAL_SWIPE, S10_TO_S11 XML fresh, S11_REPORT_SEARCH, S11_TO_S12, S14_RETURN_TO_S10.
- DOCX follow-up: 建议将桑塔纳补丁沉淀到页面契约/流程说明：同价 mileage desc、完整车卡门禁、S13 raw nodes 历史修复解析。

## 10. Consistency Checks

- [OK] All core result files and pricing fields are consistent.
- [OK] No Focus / Toyota Yaris / MINI pollution detected in current Santana result.
- [OK] No raw XML blob fields detected in result JSON.

Freeze conclusion: SANTANA_BASELINE_FREEZE_READY