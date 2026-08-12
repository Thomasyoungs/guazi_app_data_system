# S11 Report Entry Cause Diagnosis

Final status: `DIAGNOSE_S11_REPORT_ENTRY_CAUSE_DONE`

Target fingerprint: `福特|福克斯|2017款|两厢 1.6L 自动舒适型智行版|白|2017.07`

Scope confirmation:

- Code modified: no
- Page contract modified: no
- Recognizer modified: no
- Config modified: no
- Pricing modified: no
- New device run: no
- Old MINI result used: no

## Conclusion

Primary classification:

`H. S11_REPORT_ENTRY_WRONG_TARGET_CLICKED`

Runtime did enter S11 report search, used exact text `查看完整报告`, found the exact XML text node after one controlled scroll, and clicked with `text_node_bounds`. The problem is that the matched node bounds were `[80, 2445, 597, 2559]`, whose center is `[338, 2502]`, inside or immediately overlapping the bottom fixed operation bar area. The text node and its local ancestors are not clickable. After the tap, the XML visible-text digest stayed identical and `查看完整报告` was still present, so the tap did not navigate to the S12 full report page.

This is not a fuzzy-match issue and not a missing-entry issue.

## Timeline

1. S10 to S11 succeeded:
   - `recognized_page=S11`
   - `recognized_by=S11_TOP_ONE_THIRD_IMAGE_ONLY_STANDARD`
   - S11 snapshot XML: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s10_to_s11_20260507_183931_compressed.xml`

2. S11 handler reused the S10 to S11 snapshot:
   - `s11_entry_snapshot_reused=true`
   - `s11_handler_initial_dump_skipped_due_to_reuse=true`
   - `transfer_count=2`

3. `S11_REPORT_ENTRY_NODE_SEARCH` ran on the entry snapshot:
   - searched text: `查看完整报告`
   - exact match required: true
   - found in entry snapshot: false
   - entry snapshot did contain `检测报告` and `查看报价`, but those were not used as report-entry matches.

4. Controlled scroll executed:
   - `S11_REPORT_SCROLL`
   - `scroll_attempt_count=1`
   - gesture recorded by code/timing as `client.swipe("up")`
   - exact scroll bounds were not recorded in this timing row.

5. Scroll was effective:
   - before XML digest: `3fdd9126e637b6da`
   - after XML digest: `f946812d8f8038e8`
   - screenshot changed from the S11 top detail first screen into the official report preview area.
   - after-scroll XML: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_scroll_report_20260507_183940.xml`

6. After scroll, runtime recognized the page as S10:
   - timing row `S11_REPORT_SCROLL_FRESH` has `recognized_page=S10`
   - likely reason: after the internal S11 scroll, the top image area disappeared, so the S10_TO_S11-only top-image S11 recognizer no longer applied. The page still contained Focus/price-like texts, so generic recognition recorded S10.
   - This was a secondary observation; it did not interrupt the report search because the script still found the exact report-entry node and clicked it.

7. Exact node was found and clicked:
   - matched text: `查看完整报告`
   - matched bounds: `[80, 2445, 597, 2559]`
   - clicked point: `[338, 2502]`
   - click strategy: `text_node_bounds`
   - click duration: `79ms`

8. Click did not navigate to S12:
   - after-click XML: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\debug\s11_to_s12_20260507_183945.xml`
   - after-click screenshot: `C:\Users\lzc93\Desktop\定价\guazi_app_data_system\artifacts\screenshots\s11_to_s12_20260507_183945.png`
   - after-click visible-text digest: `f946812d8f8038e8`
   - before-click visible-text digest: `f946812d8f8038e8`
   - `查看完整报告` was still present after the click.
   - Final issue: `PAGE_CONTRACT_MISMATCH`, message: `Expected S12, recognized S10`

## Direct Answers

1. Entered S11 report search: yes.

Evidence: timing rows `S11_REPORT_ENTRY_NODE_SEARCH`, `S11_REPORT_SCROLL`, `S11_REPORT_SCROLL_FRESH`, `S11_REPORT_SEARCH`, `S11_CLICK_VIEW_FULL_REPORT`.

2. Actual searched text: exact `查看完整报告`.

No evidence that runtime used fuzzy `报告`, `检测报告`, `查看报价`, or bottom-bar text as the search target.

3. Exact `查看完整报告` node seen: yes, after one scroll.

Matched bounds: `[80, 2445, 597, 2559]`

4. Controlled scroll executed: yes.

`S11_REPORT_SCROLL` ran once. It changed XML, screenshot, and visible text. The entry appeared after that scroll.

5. Scroll effective: yes.

Reason: XML digest changed, screenshot changed, and the official report preview area became visible.

6. Page state after scroll: recorded as S10.

This looks like a recognizer state-loss side effect after the top image left the viewport. It did not stop the exact-node search.

7. Click behavior: yes, click executed.

Clicked text: `查看完整报告`

Clicked bounds: `[80, 2445, 597, 2559]`

Likely issue: those bounds were not a safe actionable target; they overlapped the bottom fixed operation bar area.

8. Click changed page: no semantic page transition.

The after-click XML digest matched the before-click XML digest. The report entry remained visible.

9. Was it S12 contract mismatch after a real exact-report click?

No. The evidence does not show a clear S12 candidate page after the click. It shows the same S11 report preview page, so this is not primarily an S12 recognizer problem.

10. Was Expected S12 set without exact click?

No. The exact click timing exists. The problem is that the effective tap did not navigate.

## Ruled Out

- `A. S11_REPORT_SEARCH_NOT_ENTERED`: ruled out by timing rows.
- `B. S11_REPORT_ENTRY_EXACT_MATCH_NOT_USED`: ruled out; exact text was used.
- `C. S11_REPORT_SCROLL_NOT_EXECUTED`: ruled out; one scroll executed.
- `D. S11_REPORT_SCROLL_NOT_EFFECTIVE`: ruled out; scroll changed content and exposed the entry.
- `E. S11_STATE_LOST_AFTER_SCROLL`: observed as secondary, but not the primary cause because the search continued and clicked exact text.
- `F. S11_REPORT_ENTRY_NOT_FOUND_AFTER_VALID_SCROLL`: ruled out; exact node found.
- `G. S11_REPORT_ENTRY_TEXT_VARIANT_OR_SPLIT`: ruled out; text existed as one complete XML node.
- `I. S12_CONTRACT_MISMATCH_AFTER_EXACT_REPORT_CLICK`: not primary; after click did not become a clear S12 page.
- `J. S11_TO_S12_UNKNOWN_NEEDS_MORE_EVIDENCE`: current evidence is enough to identify the ineffective/wrong effective tap target.

## Suggested Patch Scope For Later

Do not change S11 recognizer for this issue.

If a later PATCH_ONLY turn is requested, the smallest useful runtime patch would be in S11 report-entry clicking:

- require `查看完整报告` to have a safe actionable click target above the fixed bottom operation bar;
- if its bounds are clipped/overlapped by the bottom bar, scroll slightly further before tapping;
- prefer a clickable local parent only if it is a real local report-entry container, not the full-screen root;
- keep exact text matching and XML evidence.

No patch was applied in this diagnosis-only turn.
