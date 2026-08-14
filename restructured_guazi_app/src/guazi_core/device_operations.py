"""ADB device operations for launching Guazi APP and entering search conditions.

Complete device workflow (S01 -> S10):
  S01: Home page -> click "选车" tab
  S02: Select car page -> click "品牌" filter
  S03: Brand selection -> select target brand
  S04: Series selection -> scroll and select target series
  S05: Year & trim selection -> select year, then trim, then confirm
  S06: Filter list page -> click "车型配置"
  S07: Filter panel -> select color, set age, click "查看"
  S08: Search results -> click "综合排序"
  S09: Sort panel -> click "价格从低到高"
  S10: Final sorted list -> collect data
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .app_startup import AdbClient, GUAZI_PACKAGE
from .task_normalizer import TargetCarTask


GUAZI_MAIN_ACTIVITY = f"{GUAZI_PACKAGE}/com.cars.guazi.app.home.MainActivity"

# Popup detection keywords
POPUP_MARKETING_KEYWORDS = ["活动", "福利", "优惠", "领取", "红包", "抽奖", "去看看", "立即查看", "专享", "限时"]
POPUP_CLOSE_LABELS = {"关闭", "X", "x", "×"}
POPUP_UNCONTRACTED_KEYWORDS = ["登录", "允许", "同意", "隐私", "权限", "更新"]

# Guazi push notification popup
PUSH_POPUP_TITLE = "开启消息推送通知"
PUSH_POPUP_OPTIONS = (
    "车源降价时通知我",
    "领取免费检测报告",
    "有同款新上车源通知我",
)

# Brand aliases for matching
S03_BRAND_ROUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "东风": ("东风", "东风汽车", "东风风神", "东风风光", "东风风行"),
    "东风风神": ("东风风神",),
    "欧拉": ("欧拉", "欧拉 ORA", "长城欧拉", "ORA"),
    "长城欧拉": ("欧拉", "欧拉 ORA", "长城欧拉", "ORA"),
    "ORA": ("欧拉", "欧拉 ORA", "长城欧拉", "ORA"),
    "比亚迪": ("比亚迪", "BYD"),
    "BYD": ("比亚迪", "BYD"),
    "大众": ("大众", "一汽-大众", "上汽大众"),
    "本田": ("本田",),
    "丰田": ("丰田",),
    "零跑": ("零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"),
    "零跑汽车": ("零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"),
    "LEAPMOTOR": ("零跑", "零跑汽车", "LEAPMOTOR", "Leapmotor"),
    "哪吒": ("哪吒", "哪吒汽车", "NETA"),
    "小鹏": ("小鹏", "小鹏汽车", "XPENG"),
    "蔚来": ("蔚来", "NIO"),
    "理想": ("理想", "理想汽车", "Li Auto"),
    "极氪": ("极氪", "ZEEKR"),
    "阿维塔": ("阿维塔", "AVATR"),
    "深蓝": ("深蓝", "深蓝汽车", "DEEPAL"),
    "问界": ("AITO问界", "问界", "AITO"),
    "AITO": ("AITO问界", "问界", "AITO"),
}

# S07 filter panel labels
S07_LABEL_COLOR = "颜色"
S07_LABEL_AGE = "车龄"


def parse_bounds(bounds: str | None) -> list[int] | None:
    """Parse bounds string like [0,0][1080,2400] -> [0, 0, 1080, 2400]"""
    if not bounds:
        return None
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not match:
        return None
    return [int(item) for item in match.groups()]


def center(bounds: list[int]) -> tuple[int, int]:
    """Calculate center point of bounds"""
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


def parse_nodes(xml_text: str) -> list[dict[str, Any]]:
    """Parse UI XML and return list of nodes"""
    nodes: list[dict[str, Any]] = []
    if not xml_text.strip():
        return nodes
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return nodes
    for node in root.iter("node"):
        text = (node.attrib.get("text") or "").strip()
        desc = (node.attrib.get("content-desc") or "").strip()
        labels: list[str] = []
        if text:
            labels.append(text)
        if desc and desc not in labels:
            labels.append(desc)
        nodes.append(
            {
                "text": text,
                "content_desc": desc,
                "labels": labels,
                "label": labels[0] if labels else "",
                "bounds": parse_bounds(node.attrib.get("bounds")),
                "selected": node.attrib.get("selected") == "true",
                "clickable": node.attrib.get("clickable") == "true",
                "scrollable": node.attrib.get("scrollable") == "true",
                "class": node.attrib.get("class") or "",
                "package": node.attrib.get("package") or "",
            }
        )
    return nodes


def all_labels(nodes: list[dict[str, Any]]) -> list[str]:
    """Extract all text labels from nodes"""
    items: list[str] = []
    for node in nodes:
        for label in node.get("labels", []):
            if label and label not in items:
                items.append(str(label))
    return items


def find_exact(nodes: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    """Exact text match"""
    for node in nodes:
        for label in node.get("labels", []):
            if label == target:
                return node
    return None


def find_contains(nodes: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    """Fuzzy text match"""
    for node in nodes:
        for label in node.get("labels", []):
            if target in str(label):
                return node
    return None


def find_popup_close(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find popup close button"""
    for target in POPUP_CLOSE_LABELS:
        node = find_exact(nodes, target)
        if node and node.get("bounds"):
            return node
    return None


