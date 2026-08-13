"""ADB device operations for launching Guazi APP and entering search conditions.

参考老项目 runtime_s01_to_s10_mainline.py 和 runtime_recover_to_s04.py 的核心逻辑，
提供完整的设备操作流程：启动APP -> 处理弹窗/权限 -> 选择品牌 -> 选择车系 -> 录入搜索条件。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .app_startup import AdbClient, GUAZI_PACKAGE, SELECT_CAR_LABEL
from .task_normalizer import TargetCarTask


GUAZI_MAIN_ACTIVITY = f"{GUAZI_PACKAGE}/com.cars.guazi.app.home.MainActivity"

# 弹窗检测关键词
POPUP_MARKETING_KEYWORDS = ["活动", "福利", "优惠", "领取", "红包", "抽奖", "去看看", "立即查看", "专享", "限时"]
POPUP_CLOSE_LABELS = {"关闭", "X", "x", "×"}
POPUP_UNCONTRACTED_KEYWORDS = ["登录", "允许", "同意", "隐私", "权限", "更新"]

# 瓜子推送通知弹窗
PUSH_POPUP_TITLE = "开启消息推送通知"
PUSH_POPUP_OPTIONS = (
    "车源降价时通知我",
    "领取免费检测报告",
    "有同款新上车源通知我",
)


def parse_bounds(bounds: str | None) -> list[int] | None:
    """解析 bounds 字符串，如 [0,0][1080,2400] -> [0, 0, 1080, 2400]"""
    if not bounds:
        return None
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
    if not match:
        return None
    return [int(item) for item in match.groups()]


def center(bounds: list[int]) -> tuple[int, int]:
    """计算 bounds 的中心点"""
    return ((bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2)


def parse_nodes(xml_text: str) -> list[dict[str, Any]]:
    """解析 UI XML，返回节点列表"""
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
    """从节点列表中提取所有文本标签"""
    items: list[str] = []
    for node in nodes:
        for label in node.get("labels", []):
            if label and label not in items:
                items.append(str(label))
    return items


def find_exact(nodes: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    """精确匹配文本"""
    for node in nodes:
        for label in node.get("labels", []):
            if label == target:
                return node
    return None


def find_contains(nodes: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
    """模糊匹配文本"""
    for node in nodes:
        for label in node.get("labels", []):
            if target in str(label):
                return node
    return None


def find_popup_close(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """查找弹窗关闭按钮"""
    for target in POPUP_CLOSE_LABELS:
        node = find_exact(nodes, target)
        if node and node.get("bounds"):
            return node
    return None


def looks_like_marketing_popup(nodes: list[dict[str, Any]]) -> bool:
    """判断是否为营销弹窗"""
    labels = "".join(all_labels(nodes))
    if any(keyword in labels for keyword in POPUP_UNCONTRACTED_KEYWORDS):
        return False
    return any(keyword in labels for keyword in POPUP_MARKETING_KEYWORDS)


def detect_bottom_nav(nodes: list[dict[str, Any]]) -> bool:
    """检测底部导航栏是否可见"""
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
    """获取当前选中的底部导航标签"""
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
    """检测是否在品牌选择页面"""
    for node in nodes:
        for label in node.get("labels", []):
            if "选择品牌" in str(label):
                return True
    return False


def classify_page(xml_text: str, target_brand: str = "", target_series: str = "") -> str:
    """根据UI XML分类当前页面"""
    nodes = parse_nodes(xml_text)
    labels = all_labels(nodes)
    label_blob = "".join(labels)
    
    # 检查SystemUI/锁屏
    root_package = nodes[0].get("package") if nodes else ""
    if root_package == "com.android.systemui":
        return "SystemUI"
    
    # 检查是否在桌面
    if root_package.endswith(".launcher") or "launcher" in root_package.lower():
        return "Launcher"
    
    # 检查是否在品牌选择页面
    if has_select_brand_title(nodes):
        return "S03_BRAND_SELECT_PAGE"
    
    # 检查底部导航栏
    bottom_nav = detect_bottom_nav(nodes)
    current_tab = selected_tab(nodes)
    
    if bottom_nav and current_tab == "选车":
        return "S02_SELECT_CAR_TAB"
    if bottom_nav and current_tab == "首页":
        return "S01_HOME"
    
    # 检查是否是瓜子APP内页面
    if root_package == GUAZI_PACKAGE:
        # 营销弹窗
        if looks_like_marketing_popup(nodes):
            return "POPUP_MARKETING"
        # 未处理的弹窗
        if any(keyword in label_blob for keyword in POPUP_UNCONTRACTED_KEYWORDS):
            return "POPUP_UNCONTRACTED"
        return "GUAZI_UNKNOWN"
    
    return "UNKNOWN"


def detect_guazi_push_popup(xml_text: str) -> dict[str, Any]:
    """检测瓜子推送通知弹窗"""
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
    """点击节点中心位置"""
    if not node or not node.get("bounds"):
        return False
    bounds = node["bounds"]
    x, y = center(bounds)
    result = client.tap(x, y)
    return result.success


def capture(client: AdbClient, name: str, output_dir: Path | None = None) -> dict[str, Any]:
    """截图并获取UI dump"""
    if output_dir is None:
        output_dir = Path(__file__).resolve().parents[2] / "output" / "screenshots"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    tag = str(int(time.time()))
    shot_path = output_dir / f"{name}_{tag}.png"
    xml_path = output_dir / f"{name}_{tag}.xml"
    
    # 截图
    shot_result = client.screenshot(shot_path, timeout=20)
    
    # UI dump
    xml_text = client.dump_ui_xml()
    xml_path.write_text(xml_text, encoding="utf-8")
    
    nodes = parse_nodes(xml_text)
    labels = all_labels(nodes)
    
    return {
        "screenshot_path": str(shot_path) if shot_result.success else None,
        "xml_path": str(xml_path),
        "xml_text": xml_text,
        "nodes": nodes,
        "labels": labels,
        "page": classify_page(xml_text),
        "bottom_nav_visible": detect_bottom_nav(nodes),
        "selected_tab": selected_tab(nodes),
    }


def handle_popup(client: AdbClient, xml_text: str) -> dict[str, Any]:
    """处理弹窗，返回处理结果"""
    nodes = parse_nodes(xml_text)
    
    # 检查营销弹窗
    if looks_like_marketing_popup(nodes):
        close_node = find_popup_close(nodes)
        if close_node:
            success = tap_node(client, close_node)
            return {"handled": True, "type": "MARKETING", "close_clicked": success}
        return {"handled": False, "type": "MARKETING", "reason": "close_button_not_found"}
    
    # 检查瓜子推送弹窗
    push_result = detect_guazi_push_popup(xml_text)
    if push_result["popup_detected"]:
        # 尝试点击关闭
        close_node = find_popup_close(nodes)
        if close_node:
            success = tap_node(client, close_node)
            return {"handled": True, "type": "GUAZI_PUSH", "close_clicked": success}
        return {"handled": False, "type": "GUAZI_PUSH", "reason": "close_button_not_found"}
    
    # 检查未处理的弹窗（权限、登录等）
    labels = all_labels(nodes)
    label_blob = "".join(labels)
    if "本次使用时允许" in label_blob or "仅使用期间允许" in label_blob:
        # 权限弹窗 - 点击允许
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


def launch_and_navigate_to_select_car(
    client: AdbClient,
    target_brand: str = "",
    target_series: str = "",
) -> dict[str, Any]:
    """启动APP并导航到选车页面
    
    参考老项目 _recover_to_guazi_page 和 handle_s01 的核心逻辑。
    """
    result_log: list[dict[str, Any]] = []
    
    # 0. 唤醒设备并解锁（向上滑动）
    print("[DeviceOp] Waking device...")
    client.run(["shell", "input", "keyevent", "224"], timeout=5)  # WAKEUP
    time.sleep(0.5)
    
    # 检查是否锁屏，如果是则滑动解锁
    if client.is_keyguard_locked():
        print("[DeviceOp] Unlocking screen...")
        width, height = client.screen_size()
        # 向上滑动解锁
        client.run([
            "shell", "input", "swipe",
            str(width // 2), str(int(height * 0.82)),
            str(width // 2), str(int(height * 0.30)),
            "400"
        ], timeout=10)
        time.sleep(1)
    
    # 1. 强制停止APP
    print("[DeviceOp] Force stopping Guazi APP...")
    stop_result = client.run(["shell", "am", "force-stop", GUAZI_PACKAGE], timeout=20)
    result_log.append({"step": "force_stop", "success": stop_result.success})
    time.sleep(0.5)
    
    # 2. 回到桌面
    print("[DeviceOp] Going to home screen...")
    home_result = client.home_key_once()
    result_log.append({"step": "home_key", "success": home_result.get("home_success")})
    time.sleep(0.5)
    
    # 3. 启动APP
    print("[DeviceOp] Starting Guazi APP...")
    launch_result = client.run(
        ["shell", "am", "start", "-W", "-a", "android.intent.action.MAIN", "-n", GUAZI_MAIN_ACTIVITY],
        timeout=30,
    )
    result_log.append({"step": "launch", "success": launch_result.success})
    time.sleep(3)
    
    # 4. 等待并检查弹窗
    print("[DeviceOp] Checking for popups...")
    for attempt in range(5):
        xml = client.dump_ui_xml()
        popup_result = handle_popup(client, xml)
        if popup_result["handled"]:
            print(f"[DeviceOp] Popup handled: {popup_result['type']}")
            time.sleep(1.5)
        else:
            break
    
    # 5. 点击底部"选车"标签
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


def select_brand(client: AdbClient, brand: str) -> dict[str, Any]:
    """在品牌选择页面选择目标品牌
    
    参考老项目 handle_s03 的核心逻辑。
    """
    print(f"[DeviceOp] Selecting brand: {brand}")
    
    # 获取当前UI
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    
    # 检查是否在品牌选择页面
    if not has_select_brand_title(nodes):
        # 可能需要先点击品牌筛选入口
        print("[DeviceOp] Not on brand select page, trying to open...")
        brand_filter = find_contains(nodes, "品牌")
        if brand_filter:
            tap_node(client, brand_filter)
            time.sleep(1.5)
            xml = client.dump_ui_xml()
            nodes = parse_nodes(xml)
    
    # 查找并点击目标品牌
    brand_node = find_contains(nodes, brand)
    if not brand_node:
        # 尝试搜索品牌的别名
        # 简化处理：直接尝试滚动查找
        print(f"[DeviceOp] Brand '{brand}' not visible, attempting to find...")
        # 这里可以加入滚动逻辑，但简化处理
        return {"success": False, "error": f"Brand '{brand}' not found"}
    
    success = tap_node(client, brand_node)
    time.sleep(1.5)
    
    return {
        "success": success,
        "brand": brand,
        "brand_node_bounds": brand_node.get("bounds"),
    }


def select_series(client: AdbClient, series: str) -> dict[str, Any]:
    """在车系列表页面选择目标车系
    
    参考老项目 handle_s04 的核心逻辑。
    """
    print(f"[DeviceOp] Selecting series: {series}")
    
    xml = client.dump_ui_xml()
    nodes = parse_nodes(xml)
    
    # 查找目标车系
    series_node = find_contains(nodes, series)
    if not series_node:
        return {"success": False, "error": f"Series '{series}' not found"}
    
    success = tap_node(client, series_node)
    time.sleep(1.5)
    
    return {
        "success": success,
        "series": series,
        "series_node_bounds": series_node.get("bounds"),
    }


def run_device_workflow(task: TargetCarTask, adb_serial: str | None = None) -> dict[str, Any]:
    """运行完整的设备工作流程
    
    参考老项目 run_s01_to_s10_mainline 的核心流程，简化实现。
    """
    try:
        client = AdbClient(adb_serial=adb_serial) if adb_serial else AdbClient()
    except Exception as exc:
        return {"success": False, "error": f"ADB client init failed: {exc}"}
    
    if not client.available:
        return {"success": False, "error": "ADB not available"}
    
    # Check device connectivity with a quick command
    device_check = client.run(["shell", "echo", "ok"], timeout=5)
    if not device_check.success:
        return {
            "success": False,
            "error": f"ADB device not reachable: {device_check.stderr[:200] if device_check.stderr else 'unknown error'}"
        }
    
    # Step 1: 启动APP并导航到选车页面
    nav_result = launch_and_navigate_to_select_car(client, task.brand, task.series)
    if not nav_result["success"]:
        return nav_result
    
    # Step 2: 选择品牌
    if task.brand:
        brand_result = select_brand(client, task.brand)
        if not brand_result["success"]:
            return brand_result
    
    # Step 3: 选择车系
    if task.series:
        series_result = select_series(client, task.series)
        if not series_result["success"]:
            return series_result
    
    return {
        "success": True,
        "adb_serial": client.adb_serial,
        "navigated_to": "S04",
        "brand": task.brand,
        "series": task.series,
    }
