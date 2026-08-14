# Santana Full Chain Acceptance Report

Acceptance status: `SANTANA_FULL_CHAIN_ACCEPTANCE_PASSED`
Final status: `READ_ONLY_SANTANA_FULL_CHAIN_ACCEPTANCE_REPORT_DONE`
Target fingerprint: `大众|桑塔纳|2021款|1.5L 自动风尚版|白|2021.05`

## 1. Full Chain Acceptance
- First stage S10_READY: True
- Second stage status: `FULL_CHAIN_PRICED_DONE`
- S11/S12/S13/S14/S15: completed before pricing
- S16 pricing completed: True
- S17 payload output: True
- Reference loop rule: sequential collection by reference_index, stop immediately once qualified; not fixed 3 references.

## 2. Reference Collection History
### Reference 1
- Price: 3.90万
- Year / mileage / city: 2021年 | 11.63万公里 | 唐山
- Transfer count: 1
- Claim count: 1
- Max claim amount: 2000.0
- History repair counts: {'驾驶侧': 2}
- S14 repair items: 左前门 钣金
- Score: 85.0 / target 91.0
- Rejected: False 
- Qualified: False
- Decision: below target_score, continue to next reference_index

### Reference 2
- Price: 4.01万
- Year / mileage / city: 2021年 | 13.71万公里 | 唐山
- Transfer count: 0
- Claim count: 3
- Max claim amount: 3000.0
- History repair counts: {'驾驶侧': 6}
- S14 repair items: 左前翼子板 钣金
- Score: 82.0 / target 91.0
- Rejected: False 
- Qualified: False
- Decision: below target_score, continue to next reference_index

### Reference 3
- Price: 4.03万
- Year / mileage / city: 2021年 | 11.74万公里 | 唐山
- Transfer count: 2
- Claim count: 1
- Max claim amount: 1000.0
- History repair counts: {'驾驶侧': 0, '车尾': 0, '副驾驶': 6}
- S14 repair items: 右后翼子板 喷漆
- Score: 83.5 / target 91.0
- Rejected: False 
- Qualified: False
- Decision: below target_score, continue to next reference_index

### Reference 4
- Price: 4.03万
- Year / mileage / city: 2021年 | 9.9万公里 | 唐山
- Transfer count: 0
- Claim count: 1
- Max claim amount: 1000.0
- History repair counts: {'驾驶侧': 2}
- S14 repair items: 左前翼子板 钣金
- Score: 85.0 / target 91.0
- Rejected: False 
- Qualified: False
- Decision: below target_score, continue to next reference_index

### Reference 5
- Price: 4.16万
- Year / mileage / city: 2021年 | 4.62万公里 | 唐山
- Transfer count: 1
- Claim count: 0
- Max claim amount: 0.0
- History repair counts: {'驾驶侧': 0, '车尾': 0, '副驾驶': 2}
- S14 repair items: 右后翼子板 钣金
- Score: 96.0 / target 91.0
- Rejected: False 
- Qualified: True
- Decision: qualified: score >= target_score, stop reference loop and enter pricing

## 3. S10 Patch Acceptance
- reliable_s10_gate: True
- canonical_reference_order: True
- same_price_mileage_desc: True
- partial_card_click_blocked: True
- card_complete_required: True
- title_price_metadata_required: True
- empty_price_or_metadata_blocked: True
- invalid_partial_not_effective_history: True
- completion_scroll_used: True
- selected_by: canonical_reference_order
- patch_report_status: PATCH_ONLY_S10_REFERENCE_CARD_COMPLETENESS_GATE_AND_SCROLL_TO_COMPLETE_DONE

