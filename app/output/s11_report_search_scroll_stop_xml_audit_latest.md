# S11_REPORT_SEARCH 滚动停点 XML 审计

## 结论
- 归类：`XML_SCREENSHOT_NOT_SAME_MOMENT`
- 判断：不是页面契约问题；是固定脚本 fresh/XML 与截图证据配对或采集时序问题。
- 直接原因：截图已到 S11 详情页，但同名 XML 仍是 S10 列表页，因此脚本按 XML 判断 `exact_report_entry_seen=false`，没有进入点击分支。

## 关键证据
- 截图 search_3/search_4 肉眼可见完整“查看完整报告”，且按钮不在底部栏遮挡区。
- 对应 XML search_3/search_4 中“查看完整报告”精确节点数量为 0；唯一命中的关键词节点是 S10 筛选栏“成色/车况”，不是 S11 报告入口。
- 同一组 12 个 XML 文件 SHA256 完全一致，说明不是逐次 fresh 到了不同 S11 滚动位置的 XML。
- XML 原文包含 S10 列表强信号：品牌专区、价格从低到高、车型配置、12.27 万车卡价格等。
- 当前代码 _find_s11_official_report_entry_node 依赖 _find_exact_label_node(snapshot, 查看完整报告)；在 stale XML 中没有节点，所以 exact_report_entry_seen=false，后续安全区/点击分支不会触发。
- 停点拼图：`C:\Users\lzc93\Desktop\定价\guazi_app_data_system\output\s11_report_search_scroll_stop_montage.png`

## 每次 Scroll Stop
| iter | screenshot | XML size/SHA | 截图观察 | XML关键词命中 | XML 页面信号 | 判断 |
|---:|---|---|---|---:|---|---|
| 1 | `s11_report_entry_search_1_20260516_213347.png` | 99990 / `9902321e5055` | 截图为 12.27 车辆详情页上方/本车卖点附近，未看到完整“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 2 | `s11_report_entry_search_2_20260516_213401.png` | 99990 / `9902321e5055` | 截图进入瓜子官方检测报告卡片上方，未看到完整“查看完整报告”按钮。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 3 | `s11_report_entry_search_3_20260516_213415.png` | 99990 / `9902321e5055` | 截图中“查看完整报告”完整可见，位于瓜子官方检测报告卡片底部左侧；旁边为“找顾问解读报告”；未见底部栏遮挡该按钮。 | 1 | S10列表信号 | 截图可见但 XML 缺失/错配 |
| 4 | `s11_report_entry_search_4_20260516_213429.png` | 99990 / `9902321e5055` | 截图中“查看完整报告”完整可见，位置更靠上，仍在安全区域；旁边为“找顾问解读报告”；未见底部栏遮挡该按钮。 | 1 | S10列表信号 | 截图可见但 XML 缺失/错配 |
| 5 | `s11_report_entry_search_5_20260516_213443.png` | 99990 / `9902321e5055` | 截图已经滑过报告入口，显示保险理赔记录、AI 帮我解读车况、服务中心等区域；未看到“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 6 | `s11_report_entry_search_6_20260516_213457.png` | 99990 / `9902321e5055` | 截图继续向下，显示 AI 问答、服务中心、分期价格方案；未看到“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 7 | `s11_report_entry_search_7_20260516_213510.png` | 99990 / `9902321e5055` | 截图显示分期价格方案/瓜子认证车商/服务保障卡片；未看到“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 8 | `s11_report_entry_search_8_20260516_213524.png` | 99990 / `9902321e5055` | 截图显示分期价格方案/60秒测分期额度/瓜子认证车商；未看到“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 9 | `s11_report_entry_search_9_20260516_213538.png` | 99990 / `9902321e5055` | 截图显示瓜子认证车商/唐山服务中心；未看到“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 10 | `s11_report_entry_search_10_20260516_213552.png` | 99990 / `9902321e5055` | 截图显示唐山服务中心/官方检测/物流检测等服务信息；未看到“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 11 | `s11_report_entry_search_11_20260516_213605.png` | 99990 / `9902321e5055` | 截图回到车辆基础信息/本车卖点附近；未看到完整“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |
| 12 | `s11_report_entry_search_12_20260516_213619.png` | 99990 / `9902321e5055` | 截图为本车卖点/瓜子官方检测报告卡片上方；未看到完整“查看完整报告”。 | 1 | S10列表信号 | XML 无 S11 报告入口节点 |