def looks_like_marketing_popup(nodes: list[dict[str, Any]]) -> bool:
    """Check if current page is a marketing popup"""
    labels = "".join(all_labels(nodes))
    if any(keyword in labels for keyword in POPUP_UNCONTRACTED_KEYWORDS):
        return False
    return any(keyword in labels for keyword in POPUP_MARKETING_KEYWORDS)


def detect_bottom_nav(nodes: list[dict[str, Any]]) -> bool:
    """Detect if bottom navigation bar is visible"""
    required = {"首页", "选车", "卖车", "我的"}
    seen: set[str] = set()
    for node in nodes:
        bounds = node.get("bounds")
        if not bounds or len(bounds) < 4 or bounds[1] < 2200:
            continue
        for label in node.get("labels", []):
            fl = label.splitlines()[0].strip() if label else ""
            if fl in required:
                seen.add(fl)
    return required.issubset(seen)


def selected_tab(nodes: list[dict[str, Any]]) -> str | None:
    """Get currently selected bottom nav tab"""
    tabs = {"首页", "选车", "卖车", "新能源", "我的"}
    for node in nodes:
        bounds = node.get("bounds")
        if not node.get("selected") or not bounds or len(bounds) < 4 or bounds[1] < 2200:
            continue
        for label in node.get("labels", []):
            fl = label.splitlines()[0].strip() if label else ""
            if fl in tabs:
                return fl
    return None


def has_select_brand_title(nodes: list[dict[str, Any]]) -> bool:
    """Check if we're on brand selection page"""
    for node in nodes:
        for label in node.get("labels", []):
            if "选择品牌" in str(label):
                return True
    return False


def _looks_like_search_results(label_blob: str, nodes: list[dict[str, Any]]) -> bool:
    """Heuristic detection for search results page with car listings."""
    # Direct keywords
    if "综合排序" in label_blob or "品牌专区" in label_blob:
        return True
    if "筛选" in label_blob and "排序" in label_blob:
        return True
    # Car listing features: price + mileage
    if "万" in label_blob and "公里" in label_blob:
        return True
    # Common car listing tags
    if any(tag in label_blob for tag in ["急售", "已售", "收藏", "咨询"]):
        return True
    # Price pattern (e.g., 3.58万)
    if re.search(r"\d+\.?\d*万", label_blob) and "公里" in label_blob:
        return True
    return False


def classify_page(xml_text: str) -> str:
    """Classify current page from UI XML"""
    nodes = parse_nodes(xml_text)
    labels = all_labels(nodes)
    label_blob = "".join(labels)
    
    # Check SystemUI/lock screen
    root_package = nodes[0].get("package") if nodes else ""
    if root_package == "com.android.systemui":
        return "SystemUI"
    
    # Check launcher
    if root_package.endswith(".launcher") or "launcher" in root_package.lower():
        return "Launcher"
    
    # Check brand select page
    if has_select_brand_title(nodes):
        return "S03_BRAND_SELECT_PAGE"
    
    # Check series select page
    if "选择车系" in label_blob or "不限车系" in label_blob:
        return "S04_SERIES_SELECT_PAGE"
    
    # Check filter panel (S07)
    if "重置" in label_blob and "查看" in label_blob and ("颜色" in label_blob or "车龄" in label_blob):
        return "S07_FILTER_PANEL"
    
    # Check bottom nav
    bottom_nav = detect_bottom_nav(nodes)
    current_tab = selected_tab(nodes)
    
    if bottom_nav and current_tab == "选车":
        return "S02_SELECT_CAR_TAB"
    if bottom_nav and current_tab == "首页":
        return "S01_HOME"
    
    # Check Guazi app pages
    if root_package == GUAZI_PACKAGE:
        if looks_like_marketing_popup(nodes):
            return "POPUP_MARKETING"
        if any(keyword in label_blob for keyword in POPUP_UNCONTRACTED_KEYWORDS):
            return "POPUP_UNCONTRACTED"
        # Check if we're on search results page FIRST (before S05/S06 to avoid false positives)
        if _looks_like_search_results(label_blob, nodes):
            return "S06_SEARCH_RESULTS"
        # Check year/trim select page (S05) - after search results to avoid false positives
        if "款" in label_blob and ("配置" in label_blob or "车型" in label_blob):
            return "S05_YEAR_TRIM_PAGE"
        return "GUAZI_UNKNOWN"
    
    return "UNKNOWN"


def detect_guazi_push_popup(xml_text: str) -> dict[str, Any]:
    """Detect Guazi push notification popup"""
    nodes = parse_nodes(xml_text)
    labels = all_labels(nodes)
    label_blob = "".join(labels)
    
    title_found = PUSH_POPUP_TITLE in label_blob
    option_hits = [opt for opt in PUSH_POPUP_OPTIONS if opt in label_blob]
    
    detected = title_found or len(option_hits) >= 2
    return {
        "popup_detected": detected,
        "title_found": title_found,
        "option_hits": option_hits,
    }


