# 飞书定价后台调度器

飞书监听服务负责收消息、生成任务和触发一次安全调度 kick。销售或评估师回复“确认”后，任务进入 `QUEUED`，不会直接覆盖 `data/current_target_task.json`。

后台定价调度器负责串行接管队首任务：

1. 扫描 `data/feishu_tasks` 中的 `QUEUED` 任务。
2. 按 `queued_at / confirmed_at / created_at` 升序选队首。
3. 确认没有 `RUNNING_FIRST_STAGE / RUNNING_SECOND_STAGE / APP_CONTROL_LOCKED` 任务。
4. 在真正准备运行队首任务时写入 `data/current_target_task.json`。
5. 调用第一段，成功到 `S10_READY` 后调用第二段。
6. 第二段完成后执行 revalidate。
7. 自动成功则进入待发送结果状态；需要人工复核则同步主管复核流程。

不要在飞书“确认”时直接写 `current_target_task.json`。多人同时提交时，后确认的任务可能覆盖队首任务，导致 runner 检测到 `CURRENT_TARGET_TASK_TASK_ID_MISMATCH`。

飞书“确认”后的 kick 规则：

1. 后台循环已运行时，只记录任务已入队，不重复启动循环。
2. 后台循环未运行时，可调用一次安全 `dispatch_once` 封装。
3. `dispatch_once` 仍必须遵守队首任务、APP lock、target validation 和 health preflight。
4. 普通飞书业务文案不展示 dispatcher、PowerShell、runner、adb、uiautomator 等内部词。

## 命令

只读检查一次队首任务，不运行实机：

```powershell
python scripts/feishu_pricing_dispatcher.py --once --dry-run
```

真实运行一次队首任务，必须显式允许：

```powershell
python scripts/feishu_pricing_dispatcher.py --once --allow-app-run
```

后台循环部署命令：

```powershell
python scripts/feishu_pricing_dispatcher.py --loop --allow-app-run
```

未提供 `--allow-app-run` 时，调度器默认 dry-run，不会启动瓜子 APP，不会调用第一段或第二段。

办公室电脑长期部署时，应同时运行：

```powershell
python scripts/feishu_realtime_receiver.py --listen
python scripts/feishu_pricing_dispatcher.py --loop --allow-app-run
```

不要把这些命令展示给普通业务群用户。普通业务群只提示后台调度服务会自动接管；长时间未开始时联系管理员。
