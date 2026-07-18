# core · 设计决策

`core/` 是注册、邮箱、浏览器与交付后端的运行时核心。本文记录影响行为选择的决策，而不是 API 清单。

## 目标与非目标

**目标**

- 在可复现的前提下，把「邮箱 → 注册 → SSO → 可选交付」串成一条可租约、可重试的管线
- 兼容导入的 Microsoft 消费账号（opaque MSA token）与 Graph 授权账号
- 让 Docker / SOCKS / 无桌面场景的浏览器启动可预期
- 交付后端可插拔，默认不改变既有安装行为

**非目标**

- 不在本仓库捆绑官方 Docker Compose 或 systemd pool keeper
- 不保证 Cloudflare 托管挑战 100% 无人值守
- 不把 CPA 热池调度做成进程内守护（仅提供设置与导出原语）

## 架构概览

```text
RegistrationEngine
├── browser | protocol worker
├── EmailManager          # Graph / Outlook REST / IMAP
├── BrowserManager        # Chrome + HTTP/SOCKS
└── upload_registered_sso # 交付扇出
    ├── CPA export (opt-in)
    └── grok2api Web→Build (opt-in)
```

相关实现：`register.py` / `registration/`、`email_manager.py`、`browser.py`、`cpa_export.py`、`grok2api_client.py`、`database.py`。

## 设计决策

### D1 · SOCKS 走 Chromium `--proxy-server`，不走 `set_proxy`

| | |
|--|--|
| **背景** | DrissionPage `set_proxy` 主要面向 HTTP(S)。在 headless Docker 上对 `socks5://` 可能挂起启动线程。 |
| **决策** | 仅当 scheme 为 `http`/`https` 时尝试 `set_proxy`；`socks*` 统一规范化为 `socks5://host:port` 并设置 `--proxy-server`。同时为容器补 `--no-sandbox`、`--disable-dev-shm-usage` 等，启动超时默认 90s（`GROK_REGISTER_BROWSER_START_TIMEOUT`）。 |
| **后果** | SOCKS 与 HTTP 代码路径分叉；日志里看到的是规范化后的 proxy URL。 |
| **替代方案** | 外部透明代理把 SOCKS 转 HTTP — 运维成本更高，且无法修复容器 Chromium 沙箱问题。 |

### D2 · Microsoft 收信：refresh 多 audience + IMAP 回落

| | |
|--|--|
| **背景** | 导入账号常见 refresh 形态为 `M.C…`。空 scope 刷新得到 opaque access token，Graph `Mail.Read` / Outlook REST 常 401；同一 token 却可通过 IMAP XOAUTH2 读信。 |
| **决策** | `refresh_token` 按顺序尝试：空 scope（live / consumers）→ IMAP scope → Graph `.default` → Graph `Mail.Read`。`_detect_mail_api` 先 probe REST，opaque token 再 probe/标记 `imap`。验证码路径增加 `_imap_get_code`。 |
| **后果** | 单次 refresh 可能多次 token endpoint 调用；日志更吵，但对异构导入账号更稳。 |
| **替代方案** | 强制用户重新 OAuth 到 Graph — 对已买/已导入号不现实。 |

### D3 · CPA 交付默认关闭，与 grok2api 并列

| | |
|--|--|
| **背景** | 部分部署用 CLIProxyAPI 热载 `xai-*.json`，与 grok2api Web/Build 管线不同。 |
| **决策** | 新增 `cpa_export`；设置 `cpa_auto_export` 默认 `false`。`upload_registered_sso` 扇出：CPA 与 grok2api 可独立启用。 |
| **后果** | 上游安装零行为变化；开启后需挂载 `cpa_auth_dir` 并接受 probe 延迟。 |

### D4 · CPA 成功后，grok2api 失败不拖垮交付

| | |
|--|--|
| **背景** | 双开时若任一侧失败都 raise，会把「已热载 CPA」的账号标成交付失败并反复补传。 |
| **决策** | CPA 视为主路径之一：CPA 已成功时，grok2api 异常记 `grok2api_error` 并返回；CPA 失败仍 raise。两侧都关则返回 `None` 并打统一日志。 |
| **后果** | 与「grok2api-only」时代的失败语义不同；补传逻辑应以启用的后端为准。 |

### D5 · Chat probe 后再进热池；失败进 dead

| | |
|--|--|
| **背景** | 新 mint 的号常见短暂 `permission-denied`，直接热载会污染 CPA 池。 |
| **决策** | 默认 `cpa_probe_chat=true`：延迟（45s）+ 重试（2×60s），多出口（socks5h/socks5/直连）探测 `cli-chat-proxy` chat completions。失败写入 `cpa_dead_dir` 且 `disabled=true`。提供 `revive_dead_auths` 回捞。 |
| **后果** | 单号交付变慢；应用侧应接受 dead 目录与延迟。 |
| **替代方案** | 跳过 probe — 设置可关，但热池质量下降。 |

### D6 · 热池自动补号只暴露阈值，不内置 timer