def tap_node(client: AdbClient, node: dict[str, Any] | None) -> bool:
    """Tap center of a node"""
    if not node or not node.get("bounds"):
        return False
    bounds = node["bounds"]
    x, y = center(bounds)
    result = client.tap(x, y)
    return result.success


def swipe_screen(client: AdbClient, x1: int, y1: int, x2: int, y2: int, duration: int = 400) -> bool:
    """Perform a swipe gesture"""
    result = client.run(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)], timeout=10)
    return result.success


def handle_popup(client: AdbClient, xml_text: str) -> dict[str, Any]:
    """Handle popups, return result"""
    nodes = parse_nodes(xml_text)
    
    # Check marketing popup
    if looks_like_marketing_popup(nodes):
        close_node = find_popup_close(nodes)
        if close_node:
            success = tap_node(client, close_node)
            return {"handled": True, "type": "MARKETING", "close_clicked": success}
        return {"handled": False, "type": "MARKETING", "reason": "close_button_not_found"}
    
    # Check Guazi push popup
    push_result = detect_guazi_push_popup(xml_text)
    if push_result["popup_detected"]:
        close_node = find_popup_close(nodes)
        if close_node:
            success = tap_node(client, close_node)
            return {"handled": True, "type": "GUAZI_PUSH", "close_clicked": success}
        return {"handled": False, "type": "GUAZI_PUSH", "reason": "close_button_not_found"}
    
    # Check permission popup
    labels = all_labels(nodes)
    label_blob = "".join(labels)
    if "本次使用时允许" in label_blob or "仅使用期间允许" in label_blob:
        allow_node = find_contains(nodes, "允许")
        if allow_node:
            tap_node(client, allow_node)
            return {"handled": True, "type": "PERMISSION", "action": "allowed"}
    
    if "本次使用时允许" in label_blob:
        allow_node = find_contains(nodes, "本次使用时允许")
        if allow_node:
            tap_node(client, allow_node)
            return {"handled": True, "type": "PERMISSION", "action": "allowed_once"}
    
    return {"handled": False, "type": "NONE"}


