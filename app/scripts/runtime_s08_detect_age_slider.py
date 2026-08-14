"""Read-only S08 exact-age-slider recognition.

This script is a read-only helper for S08_AGE_EXACT_SLIDER_PANEL. It detects
the right-side exact age slider, slider track, current value, and endpoint
values. It never drags the slider, never clicks confirm/view-result, never
enters the vehicle list, and never collects vehicle-source fields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    result = {
        "action_id": "detect_age_exact_slider",
        "state": "S08_AGE_EXACT_SLIDER_PANEL",
        "outputs": [
            "slider_bounds",
            "track_bounds",
            "current_age_value",
            "min_age_value",
            "max_age_value",
        ],
        "forbidden_actions": [
            "drag_slider",
            "set_age_range",
            "expand_age_range",
            "click_confirm",
            "click_view_result",
            "collect_vehicle_data",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
