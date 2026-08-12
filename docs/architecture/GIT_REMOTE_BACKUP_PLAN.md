# Git 远程备份方案

本项目包含二手车定价、飞书交互、运行任务和本地配置边界。远程备份只允许使用私有仓库，禁止使用公开仓库。

## 1. 远程仓库选择

可选方案：

- GitHub Private
- Gitee Private
- 公司私有 Git 服务

远程仓库必须为 private。任何公开仓库都不允许承载本项目代码。

## 2. 首次推送前检查

首次推送前必须确认 baseline 不包含以下内容：

- `.env`、真实 token、真实 app secret、真实 app id。
- 真实 `chat_id`、`open_id`、`tenant_key`。
- `data/runtime/`。
- `data/backup/`。
- `data/feishu_tasks/`。
- `guazi_app_data_system/data/feishu_group_bindings.json`。
- `logs/`、`output/`、`evidence/`、截图、调试产物。
- ADB/platform-tools 二进制。
- zip/mp4 等大体量本地产物。
- 测试输出产物。

## 3. 推荐命令

用户提供私有仓库地址并明确授权后，才能执行：

```powershell
git remote add origin <PRIVATE_REPO_URL>
git push -u origin main
git push origin --tags
```

## 4. 禁止事项

- 未经用户明确授权，不执行 `git push`。
- 未确认 baseline 干净，不绑定远程仓库。
- 不把真实飞书凭证、真实群 ID、本地运行任务或历史生产任务状态推送到远程。
- 不把本地 `.env`、日志、运行时目录、platform-tools、证据包或临时文件推送到远程。

## 5. 用户需要提供的信息

后续执行远程备份时，用户需要提供：

- 私有 Git 仓库地址：`<PRIVATE_REPO_URL>`。
- 明确授权执行远程绑定和推送。
- 如远程需要认证，用户应在本机 Git 凭证管理器或 SSH key 中自行配置，不把凭证写入仓库文件。