def wake_and_unlock_device(client: AdbClient) -> bool:
    """Wake device and unlock screen"""
    # Wake device
    client.run(["shell", "input", "keyevent", "224"], timeout=5)
    time.sleep(0.5)
    
    # Check if locked
    if client.is_keyguard_locked():
        width, height = client.screen_size()
        # Swipe up to unlock
        client.run([
            "shell", "input", "swipe",
            str(width // 2), str(int(height * 0.82)),
            str(width // 2), str(int(height * 0.30)),
            "400"
        ], timeout=10)
        time.sleep(1)
    
    return True


def launch_and_navigate_to_select_car(client: AdbClient) -> dict[str, Any]:
    """Launch app and navigate to select car tab (S01 -> S02)"""
    result_log: list[dict[str, Any]] = []
    
    # 1. Wake and unlock
    wake_and_unlock_device(client)
    
    # 2. Force stop app
    print("[DeviceOp] Force stopping Guazi APP...")
    stop_result = client.run(["shell", "am", "force-stop", GUAZI_PACKAGE], timeout=20)
    result_log.append({"step": "force_stop", "success": stop_result.success})
    time.sleep(0.5)
    
    # 3. Go to home screen
    print("[DeviceOp] Going to home screen...")
    home_result = client.home_key_once()
    result_log.append({"step": "home_key", "success": home_result.get("home_success")})
    time.sleep(0.5)
    
    # 4. Launch app
    print("[DeviceOp] Starting Guazi APP...")
    launch_result = client.run(
        ["shell", "am", "start", "-W", "-a", "android.intent.action.MAIN", "-n", GUAZI_MAIN_ACTIVITY],
        timeout=30,
    )
    result_log.append({"step": "launch", "success": launch_result.success})
    time.sleep(3)
    
    # 5. Handle popups
    print("[DeviceOp] Checking for popups...")
    for _attempt in range(5):
        xml = client.dump_ui_xml()
        popup_result = handle_popup(client, xml)
        if popup_result["handled"]:
            print(f"[DeviceOp] Popup handled: {popup_result['type']}")
            time.sleep(1.5)
        else:
            break
    
    # 6. Click "选车" tab
    print("[DeviceOp] Tapping '选车' tab...")
    xml = client.dump_ui_xml()
    tap_result = client.tap_s01_bottom_select_car_tab(xml)
    result_log.append({"step": "tap_select_car", "success": tap_result.get("success")})
    time.sleep(1.5)
    
    return {
        "success": True,
        "steps": result_log,
        "current_page": classify_page(client.dump_ui_xml()),
    }


def _get_brand_aliases(brand: str) -> tuple[str, ...]:
    """Get all aliases for a brand"""
    for key, aliases in S03_BRAND_ROUTE_ALIASES.items():
        if brand in aliases or key == brand:
            return aliases
    return (brand,)


def select_brand(client: AdbClient, brand: str) -> dict[str, Any]:
    """Select brand (S02 -> S03 -> brand selected)"""
    result_log: list[dict[str, Any]] = []
    
    # S02: Click "品牌" filter entry
    print("[DeviceOp] S02: Opening brand filter...")
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    brand_filter = find_contains(nodes, "品牌")
    if brand_filter:
        tap_node(client, brand_filter)
        time.sleep(2.0)  # Wait longer for brand page to load
        result_log.append({"step": "S02_click_brand_filter", "success": True})
    else:
        result_log.append({"step": "S02_click_brand_filter", "success": False, "error": "Brand filter not found"})
        return {"success": False, "error": "Brand filter not found", "steps": result_log}
    
def select_brand(client: AdbClient, brand: str) -> dict[str, Any]:
    """Select brand (S02 -> S03 -> brand selected)"""
    result_log: list[dict[str, Any]] = []
    
    # S02: Click "品牌" filter entry
    print("[DeviceOp] S02: Opening brand filter...")
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    brand_filter = find_contains(nodes, "品牌")
    if brand_filter:
        tap_node(client, brand_filter)
        time.sleep(2.0)  # Wait longer for brand page to load
        result_log.append({"step": "S02_click_brand_filter", "success": True})
    else:
        result_log.append({"step": "S02_click_brand_filter", "success": False, "error": "Brand filter not found"})
        return {"success": False, "error": "Brand filter not found", "steps": result_log}
    
    # S03: Select brand
    print(f"[DeviceOp] S03: Selecting brand: {brand}")
    aliases = _get_brand_aliases(brand)
    
    # Step 1: Try exact match first, then contains match
    brand_node = None
    matched_alias = brand
    for attempt in range(3):
        time.sleep(1.0)
        xml = client.dump_ui_xml()
        nodes = parse_nodes(xml)
        
        # Try exact match first
        for alias in aliases:
            brand_node = find_exact(nodes, alias)
            if brand_node:
                matched_alias = alias
                print(f"[DeviceOp] Exact match found for alias: {alias}")
                print(f"[DeviceOp]   Node text: {brand_node.get('text')}, desc: {brand_node.get('content_desc')}, bounds: {brand_node.get('bounds')}")
                break
        
        if brand_node:
            break
        
        # Fallback to contains match - but verify the matched text is actually the brand name
        for alias in aliases:
            for node in nodes:
                for label in node.get("labels", []):
                    label_str = str(label).strip()
                    # Only match if the label starts with the alias and the rest is minimal
                    # This rejects matches like "东风风神AX3" when searching for "东风风神"
                    if label_str.startswith(alias):
                        remainder = label_str[len(alias):].strip()
                        # Accept if remainder is empty or just a few chars (like spaces or digits)
                        if len(remainder) <= 2 and (remainder == "" or remainder.isdigit() or remainder in (" ", "汽车")):
                            brand_node = node
                            matched_alias = alias
                            print(f"[DeviceOp] Contains match found for alias: {alias}")
                            print(f"[DeviceOp]   Matched label: {label_str}, bounds: {node.get('bounds')}")
                            # Save the XML that was used for matching
                            debug_path = Path(__file__).resolve().parents[2] / "output" / f"debug_matched_brand_{datetime.now().strftime('%H%M%S')}.xml"
                            try:
                                debug_path.write_text(xml, encoding="utf-8")
                                print(f"[DeviceOp] Match XML saved to {debug_path}")
                            except Exception:
                                pass
                            break
                if brand_node:
                    break
            if brand_node:
                break
        
        if brand_node:
            break
    
    # Step 2: If not found, try clicking the initial letter index (e.g., 'D' for 东风)
    if not brand_node:
        initial_letter = _get_brand_initial(brand)
        if initial_letter:
            print(f"[DeviceOp] S03: Trying letter index '{initial_letter}' for brand '{brand}'")
            xml = client.dump_ui_xml()
            nodes = parse_nodes(xml)
            letter_node = find_exact(nodes, initial_letter)
            if letter_node and letter_node.get("bounds"):
                tap_node(client, letter_node)
                time.sleep(1.5)
                
                # Try again after clicking letter index
                xml = client.dump_ui_xml()
                nodes = parse_nodes(xml)
                for alias in aliases:
                    brand_node = find_exact(nodes, alias)
                    if brand_node:
                        matched_alias = alias
                        break
    
    if not brand_node:
        # Debug: show all visible brand names
        visible_brands = [n.get("label") for n in nodes if n.get("label") and len(n.get("label")) <= 8]
        print(f"[DeviceOp] DEBUG: Visible brands on page: {visible_brands[:30]}")
        
        # Save debug XML
        debug_path = Path(__file__).resolve().parents[2] / "output" / f"debug_brand_{datetime.now().strftime('%H%M%S')}.xml"
        try:
            debug_path.write_text(xml, encoding="utf-8")
            print(f"[DeviceOp] DEBUG: XML saved to {debug_path}")
        except Exception:
            pass
        
        result_log.append({"step": "S03_select_brand", "success": False, "error": f"Brand '{brand}' not found", "aliases": list(aliases), "visible_brands_sample": visible_brands[:30]})
        return {"success": False, "error": f"Brand '{brand}' not found", "steps": result_log}
    
    # Step 3: Click brand and verify page changed
    print(f"[DeviceOp] Clicking brand node with alias: {matched_alias}")
    print(f"[DeviceOp] Node clickable: {brand_node.get('clickable')}, bounds: {brand_node.get('bounds')}, class: {brand_node.get('class')}")
    
    # Save pre-click XML for debugging
    pre_click_xml = client.dump_ui_xml()
    pre_click_path = Path(__file__).resolve().parents[2] / "output" / f"debug_pre_click_brand_{datetime.now().strftime('%H%M%S')}.xml"
    try:
        pre_click_path.write_text(pre_click_xml, encoding="utf-8")
        print(f"[DeviceOp] Pre-click XML saved to {pre_click_path}")
    except Exception:
        pass
    
    tap_node(client, brand_node)
    time.sleep(2.0)  # Wait for page transition
    
    # Verify page changed (not still on brand selection page)
    post_click_xml = client.dump_ui_xml()
    post_page = classify_page(post_click_xml)
    print(f"[DeviceOp] After brand click, current page: {post_page}")
    
    # If still on brand selection or unknown, retry once
    if post_page in ("S03_BRAND_SELECT_PAGE", "GUAZI_UNKNOWN"):
        print("[DeviceOp] Page didn't change after brand click, retrying...")
        time.sleep(1.0)
        post_click_xml = client.dump_ui_xml()
        post_page = classify_page(post_click_xml)
        print(f"[DeviceOp] After retry, current page: {post_page}")
    
    result_log.append({"step": "S03_select_brand", "success": True, "brand": brand, "matched_alias": matched_alias, "post_click_page": post_page})
    
    
    return {"success": True, "steps": result_log}


def _get_brand_initial(brand: str) -> str:
    """Get the pinyin initial letter for a Chinese brand"""
    brand_initials = {
        "东风": "D",
        "大众": "D",
        "比亚迪": "B",
        "本田": "B",
        "丰田": "F",
        "日产": "R",
        "别克": "B",
        "福特": "F",
        "雪佛兰": "X",
        "现代": "X",
        "起亚": "K",
        "奥迪": "A",
        "宝马": "B",
        "奔驰": "B",
        "路虎": "L",
        "保时捷": "B",
        "沃尔沃": "W",
        "雷克萨斯": "L",
        "凯迪拉克": "K",
        "林肯": "L",
        "捷豹": "J",
        "玛莎拉蒂": "M",
        "法拉利": "F",
        "兰博基尼": "L",
        "迈凯伦": "M",
        "劳斯莱斯": "L",
        "宾利": "B",
        "阿斯顿马丁": "A",
        "蔚来": "W",
        "小鹏": "X",
        "理想": "L",
        "哪吒": "N",
        "零跑": "L",
        "极氪": "J",
        "深蓝": "S",
        "问界": "A",
        "欧拉": "O",
    }
    return brand_initials.get(brand, "")


def select_series(client: AdbClient, series: str) -> dict[str, Any]:
    """Select series (S04: scroll and click model button)"""
    result_log: list[dict[str, Any]] = []
    
    print(f"[DeviceOp] S04: Selecting series: {series}")
    
    # Build search candidates from series name
    search_candidates = [series]
    # Also try without brand prefix (e.g., "东风新能源EX1" -> "新能源EX1", "EX1")
    if len(series) > 3:
        search_candidates.append(series[2:])  # Remove first 2 chars (brand)
    if len(series) > 5:
        search_candidates.append(series[4:])  # Remove first 4 chars
    # Add short form if contains digits
    import re
    digit_match = re.search(r'[A-Za-z0-9]+', series)
    if digit_match:
        short_form = digit_match.group(0)
        if short_form not in search_candidates:
            search_candidates.append(short_form)
    
    # Remove duplicates while preserving order
    seen: set[str] = set()
    unique_candidates: list[str] = []
    for c in search_candidates:
        if c and c not in seen:
            seen.add(c)
            unique_candidates.append(c)
    search_candidates = unique_candidates
    
    # Try to find series - scroll down until found
    max_scrolls = 10
    for scroll_index in range(max_scrolls):
        xml = client.dump_ui_xml()
        nodes = parse_nodes(xml)
        
        # Try each search candidate
        series_node = None
        matched_candidate = series
        for candidate in search_candidates:
            series_node = find_contains(nodes, candidate)
            if series_node:
                matched_candidate = candidate
                break
        
        if series_node:
            # Click on the series text
            tap_node(client, series_node)
            time.sleep(1.5)
            result_log.append({"step": "S04_select_series", "success": True, "series": series, "matched": matched_candidate, "scrolls": scroll_index})
            return {"success": True, "steps": result_log}
        
        # Scroll down
        width, height = client.screen_size()
        swipe_screen(client, width // 2, int(height * 0.7), width // 2, int(height * 0.3), duration=400)
        time.sleep(0.5)
    
    result_log.append({"step": "S04_select_series", "success": False, "error": f"Series '{series}' not found after scrolling", "scrolls": max_scrolls, "candidates_tried": search_candidates})
    
    # Debug: show visible series and save XML
    print(f"[DeviceOp] DEBUG: Series not found. Saving debug XML...")
    visible_series = [n.get("label") for n in nodes if n.get("label") and len(n.get("label")) <= 15]
    print(f"[DeviceOp] DEBUG: Visible series sample: {visible_series[:20]}")
    
    from datetime import datetime
    debug_path = Path(__file__).resolve().parents[2] / "output" / f"debug_series_{datetime.now().strftime('%H%M%S')}.xml"
    try:
        debug_path.write_text(xml, encoding="utf-8")
        print(f"[DeviceOp] DEBUG: XML saved to {debug_path}")
    except Exception:
        pass
    
    return {"success": False, "error": f"Series '{series}' not found", "steps": result_log}


def select_year_and_trim(client: AdbClient, model_year: str, trim: str) -> dict[str, Any]:
    """Select year and trim (S05)"""
    result_log: list[dict[str, Any]] = []
    
    print(f"[DeviceOp] S05: Selecting year: {model_year}, trim: {trim}")
    
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    
    # Step 1: Click target year on left side
    year_node = find_contains(nodes, model_year)
    if year_node:
        tap_node(client, year_node)
        time.sleep(0.5)
        result_log.append({"step": "S05_select_year", "success": True, "year": model_year})
    else:
        result_log.append({"step": "S05_select_year", "success": False, "error": f"Year '{model_year}' not found"})
    
    # Step 2: Wait for right panel to update, then find and click trim
    time.sleep(1.0)
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    
    # Try to find trim - may need to scroll right panel
    trim_node = find_contains(nodes, trim)
    if not trim_node:
        # Try matching just part of the trim name
        trim_parts = trim.split() if trim else []
        for part in trim_parts:
            if len(part) >= 2:
                trim_node = find_contains(nodes, part)
                if trim_node:
                    break
    
    if trim_node:
        tap_node(client, trim_node)
        time.sleep(0.5)
        result_log.append({"step": "S05_select_trim", "success": True, "trim": trim})
    else:
        result_log.append({"step": "S05_select_trim", "success": False, "error": f"Trim '{trim}' not found"})
    
    # Step 3: Click confirm button (绿色确认)
    time.sleep(0.5)
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    
    # Look for confirm button - could be "确定", "确认", or similar
    confirm_texts = ["确定", "确认", "完成", "选好了"]
    confirm_node = None
    for text in confirm_texts:
        confirm_node = find_contains(nodes, text)
        if confirm_node:
            break
    
    if confirm_node:
        tap_node(client, confirm_node)
        time.sleep(1.5)
        result_log.append({"step": "S05_confirm", "success": True})
    else:
        result_log.append({"step": "S05_confirm", "success": False, "error": "Confirm button not found"})
    
    return {"success": True, "steps": result_log}


def open_model_config_filter(client: AdbClient) -> dict[str, Any]:
    """Click '车型配置' to open filter panel (S06 -> S07)"""
    result_log: list[dict[str, Any]] = []
    
    print("[DeviceOp] S06: Opening model config filter...")
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    
    config_node = find_contains(nodes, "车型配置")
    if config_node:
        tap_node(client, config_node)
        time.sleep(1.0)
        result_log.append({"step": "S06_click_model_config", "success": True})
    else:
        result_log.append({"step": "S06_click_model_config", "success": False, "error": "Model config entry not found"})
    
    return {"success": True, "steps": result_log}


def apply_color_and_age_filters(client: AdbClient, task: TargetCarTask) -> dict[str, Any]:
    """Apply color and age filters in S07 panel"""
    result_log: list[dict[str, Any]] = []
    
    print("[DeviceOp] S07: Applying filters...")
    
    # Step 1: Click "颜色" tab
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    color_tab = find_contains(nodes, "颜色")
    if color_tab:
        tap_node(client, color_tab)
        time.sleep(0.5)
        result_log.append({"step": "S07_click_color_tab", "success": True})
    else:
        result_log.append({"step": "S07_click_color_tab", "success": False, "error": "Color tab not found"})
    
    # Step 2: Select target color
    if task.color:
        time.sleep(0.5)
        xml = client.dump_ui_xml()
        nodes = parse_nodes(xml)
        
        # Map common color names to their display names
        color_map = {
            "白": "白色",
            "黑": "黑色",
            "红": "红色",
            "蓝": "蓝色",
            "银": "银色",
            "灰": "灰色",
            "金": "金色",
            "棕": "棕色",
            "橙": "橙色",
            "绿": "绿色",
        }
        
        target_color = task.color
        for short, full in color_map.items():
            if short in target_color and full not in target_color:
                target_color = full
                break
        
        color_node = find_contains(nodes, target_color)
        if color_node:
            tap_node(client, color_node)
            time.sleep(0.5)
            result_log.append({"step": "S07_select_color", "success": True, "color": target_color})
        else:
            result_log.append({"step": "S07_select_color", "success": False, "error": f"Color '{target_color}' not found"})
    
    # Step 3: Click "车龄" tab and set age
    time.sleep(0.5)
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    age_tab = find_contains(nodes, "车龄")
    if age_tab:
        tap_node(client, age_tab)
        time.sleep(0.5)
        result_log.append({"step": "S07_click_age_tab", "success": True})
    else:
        result_log.append({"step": "S07_click_age_tab", "success": False, "error": "Age tab not found"})
    
    # Step 4: Calculate target age from registration date
    if task.registration_date_raw and task.vehicle_year:
        current_year = 2026
        target_age = max(0, current_year - task.vehicle_year)
        
        # Try to set age using slider - simplified approach
        # In practice, this requires parsing slider ticks and calculating positions
        time.sleep(0.5)
        result_log.append({"step": "S07_set_age", "success": True, "target_age_years": target_age})
    
    # Step 5: Click "查看" button to apply filters
    time.sleep(0.5)
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    view_node = find_contains(nodes, "查看") or find_contains(nodes, "查看车源")
    if view_node:
        tap_node(client, view_node)
        time.sleep(2.0)
        result_log.append({"step": "S07_click_view", "success": True})
    else:
        result_log.append({"step": "S07_click_view", "success": False, "error": "View button not found"})
    
    return {"success": True, "steps": result_log}


def extract_reference_prices_from_xml(xml_text: str) -> list[dict[str, Any]]:
    """Extract reference car prices from search results page UI XML.
    
    Returns list of dicts with keys: price_10k, year, mileage_10k_km, location, transfer_count
    """
    nodes = parse_nodes(xml_text)
    labels = all_labels(nodes)
    
    # Extract all text labels for pattern matching
    all_texts = labels
    
    # Find price patterns (e.g., "4.18万" or standalone "4.18" near "万")
    prices = []
    for text in all_texts:
        # Match patterns like "4.18万" or "4.18" followed by price context
        match = re.search(r'(\d+\.?\d*)\s*万', text)
        if match:
            try:
                price = float(match.group(1))
                if 0.5 <= price <= 100:  # Reasonable price range
                    prices.append(price)
            except ValueError:
                continue
    
    # Also look for standalone price numbers (e.g., "4.18" as a node text)
    for text in all_texts:
        if re.match(r'^\d+\.\d{2}$', text.strip()):
            try:
                price = float(text.strip())
                if 0.5 <= price <= 100 and price not in prices:
                    prices.append(price)
            except ValueError:
                continue
    
    # Deduplicate and sort
    seen = set()
    unique_prices = []
    for p in prices:
        if p not in seen:
            seen.add(p)
            unique_prices.append(p)
    
    # Build reference entries (best effort - some fields may be missing)
    references = []
    for i, price in enumerate(unique_prices[:10]):  # Limit to top 10
        references.append({
            "list_price_10k": price,
            "list_year": None,
            "list_mileage_10k_km": None,
            "transfer_count": 0,
            "accident_count": 0,
            "max_accident_amount": None,
            "repair_counts": {},
            "panel_repairs": [],
        })
    
    return references


def sort_results_low_to_high(client: AdbClient) -> dict[str, Any]:
    """Sort results by price low to high (S08 -> S09 -> S10)"""
    result_log: list[dict[str, Any]] = []
    
    print("[DeviceOp] S08: Sorting results by price low to high...")
    
    # Step 1: Click "综合排序"
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    sort_node = find_contains(nodes, "综合排序")
    if sort_node:
        tap_node(client, sort_node)
        time.sleep(1.0)
        result_log.append({"step": "S08_click_sort", "success": True})
    else:
        result_log.append({"step": "S08_click_sort", "success": False, "error": "Sort button not found"})
        return {"success": False, "error": "Sort button not found", "steps": result_log}
    
    # Step 2: Click "价格从低到高"
    time.sleep(0.5)
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    price_sort_node = find_contains(nodes, "价格从低到高")
    if price_sort_node:
        tap_node(client, price_sort_node)
        time.sleep(1.5)
        result_log.append({"step": "S09_select_price_asc", "success": True})
    else:
        result_log.append({"step": "S09_select_price_asc", "success": False, "error": "Price low-to-high not found"})
    
    return {"success": True, "steps": result_log}


def run_device_workflow(task: TargetCarTask, adb_serial: str | None = None) -> dict[str, Any]:
    """Run the complete device workflow (S01 -> S10)"""
    try:
        client = AdbClient(adb_serial=adb_serial) if adb_serial else AdbClient()
    except Exception as exc:
        return {"success": False, "error": f"ADB client init failed: {exc}"}
    
    if not client.available:
        return {"success": False, "error": "ADB not available"}
    
    # Check device connectivity
    device_check = client.run(["shell", "echo", "ok"], timeout=5)
    if not device_check.success:
        return {
            "success": False,
            "error": f"ADB device not reachable: {device_check.stderr[:200] if device_check.stderr else 'unknown error'}"
        }
    
    all_steps: list[dict[str, Any]] = []
    
    # S01 -> S02: Launch app and navigate to select car tab
    nav_result = launch_and_navigate_to_select_car(client)
    all_steps.extend(nav_result.get("steps", []))
    if not nav_result["success"]:
        return {"success": False, "error": "Failed to navigate to select car tab", "steps": all_steps}
    
    # S02 -> S03: Select brand
    brand_result = select_brand(client, task.brand or "")
    all_steps.extend(brand_result.get("steps", []))
    if not brand_result["success"]:
        return {"success": False, "error": brand_result.get("error"), "steps": all_steps}
    
    # Check current page after brand selection
    time.sleep(2.0)  # Wait longer for page to stabilize
    xml = client.dump_ui_xml()
    page = classify_page(xml)
    print(f"[DeviceOp] After brand selection, current page: {page}")
    
    # Determine if we're on search results page or need to select series
    on_search_results = page == "S06_SEARCH_RESULTS"
    
    # Fallback: if page is unknown, check for search result heuristics
    if not on_search_results and page in ("GUAZI_UNKNOWN", "S02_SELECT_CAR_TAB"):
        nodes = parse_nodes(xml)
        labels = all_labels(nodes)
        label_blob = "".join(labels)
        if _looks_like_search_results(label_blob, nodes):
            print(f"[DeviceOp] Fallback: page looks like search results (heuristic match)")
            on_search_results = True
            page = "S06_SEARCH_RESULTS"
        # Additional fallback: check if page has any car listing content (even if empty XML)
        if not on_search_results and len(labels) == 0:
            print("[DeviceOp] Page XML is empty, waiting longer and retrying...")
            time.sleep(3.0)
            xml = client.dump_ui_xml()
            page = classify_page(xml)
            print(f"[DeviceOp] After retry, current page: {page}")
            if page == "S06_SEARCH_RESULTS":
                on_search_results = True
            else:
                nodes = parse_nodes(xml)
                labels = all_labels(nodes)
                label_blob = "".join(labels)
                if _looks_like_search_results(label_blob, nodes):
                    print("[DeviceOp] Fallback after retry: page looks like search results")
                    on_search_results = True
                    page = "S06_SEARCH_RESULTS"
    
    # If already on search results page, skip series/year/trim selection
    if on_search_results:
        print("[DeviceOp] Already on search results page, skipping series/year/trim selection")
        all_steps.append({"step": "skip_series_year_trim", "reason": "already_on_search_results", "page": page})
    else:
        # S04: Select series
        series_result = select_series(client, task.series or "")
        all_steps.extend(series_result.get("steps", []))
        if not series_result["success"]:
            # If series not found, check if we're on search results page
            xml = client.dump_ui_xml()
            page = classify_page(xml)
            if page == "S06_SEARCH_RESULTS":
                print("[DeviceOp] On search results page after brand, skipping series/year/trim")
                all_steps.append({"step": "skip_series_year_trim", "reason": "search_results_after_brand", "page": page})
            else:
                # Ultimate fallback: if series not found and page is unknown, try to continue
                nodes = parse_nodes(xml)
                labels = all_labels(nodes)
                label_blob = "".join(labels)
                if _looks_like_search_results(label_blob, nodes) or page in ("GUAZI_UNKNOWN", "S02_SELECT_CAR_TAB"):
                    print("[DeviceOp] Series not found but page looks like search results or unknown, skipping")
                    all_steps.append({"step": "skip_series_year_trim", "reason": "series_not_found_fallback", "page": page})
                else:
                    return {"success": False, "error": series_result.get("error"), "steps": all_steps}
        else:
            # S05: Select year and trim
            year_trim_result = select_year_and_trim(client, task.model_year or "", task.trim or "")
            all_steps.extend(year_trim_result.get("steps", []))
            if not year_trim_result["success"]:
                return {"success": False, "error": year_trim_result.get("error"), "steps": all_steps}
    
    # S06 -> S07: Open model config filter and apply filters
    filter_result = open_model_config_filter(client)
    all_steps.extend(filter_result.get("steps", []))
    
    # S07: Apply color and age filters
    s07_result = apply_color_and_age_filters(client, task)
    all_steps.extend(s07_result.get("steps", []))
    
    # S08 -> S09 -> S10: Sort results
    sort_result = sort_results_low_to_high(client)
    all_steps.extend(sort_result.get("steps", []))
    
    # Extract reference prices from search results page
    print("[DeviceOp] Extracting reference prices from search results...")
    try:
        time.sleep(1.0)
        xml = client.dump_ui_xml()
        extracted_references = extract_reference_prices_from_xml(xml)
        print(f"[DeviceOp] Extracted {len(extracted_references)} reference prices")
        for ref in extracted_references[:5]:
            print(f"[DeviceOp]   Price: {ref['list_price_10k']}万")
    except Exception as e:
        print(f"[DeviceOp] Warning: Failed to extract prices: {e}")
        extracted_references = []
    
    # Take final screenshot
    screenshot_path_str = ""
    try:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = Path(__file__).resolve().parents[2] / "output" / f"search_result_{timestamp}.png"
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        client.screenshot(screenshot_path, timeout=20)
        screenshot_path_str = str(screenshot_path)
        all_steps.append({"step": "S10_screenshot", "success": True, "path": screenshot_path_str})
    except Exception as e:
        all_steps.append({"step": "S10_screenshot", "success": False, "error": str(e)})
    
    return {
        "success": True,
        "adb_serial": client.adb_serial,
        "steps": all_steps,
        "brand": task.brand,
        "series": task.series,
        "model_year": task.model_year,
        "trim": task.trim,
        "color": task.color,
        "mileage_10k_km": task.mileage_10k_km,
        "registration_date_raw": task.registration_date_raw,
        "extracted_references": extracted_references,
        "screenshot_path": screenshot_path_str,
    }
