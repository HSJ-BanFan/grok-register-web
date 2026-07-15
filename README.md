# Grok 自动注册平台

Web 可视化平台，用于批量管理 Hotmail/Outlook 邮箱别名并自动化 Grok (x.ai) 账号注册。  
支持验证码自动获取、浏览器自动化注册、SSO 采集，以及注册成功后自动写入 grok2api（Web 导入 + Build 转换），导入后可直接在 grok2api 中使用。

> **仅供个人学习与自用测试。** 请遵守 xAI / Microsoft 服务条款与当地法律。

## 开源与社区

- **完整开源**：本仓库全部源码公开，[MIT License](LICENSE)
- **仓库地址**：https://github.com/HSJ-BanFan/grok-register-web
- **版本更新日志**：[CHANGELOG.md](CHANGELOG.md)
- **链接认可社区**：[LINUX DO](https://linux.do/)
- **上游参考**：[AaronL725/grok-register](https://github.com/AaronL725/grok-register)
- **说明**：在社区讨论与既有开源方案启发下完成的二次开发 / Web 化改造，不是从零另起炉灶

## 功能

- **批量账号导入** — 文本粘贴或文件上传，格式：`邮箱----密码----ClientID----RefreshToken`
- **全自动注册** — 打开注册页 → 填邮箱 → 收验证码 → 填资料 → 过 Turnstile → 提取 SSO
- **Hotmail 别名** — 每主邮箱最多 N 个成功别名（`邮箱` / `邮箱+1@域名` …）
- **验证码精确匹配** — 按目标 alias、发码时间和邮件 ID 筛选 Graph / Outlook 邮件
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

| 项目 | 已验证基线 | 支持说明 |
|------|------------|----------|
| 操作系统 | Windows 11 x64 | 当前主要验证环境 |
| Python | 3.12.10 | 项目最低声明为 3.10+，推荐先复刻已验证版本 |
| Chrome | 150.0.7871.101 | 使用本机有头 Chrome；其他版本可能受页面或驱动差异影响 |
| DrissionPage | 4.1.0.9 | 已在 `requirements.txt` 精确锁定 |
| Flask | 3.1.3 | 已验证版本 |
| Flask-SocketIO | 5.6.1 | 已验证版本 |
| requests | 2.33.1 | 已验证版本 |
| curl_cffi | 0.15.0 | 已验证版本 |

Windows 本地运行是当前正式推荐方式。macOS / Linux 可运行项目代码，但浏览器、字体、沙箱和桌面会话差异尚未形成同等强度的真实注册验证。

> `requirements.txt` 中除 DrissionPage 外仍有 `>=` 依赖范围。若需要严格复现实验结果，请使用上表版本创建独立虚拟环境，并记录实际安装结果：`pip freeze > environment.txt`。

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

### Linux 服务器：Xvfb 虚拟有头模式

xAI/Cloudflare 会直接拦截部分带 `HeadlessChrome` 指纹的请求。没有物理桌面的
Linux 服务器应使用 Xvfb 运行普通有头 Chrome，而不是开启浏览器无头模式：

```bash
sudo apt-get update
sudo apt-get install -y xvfb xauth

bash scripts/run_with_xvfb.sh \
  --host 0.0.0.0 --port 5000 --allow-remote
```

启动脚本会强制：

- `GROK_REGISTER_BROWSER_HEADLESS=false`
- `GROK_REGISTER_CONCURRENCY=1`
- `1365x900x24` 虚拟显示器

如需指定 Python：

```bash
PYTHON_BIN=/path/to/venv/bin/python bash scripts/run_with_xvfb.sh \
  --host 0.0.0.0 --allow-remote
```

如果配置了浏览器代理，程序会在启动 Chrome 前从当前 Python 进程所在的网络
命名空间连接代理端口。Docker 中的 `127.0.0.1` 指向容器自身；代理运行在宿主机
时，应改用容器可访问的宿主机地址、`host.docker.internal` 或 Compose 服务名。

### 3. 使用流程

1. **导入账号** — `邮箱----密码----ClientID----RefreshToken`
2. **设置（推荐）**
   - 开启：自动导入 Web 并转换 Build
   - 关闭：注册后打开 grok.com 做 Web 激活（避免每轮 Cloudflare 人机）
   - 填写 grok2api 地址 / 管理员账号密码
   - 可选：浏览器代理
3. **注册控制** → 启动任务，看实时日志
4. **结果管理** → 导出 SSO / 账号

首次使用请注意：Git 仓库不会包含作者本机的 `data/`、SQLite 设置、邮箱 RefreshToken、SSO、浏览器 Profile 或 grok2api 密码。克隆源码后必须自行导入邮箱并完成设置，不能只启动程序就期待复现作者数据库中的运行状态。

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
├── scripts/              # Xvfb 等服务器启动脚本
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

### 推荐复现配置

先用以下保守基线完成 1 个账号，再逐项调整，不建议一开始就增加并发：

| 项目 | 推荐值 |
|------|--------|
| 注册并发 Worker 数 | `1` |
| 浏览器模式 | 有头 |
| Chrome Profile | 程序生成的临时隔离目录 |
| 浏览器代理 | 留空直连；若当前网络持续 403，再使用可信且稳定的固定出口 |
| JS stealth | 保持默认关闭 |
| Turnstile | 使用项目自带辅助扩展；必要时人工处理挑战 |
| 注册后打开 grok.com 做 Web 激活 | 关闭 |
| 自动导入 Web 并转换 Build | 按需开启；先确认 grok2api 地址可从本机访问 |

不要直接复制其他人的 `data/app.db`。数据库可能包含邮箱 Token、SSO 和服务密码，也可能带有已占用 alias、旧任务租约与不适合当前机器的设置。

## 常见错误与判断方法

| 日志或现象 | 实际含义 | 建议处理 |
|------------|----------|----------|
| `[permission_denied] HTTP 403` | xAI 拒绝了发送验证码请求；这时邮箱里本来就不会有新验证码 | 确认使用最新代码且没有自行恢复旧 stealth；停止高并发，等待风控窗口，检查公网出口/IP 信誉。不要把它当成 Outlook 收信故障 |
| `SIGNUP_ENVIRONMENT_BLOCKED` | 注册页被 Cloudflare 硬拦截，或浏览器显示代理连接错误页 | alias 会被释放且不消耗重试。Linux 服务器改用 `scripts/run_with_xvfb.sh`，并检查代理是否位于同一网络命名空间；查看 `data/diagnostics/signup-*.json/.png` |
| `Browser proxy is not reachable from this process/network namespace` | 当前程序进程无法连接配置的代理主机和端口 | Docker 中不要误用指向宿主机代理的 `127.0.0.1`；改用宿主机网关地址或代理服务名 |
| `未找到邮箱注册入口按钮` | 当前页面没有进入预期注册入口，可能仍在加载、被挑战/错误页拦截、页面结构发生变化或浏览器环境异常 | 使用有头模式观察实际页面并保存截图；确认 Chrome 可正常访问 `accounts.x.ai`，再附日志和 `data/diagnostics/` 报告问题 |
| `未找到最终注册表单或完成注册按钮` | OTP 后没有得到稳定资料表单；也可能站点已经自动完成、邮箱已有账号，或页面被重新挂载 | 当前版本已识别自动完成和 Existing account；若最新版仍出现，查看紧邻日志、最终 URL、诊断 JSON 与截图 |
| `Profile network diagnostic: ... status=...` | 资料提交阶段捕获到的网络请求，不等于这一行本身就是失败 | 重点看 create-account/sign-up 相关 POST、`status`、`failed`、最终 URL 和是否找到 SSO。某些浏览器事件会把已成功导航的请求标成 `failed=True`，最终应以进入 account/grok 页面和取得 SSO 为准 |
| 注册成功但 grok2api 显示 `sync_failed=1` | Web 导入已完成，但 Web 即时同步失败 | 继续看后续 Build conversion；若 `build_created=1`、`build_failed=0`、`build_synced=1`，则 Build 管线已成功，不应把整轮注册判为失败 |
| Render 构建成功，运行末尾 `-1` | 通常是运行容器中的 Chrome/沙箱/内存/进程退出问题，不是 Python 依赖“build 成功”就能证明注册环境可用 | 先在 Windows 本地复现。Render 数据中心 IP、临时文件系统、无桌面浏览器和资源限制均会显著影响此项目 |

### 为什么作者本机正常，其他环境仍可能失败

自动化注册不是只由仓库代码决定，实际结果还取决于以下几层状态：

1. xAI 页面版本、Turnstile 状态与公网 IP 风控信誉
2. Chrome、Python 和依赖的具体版本
3. 是否有可用桌面会话、浏览器沙箱权限和足够内存
4. 邮箱 OAuth 权限、RefreshToken 状态和 alias 是否已经注册
5. SQLite 中保存的并发、代理、激活和 grok2api 设置
6. grok2api 是否真的位于程序可访问的地址

因此“同一份源码”不等于“同一运行环境”。排障时应先用上面的单 Worker 基线复现，再比较日志，不要同时更换代理、并发、浏览器和邮箱。

### 关于 Render 和其他云平台

当前不推荐在 Render 或普通云函数/容器中运行注册 Worker：

- 数据中心公网 IP 更容易触发 403 或 Turnstile 风控
- 无头 Chrome、Linux sandbox、共享内存和进程生命周期与已验证的 Windows 有头环境不同
- Render 文件系统通常不是可靠的长期 SQLite/浏览器 Profile 存储
- Render 要求监听 `0.0.0.0:$PORT`，而项目默认只监听本机地址
- 云容器中的 `127.0.0.1:21434` 指向容器自身，不是用户电脑上的 grok2api

管理页面可以另行做云部署适配，但这不能保证注册 Worker 复刻本地 IP 和浏览器环境。若需要稳定运行，优先选择自己可控制出口、Chrome 版本和持久磁盘的 Windows 主机。

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
- 提交 Issue 时请删除邮箱 RefreshToken、验证码、SSO、Cookie 和 grok2api 密码；建议提供系统/Python/Chrome 版本、是否有头、Worker 数、公网出口类型、完整错误前后日志及 `data/diagnostics/` 中已脱敏的诊断信息

## 致谢

- 本项目在社区讨论与既有开源方案启发下完成二次开发 / Web 化改造
- 感谢 [LINUX DO](https://linux.do/) 社区
- 上游参考：[AaronL725/grok-register](https://github.com/AaronL725/grok-register)

## License

[MIT](LICENSE)