## XML 原文上下文摘录
以下为同组 XML 中靠近 `12.27` 的原文片段摘要，证明 XML 仍是 S10 车源列表节点，而非 S11 报告卡节点：

- needle `品牌专区`: `false" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][1220,2510]"><node index="0" text="品牌专区" resource-id="" class="android.webkit.WebView" package="com.ganji.android.haoche_c" content-desc="" checkable="false" checked="false" clickable="false" enabled="true" focusable="true" focused="true" scrollable="true" long-clickable="false" password="false" selected="false" bounds="[0,0][1220,2513]"><node index="0"`
- needle `价格从低到高`: `true" focusable="true" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[0,0][0,0]" /></node></node></node><node index="1" text="价格从低到高" resource-id="" class="android.widget.TextView" package="com.ganji.android.haoche_c" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[39,793][347,841]" /><node i`
- needle `车型配置`: `e" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[643,793][880,841]" /><node index="4" text="车型配置" resource-id="" class="android.widget.TextView" package="com.ganji.android.haoche_c" content-desc="" checkable="false" checked="false" clickable="true" enabled="true" focusable="false" focused="false" scrollable="false" long-clickable="false" password="false" selected="false" bounds="[965,793][1183,841]" /><node i`

同组 XML 的前序文本节点样例：
```text
品牌专区
本田
全部能源类型
yAj7GShRek3H5AAAAAElFTkSuQmCC
雅阁
3.09
万起
全国1355辆在售/本地931辆在售
qnbdp1066x652057c93ed04f5280e986b5de372e3a1755679360
本田销量排行榜 第3名
全部
雅阁 商务家用
思域 性能钢炮
CR-V 家用首选
飞度 省油耐造
皓影 家用首选
XR-V 家用首选
艾力绅 商务接待
缤智 练手车
奥德赛 奶爸专车
查看 更多
价格从低到高
价格
成色/车况
车型配置
```

## 为什么代码没有点击
- `_find_s11_official_report_entry_node(snapshot)` 通过 `_find_exact_label_node(snapshot, "查看完整报告")` 找 XML/node 精确标签。
- 本组 XML 原文没有该节点，也没有 content-desc 或拆分上下文，因此 `official_report_entry_seen=false` / `exact_report_entry_seen=false`。
- 由于候选节点不存在，`exact_report_entry_fully_visible`、`bottom_bar_blocked`、safe click region 等安全点击判断没有进入有效分支。

## 排除项
- `XML_NODE_EXISTS_BUT_FILTERED_BY_CODE`：不成立；XML 原文没有入口或报告相关候选节点。
- `XML_TEXT_SPLIT_OR_CONTENT_DESC_MISSED`：不成立；逐节点检查 text/content-desc，没有“查看/完整报告”拆分上下文，也没有 content-desc 命中。
- `SAFE_REGION_FILTER_CORRECTLY_BLOCKED`：不成立；safe-region 分支未到达，截图上 search_3/search_4 也未见底部遮挡。
- `PAGE_CONTRACT_PROBLEM`：不成立；契约要求 XML 精确入口且安全才点击，本次问题是 XML 与截图不是同一页面。

## 最终回答
这是固定脚本的 XML/fresh 证据配对或采集时序问题，不是页面契约问题。截图可见的 S11 页面和 XML 原文不是同一个逻辑页面；XML 仍停留在 S10 列表页。

本轮只读审计：未运行实机，未覆盖 `result.json`。

`S11_REPORT_SEARCH_SCROLL_STOP_XML_AUDIT_DONE`

