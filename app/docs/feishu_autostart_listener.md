# 飞书监听开机自动启动说明

## 边界

开机自动监听只负责飞书收消息。

它不会自动启动瓜子 APP，不会自动跑第一段，不会自动跑第二段，不会自动定价。它只启动：

```powershell
python scripts/feishu_realtime_receiver.py --listen
```

不要把 `FEISHU_APP_ID` 或 `FEISHU_APP_SECRET` 写进脚本。不要截图展示 App Secret。如果 App Secret 泄露，先去飞书开放平台重置。

## A. 设置 Windows 用户环境变量

在 Windows 用户环境变量中手动设置：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

不要让脚本保存真实 Secret，也不要把真实 Secret 提交到仓库。

## B. 手动自检

```powershell
cd "C:\Users\lzc93\Desktop\定价\guazi_app_data_system"
python scripts/feishu_realtime_receiver.py --self-check
```

## C. 手动监听测试

```powershell
python scripts/feishu_realtime_receiver.py --listen
```

飞书发送：

```text
测试
```

期望回复：

```text
【二手车定价系统】已收到测试消息，本地飞书网关连接正常。
```

## D. 安装开机自动监听任务

PowerShell 管理员或当前用户 PowerShell 执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install_feishu_listener_task.ps1"
```

任务名：

```text
GuaziFeishuListener
```

触发逻辑：Windows 用户登录后延迟 30 秒启动监听脚本。

## E. 检查任务

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\check_feishu_listener_task.ps1"
```

检查内容包括任务是否存在、任务状态、上次运行时间、上次运行结果，以及以下日志是否存在：

- `logs\feishu_listener_startup.log`
- `logs\feishu_listener_runtime.log`

## F. 卸载任务

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\uninstall_feishu_listener_task.ps1"
```

卸载只删除 `GuaziFeishuListener` 任务，不删除日志。
