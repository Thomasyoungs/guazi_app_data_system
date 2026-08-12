# OPPO R15 Android 10 真机调试可用性诊断报告

- 测试时间：2026-06-16T15:10:14.6014251+08:00
- 工作目录：C:\Users\lzc93\Desktop\定价\guazi_app_data_system
- 测试边界：仅执行 ADB / 设备信息 / 包名识别诊断；未修改 scripts、config、规则文件；未运行正式定价流程；未点击任何业务页面。

## 结论

**最终结论：C. 不建议使用**

原因：ADB 已识别且 5 次心跳稳定，但未找到目标瓜子二手车 APP 包名 `com.ganji.android.haoche_c`，按任务规则停止，未继续执行 APP 启动、截图、UI XML 导出测试。因此当前手机无法完成瓜子二手车 APP 自动化调试可用性验证。

## 设备基础信息

- 手机厂商：OPPO
- 手机型号：PACM00
- Android 版本：10
- SDK 版本：29
- 分辨率：Physical size: 1080x2280
- 屏幕密度：Physical density: 480

## 电池状态

- USB powered：true
- level：7 / 100
- status：2
- health：2
- voltage：3919
- temperature：326
- technology：Li-ion
- 备注：电量较低，若后续继续调试建议先充电，避免调试中断。

## 内存信息摘要

- MemTotal：5802500 kB
- MemFree：477620 kB
- MemAvailable：3143584 kB
- SwapTotal：2359292 kB
- SwapFree：1522684 kB

## ADB 识别状态

执行命令：`adb devices`

结果：

```text
List of devices attached
VSM7BMSW9LTOQOHQ	device
```

判断：通过，设备状态为 `device`。

## ADB 稳定性测试

连续 5 次执行 `adb shell echo adb_alive`，每次间隔 2 秒。

```text
1	2026-06-16T15:09:40.5200603+08:00	adb_alive
2	2026-06-16T15:09:42.5753469+08:00	adb_alive
3	2026-06-16T15:09:44.6276585+08:00	adb_alive
4	2026-06-16T15:09:46.6800964+08:00	adb_alive
5	2026-06-16T15:09:48.7205286+08:00	adb_alive
```

判断：通过，5 次均正常返回。

## 当前前台页面

执行命令：`adb shell dumpsys window | findstr mCurrentFocus`

结果：

```text
mCurrentFocus=Window{c0b8756 u0 com.oppo.launcher/com.oppo.launcher.Launcher}
```

判断：当前前台为 OPPO 桌面。

## 瓜子二手车 APP 包名识别

执行命令：

```text
adb shell pm list packages | findstr ganji
adb shell pm list packages | findstr guazi
adb shell pm list packages | findstr haoche
```

结果：三组关键字均无输出，未找到 `com.ganji.android.haoche_c`。

判断：失败。瓜子二手车 APP 未安装或包名发生变化。按任务规则停止，不继续启动 APP。

## APP 启动测试

- 是否执行：否
- 是否成功启动瓜子 APP：否
- 原因：目标包名 `com.ganji.android.haoche_c` 未找到，按规则停止。

## 截图测试

- 是否执行：否
- 是否成功截图：否
- 原因：目标包名未找到，APP 启动测试未执行，按规则未继续截图。

## UI XML Dump 测试

- 是否执行：否
- 是否成功导出 UI XML：否
- 原因：目标包名未找到，APP 启动测试未执行，按规则未继续导出 UI XML。

## 是否建议作为调试机使用

不建议使用。当前 ADB 连接本身可用，但缺少可识别的瓜子二手车 APP 包名，无法验证 APP 启动、截图和 UI XML 能力是否满足自动化调试要求。
