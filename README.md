# Grok 自动注册平台

Web 可视化平台，用于批量管理 Hotmail/Outlook 邮箱别名并自动化 Grok (x.ai) 账号注册。  
支持验证码自动获取、浏览器自动化注册、SSO 采集，以及注册成功后自动写入 grok2api（Web 导入 + Build 转换），导入后可直接在 grok2api 中使用。

> **仅供个人学习与自用测试。** 请遵守 xAI / Microsoft 服务条款与当地法律。

## 开源与社区

- **完整开源**：本仓库全部源码公开，[MIT License](LICENSE)
- **仓库地址**：https://github.com/HSJ-BanFan/grok-register-web
- **链接认可社区**：[LINUX DO](https://linux.do/)
- **上游参考**：[AaronL725/grok-register](https://github.com/AaronL725/grok-register)
- **说明**：在社区讨论与既有开源方案启发下完成的二次开发 / Web 化改造，不是从零另起炉灶

## 功能

- **批量账号导入** — 文本粘贴或文件上传，格式：`邮箱----密码----ClientID----RefreshToken`
- **全自动注册** — 打开注册页 → 填邮箱 → 收验证码 → 填资料 → 过 Turnstile → 提取 SSO
- **Hotmail 别名** — 每主邮箱最多 N 个成功别名（`邮箱` / `邮箱+1@域名` …）
- **失败重试** — 单个别名失败自动重试；耗尽后创建替换别名
- **英文 / 中文 UI 兼容** — 适配 `Sign up with email` / `Complete sign up` 等英文文案
- **实时日志** — WebSocket 推送进度
- **结果管理** — SSO / 账号密码查看、复制、导出
- **grok2api 自动接入** — 注册成功后自动：
  1. 导入 Grok Web（SSO）
  2. 转换为 Grok Build  
  导入后可在 grok2api 中直接调用，无需再手工粘贴 Token
- **浏览器代理** — 可选配置，降低注册页风控概率
- **旧账号批量补激活（可选）** — 需要 Cloudflare 出口上下文时，对历史 success SSO 批量补 TOS / 生日 / CF；同一次任务里通常只需手点第一道 CF

## 环境要求

| 依赖 | 版本 |
|------|------|
| Python | 3.10+ |
| Chrome / Chromium | 最新稳定版 |
| 操作系统 | Windows / macOS / Linux |

可选：本地 HTTP 代理（如 `http://127.0.0.1:7897`）。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动

```bash
python app.py
```

默认打开 `http://localhost:5000`。

```bash
python app.py --port 8080
python app.py --host 0.0.0.0 --port 5000 --allow-remote   # 仅限可信网络
```

### 3. 使用流程

1. **导入账号** — `邮箱----密码----ClientID----RefreshToken`
2. **设置（推荐）**
   - 开启：自动导入 Web 并转换 Build
   - 关闭：注册后打开 grok.com 做 Web 激活（避免每轮 Cloudflare 人机）
   - 填写 grok2api 地址 / 管理员账号密码
   - 可选：浏览器代理
3. **注册控制** → 启动任务，看实时日志
4. **结果管理** → 导出 SSO / 账号

成功日志示例：

```
SSO cookie found (152 chars)
grok2api auto pipeline completed: web_created=1 build_created=1 ...
Round N SUCCESS! Duration: 28.0s
```

## 推荐用法

**日常批量注册**

```
关闭「注册后打开 grok.com 做 Web 激活」
开启「自动导入 Web 并转换 Build」
→ 注册 → SSO → 上传到 grok2api → Build 转换 → 可直接使用
→ 一般不弹 Cloudflare 人机
```

**需要 Web / CF 出口时**

```
「结果管理」或「注册控制」→ 批量补激活
→ 只处理第一道 Cloudflare
→ 同一浏览器会话内后续账号复用 cf_clearance
```

说明：grok.com 的托管挑战（`Verify you are human`）无法稳定全自动过，因此默认不在每轮注册后打开 grok.com。上传与 Build 转换不依赖浏览器 CF Cookie。

## 旧账号批量补激活

适用于历史 SSO 缺少 TOS/生日，或共享 CF 出口失效：

1. 读取本地 success SSO  
2. 逐个切换 SSO → TOS → 生日 → Web 健康检查  
3. 刷新 grok2api 出口节点 `grok-register-web`  
4. 不重新注册，不重新 convert Build  

## 项目结构

```
grok-register/
├── app.py
├── config.py
├── requirements.txt
├── core/                 # 注册、激活、邮箱、浏览器、grok2api
│   └── registration/     # 注册状态、资料提交状态机与诊断
├── api/                  # REST + WebSocket
├── static/               # 前端 SPA
├── templates/
├── turnstilePatch/       # Turnstile 辅助扩展
├── tests/
└── data/                 # 运行时生成（本地数据库，勿提交）
```

## 主要设置

| 设置项 | 说明 | 建议 |
|--------|------|------|
| 每账号最大别名数 | 每主邮箱成功上限 | 5 |
| 每别名最大重试 | 失败重试次数 | 2–3 |
| 注册并发 Worker 数 | 同时运行的浏览器数量 | **1** |
| 浏览器模式 | 有头 / 无头 | 调试用有头 |
| 浏览器代理 | 如 `http://127.0.0.1:7897` | 有代理建议填 |
| 注册后打开 grok.com 做 Web 激活 | 每轮是否做人机激活 | **关闭** |
| 自动导入 Web 并转换 Build | 写入 grok2api | **开启** |
| grok2api 地址 / 账号 / 密码 | 管理端 | 本地或自建实例 |

## 技术栈

Flask · Flask-SocketIO · DrissionPage · SQLite · 原生 ES Module 前端

## 测试

```bash
python -m unittest discover -s tests -v
```

## 注意事项

- 默认绑定 `127.0.0.1`；绑定非本机地址必须显式添加 `--allow-remote`
- 需要本机已安装 Chrome / Chromium  
- 账号、Token、密码仅存本地 `data/`，请勿把运行时数据提交到公开仓库  
- 资料提交失败时会在 `data/diagnostics/` 保存状态 JSON 和页面截图
- 启动恢复会把超时的 pending 记录标记为 `interrupted`，不会消耗 alias 重试次数
- Cloudflare 托管挑战无法保证全自动，请用「推荐用法」分流  
- 注册页出现 `[permission_denied] HTTP 403` 表示 xAI 拒绝了发送验证码请求，邮箱中不会产生新邮件。程序会立即停止并保留 alias/retry 预算；请等待风控解除或更换网络出口后再启动
- 邮箱 Token 会自动识别 Microsoft Graph 与旧 Outlook REST 授权范围；无读取权限时会直接显示 OAuth/API 错误，不会切换到无关临时邮箱

## 致谢

- 本项目在社区讨论与既有开源方案启发下完成二次开发 / Web 化改造
- 感谢 [LINUX DO](https://linux.do/) 社区
- 上游参考：[AaronL725/grok-register](https://github.com/AaronL725/grok-register)

## License

[MIT](LICENSE)
