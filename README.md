# Grok Register

本地 Web 控制台：邮箱别名 / 临时邮箱批量注册 Grok，采集 SSO；可选交付到 **grok2api**（Web 导入 + Build 转换，失败自动补传）和/或 **CPA**（CLIProxyAPI 热载，chat 探测后入库）。

- **邮箱**：Microsoft Outlook/Hotmail 别名，以及 DuckMail / YYDS / Cloudflare Temp Email / Cloud Mail 等临时邮箱
- **注册路径**：HTTP **协议注册**（推荐，无业务 Chrome）或 **浏览器自动化**（调试可选）
- **人机**：本地 Camoufox Turnstile Solver，或 YesCaptcha
- **交付**：SSO 本地落库；可选 grok2api Web→Build（失败 durable 补传）；可选 CPA mint + chat probe 热载
- **状态**：协议长跑已在自用环境验证可稳定使用（含 chat 可用号池）

> **仅供个人学习与自用测试。** 请遵守 xAI、邮箱服务商条款与当地法律。

## 文档

| 文档 | 内容 |
|------|------|
| [docs/USAGE.md](docs/USAGE.md) | 日常使用：导入邮箱、开任务、读日志、导出、补传、补激活 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 设置项字典、环境变量、推荐组合、grok2api / CPA 对接 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 注册 vs 交付失败、限流、出口节点、Solver、Issue 脱敏 |
| [CHANGELOG.md](CHANGELOG.md) | 版本记录 |
| [core/DESIGN.md](core/DESIGN.md) | 核心模块设计决策 |

## 界面预览

![结果管理 · 系统统计概览](docs/screenshots/results-dashboard.png)

> 当前推荐路径：协议注册 + 本地 Turnstile Solver；结果页提供 KPI、SSO 折叠与 chat 探测统计。  
> 点击顶栏版本号可打开「关于」。

## 开源与社区