| | |
|--|--|
| **背景** | 是否补号取决于部署（systemd、K8s CronJob、人工）。 |
| **决策** | 设置键 `cpa_pool_*` 仅供外部读取；本仓库不附 unit。 |
| **后果** | 打开「自动补号」而无外部 timer 时不会发生任何自动注册。 |

## 技术选型

| 点 | 选择 | 理由 |
|----|------|------|
| 浏览器自动化 | DrissionPage + 本机 Chrome | 与现有 browser 路径一致；容器靠 flags 而非换栈 |
| MSA IMAP | stdlib `imaplib` + XOAUTH2 | 无新依赖；opaque token 可用 |
| CPA mint | 复用 `sso_to_build_credential` | 与 grok2api Build 凭证同源，避免第二套 OAuth |
| Chat probe | `curl_cffi` | 与协议注册路径一致的 TLS 指纹 |

### D7 · 本地 Turnstile Solver 子进程托管（vendoring）

| | |
|--|--|
| **背景** | 协议 Worker 已有 YesCaptcha / 本地 HTTP Solver 客户端，但仓库原先不附带真实 Solver 进程，设置页 `5072` 默认离线。 |
| **决策** | 将参考工程的 `api_solver` 迁入 `services/turnstile_solver/`；`solver_manager` 用 `subprocess` 托管。依赖放在 `requirements-solver.txt`，主依赖不捆绑 Camoufox。生命周期 B：满足「需本地 Solver」时 `app.py` 后台 `start_async`，设置页可 start/stop，退出 `stop()`。仅管理 loopback URL。任务可带 `proxy=` 与注册出口对齐；客户端访问 loopback 时 `trust_env=False`。 |
| **后果** | 未装 solver 依赖时 Web 仍可启动，自动/手动 start 会给出明确错误；填了 YesCaptcha 或 provider=browser 时不起子进程。协议 + 本地 Solver = 无注册 Chrome 的合法部署；「仅外置」禁止的是注册 Chrome 回退，不是 Camoufox Solver。 |
| **替代方案** | 懒启动（首次 solve 时再起）— 首轮延迟更大；纯 YesCaptcha — 适合绝对不能跑浏览器二进制的环境。 |

### D8 · 协议批跑：每轮重建 session + OTP 主题优先

| | |
|--|--|
| **背景** | curl_cffi cookie jar 在多域（`.x.ai` / `.grok.com`）保留上一账号 SSO；仅 `cookies.clear()` 或按 name 删不干净，导致后续轮次 duplicate SSO。Cloud Mail 正文 HTML 含 `PER100` 等 CSS 片段时，旧验证码提取会盖过主题里的 `WKT-B4B`。 |
| **决策** | 纯 HTTP 每轮 `build_protocol_session` 重建，仅移植 CF cookie；`clear_identity_cookies` 走 jar 级多域删除。`extract_verification_code` 优先 subject 的 SpaceXAI/xAI 模式，并 strip HTML。 |
| **后果** | 批跑不再串号；验证码提取对 Cloud Mail 更稳。 |

## 已知限制

1. IMAP 路径扫最近约 40 封邮件，极高噪邮箱可能漏码或变慢。
2. Chat probe 依赖外网可达 `cli-chat-proxy.grok.com`；公司网/防火墙可能导致误进 dead。
3. 仓库无官方 Compose；Docker 文档只覆盖代理与 Chrome 启动约束。
4. `cpa_pool_enabled` 无进程内执行器，文档必须写清依赖外部 timer。
5. 完整「xAI 发码 → IMAP 取码」e2e 仍依赖真实注册轮次与邮箱库存。
6. 本地 Solver 首次需下载 Camoufox（约 100MB）；自动 start 失败累计 3 次后停止重试，需手动 restart。

## 安全考量

- CPA / grok2api 凭证与 SSO 同属高敏感；`write_auth` 使用临时文件 + `os.replace`，文件模式 `0600`。
- 日志对 proxy URL 做凭据脱敏（`redact_proxy_url`）。
- 不要把 `cpa_auth_dir` / `data/grok.db` 提交到公开仓库。
- 设置页与 API 默认本机绑定；远程监听必须 `--allow-remote`。

## 变更历史

| 日期 | 变更 | 理由 |
|------|------|------|
| 2026-07-18 | D8：协议批跑 session 重建 + jar 级多域 SSO 清理；OTP subject-first（SpaceXAI） | 连续注册 duplicate SSO；Cloud Mail 主题码被 HTML 伪码抢走 |
| 2026-07-18 | D7：本地 Turnstile Solver vendoring + solver_manager 生命周期 B | 设置页 5072 默认离线；协议路径需要可复现的真实外置求解能力 |
| 2026-07-18 | PR #12：Docker/SOCKS 浏览器、MSA IMAP OTP、可选 CPA 导出与双交付语义；补本设计文档 | 容器 SOCKS 挂起、导入号 Graph 401、CPA 热载需求；verify-change 要求大改有 DESIGN 留痕 |
