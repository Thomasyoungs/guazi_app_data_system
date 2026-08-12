import importlib.util
import json
import re
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "runtime_s01_to_s10_mainline.py"
S10_TO_S16_SCRIPT = ROOT / "scripts" / "runtime_s10_to_s16_mainline.py"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def kw(*parts: str) -> str:
    return "".join(parts)


def load_script_module():
    spec = importlib.util.spec_from_file_location("runtime_s01_to_s10_mainline", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def launcher_package(module) -> str:
    return getattr(module, "S_LOGIN_PACKAGE", module.S_LOGIN_LEGACY_PACKAGE)


def make_runtime_stub() -> dict:
    return {
        "configs": {
            "system": {
                "paths": {
                    "result_json": "output/test_result.json",
                }
            },
            "pages": {},
            "fields": {},
            "actions": {},
            "exceptions": {},
        },
        "audit": object(),
        "issues": None,
    }


def write_valid_runtime_target_task(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "brand": "本田",
                "series": "雅阁",
                "year_model": "2021款",
                "config_model": "260TURBO 豪华版",
                "color": "白",
                "registration_date": "2021.06",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def make_minimal_pages_config() -> dict:
    def page(page_id: str, allowed_actions: list[str] | None = None) -> dict:
        recognition = {"strong_contains": ["选择品牌"]} if page_id == "S03" else {"strong_contains": []}
        return {
            "id": page_id,
            "name": page_id,
            "recognition": recognition,
            "allowed_actions": list(allowed_actions or []),
            "forbidden_actions": [],
            "next": [],
            "return_to": None,
            "exception": {},
        }

    return {
        "pages": [
            page("S01", ["click_bottom_select_car_tab"]),
            page("S03", ["tap_brand_letter", "tap_target_brand", "scroll_brand_list"]),
            page("S04", ["click_series_model_button"]),
        ]
    }


class DummyIssues:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, code, state_id, message, context, resolution, recognized_text=None, attempts=0):
        record = {
            "code": code,
            "state_id": state_id,
            "message": message,
            "context": context,
            "resolution": resolution,
            "knowledge_lookup": {"auto_continue_allowed": False},
        }
        self.records.append(record)
        return record


class FakeAdbResult:
    def __init__(self, success=True, stdout="", stderr="", returncode=0) -> None:
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class LoginFakeClient:
    def __init__(self) -> None:
        self.tapped_texts: list[str] = []
        self.icon_tap_xmls: list[str] = []
        self.taps: list[tuple[int, int]] = []

    def wake_screen_once(self):
        return {}

    def power_state(self):
        return {"wakefulness": "Awake", "interactive": True, "display_state": "ON"}

    def screen_size(self):
        return (1220, 2712)

    def run(self, *_args, **_kwargs):
        return FakeAdbResult(stdout="isKeyguardShowing=false mShowingLockscreen=false mCurrentFocus=com.android.launcher/.Launcher")

    def home_key_once(self):
        return {}

    def wake_swipe_once(self, *args, **kwargs):
        return {"swipe_success": True}

    def dump_ui_xml(self):
        return "<hierarchy>瓜子二手车</hierarchy>"

    def tap_guazi_app_icon_exact_text(self, _xml):
        self.icon_tap_xmls.append(_xml)
        return FakeAdbResult(success=True)

    def tap_text(self, text):
        self.tapped_texts.append(text)
        return FakeAdbResult(success=True)

    def tap(self, x: int, y: int):
        self.taps.append((x, y))
        return FakeAdbResult(success=True)

    def back(self):
        return FakeAdbResult(success=True)


class S01Recognizer:
    def recognize(self, blob, candidate_ids=None, context=None):
        if "S01_OK" in blob and candidate_ids and "S01" in candidate_ids:
            return {"id": "S01"}
        return None


class S01S02GreedyRecognizer:
    def recognize(self, blob, candidate_ids=None, context=None):
        if "S02_OK" in blob and candidate_ids:
            if "S02_SELECT_CAR_TAB" in candidate_ids:
                return {"id": "S02_SELECT_CAR_TAB"}
            if "S02" in candidate_ids:
                return {"id": "S02"}
            if "S09" in candidate_ids and "价格从低到高" in blob:
                return {"id": "S09"}
            if "S08" in candidate_ids and ("综合排序" in blob or "万公里" in blob):
                return {"id": "S08"}
            if "S10" in candidate_ids and "价格从低到高" in blob:
                return {"id": "S10"}
        if "S01_OK" in blob and candidate_ids:
            if "S01" in candidate_ids:
                return {"id": "S01"}
            if "S09" in candidate_ids and "价格从低到高" in blob:
                return {"id": "S09"}
            if "S08" in candidate_ids and "综合排序" in blob:
                return {"id": "S08"}
        return None


class NoPageRecognizer:
    def recognize(self, *_args, **_kwargs):
        return None


class S03Recognizer:
    def recognize(self, blob, candidate_ids=None, context=None):
        if "选择品牌" in blob and candidate_ids and "S03" in candidate_ids:
            return {"id": "S03"}
        if "S04_OK" in blob:
            if candidate_ids and "S04" in candidate_ids:
                return {"id": "S04"}
            return None
        if "S05_OK" in blob and candidate_ids and "S05" in candidate_ids:
            return {"id": "S05"}
        if "车系" in blob and candidate_ids and "S04" in candidate_ids:
            return {"id": "S04"}
        if "车型" in blob and candidate_ids and "S05" in candidate_ids:
            return {"id": "S05"}
        return None


class S02ToS03Recognizer:
    def recognize(self, blob, candidate_ids=None, context=None):
        if "选择品牌" in blob and candidate_ids and "S03" in candidate_ids:
            return {"id": "S03"}
        return None


class S03FakeClient:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []
        self.swipes: list[str] = []
        self.home_calls = 0
        self.icon_taps = 0

    def tap(self, x: int, y: int):
        self.taps.append((x, y))
        return FakeAdbResult(success=True)

    def swipe(self, direction: str = "up"):
        self.swipes.append(direction)
        return FakeAdbResult(success=True)

    def run(self, args, timeout=20):
        if args[:3] == ["shell", "input", "swipe"]:
            self.swipes.append("adb_input_swipe")
        return FakeAdbResult(success=True)

    def home_key_once(self):
        self.home_calls += 1
        return FakeAdbResult(success=True)

    def tap_guazi_app_icon_exact_text(self, _xml):
        self.icon_taps += 1
        return FakeAdbResult(success=True)


class S02Machine:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    def assert_action_allowed(self, state_id: str, action_id: str) -> None:
        if state_id != "S02" or action_id != "tap_brand_filter":
            raise AssertionError((state_id, action_id))
        self.actions.append((state_id, action_id))


class S03Machine:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    def assert_action_allowed(self, state_id: str, action_id: str) -> None:
        if state_id != "S03" or action_id not in {"tap_brand_letter", "scroll_brand_list", "tap_target_brand"}:
            raise AssertionError((state_id, action_id))
        self.actions.append((state_id, action_id))


class S04Machine:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    def assert_action_allowed(self, state_id: str, action_id: str) -> None:
        if state_id != "S04" or action_id not in {"click_series_model_button", "scroll_series_list"}:
            raise AssertionError((state_id, action_id))
        self.actions.append((state_id, action_id))


class S05Recognizer:
    def recognize(self, blob, candidate_ids=None, context=None):
        if "S06_OK" in blob and candidate_ids and "S06" in candidate_ids:
            return {"id": "S06"}
        if "S05_TRIM_SELECTED_OK" in blob and candidate_ids and "S05_TRIM_SELECTED" in candidate_ids:
            return {"id": "S05_TRIM_SELECTED"}
        if "S05_YEAR_SELECTED_OK" in blob and candidate_ids and "S05_MODEL_YEAR_SELECTED" in candidate_ids:
            return {"id": "S05_MODEL_YEAR_SELECTED"}
        if "S05_OK" in blob and candidate_ids and "S05" in candidate_ids:
            return {"id": "S05"}
        return None


class S05Machine:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []

    def assert_action_allowed(self, state_id: str, action_id: str) -> None:
        allowed = {
            ("S05", "tap_target_year"),
            ("S05_MODEL_YEAR_SELECTED", "tap_exact_trim"),
            ("S05_TRIM_SELECTED", "tap_green_confirm"),
        }
        if (state_id, action_id) not in allowed:
            raise AssertionError((state_id, action_id))
        self.actions.append((state_id, action_id))


def make_login_snapshot(module, marker: str, *, with_later: bool = True, bounds: list[int] | None = None) -> dict:
    visible_texts = ["检测到您的账号已退出登录", "请重新登录账号"]
    nodes = []
    if with_later:
        visible_texts.append(module.S_LOGIN_LATER_TEXT)
        nodes.append(
            {
                "labels": [module.S_LOGIN_LATER_TEXT],
                "bounds": bounds or [133, 1366, 569, 1509],
            }
        )
    visible_texts.append("去登录")
    return {
        "foreground_package": launcher_package(module),
        "xml_package": launcher_package(module),
        "focused_window": f"{launcher_package(module)}/Launcher",
        "visible_blob": "".join(visible_texts),
        "visible_texts": visible_texts,
        "nodes": nodes,
        "fresh_xml": f"<login marker='{marker}' />",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s03_snapshot(module, texts: list[str], *, marker: str = "s03") -> dict:
    nodes = []
    for index, text in enumerate(texts):
        if re.fullmatch(r"[A-Z]", str(text).strip()):
            bounds = [1040, 200 + index * 70, 1110, 250 + index * 70]
        else:
            bounds = [60, 200 + index * 90, 360, 260 + index * 90]
        nodes.append(
            {
                "labels": [text],
                "bounds": bounds,
            }
        )
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "选择品牌" + "".join(texts),
        "visible_texts": ["选择品牌", *texts],
        "nodes": nodes,
        "fresh_xml": f"<s03 marker='{marker}' />",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s03_context(module, client: S03FakeClient | None = None) -> dict:
    return {
        "client": client or S03FakeClient(),
        "recognizer": S03Recognizer(),
        "issues": DummyIssues(),
        "machine": S03Machine(),
        "timing": module.TimingRecorder(),
        "task_params": {},
    }


def make_s04_snapshot(module, series_names: list[str], *, marker: str = "s04") -> dict:
    nodes = [
        {
            "labels": ["本田车系"],
            "bounds": [506, 169, 714, 234],
        },
        {
            "labels": ["全部"],
            "bounds": [26, 292, 210, 435],
        },
        {
            "labels": ["轿车"],
            "bounds": [52, 435, 1168, 552],
        },
    ]
    y = 552
    for name in series_names:
        row_bounds = [52, y, 1168, y + 306]
        button_bounds = [869, y + 50, 1129, y + 229]
        nodes.append({"labels": [f"{name}\n本地10辆在售\n1.00-9.99万"], "bounds": row_bounds})
        nodes.append({"labels": ["车型"], "bounds": button_bounds})
        y += 306
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "S04_OK本田车系全部轿车" + "".join(series_names) + "车型",
        "visible_texts": ["S04_OK", "本田车系", "全部", "轿车", *series_names, "车型"],
        "nodes": nodes,
        "fresh_xml": f"<s04 marker='{marker}'>{''.join(series_names)}</s04>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s04_context(module, client: S03FakeClient | None = None) -> dict:
    return {
        "client": client or S03FakeClient(),
        "recognizer": S03Recognizer(),
        "issues": DummyIssues(),
        "machine": S04Machine(),
        "timing": module.TimingRecorder(),
        "task_params": {"series": "缤智"},
    }


def make_s05_snapshot(
    module,
    marker: str,
    state_marker: str,
    *,
    right_years: list[str] | None = None,
    selected_count: int = 0,
    confirm_clickable: bool = False,
    selected_trim: bool = False,
    xml_missing: bool = False,
) -> dict:
    right_years = right_years if right_years is not None else ["2026款", "2025款", "2023款", "2022款"]
    target_trim = "1.5L CVT两驱科技精英"
    nodes = [
        {"labels": ["缤智车型"], "bounds": [0, 0, 1180, 90]},
        {"labels": ["2018款"], "bounds": [0, 640, 292, 750], "selected": "2018款" in right_years},
    ]
    for index, year in enumerate(right_years):
        label = f"{year} {target_trim}" if year == "2018款" else f"{year} 1.5L CVT 示例车型"
        nodes.append(
            {
                "labels": [label],
                "bounds": [330, 240 + index * 150, 1120, 340 + index * 150],
                "selected": bool(selected_trim and year == "2018款"),
            }
        )
    selected_label = f"已选{selected_count}项"
    nodes.extend(
        [
            {"labels": [selected_label], "bounds": [40, 1740, 320, 1820]},
            {"labels": ["确定"], "bounds": [420, 1740, 1130, 1820], "clickable": confirm_clickable},
        ]
    )
    labels = [label for node in nodes for label in node["labels"]]
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": f"{state_marker}{''.join(labels)}",
        "visible_texts": [state_marker, *labels],
        "nodes": nodes,
        "fresh_xml": "" if xml_missing else f"<s05 marker='{marker}'>{state_marker}</s05>",
        "xml_missing": xml_missing,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": None if xml_missing else f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s05_context(module, client: S03FakeClient | None = None) -> dict:
    return {
        "client": client or S03FakeClient(),
        "recognizer": S05Recognizer(),
        "issues": DummyIssues(),
        "machine": S05Machine(),
        "timing": module.TimingRecorder(),
        "task_params": {"model_year": "2018款", "trim": "1.5L CVT两驱科技精英"},
    }


def make_s01_snapshot(module) -> dict:
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "S01_OK 首页选车卖车新能源我的",
        "visible_texts": ["S01_OK", "首页", "选车", "卖车", "新能源", "我的"],
        "nodes": [],
        "fresh_xml": "<s01 />",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": "artifacts/screenshots/s01.png",
        "xml_path": "artifacts/debug/s01.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s02_snapshot(module) -> dict:
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "S02_OK 首页选车卖车新能源我的品牌选车AI选车搜索品牌综合排序价格从低到高2019年 | 1.01万公里",
        "visible_texts": [
            "S02_OK",
            "首页",
            "选车",
            "卖车",
            "新能源",
            "我的",
            "品牌选车",
            "AI选车",
            "搜索",
            "品牌",
            "综合排序",
            "价格从低到高",
            "2019年 | 1.01万公里",
        ],
        "nodes": [],
        "fresh_xml": "<s02 current='fresh'><node text='品牌'/><node text='综合排序'/></s02>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": "artifacts/screenshots/s02.png",
        "xml_path": "artifacts/debug/s02.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s02_filter_entry_snapshot_without_sell(module, search_text: str = "理想汽车理想L7") -> dict:
    visible_texts = [
        "全部",
        "官方自营",
        "品牌选车",
        "AI选车",
        "唐山",
        search_text,
        "综合排序",
        "品牌",
        "价格",
        "车龄/里程",
        "筛选",
        "5万以下",
        "零跑汽车 零跑T03 2020款 400豪华版",
        "2020年 | 6.92万公里",
        "门店实车",
        "3.49",
        "万",
        "首付0.35万",
        "首页",
        "选车",
        "新能源",
        "我的",
    ]
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "".join(visible_texts),
        "visible_texts": visible_texts,
        "nodes": [
            {
                "labels": ["品牌"],
                "bounds": [339, 396, 471, 526],
                "clickable": True,
                "enabled": True,
                "selected": False,
                "resource_id": "com.ganji.android.haoche_c:id/ftv_brand",
            },
            {
                "labels": ["选车"],
                "bounds": [244, 2354, 488, 2536],
                "clickable": False,
                "enabled": True,
                "selected": True,
                "resource_id": "com.ganji.android.haoche_c:id/largeLabel",
            },
        ],
        "fresh_xml": "<s02><node text='品牌' resource-id='com.ganji.android.haoche_c:id/ftv_brand' bounds='[339,396][471,526]'/></s02>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": "artifacts/screenshots/s02_filter_entry.png",
        "xml_path": "artifacts/debug/s01_to_s02_20260619_115240.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s02_snapshot_from_live_xml(module) -> dict:
    xml_path = ROOT / "artifacts" / "debug" / "s01_to_s02_20260619_115240.xml"
    xml_text = xml_path.read_text(encoding="utf-8")
    visible_texts = module._visible_texts(xml_text)
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "".join(visible_texts),
        "visible_texts": visible_texts,
        "nodes": module._parse_nodes(xml_text),
        "fresh_xml": xml_text,
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": "artifacts/screenshots/s01_to_s02_20260619_115240.png",
        "xml_path": str(xml_path),
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s03_brand_panel_snapshot(module) -> dict:
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "选择品牌ABCDE",
        "visible_texts": ["选择品牌", "A", "B", "C", "D", "E"],
        "nodes": [],
        "fresh_xml": "<s03><node text='选择品牌'/></s03>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": "artifacts/screenshots/s03_brand_panel.png",
        "xml_path": "artifacts/debug/s03_brand_panel.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s10_snapshot(module) -> dict:
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/Main",
        "visible_blob": "价格从低到高12.80万2020年 | 6.20万公里大众帕萨特",
        "visible_texts": ["价格从低到高", "12.80万", "2020年 | 6.20万公里", "大众帕萨特"],
        "nodes": [],
        "fresh_xml": "<s10 current='fresh'><node text='12.80万'/><node text='2020年 | 6.20万公里'/></s10>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": "artifacts/screenshots/s10.png",
        "xml_path": "artifacts/debug/s10.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_s14_detail_snapshot(module, marker: str = "s14_detail") -> dict:
    return {
        "foreground_package": module.GUAZI_PACKAGE,
        "xml_package": module.GUAZI_PACKAGE,
        "focused_window": f"{module.GUAZI_PACKAGE}/com.guazi.h5.Html5NewContainerActivity",
        "visible_blob": "瓜子官方检测报告欧拉黑猫 2019款 351km 亲子版后保险杠—拆卸痕迹AI详细解读【后保险杠】异常细节",
        "visible_texts": [
            "瓜子官方检测报告",
            "欧拉黑猫 2019款 351km 亲子版",
            "后保险杠—拆卸痕迹",
            "AI详细解读【后保险杠】异常细节",
        ],
        "nodes": [],
        "fresh_xml": "<s14><node text='瓜子官方检测报告'/><node text='后保险杠—拆卸痕迹'/></s14>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_app_icon_snapshot(module, marker: str = "app_icon") -> dict:
    return {
        "foreground_package": launcher_package(module),
        "xml_package": launcher_package(module),
        "focused_window": f"{launcher_package(module)}/Launcher",
        "visible_blob": f"应用列表{module.GUAZI_APP_ICON_LABEL}",
        "visible_texts": ["应用列表", module.GUAZI_APP_ICON_LABEL],
        "nodes": [
            {
                "labels": [module.GUAZI_APP_ICON_LABEL],
                "bounds": [371, 1624, 566, 1676],
            }
        ],
        "fresh_xml": f"<launcher marker='{marker}'>{module.GUAZI_APP_ICON_LABEL}</launcher>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_miui_newhome_ad_snapshot(module, marker: str = "miui_newhome_ad") -> dict:
    visible_texts = ["看点", "搜索资讯、视频、作者...", "穿山甲AD", "广告", "立即下载", "首页", "视频", "热榜", "我的"]
    return {
        "foreground_package": "",
        "xml_package": "com.miui.newhome",
        "focused_window": "LauncherOverlayWindow:com.miui.newhome",
        "visible_blob": "".join(visible_texts),
        "visible_texts": visible_texts,
        "keyguard_showing": True,
        "keyguard_secure": False,
        "nodes": [
            {
                "labels": ["立即下载"],
                "resource_id": "com.miui.newhome:id/actionButton",
                "package": "com.miui.newhome",
                "clickable": True,
                "enabled": True,
                "bounds": [777, 1843, 933, 1884],
            },
            {
                "labels": [],
                "resource_id": "com.miui.newhome:id/ad_close",
                "package": "com.miui.newhome",
                "clickable": True,
                "enabled": True,
                "bounds": [945, 1814, 1035, 1912],
            },
        ],
        "fresh_xml": (
            f"<hierarchy marker='{marker}' package='com.miui.newhome'>"
            "<node text='立即下载' resource-id='com.miui.newhome:id/actionButton'/>"
            "<node resource-id='com.miui.newhome:id/ad_close'/>"
            "</hierarchy>"
        ),
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


def make_miui_desktop_snapshot(module, marker: str = "miui_desktop") -> dict:
    snapshot = make_app_icon_snapshot(module, marker)
    snapshot["foreground_package"] = "com.miui.home"
    snapshot["xml_package"] = "com.miui.home"
    snapshot["focused_window"] = "com.miui.home/com.miui.home.launcher.Launcher"
    snapshot["keyguard_showing"] = True
    snapshot["keyguard_secure"] = False
    snapshot["visible_texts"] = ["设置", "相册", "天气", module.GUAZI_APP_ICON_LABEL]
    snapshot["visible_blob"] = "设置相册天气" + module.GUAZI_APP_ICON_LABEL
    snapshot["fresh_xml"] = f"<launcher marker='{marker}' package='com.miui.home'>{module.GUAZI_APP_ICON_LABEL}</launcher>"
    return snapshot


def make_desktop_upgrade_snapshot(
    module,
    marker: str = "desktop_upgrade",
    *,
    with_later: bool = True,
    with_now: bool = True,
    with_title: bool = True,
    with_launcher_text: bool = True,
) -> dict:
    visible_texts: list[str] = []
    nodes: list[dict] = []
    if with_title:
        visible_texts.append(module.DESKTOP_UPGRADE_MODAL_TITLE_TEXT)
        nodes.append(
            {
                "labels": [module.DESKTOP_UPGRADE_MODAL_TITLE_TEXT],
                "bounds": [220, 760, 1000, 840],
                "clickable": False,
                "enabled": True,
            }
        )
    if with_launcher_text:
        visible_texts.append(module.DESKTOP_UPGRADE_MODAL_LAUNCHER_TEXT)
        nodes.append(
            {
                "labels": [module.DESKTOP_UPGRADE_MODAL_LAUNCHER_TEXT],
                "bounds": [220, 850, 1000, 930],
                "clickable": False,
                "enabled": True,
            }
        )
    if with_later:
        visible_texts.append(module.DESKTOP_UPGRADE_MODAL_LATER_TEXT)
        nodes.append(
            {
                "labels": [module.DESKTOP_UPGRADE_MODAL_LATER_TEXT],
                "bounds": [170, 1320, 560, 1460],
                "clickable": True,
                "enabled": True,
            }
        )
    if with_now:
        visible_texts.append(module.DESKTOP_UPGRADE_MODAL_NOW_TEXT)
        nodes.append(
            {
                "labels": [module.DESKTOP_UPGRADE_MODAL_NOW_TEXT],
                "bounds": [620, 1320, 1040, 1460],
                "clickable": True,
                "enabled": True,
            }
        )
    return {
        "foreground_package": launcher_package(module),
        "xml_package": launcher_package(module),
        "focused_window": f"{launcher_package(module)}/Launcher",
        "visible_blob": "".join(visible_texts),
        "visible_texts": visible_texts,
        "nodes": nodes,
        "fresh_xml": f"<launcher marker='{marker}'>{''.join(visible_texts)}</launcher>",
        "xml_missing": False,
        "screenshot_missing": False,
        "screenshot_path": f"artifacts/screenshots/{marker}.png",
        "xml_path": f"artifacts/debug/{marker}.xml",
        "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
    }


class S01ToS10MainlineExecutorTest(unittest.TestCase):
    def test_runtime_s01_to_s10_mainline_script_exists(self):
        self.assertTrue(SCRIPT_PATH.exists())

    def test_s01_to_s10_mainline_has_required_states(self):
        source = read_text(SCRIPT_PATH)
        for name in [
            "run_s01_to_s10_mainline",
            "handle_s01",
            "handle_s02",
            "handle_s03",
            "handle_s04",
            "handle_s05",
            "handle_s06",
            "handle_s07",
            "handle_s08",
            "handle_s09",
            "handle_s10_ready_check",
        ]:
            self.assertIn(f"def {name}(", source)

    def test_s01_to_s10_uses_adb_client_without_bare_adb_subprocess(self):
        source = read_text(SCRIPT_PATH)
        self.assertIn("AdbClient", source)
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("subprocess.run", source)
        self.assertNotIn("adb.exe", source)
        self.assertNotIn("ANDROID_SDK_HOME", source)
        self.assertNotIn("ADB_VENDOR_KEYS", source)

    def test_s01_to_s10_imports_project_src_app_startup(self):
        module = load_script_module()
        app_startup_path = Path(sys.modules[module.AdbClient.__module__].__file__).resolve()

        self.assertEqual(app_startup_path, ROOT / "src" / "guazi_app_data_system" / "app_startup.py")

    def test_s01_to_s10_adbclient_inherits_runtime_adb_environment(self):
        module = load_script_module()
        with mock.patch.dict(
            "os.environ",
            {
                "ANDROID_SDK_HOME": r"C:\Users\lzc93\AppData\Local\Android\Sdk",
                "ANDROID_USER_HOME": r"C:\Users\lzc93\.android",
                "HOME": r"C:\Users\lzc93",
                "USERPROFILE": r"C:\Users\lzc93",
            },
            clear=False,
        ):
            client = module.AdbClient()
            env = client.adb_environment()

        self.assertEqual(env["ANDROID_SDK_HOME"], r"C:\Users\lzc93\AppData\Local\Android\Sdk")
        self.assertEqual(env["ANDROID_USER_HOME"], r"C:\Users\lzc93\.android")
        self.assertEqual(env["HOME"], r"C:\Users\lzc93")
        self.assertEqual(env["USERPROFILE"], r"C:\Users\lzc93")
        self.assertNotIn("output\\adb_home", str(env))

    def test_s01_select_car_uses_bottom_safe_area(self):
        source = read_text(SCRIPT_PATH)
        self.assertIn("tap_s01_bottom_select_car_tab", source)

    def test_s01_select_car_forbids_direct_tap_text(self):
        source = read_text(SCRIPT_PATH)
        self.assertNotIn('tap_text("选车")', source)
        self.assertNotIn("tap_text('选车')", source)

    def test_s07_color_confirmed_before_age(self):
        source = read_text(SCRIPT_PATH)
        self.assertIn("_target_color_selected", source)
        self.assertRegex(source, r"if not _target_color_selected\(snapshot, .*?\):")

    def test_s07_age_exact_before_view_result(self):
        source = read_text(SCRIPT_PATH)
        self.assertIn("_exact_age_confirmed", source)
        self.assertIn("if exact_snapshot is not None and _exact_age_confirmed", source)
        self.assertIn('age_confirm_action = "reuse_internal_exact_age_fresh_evidence"', source)

    def test_s08_sort_before_detail(self):
        source = read_text(SCRIPT_PATH)
        self.assertIn("综合排序", source)
        self.assertNotIn("S11", source.split("def handle_s08(", 1)[1].split("def handle_s09(", 1)[0])

    def test_s09_click_low_to_high_only(self):
        source = read_text(SCRIPT_PATH)
        section = source.split("def handle_s09(", 1)[1].split("def handle_s10_ready_check(", 1)[0]
        self.assertIn("价格从低到高", section)
        self.assertNotIn("价格从高到低", section)

    def test_s10_ready_does_not_enter_s11(self):
        source = read_text(SCRIPT_PATH)
        section = source.split("def handle_s10_ready_check(", 1)[1]
        self.assertIn('"status": "S10_READY"', section)
        self.assertNotIn("S11", section)

    def test_s10_ready_requires_state_machine_expected_s10(self):
        module = load_script_module()
        issues = DummyIssues()
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._assert_s10_ready_contract(issues, make_s10_snapshot(module), source=None)
        self.assertEqual(raised.exception.code, "S10_READY_BLOCKED_BEFORE_FILTER_OR_SORT_DONE")

    def test_home_page_cannot_be_s10_ready(self):
        module = load_script_module()

        class GreedyRecognizer:
            def recognize(self, blob, candidate_ids=None, context=None):
                if candidate_ids and "S10" in candidate_ids:
                    return {"id": "S10"}
                if "S01_OK" in blob and candidate_ids and "S01" in candidate_ids:
                    return {"id": "S01"}
                return None

        snapshot = make_s01_snapshot(module)
        self.assertFalse(module._looks_like_s10_ready_contract(snapshot))
        self.assertEqual(module._recognize_page(GreedyRecognizer(), snapshot), "S01")

    def test_select_car_page_cannot_be_s10_ready(self):
        module = load_script_module()

        class GreedyRecognizer:
            def recognize(self, blob, candidate_ids=None, context=None):
                if candidate_ids and "S10" in candidate_ids:
                    return {"id": "S10"}
                if "S02_OK" in blob and candidate_ids and "S02" in candidate_ids:
                    return {"id": "S02"}
                return None

        snapshot = make_s01_snapshot(module)
        snapshot["visible_blob"] = "S02_OK 首页选车卖车新能源我的品牌"
        snapshot["visible_texts"] = ["S02_OK", "首页", "选车", "卖车", "新能源", "我的", "品牌"]
        self.assertFalse(module._looks_like_s10_ready_contract(snapshot))
        self.assertEqual(module._recognize_page(GreedyRecognizer(), snapshot), "S02")

    def test_live_s02_filter_entry_xml_without_sell_is_recognized_as_s02_not_s10(self):
        module = load_script_module()
        snapshot = make_s02_snapshot_from_live_xml(module)
        recognizer = module.PageRecognizer(module.load_config("pages.yaml"))

        self.assertNotIn("卖车", set(snapshot["visible_texts"]))
        self.assertTrue(module._looks_like_s02_select_page(snapshot))
        self.assertFalse(module._looks_like_s10_ready_contract(snapshot))
        self.assertEqual(module._recognize_page(recognizer, snapshot), "S02_SELECT_CAR_TAB")
        brand_node = module._find_brand_filter_node(snapshot)
        self.assertIsNotNone(brand_node)
        self.assertIn("ftv_brand", brand_node["resource_id"])

    def test_guazi_home_with_empty_foreground_uses_xml_package_not_app_icon(self):
        module = load_script_module()

        class GuaziHomeRecognizer:
            def recognize(self, blob, candidate_ids=None, context=None):
                if candidate_ids and "S01" in candidate_ids and "首页" in blob and "选车" in blob:
                    return {"id": "S01"}
                return None

        visible_texts = ["唐山", "搜索", "卖车", "新能源", "首页", "选车", "我的", "唐山瓜子二手车直卖场"]
        snapshot = {
            "foreground_package": "",
            "xml_package": module.GUAZI_PACKAGE,
            "focused_window": "",
            "visible_blob": "".join(visible_texts),
            "visible_texts": visible_texts,
            "nodes": [],
            "fresh_xml": "<hierarchy package='com.ganji.android.haoche_c'>唐山瓜子二手车直卖场</hierarchy>",
            "xml_missing": False,
            "screenshot_missing": False,
        }

        self.assertEqual(module._effective_foreground_package(snapshot), module.GUAZI_PACKAGE)
        self.assertNotEqual(module._recognize_page(GuaziHomeRecognizer(), snapshot), "S_APP_ICON")
        self.assertEqual(module._recognize_page(GuaziHomeRecognizer(), snapshot), "S01")

    def test_launcher_guazi_icon_still_recognized_as_s_app_icon(self):
        module = load_script_module()
        snapshot = make_app_icon_snapshot(module, "real_launcher_icon")

        self.assertNotEqual(snapshot["xml_package"], module.GUAZI_PACKAGE)
        self.assertTrue(module._looks_like_launcher_surface(snapshot))
        self.assertEqual(module._recognize_page(NoPageRecognizer(), snapshot), "S_APP_ICON")

    def test_s02_filter_entry_without_sell_and_with_search_residue_is_recognized(self):
        module = load_script_module()
        snapshot = make_s02_filter_entry_snapshot_without_sell(module, search_text="理想汽车理想L7")

        self.assertNotIn("卖车", set(snapshot["visible_texts"]))
        self.assertTrue(module._looks_like_s02_select_page(snapshot))
        self.assertFalse(module._looks_like_s10_ready_contract(snapshot))
        self.assertEqual(module._recognize_page(NoPageRecognizer(), snapshot), "S02_SELECT_CAR_TAB")

    def test_bottom_main_nav_cannot_be_s10_ready(self):
        module = load_script_module()
        snapshot = make_s10_snapshot(module)
        snapshot["visible_blob"] += "首页选车卖车新能源我的"
        snapshot["visible_texts"].extend(["首页", "选车", "卖车", "新能源", "我的"])
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._assert_s10_ready_contract(
                DummyIssues(),
                snapshot,
                source="S09_PRICE_LOW_TO_HIGH",
                flow_state={"S07_FILTER_DONE": True, "COLOR_FILTER_DONE": True, "AGE_FILTER_DONE": True, "SORT_DONE": True},
            )
        self.assertEqual(raised.exception.code, "PAGE_CONTRACT_MISMATCH")

    def test_s08_before_sort_cannot_be_s10_ready(self):
        module = load_script_module()

        class GreedyRecognizer:
            def recognize(self, blob, candidate_ids=None, context=None):
                if candidate_ids and "S10" in candidate_ids:
                    return {"id": "S10"}
                if "S08_OK" in blob and candidate_ids and "S08" in candidate_ids:
                    return {"id": "S08"}
                return None

        snapshot = make_s10_snapshot(module)
        snapshot["visible_blob"] = "S08_OK 综合排序12.80万2020年 | 6.20万公里"
        snapshot["visible_texts"] = ["S08_OK", "综合排序", "12.80万", "2020年 | 6.20万公里"]
        self.assertFalse(module._looks_like_s10_ready_contract(snapshot))
        self.assertEqual(module._recognize_page(GreedyRecognizer(), snapshot, {"S07_FILTER_DONE": True}), "S08")

    def test_s10_ready_requires_low_to_high_sort_when_not_single_branch(self):
        module = load_script_module()
        snapshot = make_s10_snapshot(module)
        snapshot["visible_blob"] = "12.80万2020年 | 6.20万公里大众帕萨特"
        snapshot["visible_texts"] = ["12.80万", "2020年 | 6.20万公里", "大众帕萨特"]
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._assert_s10_ready_contract(
                DummyIssues(),
                snapshot,
                source="S09_PRICE_LOW_TO_HIGH",
                flow_state={"S07_FILTER_DONE": True, "COLOR_FILTER_DONE": True, "AGE_FILTER_DONE": True, "SORT_DONE": True},
            )
        self.assertEqual(raised.exception.code, "PAGE_CONTRACT_MISMATCH")

    def test_s09_low_to_high_then_fresh_s10_allows_s10_ready(self):
        module = load_script_module()
        cards = module._assert_s10_ready_contract(
            DummyIssues(),
            make_s10_snapshot(module),
            source="S09_PRICE_LOW_TO_HIGH",
            flow_state={"S07_FILTER_DONE": True, "COLOR_FILTER_DONE": True, "AGE_FILTER_DONE": True, "SORT_DONE": True},
        )
        self.assertEqual(cards[0]["list_price_10k"], 12.8)
        self.assertEqual(cards[0]["list_year"], 2020)
        self.assertEqual(cards[0]["list_mileage_10k_km"], 6.2)

    def test_s10_ready_uses_current_fresh_evidence(self):
        module = load_script_module()
        current_snapshot = make_s01_snapshot(module)
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._assert_s10_ready_contract(DummyIssues(), current_snapshot, source="S09_PRICE_LOW_TO_HIGH")
        self.assertEqual(raised.exception.code, "S10_READY_BLOCKED_BEFORE_FILTER_OR_SORT_DONE")

    def test_s10_ready_does_not_require_same_text_for_price_year_mileage(self):
        module = load_script_module()
        snapshot = make_s10_snapshot(module)
        snapshot["visible_texts"] = ["价格从低到高", "12.80万", "大众帕萨特", "2020年 | 6.20万公里"]
        cards = module._assert_s10_ready_contract(
            DummyIssues(),
            snapshot,
            source="S09_PRICE_LOW_TO_HIGH",
            flow_state={"S07_FILTER_DONE": True, "COLOR_FILTER_DONE": True, "AGE_FILTER_DONE": True, "SORT_DONE": True},
        )
        self.assertEqual(cards[0]["list_price_10k"], 12.8)

    def test_action_requires_current_page_contract(self):
        module = load_script_module()
        context = {"recognizer": S01S02GreedyRecognizer(), "issues": DummyIssues()}
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._ensure_current_page_contract(context, make_s02_snapshot(module), {"S08"}, action_page="S08")
        self.assertEqual(raised.exception.code, "PAGE_CONTRACT_MISMATCH")

    def test_expected_state_cannot_override_current_page_contract(self):
        module = load_script_module()
        context = {"recognizer": S01S02GreedyRecognizer(), "issues": DummyIssues()}
        self.assertEqual(module._current_state_or_stop(context, make_s02_snapshot(module)), "S02_SELECT_CAR_TAB")

    def test_s01_page_can_only_execute_s01_action(self):
        module = load_script_module()
        context = {"recognizer": S01S02GreedyRecognizer(), "issues": DummyIssues()}
        snapshot = make_s01_snapshot(module)
        self.assertEqual(module._ensure_current_page_contract(context, snapshot, {"S01"}, action_page="S01"), "S01")
        with self.assertRaises(module.GuaziFlowError):
            module._ensure_current_page_contract(context, snapshot, {"S08", "S09"}, action_page="S08")

    def test_s02_page_can_only_execute_s02_action(self):
        module = load_script_module()
        context = {"recognizer": S01S02GreedyRecognizer(), "issues": DummyIssues()}
        snapshot = make_s02_snapshot(module)
        self.assertEqual(
            module._ensure_current_page_contract(context, snapshot, {"S02", "S02_SELECT_CAR_TAB"}, action_page="S02"),
            "S02_SELECT_CAR_TAB",
        )
        with self.assertRaises(module.GuaziFlowError):
            module._ensure_current_page_contract(context, snapshot, {"S08", "S09"}, action_page="S08")

    def test_handle_s02_clicks_brand_node_by_resource_id(self):
        module = load_script_module()
        snapshot = make_s02_filter_entry_snapshot_without_sell(module)
        client = S03FakeClient()
        context = {
            "recognizer": S02ToS03Recognizer(),
            "issues": DummyIssues(),
            "client": client,
            "machine": S02Machine(),
            "timing": module.TimingRecorder(),
            "flow_state": {},
        }

        with mock.patch.object(module.time, "sleep", return_value=None), mock.patch.object(
            module, "_capture", return_value=make_s03_brand_panel_snapshot(module)
        ):
            next_state, _next_snapshot = module.handle_s02(context, snapshot)

        self.assertEqual(next_state, "S03")
        self.assertEqual(client.taps, [(405, 461)])
        self.assertEqual(context["machine"].actions, [("S02", "tap_brand_filter")])

    def test_handle_s02_missing_brand_node_raises_specific_error(self):
        module = load_script_module()
        snapshot = make_s02_filter_entry_snapshot_without_sell(module)
        snapshot["nodes"] = [node for node in snapshot["nodes"] if "品牌" not in node.get("labels", [])]
        context = {
            "recognizer": S02ToS03Recognizer(),
            "issues": DummyIssues(),
            "client": S03FakeClient(),
            "machine": S02Machine(),
            "timing": module.TimingRecorder(),
            "flow_state": {},
        }

        with self.assertRaises(module.GuaziFlowError) as raised:
            module.handle_s02(context, snapshot)

        self.assertEqual(raised.exception.code, "BRAND_FILTER_NOT_FOUND")

    def test_handle_s02_brand_panel_not_opened_raises_specific_error(self):
        module = load_script_module()
        snapshot = make_s02_filter_entry_snapshot_without_sell(module)
        client = S03FakeClient()
        context = {
            "recognizer": S02ToS03Recognizer(),
            "issues": DummyIssues(),
            "client": client,
            "machine": S02Machine(),
            "timing": module.TimingRecorder(),
            "flow_state": {},
        }

        with mock.patch.object(module.time, "sleep", return_value=None), mock.patch.object(module, "_capture", return_value=snapshot):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s02(context, snapshot)

        self.assertEqual(raised.exception.code, "BRAND_FILTER_PANEL_NOT_OPENED")
        self.assertEqual(client.taps, [(405, 461)])

    def test_s08_action_requires_current_s08_contract(self):
        module = load_script_module()
        context = {"recognizer": S01S02GreedyRecognizer(), "issues": DummyIssues()}
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._ensure_current_page_contract(context, make_s02_snapshot(module), {"S08"}, action_page="S08")
        self.assertEqual(raised.exception.context["actual_state"], "S02_SELECT_CAR_TAB")

    def test_s09_action_requires_current_s09_contract(self):
        module = load_script_module()
        context = {"recognizer": S01S02GreedyRecognizer(), "issues": DummyIssues()}
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._ensure_current_page_contract(context, make_s02_snapshot(module), {"S09"}, action_page="S09")
        self.assertEqual(raised.exception.context["actual_state"], "S02_SELECT_CAR_TAB")

    def test_s10_ready_requires_current_s10_contract(self):
        module = load_script_module()
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._assert_s10_ready_contract(DummyIssues(), make_s02_snapshot(module), source="S09_PRICE_LOW_TO_HIGH")
        self.assertEqual(raised.exception.code, "S10_READY_BLOCKED_BEFORE_FILTER_OR_SORT_DONE")

    def test_s03_brand_honda_uses_letter_b(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s03_context(module, client)
        context["task_params"] = {"brand": "本田", "brand_initial": "B"}
        target_path = ROOT / "output" / "tmp_test" / "s03_target_brand.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps({"brand": "本田"}, ensure_ascii=False), encoding="utf-8")
        initial = make_s03_snapshot(module, ["A", "B", "D", "大众"], marker="s03_initial")
        after_b = make_s03_snapshot(module, ["B", "本田\n本地12714辆在售"], marker="s03_after_b")
        after_tap = make_s03_snapshot(module, ["车系"], marker="s04_after_brand")

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
            mock.patch.object(module, "_capture", side_effect=[after_b, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, _snapshot = module.handle_s03(context, initial)

        self.assertEqual(state, "S04")
        self.assertEqual(client.taps[0], module._center(initial["nodes"][1]["bounds"]))
        self.assertLess(client.taps[1][0], after_b["nodes"][1]["bounds"][0])
        self.assertEqual(client.taps[1][1], module._center(after_b["nodes"][1]["bounds"])[1])

    def test_s03_brand_initials_use_generic_routing_table(self):
        module = load_script_module()

        cases = {
            "欧拉": "O",
            "长城欧拉": "O",
            "ORA": "O",
            "雪佛兰": "X",
            "本田": "B",
            "比亚迪": "B",
            "零跑": "L",
            "大众": "D",
        }
        for brand, expected in cases.items():
            with self.subTest(brand=brand):
                aliases = module.get_target_brand_aliases(brand)
                self.assertEqual(module.derive_brand_initial(brand, aliases), expected)

        self.assertEqual(module.get_target_brand_aliases("欧拉"), ["欧拉", "欧拉 ORA", "长城欧拉", "ORA"])

    def test_s03_brand_ora_uses_letter_o_and_alias(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s03_context(module, client)
        target_path = ROOT / "output" / "tmp_test" / "s03_target_ora.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps({"brand": "欧拉"}, ensure_ascii=False), encoding="utf-8")
        initial = make_s03_snapshot(module, ["A", "O", "奥迪"], marker="s03_initial_ora")
        after_o = make_s03_snapshot(module, ["O", "欧拉 ORA\n本地12辆在售"], marker="s03_after_o")
        after_tap = make_s03_snapshot(module, ["车系"], marker="s04_after_ora")

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
            mock.patch.object(module, "_capture", side_effect=[after_o, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, _snapshot = module.handle_s03(context, initial)

        self.assertEqual(state, "S04")
        self.assertEqual(client.taps[0], module._center(initial["nodes"][1]["bounds"]))
        self.assertEqual(context["s03_brand_search_v2"]["clicked_initial_letter"], "O")
        self.assertEqual(context["s03_brand_search_v2"]["matched_alias"], "欧拉 ORA")

    def test_s03_unknown_brand_initial_falls_back_to_scroll_scan(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s03_context(module, client)
        target_path = ROOT / "output" / "tmp_test" / "s03_unknown_brand.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps({"brand": "未知牌"}, ensure_ascii=False), encoding="utf-8")
        initial = make_s03_snapshot(module, ["A", "奥迪"], marker="s03_unknown_initial")
        after_scroll = make_s03_snapshot(module, ["未知牌\n本地1辆在售"], marker="s03_unknown_after_scroll")
        after_tap = make_s03_snapshot(module, ["车系"], marker="s04_after_unknown")

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
            mock.patch.object(module, "_capture", side_effect=[after_scroll, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, _snapshot = module.handle_s03(context, initial)

        self.assertEqual(state, "S04")
        self.assertTrue(context["s03_brand_search_v2"]["attempted_fallback_brand_scan"])
        self.assertEqual(context["s03_brand_search_v2"]["initial_derivation_error_code"], "S03_TARGET_INITIAL_LETTER_NOT_DERIVABLE")
        self.assertEqual(client.swipes, ["up"])

    def test_s03_brand_search_scrolls_within_s03_when_not_visible(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s03_context(module, client)
        target_path = ROOT / "output" / "tmp_test" / "s03_scroll_brand.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps({"brand": "本田"}, ensure_ascii=False), encoding="utf-8")
        initial = make_s03_snapshot(module, ["B", "比亚迪", "别克"], marker="s03_initial")
        after_b = make_s03_snapshot(module, ["B", "比亚迪", "别克"], marker="s03_after_b")
        after_scroll = make_s03_snapshot(module, ["B", "宝马", "奔驰"], marker="s03_after_scroll")
        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
            mock.patch.object(module, "_capture", side_effect=[after_b, after_scroll, after_scroll]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s03(context, initial)

        self.assertEqual(raised.exception.code, "S03_TARGET_BRAND_NOT_FOUND")
        self.assertEqual(client.swipes, ["up", "up"])

    def test_s03_brand_not_found_does_not_restart_app(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s03_context(module, client)
        target_path = ROOT / "output" / "tmp_test" / "s03_not_found_no_restart.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps({"brand": "本田"}, ensure_ascii=False), encoding="utf-8")
        initial = make_s03_snapshot(module, ["B", "比亚迪", "别克"], marker="s03_initial")
        after_b = make_s03_snapshot(module, ["B", "比亚迪", "别克"], marker="s03_after_b")
        after_scroll = make_s03_snapshot(module, ["B", "宝马", "奔驰"], marker="s03_after_scroll")

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
            mock.patch.object(module, "_capture", side_effect=[after_b, after_scroll, after_scroll]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError):
                module.handle_s03(context, initial)

        self.assertEqual(client.home_calls, 0)
        self.assertEqual(client.icon_taps, 0)

    def test_s03_brand_exact_match_honda_only(self):
        module = load_script_module()
        similar = make_s03_snapshot(module, ["本田技研", "本田新能源"], marker="s03_similar")
        exact = make_s03_snapshot(module, ["本田\n本地12714辆在售"], marker="s03_exact")
        self.assertIsNone(module._find_s03_target_brand(similar, "本田"))
        self.assertIsNotNone(module._find_s03_target_brand(exact, "本田"))

    def test_s03_brand_not_found_reports_target_brand_not_found(self):
        module = load_script_module()
        context = make_s03_context(module)
        target_path = ROOT / "output" / "tmp_test" / "s03_not_found_code.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps({"brand": "本田"}, ensure_ascii=False), encoding="utf-8")
        initial = make_s03_snapshot(module, ["B", "比亚迪", "别克"], marker="s03_initial")
        after_b = make_s03_snapshot(module, ["B", "比亚迪", "别克"], marker="s03_after_b")
        after_scroll = make_s03_snapshot(module, ["B", "宝马", "奔驰"], marker="s03_after_scroll")

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
            mock.patch.object(module, "_capture", side_effect=[after_b, after_scroll, after_scroll]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s03(context, initial)

        self.assertEqual(raised.exception.code, "S03_TARGET_BRAND_NOT_FOUND")

    def test_s03_target_brand_click_failure_is_specific(self):
        module = load_script_module()

        class FailingTapClient(S03FakeClient):
            def tap(self, x: int, y: int):
                self.taps.append((x, y))
                raise RuntimeError("tap failed")

        client = FailingTapClient()
        context = make_s03_context(module, client)
        target_path = ROOT / "output" / "tmp_test" / "s03_click_fail.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps({"brand": "本田"}, ensure_ascii=False), encoding="utf-8")
        initial = make_s03_snapshot(module, ["本田\n本地12714辆在售"], marker="s03_visible_honda")

        with mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s03(context, initial)

        self.assertEqual(raised.exception.code, "S03_TARGET_BRAND_CLICK_FAILED")

    def test_s04_series_not_visible_scrolls_within_s04(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁", "思域", "飞度"], marker="s04_initial")
        after_scroll = make_s04_snapshot(module, ["缤智"], marker="s04_after_scroll")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_scroll, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, _snapshot = module.handle_s04(context, initial)

        self.assertEqual(state, "S05")
        self.assertEqual(client.swipes, ["up"])

    def test_s04_series_found_after_scroll_taps_model_button(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁", "思域", "飞度"], marker="s04_initial")
        after_scroll = make_s04_snapshot(module, ["缤智"], marker="s04_after_scroll")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_scroll, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(client.taps[-1], module._center(after_scroll["nodes"][4]["bounds"]))

    def test_s04_series_not_found_at_end_reports_target_series_not_found(self):
        module = load_script_module()
        context = make_s04_context(module)
        initial = make_s04_snapshot(module, ["雅阁", "思域"], marker="s04_initial")
        unchanged = make_s04_snapshot(module, ["雅阁", "思域"], marker="s04_unchanged")

        with (
            mock.patch.object(module, "_capture", side_effect=[unchanged, unchanged]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s04(context, initial)

        self.assertEqual(raised.exception.code, "TARGET_SERIES_NOT_FOUND_IN_S04")

    def test_s04_series_not_visible_does_not_restart_app(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_initial")
        unchanged = make_s04_snapshot(module, ["雅阁"], marker="s04_unchanged")

        with (
            mock.patch.object(module, "_capture", side_effect=[unchanged, unchanged]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError):
                module.handle_s04(context, initial)

        self.assertEqual(client.home_calls, 0)
        self.assertEqual(client.icon_taps, 0)

    def test_s04_series_not_visible_does_not_trigger_learning_loop_first(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁"], marker="s04_top")
        after_scroll = make_s04_snapshot(module, ["缤智"], marker="s04_after_scroll")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_scroll, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(context["issues"].records, [])
        self.assertEqual(client.swipes, ["up"])

    def test_s04_search_normalizes_to_top_before_downward_scan(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["奥德赛"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁"], marker="s04_top")
        target = make_s04_snapshot(module, ["缤智"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[target, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(client.swipes, ["up"])
        self.assertNotIn("s04_search_top_reached_before_downward", context)

    def test_s04_search_downward_from_top_finds_target(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["奥德赛"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁"], marker="s04_top")
        target = make_s04_snapshot(module, ["缤智"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[target, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, _snapshot = module.handle_s04(context, initial)

        self.assertEqual(state, "S05")
        self.assertEqual(client.taps[-1], module._center(target["nodes"][4]["bounds"]))

    def test_s04_search_does_not_start_from_middle_and_report_not_found(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["奥德赛"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁"], marker="s04_top")
        target = make_s04_snapshot(module, ["缤智"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[target, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(context["issues"].records, [])
        self.assertIn(["奥德赛"], context["s04_visible_series_history"])
        self.assertIn(["缤智"], context["s04_visible_series_history"])

    def test_s04_visible_series_names_extracted_each_screen(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["奥德赛"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁", "思域"], marker="s04_top")
        target = make_s04_snapshot(module, ["缤智"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[target, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(
            context["s04_visible_series_history"],
            [["奥德赛"], ["缤智"]],
        )

    def test_s04_xml_missing_visible_series_stops_for_learning(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["奥德赛"], marker="s04_initial")
        empty = make_s04_snapshot(module, [], marker="s04_empty")

        with (
            mock.patch.object(module, "_capture", side_effect=[empty]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s04(context, initial)

        self.assertEqual(raised.exception.code, "XML_TEXT_MISSING_FOR_VISIBLE_SERIES")

    def test_s04_single_direction_search_only(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_initial")
        unchanged = make_s04_snapshot(module, ["雅阁"], marker="s04_unchanged")

        with (
            mock.patch.object(module, "_capture", side_effect=[unchanged, unchanged, unchanged, unchanged]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError):
                module.handle_s04(context, initial)

        self.assertEqual(client.swipes, ["up", "up"])
        self.assertNotIn("down", client.swipes)

    def test_s04_visible_series_extraction_detects_target(self):
        module = load_script_module()
        snapshot = make_s04_snapshot(module, ["缤智", "XR-V", "冠道", "UR-V"], marker="s04_target_visible")

        self.assertIn("缤智", module._s04_visible_series_names(snapshot))

    def test_s04_visible_target_clicks_same_row_model_button(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["XR-V", "缤智", "冠道"], marker="s04_initial")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, _snapshot = module.handle_s04(context, initial)

        self.assertEqual(state, "S05")
        self.assertEqual(client.swipes, [])
        self.assertEqual(client.taps[-1], module._center(initial["nodes"][6]["bounds"]))

    def test_s04_does_not_click_other_series_model_button(self):
        module = load_script_module()
        snapshot = make_s04_snapshot(module, ["XR-V", "缤智", "冠道", "UR-V", "CR-V", "皓影"], marker="s04_many")

        button = module._find_series_model_button(snapshot, "缤智")

        self.assertIsNotNone(button)
        self.assertEqual(button["bounds"], snapshot["nodes"][6]["bounds"])
        self.assertNotEqual(button["bounds"], snapshot["nodes"][4]["bounds"])
        self.assertNotEqual(button["bounds"], snapshot["nodes"][8]["bounds"])

    def test_s04_not_found_only_after_downward_no_change(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁"], marker="s04_top")
        after_scroll = make_s04_snapshot(module, ["思域"], marker="s04_after_scroll")
        unchanged = make_s04_snapshot(module, ["思域"], marker="s04_unchanged")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_scroll, unchanged, unchanged]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s04(context, initial)

        self.assertEqual(raised.exception.code, "TARGET_SERIES_NOT_FOUND_IN_S04")
        self.assertEqual(client.swipes, ["up", "up", "up"])
        self.assertEqual(context["s04_visible_series_history"], [["雅阁"], ["思域"], ["思域"], ["思域"]])

    def test_s04_scrolls_down_within_page_when_target_not_visible(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁", "思域"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁", "思域"], marker="s04_top")
        after_scroll = make_s04_snapshot(module, ["缤智"], marker="s04_after_scroll")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_scroll, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(client.swipes, ["up"])
        self.assertEqual(client.home_calls, 0)
        self.assertEqual(client.icon_taps, 0)

    def test_s04_extracts_visible_series_after_each_scroll(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁", "思域", "飞度"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁", "思域", "飞度"], marker="s04_top")
        after_scroll = make_s04_snapshot(module, ["奥德赛", "缤智"], marker="s04_after_scroll")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        self.assertEqual(module._s04_visible_series_names(initial), ["雅阁", "思域", "飞度"])

        with (
            mock.patch.object(module, "_capture", side_effect=[after_scroll, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(
            context["s04_visible_series_history"],
            [["雅阁", "思域", "飞度"], ["奥德赛", "缤智"]],
        )

    def test_s04_series_not_visible_does_not_trigger_learning_loop_first(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_initial")
        top = make_s04_snapshot(module, ["雅阁"], marker="s04_top")
        after_scroll = make_s04_snapshot(module, ["缤智"], marker="s04_after_scroll")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_scroll, after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(context["issues"].records, [])
        self.assertEqual(client.swipes, ["up"])

    def test_s04_raw_xml_contains_target_blocks_scroll(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["缤智"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(client.swipes, [])
        self.assertEqual(context["s04_search_records"][0]["raw_xml_contains_target"], True)
        self.assertEqual(context["s04_search_records"][0]["action_taken"], "tap_target_model_button")

    def test_s04_visible_series_contains_target_blocks_scroll(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["XR-V", "缤智", "冠道"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(client.swipes, [])
        self.assertEqual(context["s04_search_records"][0]["target_in_visible_series"], True)

    def test_s04_raw_xml_target_missing_from_visible_series_reports_extraction_failed(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_xml_has_target")
        initial["fresh_xml"] = "<s04>缤智</s04>"

        with self.assertRaises(module.GuaziFlowError) as raised:
            module.handle_s04(context, initial)

        self.assertEqual(raised.exception.code, "S04_VISIBLE_TARGET_EXTRACTION_FAILED")
        self.assertEqual(client.swipes, [])

    def test_s04_visible_target_button_missing_reports_binding_failed(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["缤智"], marker="s04_target_without_button")
        initial["nodes"] = [node for node in initial["nodes"] if "车型" not in node.get("labels", [])]

        with self.assertRaises(module.GuaziFlowError) as raised:
            module.handle_s04(context, initial)

        self.assertEqual(raised.exception.code, "S04_TARGET_MODEL_BUTTON_BINDING_FAILED")
        self.assertEqual(client.swipes, [])

    def test_s04_target_seen_cannot_report_not_found(self):
        module = load_script_module()
        context = make_s04_context(module)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_xml_has_target")
        initial["fresh_xml"] = "<s04>缤智</s04>"

        with self.assertRaises(module.GuaziFlowError) as raised:
            module.handle_s04(context, initial)

        self.assertNotEqual(raised.exception.code, "TARGET_SERIES_NOT_FOUND_IN_S04")

    def test_s04_forbids_upward_or_bidirectional_search(self):
        source = read_text(SCRIPT_PATH)

        for forbidden in (
            "s04_series_top",
            "top_reached",
            "up_scan",
            "back_up_scan",
            "bidirectional",
            "reverse_scan",
            "scroll_back_to_top",
            "向上回查",
            "双向",
        ):
            self.assertNotIn(forbidden, source)

    def test_s04_not_found_only_when_target_never_seen_and_list_no_longer_changes(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_initial")
        unchanged = make_s04_snapshot(module, ["雅阁"], marker="s04_unchanged")

        with (
            mock.patch.object(module, "_capture", side_effect=[unchanged, unchanged]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s04(context, initial)

        self.assertEqual(raised.exception.code, "TARGET_SERIES_NOT_FOUND_IN_S04")
        self.assertTrue(all(not item["raw_xml_contains_target"] for item in context["s04_search_records"]))

    def test_s04_allows_scroll_series_list(self):
        module = load_script_module()
        pages = make_minimal_pages_config()

        module._enable_s04_scroll_series_list_action(pages)
        machine = module.PageStateMachine(pages)

        machine.assert_action_allowed("S04", "scroll_series_list")

    def test_scroll_series_list_only_allowed_in_s04(self):
        module = load_script_module()
        pages = make_minimal_pages_config()

        module._enable_s04_scroll_series_list_action(pages)
        machine = module.PageStateMachine(pages)

        with self.assertRaises(module.GuaziFlowError):
            machine.assert_action_allowed("S01", "scroll_series_list")
        with self.assertRaises(module.GuaziFlowError):
            machine.assert_action_allowed("S03", "scroll_series_list")

    def test_s04_scroll_series_list_requires_visible_series_checked_first(self):
        source = read_text(SCRIPT_PATH)
        handle_s04 = source.split("def handle_s04(", 1)[1].split("def handle_s05(", 1)[0]

        self.assertLess(
            handle_s04.index("visible_series_names = _s04_visible_series_names"),
            handle_s04.index('machine.assert_action_allowed("S04", "scroll_series_list")'),
        )

    def test_s04_scroll_series_list_blocked_when_target_in_raw_xml(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["雅阁"], marker="s04_xml_has_target")
        initial["fresh_xml"] = "<s04>缤智</s04>"

        with self.assertRaises(module.GuaziFlowError) as raised:
            module.handle_s04(context, initial)

        self.assertEqual(raised.exception.code, "S04_VISIBLE_TARGET_EXTRACTION_FAILED")
        self.assertEqual(client.swipes, [])

    def test_s04_scroll_series_list_blocked_when_target_in_visible_series(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s04_context(module, client)
        initial = make_s04_snapshot(module, ["缤智"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_tap]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s04(context, initial)

        self.assertEqual(client.swipes, [])
        self.assertEqual(context["s04_search_records"][0]["action_taken"], "tap_target_model_button")

    def test_s05_select_year_trim_runs_once(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=True,
            selected_trim=True,
        )
        still_s05 = make_s05_snapshot(module, "s05_still", "S05_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim, still_s05]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_CONFIRM_NO_EFFECT")
        self.assertEqual(len(client.taps), 3)

        with self.assertRaises(module.GuaziFlowError) as raised_again:
            module.handle_s05(context, initial)

        self.assertEqual(raised_again.exception.code, "S05_NO_PROGRESS_AFTER_CONFIRM")
        self.assertEqual(len(client.taps), 3)

    def test_s05_confirm_then_s06_continues(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=True,
            selected_trim=True,
        )
        s06 = make_s05_snapshot(module, "s06", "S06_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim, s06]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, snapshot = module.handle_s05(context, initial)

        self.assertEqual(state, "S06")
        self.assertIs(snapshot, s06)
        self.assertEqual(len(client.taps), 3)

    def test_s05_no_progress_after_confirm_stops(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=True,
            selected_trim=True,
        )
        still_s05 = make_s05_snapshot(module, "s05_still", "S05_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim, still_s05]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_CONFIRM_NO_EFFECT")
        self.assertEqual(context["s05_after_confirm_state"], "S05")

    def test_s05_does_not_loop_on_same_page(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=True,
            selected_trim=True,
        )
        still_s05 = make_s05_snapshot(module, "s05_still", "S05_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim, still_s05]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError):
                module.handle_s05(context, initial)

        taps_after_first_attempt = list(client.taps)
        with self.assertRaises(module.GuaziFlowError):
            module.handle_s05(context, initial)

        self.assertEqual(client.taps, taps_after_first_attempt)

    def test_s05_xml_dump_failure_not_used_as_loop_reason(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=True,
            selected_trim=True,
        )
        still_s05_missing_xml = make_s05_snapshot(module, "s05_missing_xml", "S05_OK", xml_missing=True)

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim, still_s05_missing_xml]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_CONFIRM_NO_EFFECT")

    def test_s05_requires_click_target_year_first(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=True,
            selected_trim=True,
        )
        s06 = make_s05_snapshot(module, "s06", "S06_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim, s06]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s05(context, initial)

        self.assertEqual(context["machine"].actions[0], ("S05", "tap_target_year"))
        self.assertLess(client.taps[0][0], 330)

    def test_s05_year_click_must_change_right_list(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year_no_effect = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year_no_effect] * 10),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_TARGET_CONFIG_NOT_FOUND")
        self.assertEqual(len(client.taps), 1)
        self.assertGreaterEqual(client.swipes.count("adb_input_swipe"), 1)

    def test_s05_misrecognized_year_selected_still_clicks_target_year_when_right_list_not_switched(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_YEAR_SELECTED_OK")
        after_year_no_effect = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year_no_effect] * 10),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_TARGET_CONFIG_NOT_FOUND")
        self.assertEqual(context["machine"].actions[0], ("S05", "tap_target_year"))
        self.assertEqual(client.taps[0], module._center(initial["nodes"][1]["bounds"]))
        self.assertGreaterEqual(client.swipes.count("adb_input_swipe"), 1)

    def test_s05_finds_target_trim_after_year_selected(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=True,
            selected_trim=True,
        )
        s06 = make_s05_snapshot(module, "s06", "S06_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim, s06]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            module.handle_s05(context, initial)

        self.assertEqual(context["machine"].actions[1], ("S05_MODEL_YEAR_SELECTED", "tap_exact_trim"))
        self.assertGreater(client.taps[1][0], 290)

    def test_s05_trim_match_strips_brand_series_prefix_on_target_series_page(self):
        module = load_script_module()
        prefix_terms = module._s05_target_trim_prefix_terms_from_params({"brand": "欧拉", "series": "黑猫"})

        self.assertTrue(
            module._s05_trim_label_matches_target(
                "2019款 351km 亲子版",
                "2019款",
                "欧拉黑猫 351km 亲子版",
                prefix_terms=prefix_terms,
            )
        )
        self.assertTrue(
            module._s05_trim_label_matches_target(
                "2019款 351km 亲子版",
                "2019款",
                "2019款 欧拉黑猫 351km 亲子版",
                prefix_terms=prefix_terms,
            )
        )
        self.assertTrue(
            module._s05_trim_label_matches_target(
                "2019款 351km 亲子版",
                "2019款",
                "351km 亲子版",
                prefix_terms=prefix_terms,
            )
        )

    def test_s05_trim_match_keeps_strict_config_boundary_after_prefix_strip(self):
        module = load_script_module()
        prefix_terms = module._s05_target_trim_prefix_terms_from_params({"brand": "欧拉", "series": "黑猫"})
        false_page_trims = [
            "351km 女神版",
            "351km 灵智版",
            "351km 灵睿版",
            "351km 灵趣版",
            "301km 标准版",
            "310km 灵动版",
            "405km 公务版",
        ]

        for page_trim in false_page_trims:
            with self.subTest(page_trim=page_trim):
                self.assertFalse(
                    module._s05_trim_label_matches_target(
                        f"2019款 {page_trim}",
                        "2019款",
                        "欧拉黑猫 351km 亲子版",
                        prefix_terms=prefix_terms,
                    )
                )

    def test_s05_field_xml_matches_visible_ora_black_cat_parent_child_trim(self):
        module = load_script_module()
        xml_path = ROOT / "artifacts" / "debug" / "s05_after_year_20260622_132040.xml"
        self.assertTrue(xml_path.exists(), xml_path)
        prefix_terms = module._s05_target_trim_prefix_terms_from_params({"brand": "欧拉", "series": "黑猫"})
        snapshot = {
            "fresh_xml": xml_path.read_text(encoding="utf-8"),
            "nodes": [
                {"labels": ["2019款"], "bounds": [0, 721, 248, 831]},
                {"labels": ["2019款 351km 亲子版"], "bounds": [248, 1520, 1080, 1638]},
            ],
        }

        node = module._s05_find_target_trim_node_from_xml(
            snapshot,
            "2019款",
            "欧拉黑猫 351km 亲子版",
            prefix_terms,
        )

        self.assertIsNotNone(node)
        self.assertEqual(node["matched_trim_text"], "2019款 351km 亲子版")
        self.assertEqual(node["content_desc"], "2019款 351km 亲子版")
        self.assertTrue(node["clickable"])
        self.assertEqual(list(node["bounds"]), [248, 1520, 1080, 1638])

    def test_s05_trim_click_must_enable_confirm(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim_no_effect = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=0,
            confirm_clickable=False,
        )

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim_no_effect]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_SELECTED_COUNT_MISMATCH")
        self.assertEqual(len(client.taps), 2)

    def test_s05_confirm_only_after_selected_one(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK", right_years=["2018款"])
        after_trim_grey_confirm = make_s05_snapshot(
            module,
            "s05_after_trim",
            "S05_TRIM_SELECTED_OK",
            right_years=["2018款"],
            selected_count=1,
            confirm_clickable=False,
            selected_trim=True,
        )

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year, after_trim_grey_confirm]),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_TARGET_CONFIG_SELECTED_NOT_CONFIRMED")
        self.assertEqual(len(client.taps), 2)

    def test_s05_no_false_report_action_executed(self):
        module = load_script_module()
        client = S03FakeClient()
        context = make_s05_context(module, client)
        initial = make_s05_snapshot(module, "s05_initial", "S05_OK")
        after_year_no_effect = make_s05_snapshot(module, "s05_after_year", "S05_YEAR_SELECTED_OK")

        with (
            mock.patch.object(module, "_capture", side_effect=[after_year_no_effect] * 10),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module.handle_s05(context, initial)

        self.assertEqual(raised.exception.code, "S05_TARGET_CONFIG_NOT_FOUND")
        self.assertNotIn("s05_after_confirm_state", context)
        self.assertEqual(len(client.taps), 1)
        self.assertGreaterEqual(client.swipes.count("adb_input_swipe"), 1)

    def test_s01_to_s10_script_imports_write_json_or_defines_it(self):
        source = read_text(SCRIPT_PATH)
        self.assertTrue(
            "from guazi_app_data_system.output_writer import write_json" in source or "def write_json(" in source
        )

    def test_s01_to_s10_write_json_creates_non_empty_utf8_json(self):
        module = load_script_module()
        target_dir = ROOT / "output" / "tmp_test" / "s01_to_s10_write_json"
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_path = target_dir / "result.json"
        module.write_json(target_path, {"message": "测试", "status": "ok"})
        self.assertTrue(target_path.exists())
        self.assertGreater(target_path.stat().st_size, 0)
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["message"], "测试")

    def test_s01_to_s10_artifact_paths_use_repo_relative_dirs(self):
        module = load_script_module()
        artifact_path = ROOT / "artifacts" / "screenshots" / "sample.png"
        debug_path = ROOT / "artifacts" / "debug" / "sample.xml"
        self.assertEqual(module._repo_relative(artifact_path), "artifacts/screenshots/sample.png")
        self.assertEqual(module._repo_relative(debug_path), "artifacts/debug/sample.xml")

    def test_s01_to_s10_does_not_emit_garbled_chinese_path(self):
        module = load_script_module()
        artifact_path = ROOT / "artifacts" / "screenshots" / "sample.png"
        relative_path = module._repo_relative(artifact_path)
        self.assertFalse(relative_path.startswith("C:"))
        self.assertNotIn("瀹氫环", relative_path)

    def test_s01_to_s10_empty_xml_is_not_valid_contract_evidence(self):
        module = load_script_module()

        class FakeClient:
            def screenshot(self, screenshot_path):
                return FakeAdbResult(success=False, stderr="screencap_failed", returncode=1)

            def power_state(self):
                return {"wakefulness": "Awake", "interactive": True, "display_state": "ON"}

            def run(self, args, timeout=20):
                if args[:3] == ["shell", "dumpsys", "window"]:
                    return FakeAdbResult(stdout="")
                if args[:4] == ["shell", "dumpsys", "activity", "activities"]:
                    return FakeAdbResult(stdout="")
                if args[:4] == ["shell", "uiautomator", "dump", "/sdcard/window.xml"]:
                    return FakeAdbResult(stdout="")
                if args[:3] == ["exec-out", "cat", "/sdcard/window.xml"]:
                    return FakeAdbResult(stdout="")
                raise AssertionError(f"unexpected args: {args}")

        snapshot = module._capture(FakeClient(), "unit_test_empty_xml")
        self.assertTrue(snapshot["xml_missing"])
        self.assertIsNone(snapshot["xml_path"])
        self.assertTrue(snapshot["screenshot_missing"])
        self.assertIsNone(snapshot["screenshot_path"])
        self.assertEqual(snapshot["nodes"], [])
        self.assertEqual(snapshot["screenshot_error"], "screencap_failed")
        self.assertEqual(snapshot["xml_dump_error"], "xml_dump_empty")

    def test_s01_to_s10_capture_writes_non_empty_screenshot_or_xml(self):
        module = load_script_module()
        temp_root = ROOT / "output" / "tmp_test" / "s01_to_s10_capture_ok"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True, exist_ok=True)

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        xml_text = "<hierarchy><node text='sample' package='com.ganji.android.haoche_c' bounds='[0,0][10,10]'/></hierarchy>"

        class FakeClient:
            def screenshot(self, screenshot_path):
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                screenshot_path.write_bytes(png_bytes)
                return FakeAdbResult(success=True, stdout=str(screenshot_path))

            def power_state(self):
                return {"wakefulness": "Awake", "interactive": True, "display_state": "ON"}

            def run(self, args, timeout=20):
                if args[:3] == ["shell", "dumpsys", "window"]:
                    return FakeAdbResult(stdout="mCurrentFocus=Window{1 u0 com.ganji.android.haoche_c/com.guazi.h5.Html5NewContainerActivity}")
                if args[:4] == ["shell", "dumpsys", "activity", "activities"]:
                    return FakeAdbResult(stdout="topResumedActivity: com.ganji.android.haoche_c/.MainActivity")
                if args[:4] == ["shell", "uiautomator", "dump", "/sdcard/window.xml"]:
                    return FakeAdbResult(stdout="UI hierarchy dumped to: /sdcard/window.xml")
                if args[:3] == ["exec-out", "cat", "/sdcard/window.xml"]:
                    return FakeAdbResult(success=True, stdout=xml_text)
                raise AssertionError(f"unexpected args: {args}")

        with mock.patch.object(module, "ROOT", temp_root):
            snapshot = module._capture(FakeClient(), "unit_test_capture_ok")

        self.assertFalse(snapshot["screenshot_missing"])
        self.assertFalse(snapshot["xml_missing"])
        self.assertEqual(snapshot["screenshot_path"], "artifacts/screenshots/unit_test_capture_ok.png")
        self.assertEqual(snapshot["xml_path"], "artifacts/debug/unit_test_capture_ok.xml")
        self.assertGreater((temp_root / "artifacts" / "screenshots" / "unit_test_capture_ok.png").stat().st_size, 0)
        self.assertGreater((temp_root / "artifacts" / "debug" / "unit_test_capture_ok.xml").stat().st_size, 0)
        self.assertGreaterEqual(snapshot["capture_metrics"]["screenshot_ms"], 0)
        self.assertGreaterEqual(snapshot["capture_metrics"]["xml_ms"], 0)

    def test_s01_to_s10_executor_missing_not_used_after_script_started(self):
        source = read_text(SCRIPT_PATH)
        self.assertNotIn("PAGE_CONTRACT_EXECUTOR_MISSING_FOR_FULL_MAINLINE", source)
        self.assertIn("RUNTIME_FRESH_EVIDENCE_MISSING", source)
        self.assertIn("APP_FORCE_RESTART_NON_CONTRACT_PAGE", source)

    def test_runtime_missing_initial_xml_does_not_stop_before_recovery(self):
        module = load_script_module()
        runtime = make_runtime_stub()
        runtime["issues"] = DummyIssues()
        temp_root = ROOT / "output" / "tmp_test" / "runtime_order_ok"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        (temp_root / "output").mkdir(parents=True, exist_ok=True)
        target_path = temp_root / "data" / "current_target_task.json"
        write_valid_runtime_target_task(target_path)

        class DummyRecognizer:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyMachine:
            def __init__(self, *_args, **_kwargs):
                pass

        called = {"recover": False}

        def fake_recover(_context, **_kwargs):
            called["recover"] = True
            return {
                "xml_missing": False,
                "screenshot_missing": False,
                "visible_blob": "",
                "screenshot_path": "artifacts/screenshots/after.png",
                "xml_path": "artifacts/debug/after.xml",
            }

        with (
            mock.patch.object(module, "ensure_runtime_dirs", lambda: None),
            mock.patch.object(module, "AdbClient", lambda: object()),
            mock.patch.object(module, "PageRecognizer", DummyRecognizer),
            mock.patch.object(module, "PageStateMachine", DummyMachine),
            mock.patch.object(module, "validate_current_target_task", lambda: {"app_operation_params": {}}),
            mock.patch.object(module, "_run_first_stage_target_device_gate", lambda _context: {"passed": True}),
            mock.patch.object(module, "_recover_to_guazi_page", fake_recover),
            mock.patch.object(module, "_capture", side_effect=AssertionError("should not capture before recovery")),
            mock.patch.object(module, "_recognize_page", lambda *_args, **_kwargs: "S10"),
            mock.patch.object(module, "handle_s10_ready_check", lambda *_args, **_kwargs: {"status": "S10_READY"}),
            mock.patch.object(module, "project_path", lambda *parts: temp_root.joinpath(*parts)),
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
        ):
            result = module.run_s01_to_s10_mainline(runtime)

        self.assertTrue(called["recover"])
        self.assertEqual(result["status"], "S10_READY")
        self.assertEqual(runtime["issues"].records, [])

    def test_runtime_fresh_evidence_missing_only_after_recovery(self):
        module = load_script_module()
        runtime = make_runtime_stub()
        runtime["issues"] = DummyIssues()
        temp_root = ROOT / "output" / "tmp_test" / "runtime_missing_after_recovery"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        (temp_root / "output").mkdir(parents=True, exist_ok=True)
        target_path = temp_root / "data" / "current_target_task.json"
        write_valid_runtime_target_task(target_path)

        class DummyRecognizer:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyMachine:
            def __init__(self, *_args, **_kwargs):
                pass

        called = {"recover": False}

        def fake_recover(_context, **_kwargs):
            called["recover"] = True
            raise module.GuaziFlowError("RUNTIME_FRESH_EVIDENCE_MISSING", "missing after recovery", {"stage": "after_recovery"})

        with (
            mock.patch.object(module, "ensure_runtime_dirs", lambda: None),
            mock.patch.object(module, "AdbClient", lambda: object()),
            mock.patch.object(module, "PageRecognizer", DummyRecognizer),
            mock.patch.object(module, "PageStateMachine", DummyMachine),
            mock.patch.object(module, "validate_current_target_task", lambda: {"app_operation_params": {}}),
            mock.patch.object(module, "_run_first_stage_target_device_gate", lambda _context: {"passed": True}),
            mock.patch.object(module, "_recover_to_guazi_page", fake_recover),
            mock.patch.object(module, "_capture", side_effect=AssertionError("should not capture before recovery")),
            mock.patch.object(module, "project_path", lambda *parts: temp_root.joinpath(*parts)),
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
        ):
            result = module.run_s01_to_s10_mainline(runtime)

        self.assertTrue(called["recover"])
        self.assertEqual(result["status"], "RUNTIME_FRESH_EVIDENCE_MISSING")
        self.assertEqual(result["error"], "missing after recovery")

    def test_runtime_recovery_order_wake_swipe_icon_before_fresh_evidence_gate(self):
        source = read_text(SCRIPT_PATH)
        device_section = source.split("def _device_ready_gate_before_app_entry(", 1)[1].split("def _handle_launcher_account_dialog_until_closed(", 1)[0]
        recover_section = source.split("def _recover_to_guazi_page(", 1)[1].split("def _dismiss_initial_s_login(", 1)[0]
        self.assertLess(device_section.index("wake_screen_once"), device_section.index("wake_swipe_once"))
        self.assertLess(recover_section.index("home_key_once"), recover_section.index("tap_guazi_app_icon_exact_text"))
        self.assertLess(recover_section.index("tap_guazi_app_icon_exact_text"), recover_section.index("_ensure_runtime_fresh_evidence"))

    def test_fixed_recovery_swipe_parameters_are_updated(self):
        source = read_text(SCRIPT_PATH)
        section = source.split("def _device_ready_gate_before_app_entry(", 1)[1].split("def _handle_launcher_account_dialog_until_closed(", 1)[0]
        self.assertIn("wake_swipe_once(duration_ms=700)", section)
        self.assertIn("NON_SECURE_" "KEYGUARD_" "SWIPE_" "FAILED", section)
        self.assertNotIn('["shell", "input", "swipe"', section)

    def test_miui_newhome_ad_overlay_recovery_uses_home_not_ad_click(self):
        module = load_script_module()

        class FakeClient:
            def __init__(self):
                self.home_calls = 0
                self.taps = []

            def wake_screen_once(self):
                return {"wake_success": True}

            def wake_swipe_once(self, *args, **kwargs):
                return {"swipe_success": True}

            def home_key_once(self):
                self.home_calls += 1
                return {"home_success": True}

            def tap(self, x, y):
                self.taps.append((x, y))
                return FakeAdbResult(success=True)

        client = FakeClient()
        context = {"client": client, "issues": DummyIssues(), "timing": module.TimingRecorder(), "startup": {}}
        state = {"keyguard_showing": True, "keyguard_secure": False, "focused_window": "NotificationShade", "foreground_package": ""}
        overlay = make_miui_newhome_ad_snapshot(module)
        desktop = make_miui_desktop_snapshot(module, "miui_desktop_after_home")

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state]),
            mock.patch.object(module, "_capture", side_effect=[overlay, desktop]),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._device_ready_gate_before_app_entry(context, reason="APP_FORCE_RESTART")

        self.assertIs(result, desktop)
        self.assertEqual(client.home_calls, 1)
        self.assertEqual(client.taps, [])
        self.assertTrue(context["startup"]["miui_launcher_overlay_detected"])
        self.assertTrue(context["startup"]["miui_newhome_ad_close_detected"])
        self.assertTrue(context["startup"]["miui_launcher_overlay_did_not_click_download"])
        self.assertTrue(context["startup"]["unlock_gate_passed_by_launcher_visible_evidence"])
        self.assertEqual(context["issues"].records, [])

    def test_miui_desktop_visible_with_stale_keyguard_passes_without_home(self):
        module = load_script_module()

        class FakeClient:
            def __init__(self):
                self.home_calls = 0

            def wake_screen_once(self):
                return {"wake_success": True}

            def wake_swipe_once(self, *args, **kwargs):
                return {"swipe_success": True}

            def home_key_once(self):
                self.home_calls += 1
                return {"home_success": True}

        client = FakeClient()
        context = {"client": client, "issues": DummyIssues(), "timing": module.TimingRecorder(), "startup": {}}
        state = {"keyguard_showing": True, "keyguard_secure": False, "focused_window": "NotificationShade", "foreground_package": ""}
        desktop = make_miui_desktop_snapshot(module)

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state]),
            mock.patch.object(module, "_capture", return_value=desktop),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._device_ready_gate_before_app_entry(context, reason="APP_FORCE_RESTART")

        self.assertIs(result, desktop)
        self.assertEqual(client.home_calls, 0)
        self.assertTrue(context["startup"]["unlock_gate_passed_by_launcher_visible_evidence"])
        self.assertTrue(context["startup"]["keyguard_showing_stale_but_launcher_visible"])

    def test_guazi_s14_foreground_stale_keyguard_passes_for_forced_reopen_only(self):
        module = load_script_module()

        class FakeClient:
            def wake_screen_once(self):
                return {"wake_success": True}

            def wake_swipe_once(self, *args, **kwargs):
                return {"swipe_success": True}

            def home_key_once(self):
                raise AssertionError("old Guazi foreground should pass the gate without HOME recovery")

        context = {"client": FakeClient(), "issues": DummyIssues(), "timing": module.TimingRecorder(), "startup": {}}
        state = {
            "keyguard_showing": True,
            "keyguard_secure": False,
            "focused_window": f"{module.GUAZI_PACKAGE}/com.guazi.h5.Html5NewContainerActivity",
            "foreground_package": module.GUAZI_PACKAGE,
        }
        s14_old_page = make_s14_detail_snapshot(module)
        s14_old_page["keyguard_showing"] = True
        s14_old_page["keyguard_secure"] = False

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state]),
            mock.patch.object(module, "_capture", return_value=s14_old_page),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._device_ready_gate_before_app_entry(context, reason="APP_FORCE_RESTART")

        self.assertIs(result, s14_old_page)
        self.assertTrue(context["startup"]["guazi_foreground_visible_despite_keyguard"])
        self.assertTrue(context["startup"]["stale_keyguard_ignored_for_reopen"])
        self.assertTrue(context["startup"]["old_guazi_page_detected"])
        self.assertEqual(context["startup"]["old_guazi_page_type"], "S14_DETAIL_POPUP")
        self.assertEqual(
            context["startup"]["device_ready_pass_reason"],
            "GUAZI_FOREGROUND_XML_READABLE_WITH_NON_SECURE_KEYGUARD",
        )
        self.assertTrue(context["startup"]["must_reopen_guazi_app"])
        self.assertEqual(context["issues"].records, [])

    def test_guazi_s10_and_s01_stale_keyguard_are_operable_but_classified_as_old_pages(self):
        module = load_script_module()
        s10 = make_s10_snapshot(module)
        s01 = make_s01_snapshot(module)
        for snapshot, expected in ((s10, "S10_READY"), (s01, "S01_OR_S02")):
            snapshot["keyguard_showing"] = True
            snapshot["keyguard_secure"] = False
            self.assertTrue(module.is_guazi_foreground_operable_despite_stale_keyguard(snapshot))
            self.assertEqual(module._classify_old_guazi_page(snapshot), expected)

    def test_guazi_s01_foreground_stale_keyguard_accepts_home_without_force_reopen(self):
        module = load_script_module()

        class FakeClient:
            def wake_screen_once(self):
                return {"wake_success": True}

            def wake_swipe_once(self, *args, **kwargs):
                return {"swipe_success": True}

            def home_key_once(self):
                raise AssertionError("Guazi home already ready should not need HOME recovery")

        context = {"client": FakeClient(), "issues": DummyIssues(), "timing": module.TimingRecorder(), "startup": {}}
        state = {
            "keyguard_showing": True,
            "keyguard_secure": False,
            "focused_window": f"{module.GUAZI_PACKAGE}/Main",
            "foreground_package": module.GUAZI_PACKAGE,
        }
        s01 = make_s01_snapshot(module)
        s01["keyguard_showing"] = True
        s01["keyguard_secure"] = False

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state]),
            mock.patch.object(module, "_capture", return_value=s01),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._device_ready_gate_before_app_entry(context, reason="APP_FORCE_RESTART")

        self.assertIs(result, s01)
        self.assertTrue(context["startup"]["guazi_home_foreground_accepted"])
        self.assertFalse(context["startup"]["must_reopen_guazi_app"])
        self.assertFalse(context["startup"]["force_reopen_required"])
        self.assertEqual(context["startup"]["guazi_frontend_ready_attempt"], 1)

    def test_recover_to_guazi_page_returns_existing_guazi_home_without_force_stop(self):
        module = load_script_module()

        class NoForceStopClient(LoginFakeClient):
            def run(self, args, **_kwargs):
                if list(args) == ["shell", "am", "force-stop", module.GUAZI_PACKAGE]:
                    raise AssertionError("Guazi home fastpath must not force-stop the app")
                return FakeAdbResult(success=True)

        client = NoForceStopClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": DummyIssues(),
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        state = {
            "keyguard_showing": True,
            "keyguard_secure": False,
            "focused_window": f"{module.GUAZI_PACKAGE}/Main",
            "foreground_package": module.GUAZI_PACKAGE,
        }
        s01 = make_s01_snapshot(module)
        s01["keyguard_showing"] = True
        s01["keyguard_secure"] = False

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state]),
            mock.patch.object(module, "_capture", return_value=s01),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertIs(result, s01)
        self.assertFalse(context["startup"]["app_force_restart_called"])
        self.assertFalse(context["startup"]["force_stop_done"])
        self.assertEqual(context["startup"]["app_entry_mode"], "guazi_home_foreground_accepted")

    def test_guazi_frontend_retry_succeeds_on_second_attempt_and_continues(self):
        module = load_script_module()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": DummyIssues(),
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        missing = make_s01_snapshot(module)
        missing.update({"xml_missing": True, "fresh_xml": "", "xml_path": None, "xml_dump_error": "xml dump failed rc=137"})
        ready = make_s01_snapshot(module)

        with (
            mock.patch.object(module, "_capture", return_value=ready),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result, state = module._retry_guazi_frontend_until_ready(context, missing, None, reason="startup")

        self.assertIs(result, ready)
        self.assertEqual(state, "S01")
        self.assertEqual(context["startup"]["guazi_frontend_ready_attempt"], 2)
        self.assertEqual(len(context["startup"]["guazi_frontend_retry_attempts"]), 2)

    def test_guazi_frontend_retry_three_foreground_xml_failures_uses_evidence_missing_code(self):
        module = load_script_module()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": NoPageRecognizer(),
            "issues": DummyIssues(),
            "timing": module.TimingRecorder(),
            "startup": {},
        }

        def missing_snapshot(marker):
            snapshot = make_s01_snapshot(module)
            snapshot.update(
                {
                    "visible_blob": "",
                    "visible_texts": [],
                    "xml_missing": True,
                    "fresh_xml": "",
                    "xml_path": None,
                    "xml_dump_error": f"{marker} xml dump failed rc=137",
                }
            )
            return snapshot

        first = missing_snapshot("first")
        second = missing_snapshot("second")
        third = missing_snapshot("third")

        with (
            mock.patch.object(module, "_capture", side_effect=[second, third]),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
            self.assertRaises(module.GuaziFlowError) as raised,
        ):
            module._retry_guazi_frontend_until_ready(context, first, None, reason="startup")

        self.assertEqual(raised.exception.code, "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES")
        self.assertEqual(context["startup"]["guazi_frontend_retry_failure_code"], "GUAZI_FOREGROUND_EVIDENCE_MISSING_AFTER_3_RETRIES")
        self.assertEqual(len(context["startup"]["guazi_frontend_retry_attempts"]), 3)

    def test_secure_or_password_guazi_page_does_not_pass_stale_keyguard_gate(self):
        module = load_script_module()
        secure = make_s14_detail_snapshot(module, "secure_s14")
        secure["keyguard_secure"] = True
        secure["visible_texts"].append("输入密码")
        secure["visible_blob"] += "输入密码"

        pin = make_s14_detail_snapshot(module, "pin_s14")
        pin["visible_texts"].extend(["PIN", "紧急呼叫"])
        pin["visible_blob"] += "PIN紧急呼叫"

        for snapshot in (secure, pin):
            self.assertTrue(module.has_secure_keyguard_input_evidence(snapshot))
            self.assertFalse(module.is_guazi_foreground_operable_despite_stale_keyguard(snapshot))

    def test_guazi_stale_page_recovery_still_force_restarts_and_does_not_reuse_page(self):
        module = load_script_module()
        issues = DummyIssues()

        class ReopenClient(LoginFakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.run_args: list[list[str]] = []
                self.icon_tapped = False

            def run(self, args, **_kwargs):
                self.run_args.append(list(args))
                return FakeAdbResult(success=True)

            def tap_guazi_app_icon_exact_text(self, xml):
                self.icon_tapped = True
                return super().tap_guazi_app_icon_exact_text(xml)

        client = ReopenClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        state = {
            "keyguard_showing": True,
            "keyguard_secure": False,
            "focused_window": f"{module.GUAZI_PACKAGE}/com.guazi.h5.Html5NewContainerActivity",
            "foreground_package": module.GUAZI_PACKAGE,
        }
        s14_old_page = make_s14_detail_snapshot(module)
        s14_old_page["keyguard_showing"] = True
        s14_old_page["keyguard_secure"] = False
        launcher = make_app_icon_snapshot(module, "launcher_after_old_guazi")
        launcher["capture_taken_monotonic"] = module.time.perf_counter()
        s01 = make_s01_snapshot(module)

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state, state, state]),
            mock.patch.object(module, "_capture", side_effect=[s14_old_page, launcher, s01]) as capture_mock,
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertIs(result, s01)
        self.assertEqual(capture_mock.call_count, 3)
        self.assertIn(["shell", "am", "force-stop", module.GUAZI_PACKAGE], client.run_args)
        self.assertTrue(context["startup"]["old_guazi_page_detected"])
        self.assertTrue(context["startup"]["force_reopen_required"])
        self.assertTrue(context["startup"]["force_reopen_executed"])
        self.assertFalse(context["startup"]["launcher_snapshot_reused_for_icon_lookup"])
        self.assertTrue(context["startup"]["tap_guazi_app_icon_done"])

    def test_guazi_stale_page_reopen_failure_uses_specific_error_not_phone_awake(self):
        module = load_script_module()
        issues = DummyIssues()

        class ReopenFailClient(LoginFakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.run_args: list[list[str]] = []

            def run(self, args, **_kwargs):
                self.run_args.append(list(args))
                return FakeAdbResult(success=True)

        client = ReopenFailClient()
        context = {
            "client": client,
            "recognizer": NoPageRecognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        state = {
            "keyguard_showing": True,
            "keyguard_secure": False,
            "focused_window": f"{module.GUAZI_PACKAGE}/com.guazi.h5.Html5NewContainerActivity",
            "foreground_package": module.GUAZI_PACKAGE,
        }
        s14_old_page = make_s14_detail_snapshot(module)
        s14_old_page["keyguard_showing"] = True
        s14_old_page["keyguard_secure"] = False
        launcher_without_icon = {
            **make_app_icon_snapshot(module, "launcher_without_icon"),
            "visible_blob": "应用列表设置相册",
            "visible_texts": ["应用列表", "设置", "相册"],
            "nodes": [],
            "fresh_xml": "<launcher>设置</launcher>",
        }

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state, state]),
            mock.patch.object(module, "_capture", side_effect=[s14_old_page, launcher_without_icon]),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
            self.assertRaises(module.GuaziFlowError) as raised,
        ):
            module._recover_to_guazi_page(context)

        self.assertEqual(raised.exception.code, "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE")
        self.assertNotEqual(raised.exception.code, kw("NON_SECURE_", "KEYGUARD_", "SWIPE_", "FAILED"))
        self.assertEqual(issues.records[-1]["code"], "GUAZI_APP_REOPEN_FAILED_AFTER_OLD_PAGE_VISIBLE")

    def test_secure_keyguard_input_is_not_treated_as_miui_overlay(self):
        module = load_script_module()
        snapshot = make_miui_newhome_ad_snapshot(module)
        snapshot["keyguard_secure"] = True
        snapshot["visible_texts"].append("输入密码")
        snapshot["visible_blob"] += "输入密码"

        self.assertTrue(module.has_secure_keyguard_input_evidence(snapshot))
        self.assertFalse(module.is_miui_launcher_overlay_visible(snapshot))
        self.assertFalse(module.is_miui_newhome_ad_overlay(snapshot))
        self.assertFalse(module.is_launcher_operable_despite_stale_keyguard(snapshot))

    def test_miui_overlay_recovery_failure_returns_specific_code(self):
        module = load_script_module()

        class FakeClient:
            def __init__(self):
                self.home_calls = 0
                self.taps = []

            def wake_screen_once(self):
                return {"wake_success": True}

            def wake_swipe_once(self, *args, **kwargs):
                return {"swipe_success": True}

            def home_key_once(self):
                self.home_calls += 1
                return {"home_success": True}

            def tap(self, x, y):
                self.taps.append((x, y))
                return FakeAdbResult(success=True)

        client = FakeClient()
        issues = DummyIssues()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        state = {"keyguard_showing": True, "keyguard_secure": False, "focused_window": "NotificationShade", "foreground_package": ""}
        overlay_1 = make_miui_newhome_ad_snapshot(module, "overlay_1")
        overlay_2 = make_miui_newhome_ad_snapshot(module, "overlay_2")
        overlay_3 = make_miui_newhome_ad_snapshot(module, "overlay_3")

        with (
            mock.patch.object(module, "_device_state_only", side_effect=[state, state, state, state]),
            mock.patch.object(module, "_capture", side_effect=[overlay_1, overlay_2, overlay_3]),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            with self.assertRaises(module.GuaziFlowError) as raised:
                module._device_ready_gate_before_app_entry(context, reason="APP_FORCE_RESTART")

        self.assertEqual(raised.exception.code, "DEVICE_READY_LAUNCHER_OVERLAY_KEYGUARD_STALE")
        self.assertEqual(client.home_calls, 2)
        self.assertEqual(client.taps, [])
        self.assertTrue(context["startup"]["miui_launcher_overlay_detected"])
        self.assertFalse(context["startup"]["miui_launcher_overlay_recovered"])

    def test_fixed_recovery_swipes_once_without_precheck_or_fallback(self):
        source = read_text(SCRIPT_PATH)
        section = source.split("def _device_ready_gate_before_app_entry(", 1)[1].split("def _handle_launcher_account_dialog_until_closed(", 1)[0]
        self.assertEqual(section.count("wake_swipe_once("), 1)
        self.assertNotIn("runtime_precheck", source)
        self.assertNotIn("precheck", section.lower())
        self.assertNotIn("fallback", section.lower())

    def test_runtime_recovery_timing_is_split_into_fixed_actions(self):
        source = read_text(SCRIPT_PATH)
        device_section = source.split("def _device_ready_gate_before_app_entry(", 1)[1].split("def _handle_launcher_account_dialog_until_closed(", 1)[0]
        recover_section = source.split("def _recover_to_guazi_page(", 1)[1].split("def _dismiss_initial_s_login(", 1)[0]
        for action_name in ["wake_screen", "non_secure_keyguard_swipe_unlock"]:
            self.assertIn(f'action_name="{action_name}"', device_section)
        for action_name in [
            "force_stop_guazi_app",
            "home_to_launcher",
            "verify_launcher_and_guazi_icon",
            "tap_guazi_app_icon",
            "wait_app_open",
            "capture_runtime_screenshot",
            "dump_runtime_xml",
        ]:
            self.assertIn(f'action_name="{action_name}"', recover_section)

    def test_wake_launcher_icon_xml_reused_but_app_force_restart_still_runs(self):
        module = load_script_module()
        issues = DummyIssues()

        class FastpathClient(LoginFakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.run_args: list[list[str]] = []
                self.icon_tapped = False

            def run(self, args, **_kwargs):
                self.run_args.append(list(args))
                if args[:2] == ["shell", "dumpsys"] and args[2] == "window":
                    if self.icon_tapped:
                        return FakeAdbResult(stdout=f"isKeyguardShowing=false mCurrentFocus={module.GUAZI_PACKAGE}/Main")
                    return FakeAdbResult(stdout=f"isKeyguardShowing=false mCurrentFocus={launcher_package(module)}/Launcher")
                return FakeAdbResult(success=True)

            def tap_guazi_app_icon_exact_text(self, xml):
                self.icon_tapped = True
                return super().tap_guazi_app_icon_exact_text(xml)

        client = FastpathClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        launcher = make_app_icon_snapshot(module, "fresh_launcher_from_device_gate")
        launcher["capture_taken_monotonic"] = module.time.perf_counter()
        s01 = make_s01_snapshot(module)

        with (
            mock.patch.object(module, "_capture", side_effect=[launcher, s01]) as capture_mock,
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertIs(result, s01)
        self.assertEqual(capture_mock.call_count, 2)
        self.assertIn(["shell", "am", "force-stop", module.GUAZI_PACKAGE], client.run_args)
        self.assertEqual(len(client.icon_tap_xmls), 1)
        self.assertIn(module.GUAZI_APP_ICON_LABEL, client.icon_tap_xmls[0])
        self.assertTrue(context["startup"]["launcher_snapshot_reused_for_icon_lookup"])
        self.assertTrue(context["startup"]["fastpath_used"])
        self.assertEqual(context["startup"]["reused_capture_count"], 1)
        self.assertTrue(context["startup"]["app_force_restart_called"])

    def test_current_guazi_s10_still_force_restarts_before_contract_capture(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        s10_current = make_s10_snapshot(module)
        s10_current["capture_taken_monotonic"] = module.time.perf_counter()
        launcher = make_app_icon_snapshot(module, "launcher_after_home")
        launcher["capture_taken_monotonic"] = module.time.perf_counter()
        s01 = make_s01_snapshot(module)

        with (
            mock.patch.object(module, "_capture", side_effect=[s10_current, launcher, s01]) as capture_mock,
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertIs(result, s01)
        self.assertEqual(capture_mock.call_count, 3)
        self.assertFalse(context["startup"]["launcher_snapshot_reused_for_icon_lookup"])
        self.assertTrue(context["startup"]["force_stop_done"])
        self.assertTrue(context["startup"]["tap_guazi_app_icon_done"])

    def test_current_guazi_s14_detail_still_force_restarts_before_contract_capture(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        s14_current = {
            "foreground_package": module.GUAZI_PACKAGE,
            "xml_package": module.GUAZI_PACKAGE,
            "focused_window": f"{module.GUAZI_PACKAGE}/Main",
            "visible_blob": "瓜子官方检测报告异常细节后保险杠拆卸痕迹",
            "visible_texts": ["瓜子官方检测报告", "异常细节", "后保险杠拆卸痕迹"],
            "nodes": [],
            "fresh_xml": "<s14>后保险杠拆卸痕迹</s14>",
            "xml_missing": False,
            "screenshot_missing": False,
            "screenshot_path": "artifacts/screenshots/s14.png",
            "xml_path": "artifacts/debug/s14.xml",
            "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
            "capture_taken_monotonic": module.time.perf_counter(),
        }
        launcher = make_app_icon_snapshot(module, "launcher_after_s14_home")
        launcher["capture_taken_monotonic"] = module.time.perf_counter()
        s01 = make_s01_snapshot(module)

        with (
            mock.patch.object(module, "_capture", side_effect=[s14_current, launcher, s01]) as capture_mock,
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertIs(result, s01)
        self.assertEqual(capture_mock.call_count, 3)
        self.assertFalse(context["startup"]["launcher_snapshot_reused_for_icon_lookup"])
        self.assertTrue(context["startup"]["app_force_restart_called"])

    def test_secure_keyguard_does_not_use_launcher_fastpath(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "issues": issues,
            "timing": module.TimingRecorder(),
            "startup": {},
        }
        secure = make_app_icon_snapshot(module, "secure_keyguard")
        secure["keyguard_showing"] = True
        secure["keyguard_secure"] = True
        secure["capture_taken_monotonic"] = module.time.perf_counter()

        with (
            mock.patch.object(
                module,
                "_device_state_only",
                return_value={"keyguard_showing": True, "keyguard_secure": True, "focused_window": "Keyguard"},
            ),
            mock.patch.object(module, "_capture", return_value=secure),
            self.assertRaises(module.GuaziFlowError) as raised,
        ):
            module._device_ready_gate_before_app_entry(context, reason="APP_FORCE_RESTART")

        self.assertEqual(raised.exception.code, "SECURE_KEYGUARD_HUMAN_REQUIRED")
        self.assertFalse(context["startup"].get("fastpath_used"))

    def test_desktop_upgrade_modal_handler_runs_before_app_icon_click(self):
        source = read_text(SCRIPT_PATH)
        section = source.split("def _recover_to_guazi_page(", 1)[1].split("def _dismiss_initial_s_login(", 1)[0]
        self.assertLess(
            section.index("_handle_desktop_upgrade_modal_until_closed"),
            section.index("tap_guazi_app_icon_exact_text"),
        )

    def test_desktop_upgrade_modal_clicks_later_upgrade(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        before = make_desktop_upgrade_snapshot(module, "upgrade_before")
        after = make_app_icon_snapshot(module, "upgrade_after")

        with (
            mock.patch.object(module, "_capture", return_value=after),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._handle_desktop_upgrade_modal_until_closed(context, before)

        self.assertIs(result, after)
        self.assertEqual(client.taps, [module._center((170, 1320, 560, 1460))])
        self.assertEqual(client.icon_tap_xmls, [])
        self.assertEqual(context["startup"]["desktop_upgrade_modal_action"], "click_later")
        self.assertEqual(context["startup"]["desktop_upgrade_modal_status"], "DISMISSED")
        self.assertEqual(issues.records, [])

    def test_desktop_upgrade_modal_xml_nodes_click_later_upgrade(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        xml = (
            "<hierarchy>"
            f"<node text=\"{module.DESKTOP_UPGRADE_MODAL_TITLE_TEXT}\" bounds=\"[220,760][1000,840]\" />"
            f"<node text=\"{module.DESKTOP_UPGRADE_MODAL_LAUNCHER_TEXT}\" bounds=\"[220,850][1000,930]\" />"
            f"<node text=\"{module.DESKTOP_UPGRADE_MODAL_LATER_TEXT}\" clickable=\"true\" enabled=\"true\" bounds=\"[170,1320][560,1460]\" />"
            f"<node text=\"{module.DESKTOP_UPGRADE_MODAL_NOW_TEXT}\" clickable=\"true\" enabled=\"true\" bounds=\"[620,1320][1040,1460]\" />"
            "</hierarchy>"
        )
        before = {
            "foreground_package": launcher_package(module),
            "xml_package": launcher_package(module),
            "focused_window": f"{launcher_package(module)}/Launcher",
            "visible_blob": "",
            "visible_texts": [],
            "nodes": module._parse_nodes(xml),
            "fresh_xml": xml,
            "xml_missing": False,
            "screenshot_missing": False,
            "screenshot_path": "artifacts/screenshots/upgrade_xml.png",
            "xml_path": "artifacts/debug/upgrade_xml.xml",
            "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
        }

        with (
            mock.patch.object(module, "_capture", return_value=make_app_icon_snapshot(module, "upgrade_after_xml")),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            module._handle_desktop_upgrade_modal_until_closed(context, before)

        self.assertEqual(client.taps, [module._center((170, 1320, 560, 1460))])
        self.assertNotIn(module._center((620, 1320, 1040, 1460)), client.taps)

    def test_desktop_upgrade_modal_now_only_stops_without_click(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        before = make_desktop_upgrade_snapshot(
            module,
            "upgrade_now_only",
            with_later=False,
            with_title=False,
            with_launcher_text=False,
        )

        with self.assertRaises(module.GuaziFlowError) as raised:
            module._handle_desktop_upgrade_modal_until_closed(context, before)

        self.assertEqual(raised.exception.code, "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS")
        self.assertEqual(client.taps, [])
        self.assertEqual(context["startup"]["desktop_upgrade_modal_action"], "none")
        self.assertEqual(context["startup"]["desktop_upgrade_modal_status"], "NO_SAFE_DISMISS")
        self.assertEqual(issues.records[-1]["code"], "DESKTOP_UPGRADE_MODAL_NO_SAFE_DISMISS")

    def test_desktop_upgrade_modal_retries_once_when_still_visible(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        before = make_desktop_upgrade_snapshot(module, "upgrade_before")
        still_visible = make_desktop_upgrade_snapshot(module, "upgrade_still_visible")
        after = make_app_icon_snapshot(module, "upgrade_after_retry")

        with (
            mock.patch.object(module, "_capture", side_effect=[still_visible, after]),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._handle_desktop_upgrade_modal_until_closed(context, before)

        self.assertIs(result, after)
        self.assertEqual(len(client.taps), 2)
        self.assertEqual(context["startup"]["desktop_upgrade_modal_click_attempts"], 2)
        self.assertEqual(context["startup"]["desktop_upgrade_modal_status"], "DISMISSED")
        self.assertEqual(issues.records, [])

    def test_desktop_upgrade_modal_dismiss_failed_after_retry(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        before = make_desktop_upgrade_snapshot(module, "upgrade_before")
        still_visible_1 = make_desktop_upgrade_snapshot(module, "upgrade_still_visible_1")
        still_visible_2 = make_desktop_upgrade_snapshot(module, "upgrade_still_visible_2")

        with (
            mock.patch.object(module, "_capture", side_effect=[still_visible_1, still_visible_2]),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
            self.assertRaises(module.GuaziFlowError) as raised,
        ):
            module._handle_desktop_upgrade_modal_until_closed(context, before)

        self.assertEqual(raised.exception.code, "DESKTOP_UPGRADE_MODAL_DISMISS_FAILED")
        self.assertEqual(len(client.taps), 2)
        self.assertEqual(context["startup"]["desktop_upgrade_modal_status"], "DISMISS_FAILED")
        self.assertEqual(issues.records[-1]["code"], "DESKTOP_UPGRADE_MODAL_DISMISS_FAILED")

    def test_desktop_upgrade_modal_never_clicks_immediate_upgrade(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        before = make_desktop_upgrade_snapshot(module, "upgrade_before")
        after = make_app_icon_snapshot(module, "upgrade_after")
        immediate_center = module._center((620, 1320, 1040, 1460))

        with (
            mock.patch.object(module, "_capture", return_value=after),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            module._handle_desktop_upgrade_modal_until_closed(context, before)

        self.assertNotIn(immediate_center, client.taps)
        self.assertFalse(context["startup"]["desktop_upgrade_modal_clicked_immediate_upgrade"])

    def test_desktop_upgrade_modal_does_not_modify_current_target_task_json(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {"client": client, "issues": issues, "timing": module.TimingRecorder(), "startup": {}}
        temp_root = ROOT / "output" / "tmp_test" / "desktop_upgrade_current_task"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        temp_root.mkdir(parents=True)
        task_path = temp_root / "current_target_task.json"
        original = json.dumps({"task_id": "FS_TEST", "series": "demo"}, ensure_ascii=False)
        task_path.write_text(original, encoding="utf-8")

        with (
            mock.patch.object(module, "_capture", return_value=make_app_icon_snapshot(module, "upgrade_after")),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            module._handle_desktop_upgrade_modal_until_closed(context, make_desktop_upgrade_snapshot(module))

        self.assertEqual(task_path.read_text(encoding="utf-8"), original)
        self.assertEqual(client.icon_tap_xmls, [])

    def test_shuqing_launcher_login_prompt_is_s_login(self):
        module = load_script_module()

        class RejectingRecognizer:
            def recognize(self, *_args, **_kwargs):
                raise AssertionError("login prompt should be recognized before generic page recognition")

        snapshot = {
            "foreground_package": "com.shuqing.launcher",
            "xml_package": "com.shuqing.launcher",
            "visible_blob": "欢迎登录请输入手机号码获取验证码稍后",
        }
        self.assertEqual(module._recognize_page(RejectingRecognizer(), snapshot), "S_LOGIN")

    def test_s_login_clicks_later_until_closed(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    make_login_snapshot(module, "login_1", bounds=[133, 1366, 569, 1509]),
                    make_login_snapshot(module, "login_2", bounds=[134, 1366, 570, 1509]),
                    make_s01_snapshot(module),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertEqual(client.tapped_texts, [module.S_LOGIN_LATER_TEXT, module.S_LOGIN_LATER_TEXT])
        self.assertEqual(result["foreground_package"], module.GUAZI_PACKAGE)
        self.assertEqual(issues.records, [])

    def test_s_login_not_limited_to_three_clicks(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    make_login_snapshot(module, "login_1", bounds=[133, 1366, 569, 1509]),
                    make_login_snapshot(module, "login_2", bounds=[134, 1366, 570, 1509]),
                    make_login_snapshot(module, "login_3", bounds=[135, 1366, 571, 1509]),
                    make_login_snapshot(module, "login_4", bounds=[136, 1366, 572, 1509]),
                    make_s01_snapshot(module),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertEqual(client.tapped_texts, [module.S_LOGIN_LATER_TEXT] * 4)
        self.assertEqual(result["foreground_package"], module.GUAZI_PACKAGE)
        self.assertFalse(hasattr(module, "S_LOGIN_LATER_MAX_CLICKS"))

    def test_s_login_no_later_requires_human(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": NoPageRecognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    make_login_snapshot(module, "login_no_later", with_later=False),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
            self.assertRaises(module.GuaziFlowError) as raised,
        ):
            module._recover_to_guazi_page(context)

        self.assertEqual(raised.exception.code, "HUMAN_LOGIN_REQUIRED")
        self.assertEqual(client.tapped_texts, [])
        self.assertEqual(issues.records[-1]["code"], "HUMAN_LOGIN_REQUIRED")

    def test_after_login_later_s_app_icon_taps_guazi_icon(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    make_login_snapshot(module, "login"),
                    make_app_icon_snapshot(module, "after_later_icon"),
                    make_s01_snapshot(module),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertEqual(client.tapped_texts, [module.S_LOGIN_LATER_TEXT])
        self.assertEqual(len(client.icon_tap_xmls), 2)
        self.assertIn(module.GUAZI_APP_ICON_LABEL, client.icon_tap_xmls[-1])
        self.assertEqual(result["foreground_package"], module.GUAZI_PACKAGE)

    def test_s_app_icon_is_not_page_contract_mismatch(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    make_login_snapshot(module, "login"),
                    make_app_icon_snapshot(module, "after_later_icon"),
                    make_s01_snapshot(module),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            module._recover_to_guazi_page(context)

        self.assertNotIn("PAGE_CONTRACT_MISMATCH", [record["code"] for record in issues.records])

    def test_s_app_icon_click_then_s01_continues_mainline(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    make_login_snapshot(module, "login"),
                    make_app_icon_snapshot(module, "after_later_icon"),
                    make_s01_snapshot(module),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertEqual(module._recognize_page(S01Recognizer(), result), "S01")
        self.assertEqual(issues.records, [])

    def test_s_app_icon_click_no_progress_reports_icon_click_failed(self):
        module = load_script_module()
        issues = DummyIssues()
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": NoPageRecognizer(),
            "issues": issues,
            "timing": module.TimingRecorder(),
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    make_login_snapshot(module, "login"),
                    make_app_icon_snapshot(module, "after_later_icon"),
                    make_app_icon_snapshot(module, "after_icon_still_icon"),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
            self.assertRaises(module.GuaziFlowError) as raised,
        ):
            module._recover_to_guazi_page(context)

        self.assertEqual(raised.exception.code, "APP_ICON_CLICK_DID_NOT_OPEN_GUAZI")
        self.assertEqual(issues.records[-1]["code"], "APP_ICON_CLICK_DID_NOT_OPEN_GUAZI")

    def test_s_login_later_is_clicked_once_before_learning_loop(self):
        module = load_script_module()
        issues = DummyIssues()
        timing = module.TimingRecorder()

        class LoginFakeClient:
            def __init__(self) -> None:
                self.tapped_texts: list[str] = []

            def wake_screen_once(self):
                return {}

            def power_state(self):
                return {"wakefulness": "Awake", "interactive": True, "display_state": "ON"}

            def screen_size(self):
                return (1220, 2712)

            def run(self, *_args, **_kwargs):
                return FakeAdbResult(stdout="isKeyguardShowing=false mShowingLockscreen=false mCurrentFocus=com.android.launcher/.Launcher")

            def home_key_once(self):
                return {}

            def wake_swipe_once(self, *args, **kwargs):
                return {"swipe_success": True}

            def dump_ui_xml(self):
                return "<hierarchy>瓜子二手车</hierarchy>"

            def tap_guazi_app_icon_exact_text(self, _xml):
                return FakeAdbResult(success=True)

            def tap_text(self, text):
                self.tapped_texts.append(text)
                return FakeAdbResult(success=True)

            def back(self):
                return FakeAdbResult(success=True)

        class S01Recognizer:
            def recognize(self, blob, candidate_ids=None, context=None):
                if "首页" in blob and candidate_ids and "S01" in candidate_ids:
                    return {"id": "S01"}
                return None

        login_snapshot = {
            "foreground_package": "com.shuqing.launcher",
            "xml_package": "com.shuqing.launcher",
            "visible_blob": "检测到您的账号已退出登录请重新登录账号手机号码验证码获取验证码稍后去登录",
            "visible_texts": ["检测到您的账号已退出登录", "请重新登录账号", "手机号码", "验证码", "获取验证码", "稍后", "去登录"],
            "nodes": [{"labels": ["稍后"], "bounds": [133, 1366, 569, 1509]}],
            "fresh_xml": "<login>稍后</login>",
            "xml_missing": False,
            "screenshot_missing": False,
            "screenshot_path": "artifacts/screenshots/login.png",
            "xml_path": "artifacts/debug/login.xml",
            "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
        }
        s01_snapshot = {
            "foreground_package": module.GUAZI_PACKAGE,
            "xml_package": module.GUAZI_PACKAGE,
            "visible_blob": "首页选车卖车新能源我的",
            "xml_missing": False,
            "screenshot_missing": False,
            "screenshot_path": "artifacts/screenshots/s01.png",
            "xml_path": "artifacts/debug/s01.xml",
            "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
        }
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": S01Recognizer(),
            "issues": issues,
            "timing": timing,
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    login_snapshot,
                    s01_snapshot,
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
        ):
            result = module._recover_to_guazi_page(context)

        self.assertIs(result, s01_snapshot)
        self.assertEqual(client.tapped_texts, ["稍后"])
        self.assertEqual(issues.records, [])

    def test_s_login_forbids_login_inputs(self):
        module = load_script_module()
        source = read_text(SCRIPT_PATH)
        self.assertIn("client.tap_text(S_LOGIN_LATER_TEXT)", source)
        self.assertNotIn('client.tap_text("获取验证码")', source)
        self.assertNotIn('client.tap_text("登录")', source)
        self.assertNotIn('client.tap_text("去登录")', source)
        self.assertNotIn('client.tap_text("验证码")', source)
        self.assertNotIn('client.tap_text("手机号码")', source)

    def test_s_login_stops_when_later_click_no_progress(self):
        module = load_script_module()
        issues = DummyIssues()
        timing = module.TimingRecorder()

        class LoginFakeClient:
            def __init__(self) -> None:
                self.tapped_texts: list[str] = []

            def wake_screen_once(self):
                return {}

            def power_state(self):
                return {"wakefulness": "Awake", "interactive": True, "display_state": "ON"}

            def screen_size(self):
                return (1220, 2712)

            def run(self, *_args, **_kwargs):
                return FakeAdbResult(stdout="isKeyguardShowing=false mShowingLockscreen=false mCurrentFocus=com.android.launcher/.Launcher")

            def home_key_once(self):
                return {}

            def wake_swipe_once(self, *args, **kwargs):
                return {"swipe_success": True}

            def dump_ui_xml(self):
                return "<hierarchy>瓜子二手车</hierarchy>"

            def tap_guazi_app_icon_exact_text(self, _xml):
                return FakeAdbResult(success=True)

            def tap_text(self, text):
                self.tapped_texts.append(text)
                return FakeAdbResult(success=True)

            def back(self):
                return FakeAdbResult(success=True)

        class NoPageRecognizer:
            def recognize(self, *_args, **_kwargs):
                return None

        login_snapshot = {
            "foreground_package": "com.shuqing.launcher",
            "xml_package": "com.shuqing.launcher",
            "visible_blob": "检测到您的账号已退出登录请重新登录账号稍后去登录",
            "visible_texts": ["检测到您的账号已退出登录", "请重新登录账号", "稍后", "去登录"],
            "nodes": [{"labels": ["稍后"], "bounds": [133, 1366, 569, 1509]}],
            "fresh_xml": "<login>稍后</login>",
            "xml_missing": False,
            "screenshot_missing": False,
            "screenshot_path": "artifacts/screenshots/login.png",
            "xml_path": "artifacts/debug/login.xml",
            "capture_metrics": {"screenshot_ms": 1, "xml_ms": 1},
        }
        client = LoginFakeClient()
        context = {
            "client": client,
            "recognizer": NoPageRecognizer(),
            "issues": issues,
            "timing": timing,
        }

        with (
            mock.patch.object(
                module,
                "_capture",
                side_effect=[
                    make_app_icon_snapshot(module, "launcher_before_icon"),
                    make_app_icon_snapshot(module, "launcher_after_home"),
                    login_snapshot,
                    dict(login_snapshot),
                ],
            ),
            mock.patch.object(module.time, "sleep", lambda _seconds: None),
            self.assertRaises(module.GuaziFlowError) as raised,
        ):
            module._recover_to_guazi_page(context)

        self.assertEqual(raised.exception.code, "S_LOGIN_LATER_NO_PROGRESS")
        self.assertEqual(client.tapped_texts, ["稍后"])
        self.assertEqual(issues.records[-1]["code"], "S_LOGIN_LATER_NO_PROGRESS")

    def test_guazi_flow_error_uses_str_not_exc_message(self):
        source = read_text(SCRIPT_PATH)
        self.assertNotIn("exc.message", source)
        self.assertIn('"error": str(exc)', source)

    def test_runtime_failure_writes_result_and_timing_report(self):
        module = load_script_module()
        runtime = make_runtime_stub()
        runtime["issues"] = DummyIssues()
        temp_root = ROOT / "output" / "tmp_test" / "runtime_failure_outputs"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        (temp_root / "output").mkdir(parents=True, exist_ok=True)
        target_path = temp_root / "data" / "current_target_task.json"
        write_valid_runtime_target_task(target_path)

        class DummyRecognizer:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyMachine:
            def __init__(self, *_args, **_kwargs):
                pass

        def fake_recover(context, **_kwargs):
            context["timing"].add(
                step_name="runtime_recover_to_guazi_mainline",
                page_name="RUNTIME",
                action_name="capture_runtime_screenshot",
                contract_check_ms=0,
                field_read_ms=0,
                action_ms=500,
                transition_wait_ms=300,
                screenshot_path="artifacts/screenshots/after.png",
                xml_path="artifacts/debug/after.xml",
            )
            raise module.GuaziFlowError("RUNTIME_RECOVERY_FAILED", "boom", {"why": "test"})

        with (
            mock.patch.object(module, "ensure_runtime_dirs", lambda: None),
            mock.patch.object(module, "AdbClient", lambda: object()),
            mock.patch.object(module, "PageRecognizer", DummyRecognizer),
            mock.patch.object(module, "PageStateMachine", DummyMachine),
            mock.patch.object(module, "validate_current_target_task", lambda: {"app_operation_params": {}}),
            mock.patch.object(module, "_run_first_stage_target_device_gate", lambda _context: {"passed": True}),
            mock.patch.object(module, "_recover_to_guazi_page", fake_recover),
            mock.patch.object(module, "project_path", lambda *parts: temp_root.joinpath(*parts)),
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", target_path),
        ):
            result = module.run_s01_to_s10_mainline(runtime)

        result_path = temp_root / "output" / "test_result.json"
        timing_path = temp_root / "output" / "page_contract_timing_report.md"
        self.assertEqual(result["status"], "RUNTIME_RECOVERY_FAILED")
        self.assertTrue(result_path.exists())
        self.assertGreater(result_path.stat().st_size, 0)
        self.assertTrue(timing_path.exists())
        self.assertGreater(timing_path.stat().st_size, 0)
        self.assertIn("runtime_recover_to_guazi_mainline", timing_path.read_text(encoding="utf-8"))

    def test_runtime_fresh_evidence_missing_keeps_failure_cause(self):
        module = load_script_module()
        issues = DummyIssues()
        snapshot = {
            "xml_missing": True,
            "screenshot_missing": True,
            "runtime_recovery_cause": "GUAZI_APP_ICON_NOT_FOUND",
            "screenshot_error": "screencap_failed",
            "xml_dump_error": "xml_dump_empty",
        }
        with self.assertRaises(module.GuaziFlowError) as raised:
            module._ensure_runtime_fresh_evidence(issues, snapshot, state_id="RUNTIME")
        self.assertEqual(raised.exception.code, "RUNTIME_FRESH_EVIDENCE_MISSING")
        self.assertEqual(
            snapshot["evidence_missing_cause"],
            ["GUAZI_APP_ICON_NOT_FOUND", "screencap_failed", "xml_dump_empty"],
        )

    def test_s01_to_s10_default_task_source_is_data_current_target_task(self):
        module = load_script_module()
        temp_root = ROOT / "output" / "tmp_test" / "target_source_default"
        temp_root.mkdir(parents=True, exist_ok=True)
        data_path = temp_root / "current_target_task.json"
        data_path.write_text(
            json.dumps(
                {
                    "brand": "本田",
                    "series": "缤智",
                    "year_model": "2018款",
                    "config_model": "1.5L CVT两驱科技精英",
                    "color": "白",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", data_path),
            mock.patch.object(module, "validate_current_target_task", side_effect=AssertionError("must not read input/current_target_task.json")),
        ):
            params = module._task_params()

        self.assertEqual(params["target_task_path"], str(data_path))
        self.assertEqual(params["brand"], "本田")
        self.assertEqual(params["series"], "缤智")
        self.assertEqual(params["model_year"], "2018款")
        self.assertEqual(params["trim"], "1.5L CVT两驱科技精英")
        self.assertEqual(params["color"], "白")

    def test_s01_to_s10_does_not_default_to_input_current_target_task(self):
        module = load_script_module()
        temp_root = ROOT / "output" / "tmp_test" / "target_source_no_input_default"
        temp_root.mkdir(parents=True, exist_ok=True)
        data_path = temp_root / "current_target_task.json"
        data_path.write_text(
            json.dumps(
                {
                    "brand": "本田",
                    "series": "缤智",
                    "year_model": "2018款",
                    "config_model": "1.5L CVT两驱科技精英",
                    "color": "白",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", data_path),
            mock.patch.object(module, "validate_current_target_task", side_effect=AssertionError("input task must be explicit only")),
        ):
            params = module._task_params()

        self.assertEqual(params["series"], "缤智")

    def test_s01_to_s10_outputs_actual_target_task_path(self):
        module = load_script_module()
        temp_root = ROOT / "output" / "tmp_test" / "target_source_path"
        temp_root.mkdir(parents=True, exist_ok=True)
        data_path = temp_root / "current_target_task.json"
        data_path.write_text(
            json.dumps(
                {
                    "brand": "本田",
                    "series": "缤智",
                    "year_model": "2018款",
                    "config_model": "1.5L CVT两驱科技精英",
                    "color": "白",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        params = module._task_params(data_path)

        self.assertEqual(params["target_task_path"], str(data_path))

    def test_s01_to_s10_stops_on_target_task_mismatch(self):
        module = load_script_module()
        runtime = make_runtime_stub()
        runtime["issues"] = DummyIssues()
        temp_root = ROOT / "output" / "tmp_test" / "target_mismatch"
        if temp_root.exists():
            shutil.rmtree(temp_root)
        (temp_root / "output").mkdir(parents=True, exist_ok=True)
        data_path = temp_root / "current_target_task.json"
        data_path.write_text(
            json.dumps(
                {
                    "brand": "大众",
                    "series": "帕萨特",
                    "year_model": "2020款",
                    "config_model": "330TSI 尊贵版 国VI",
                    "color": "黑色",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        class DummyRecognizer:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyMachine:
            def __init__(self, *_args, **_kwargs):
                pass

        with (
            mock.patch.object(module, "TARGET_TASK_DATA_PATH", data_path),
            mock.patch.object(module, "ensure_runtime_dirs", lambda: None),
            mock.patch.object(module, "project_path", lambda *parts: temp_root.joinpath(*parts)),
            mock.patch.object(module, "PageRecognizer", DummyRecognizer),
            mock.patch.object(module, "PageStateMachine", DummyMachine),
            mock.patch.object(module, "AdbClient", side_effect=AssertionError("must stop before device actions")),
        ):
            result = module.run_s01_to_s10_mainline(runtime)

        self.assertEqual(result["status"], "TARGET_TASK_FIELD_MISSING")
        self.assertEqual(result["target_task"]["actual_target_task_path"], str(data_path))
        self.assertEqual(result["target_task"]["series"], "帕萨特")
        self.assertTrue((temp_root / "output" / "test_result.json").exists())

    def test_s04_uses_runtime_target_series_from_data_task(self):
        module = load_script_module()
        temp_root = ROOT / "output" / "tmp_test" / "s04_runtime_target_series"
        temp_root.mkdir(parents=True, exist_ok=True)
        data_path = temp_root / "current_target_task.json"
        data_path.write_text(
            json.dumps(
                {
                    "brand": "本田",
                    "series": "缤智",
                    "year_model": "2018款",
                    "config_model": "1.5L CVT两驱科技精英",
                    "color": "白",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        client = S03FakeClient()
        context = make_s04_context(module, client)
        context["task_params"] = module._task_params(data_path)
        initial = make_s04_snapshot(module, ["XR-V", "缤智", "冠道"], marker="s04_target")
        after_tap = make_s04_snapshot(module, ["车型"], marker="s05_after_series")

        with (
            mock.patch.object(module, "_capture", return_value=after_tap),
            mock.patch.object(module.time, "sleep", lambda *_args, **_kwargs: None),
        ):
            state, _snapshot = module.handle_s04(context, initial)

        self.assertEqual(state, "S05")
        self.assertEqual(context["s04_search_records"][0]["action_taken"], "tap_target_model_button")
        self.assertEqual(context["task_params"]["series"], "缤智")

    def test_old_keywords_still_zero(self):
        targets = [
            ROOT / "src",
            ROOT / "config",
            ROOT / "knowledge_base" / "solutions.jsonl",
            ROOT / "tests",
            SCRIPT_PATH,
            S10_TO_S16_SCRIPT,
        ]
        keywords = [
            kw("wake", "_device_with_", "menu", "_key"),
            kw("menu", "_key_", "once"),
            kw("SCREEN_", "WAKE_", "FAILED"),
            kw("SCREEN_", "OFF_", "WAKE_", "REQUIRED"),
            kw("APP_NOT_", "FOREGROUND_", "RECOVERY_", "REQUIRED"),
            kw("discover_", "target_", "app"),
            kw("launch_", "activity_", "component"),
            kw("tap_", "bottom_", "buy_", "car"),
            kw("click_", "bottom_", "home_", "tab"),
            kw("collect_", "panel_", "and_", "damage_", "type"),
            kw("SOL-", "APP-", "LABEL-", "UNREADABLE"),
            kw("SOL-", "TARGET-", "APP-", "VERIFIED"),
            kw("SOL-", "STARTUP-", "LANDS-", "ON-", "MY-", "TAB"),
            kw("SOL-", "SYSTEM-", "OVERLAY-", "OR-", "KEYGUARD-", "BLOCKING-", "APP"),
            kw("SOL-", "RUNTIME-", "SCREEN-", "WAKE-", "MECHANISM"),
            kw("SOL-", "RUNTIME-", "WAKE-", "SWIPE-", "OPEN-", "GUAZI-", "AND-", "REFRESH"),
            kw("APP_", "LABEL_", "UNREADABLE"),
            kw("APP_", "IDENTITY_", "NOT_", "FOUND"),
            kw("APP_", "IDENTITY_", "AMBIGUOUS"),
        ]
        pattern = re.compile("|".join(re.escape(item) for item in keywords))
        hits = []
        for target in targets:
            if target.is_dir():
                for path in target.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix in {".pyc", ".png", ".jpg", ".jsonl"}:
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    if pattern.search(text):
                        hits.append(str(path))
            elif target.exists():
                text = target.read_text(encoding="utf-8", errors="ignore")
                if pattern.search(text):
                    hits.append(str(target))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