## 4. S13 History Repair Parser Acceptance
- raw_nodes_bounds_local_binding: True
- dedup_visible_texts_not_used: True
- duplicate_zero_preserved: True
- detection_pass_numbers_excluded: True
- Region 驾驶侧: bound_count_text=0, excluded_numbers=[{'text': '驾驶侧：检测通过34', 'numbers': ['34'], 'bounds': [78, 2232, 1144, 2375], 'reason': 'detection_pass_summary_not_history_repair_count'}]
- Region 车尾: bound_count_text=0, excluded_numbers=[{'text': '车尾：检测通过10', 'numbers': ['10'], 'bounds': [78, 2200, 1144, 2343], 'reason': 'detection_pass_summary_not_history_repair_count'}]
- Region 副驾驶: bound_count_text=2, excluded_numbers=[{'text': '副驾驶：检测通过33', 'numbers': ['33'], 'bounds': [78, 2340, 1144, 2483], 'reason': 'detection_pass_summary_not_history_repair_count'}]

## 5. Final Pricing
- target_score: 91.0
- final_reference_index: 5
- selected_reference_score: 96.0
- selected_reference_price: 4.16万
- suggested_listing_price: 41600
- guazi_service_fee_yuan: 2500
- guazi_net_payout_yuan: 39100
- guazi_return_price_yuan: 39100
- suggested_purchase_price_yuan: 34472
- old_95_percent_rule_used: False
- S17 payload summary: {'task_status': 'priced', 'suggested_listing_price_yuan': 41600, 'suggested_acquisition_price_yuan': 34472, 'final_reference_index': 5, 'reference_score': 96.0, 'target_score': 91.0}
- Old x95% rule used: false

## 6. Manual Review / Risk Notes
- Manual review reasons: ['目标车缺少出险次数，已采用默认分。', '目标车缺少最大金额，已采用默认分。', '三同车源少于 3 台，样本不足，结论参考性下降，需要人工审核。']
- Sample shortage warning present: True
- Target minor-damage scoring review flag: false
- Reference shortage / all-low-score risk: false; reference 5 qualified.

## 7. Slow Actions
- S14_COLLECT: 21238ms; affects correctness: false; optimize immediately: false; recommendation: Keep as baseline evidence; optimize later in a separate performance task.
- S14_HORIZONTAL_SWIPE: 5427ms; affects correctness: false; optimize immediately: false; recommendation: Keep as baseline evidence; optimize later in a separate performance task.
- S10_TO_S11 XML fresh: 10073ms; affects correctness: false; optimize immediately: false; recommendation: Keep as baseline evidence; optimize later in a separate performance task.
- S11_REPORT_SEARCH: 9821ms; affects correctness: false; optimize immediately: false; recommendation: Keep as baseline evidence; optimize later in a separate performance task.
- S11_TO_S12: 11544ms; affects correctness: false; optimize immediately: false; recommendation: Keep as baseline evidence; optimize later in a separate performance task.
- S14_RETURN_TO_S10: 8319ms; affects correctness: false; optimize immediately: false; recommendation: Keep as baseline evidence; optimize later in a separate performance task.

## 8. Capability Reuse Acceptance
- APP_FORCE_RESTART: True
- S01_to_S10_first_stage: True
- S07_color_age_filter: True
- S09_price_low_to_high: True
- S10_READY_gate: True
- reference_index_sequential_loop: True
- canonical_reference_order: True
- same_price_mileage_desc: True
- complete_card_gate: True
- S11_top_image_recognition: True
- report_entry_full_visible_safe_click: True
- S11_TO_S12_stable_wait: True
- S12_priority_over_S14: True
- S13_raw_nodes_history_repair_parser: True
- S14_repair_item_collect: True
- S15_single_reference_scoring: True
- S16_service_fee_tier_pricing: True
- S17_payload_output: True
- reference_history_retained: True
- old_target_pollution_protection: True
- raw_xml_not_written_to_result_json: True

## 9. Final Acceptance Status
`SANTANA_FULL_CHAIN_ACCEPTANCE_PASSED`

Final state: `READ_ONLY_SANTANA_FULL_CHAIN_ACCEPTANCE_REPORT_DONE`