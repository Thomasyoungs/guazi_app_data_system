# S_LOGIN System Nav Back Recovery Patch

Status: `S_LOGIN_SYSTEM_NAV_BACK_RECOVERY_PATCHED`

## Modified Files

- `scripts/runtime_s01_to_s10_mainline.py`

## Implementation

- Keeps S_LOGIN order unchanged: exact `??` first.
- If no `??` and XML exposes a bottom `< / ?` node, clicks that XML node once.
- If no `??` and XML does not expose the system navigation key, but the current page is confirmed S_LOGIN, executes one `S_LOGIN_ONLY_ALLOWED_ACTION_PRESS_BACK` via system BACK.
- Captures screenshot/XML immediately after BACK and re-runs page recognition.
- Stops with `HUMAN_LOGIN_REQUIRED` if one BACK still leaves S_LOGIN with no `??`.
- Stops with `PAGE_CONTRACT_MISMATCH_AFTER_LOGIN_EXIT` if BACK exits to a non-contract page.

## Safety

No login-page content is clicked or typed. The system BACK branch is scoped only to confirmed S_LOGIN and is not available as a generic fallback on business pages.

## Verification

- `py_compile`: PASS
- module import: PASS
- offline A-F validation: PASS
- real-device first-stage validation: pending