- **完整开源**：本仓库全部源码公开，[MIT License](LICENSE)
- **仓库地址**：https://github.com/HSJ-BanFan/grok-register-web
- **版本更新日志**：[CHANGELOG.md](CHANGELOG.md)
- **链接认可社区**：[LINUX DO](https://linux.do/)
- **协议注册思路**：[LINUX DO · 协议注册讨论](https://linux.do/t/topic/2594078)
- **上游参考**：[AaronL725/grok-register](https://github.com/AaronL725/grok-register)
- **说明**：在社区讨论与既有开源方案启发下完成的二次开发 / Web 化改造，不是从零另起炉灶

## 功能摘要

- **多邮箱服务** — Microsoft Graph / Outlook REST / IMAP XOAUTH2、DuckMail、YYDS、Cloudflare Temp Email、Cloud Mail API
- **协议注册（推荐）** — HTTP 发码 / 验码 / 提交 / SSO；浏览器路径保留为调试可选
- **注册 / 上传 / chat 解耦** — `Round SUCCESS` 表示出号成功；即时上传失败不推翻注册结果
- **grok2api 交付（可选）** — Web 导入 → Build 转换；瞬时失败重试 + durable 补传
- **CPA 热载（可选）** — mint → chat probe → `xai-*.json`；不可用进 dead 目录
- **本地 Turnstile Solver** — Camoufox HTTP API，协议路径推荐
- **结果管理** — SSO / 账号导出、KPI、chat 探测统计
- **旧账号批量补激活** — 历史 SSO 补 TOS / 生日 / CF 出口上下文

完整说明见 [docs/USAGE.md](docs/USAGE.md)。

## 环境要求

| 项目 | 已验证基线 | 支持说明 |
|------|------------|----------|
| 操作系统 | Windows 11 x64 | 当前主要验证环境 |
| Python | 3.12.10 | 项目最低声明为 3.10+，推荐先复刻已验证版本 |
| Chrome | 150.0.7871.101 | 浏览器路径需要；协议 + 本地 Solver 主要依赖 Camoufox |
| DrissionPage | 见 `requirements.txt` | 已精确锁定 |
| Flask / Flask-SocketIO | 见已验证环境 | 随 `requirements.txt` |
| curl_cffi | 见已验证环境 | 协议路径依赖 |

Windows 本地运行是当前正式推荐方式。macOS / Linux 可运行，但真实注册验证强度低于 Windows。

可选：本地 HTTP 或 SOCKS 代理。容器内不要用容器自己的 `127.0.0.1` 指宿主机代理。

## 快速开始

### 1. 安装

```bash
pip install -r requirements.txt

# 协议路径强烈建议
pip install -r requirements-solver.txt
python -m camoufox fetch
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

Windows 若遇 `WinError 216`，可指定 Chrome：

```powershell
$env:GROK_REGISTER_BROWSER_PATH = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
python app.py
```

Linux 无桌面跑**浏览器路径**时用 Xvfb（不要硬开无头）：

```bash
bash scripts/run_with_xvfb.sh --host 0.0.0.0 --port 5000 --allow-remote
```

### 3. 首次配置（推荐长跑）

1. **邮箱**页导入 Microsoft 账号，或设置临时邮箱  
2. **设置**页：
   - 注册传输后端 = **协议**
   - Turnstile = **仅外置** + 本地 Solver（`http://127.0.0.1:5072`）
   - 并发 Worker = **1**
   - 关闭「注册后打开 grok.com 做 Web 激活」
   - 可选：grok2api 自动上传 / Chat probe / CPA  
3. **注册**页启动 1 轮验证  
4. 日志出现 `Round SUCCESS`（及可选 delivery 完成）

更细的操作步骤：[docs/USAGE.md](docs/USAGE.md)  
设置项与环境变量：[docs/CONFIGURATION.md](docs/CONFIGURATION.md)

### 4. 成功日志示例

```text
SSO cookie found (152 chars)
grok2api auto pipeline completed: web_created=1 build_created=1 ...
Round N SUCCESS! Duration: 28.0s transport=http turnstile=local_solver
```

## 稳定长跑推荐配置

| 项 | 值 | 说明 |
|----|----|------|
| 注册传输后端 | `protocol` | 无业务 Chrome，批跑更稳 |
| Turnstile | `external` + 本地 Solver | 禁止回退注册浏览器 |
| 并发 Worker | `1` | 先稳再谈并发 |
| 每轮间隔 | `0`–`30`s | 风控紧时加大 |
| 注册后 Web 激活 | 关 | 仅 browser 路径有意义 |
| grok2api 自动上传 | 按需开 | 本机需可达管理端 |
| Chat probe | 要「有 chat 才入库」时开 | 无权限仍会本地存 SSO |
| CPA 热载 | 按需开 | 与 grok2api 可独立或同时启用 |
| 出口代理 | 与 Solver 一致 | Docker 勿写容器内 `127.0.0.1` 当宿主机代理 |

更多组合见 [docs/CONFIGURATION.md](docs/CONFIGURATION.md#3-推荐组合)。

## 如何判断「这一轮算不算成功」

| 日志 | 含义 | 是否算出号成功 |
|------|------|----------------|
| `Round N SUCCESS` | SSO 已采集并落库 | **是** |
| `grok2api auto upload failed: ...` | 即时交付失败 | 否，但出号仍成功 |
| `Build conversion reported failed=1; retrying once` | 转换瞬时失败，正在重试 | 观察下一行 |
| `durable retry completed` | 后台补传成功 | 交付最终成功 |
| `chat probe passed` | 当前凭证可 chat | 号池质量 OK |

**注册成功 ≠ 即时上传成功 ≠ chat 可用。**  
交付失败时不要重注册；完整故障表见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 项目结构

```
grok-register/
├── app.py
├── config.py
├── requirements.txt
├── requirements-solver.txt
├── core/                 # 注册、激活、邮箱、浏览器、交付
├── services/             # 本地 Turnstile Solver 托管
├── api/                  # REST + WebSocket
├── static/               # 前端 SPA
├── templates/
├── docs/
│   ├── USAGE.md
│   ├── CONFIGURATION.md
│   ├── TROUBLESHOOTING.md
│   └── screenshots/
├── scripts/
├── tests/
└── data/                 # 运行时生成（勿提交）
```

## 技术栈

Flask · Flask-SocketIO · DrissionPage · curl_cffi · SQLite · 原生 ES Module 前端

## 测试

```bash
python -m unittest discover -s tests -v
```

## 注意事项

- 默认绑定本机；绑定非本机地址必须显式 `--allow-remote`
- 账号、Token、密码仅存本地 `data/`，请勿提交公开仓库
- 资料/环境异常时会在 `data/diagnostics/` 写诊断文件
- CPA / grok2api 交付默认关闭；开启后注意凭证目录权限
- 提交 Issue 前请脱敏，清单见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md#10-提交-issue-时的脱敏清单)

## 致谢

- 本项目在社区讨论与既有开源方案启发下完成二次开发 / Web 化改造
- 感谢 [LINUX DO](https://linux.do/) 社区
- 协议注册思路参考：[LINUX DO · 协议注册讨论](https://linux.do/t/topic/2594078)
- 上游参考：[AaronL725/grok-register](https://github.com/AaronL725/grok-register)

## License

[MIT](LICENSE)
