# 配置参考

设置页字典、环境变量、推荐组合，以及与 grok2api / CPA 的对接约定。

相关文档：

- 日常操作：[USAGE.md](USAGE.md)
- 排障：[TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- 项目概览：[README.md](../README.md)

设置分区保存后对**后续**注册任务立即生效，一般无需重启服务（本地 Solver 子进程除外，可用设置页启停）。

---

## 1. 设置页字典

### 1.1 邮箱服务

| 设置项 | 说明 | 建议 |
|--------|------|------|
| 注册邮箱服务 | Microsoft / DuckMail / YYDS / Cloudflare / Cloud Mail | 先配好凭证再开跑 |
| 每账号最大别名数 | 每主邮箱成功上限（Microsoft） | `5` |
| 每别名最大重试 | 单个别名失败重试次数 | `2`–`3` |

| Provider | 创建与收信方式 | 必要配置 |
|----------|----------------|----------|
| `microsoft` | 账号库 OAuth RefreshToken；按能力走 Graph / 旧 Outlook REST / **IMAP XOAUTH2**；支持加号别名 | 邮箱页授权或导入账号 |
| `duckmail` | Mail.tm 风格接口 | `duckmail_api_base`；私有服务可填 API Key |
| `yyds` | YYDS API | API Key 或 JWT |
| `cloudflare` | 兼容 `cloudflare_temp_email` 路径，并回退 Mail.tm 风格 | API Base、鉴权、路径、默认域名 |
| `cloud_mail` | 管理员 token 建用户 + `emailList` 收信 | API Key，或管理员邮箱 + 密码 |

邮箱 API 请求会沿用 `browser_proxy`（若已配置），便于与注册出口一致。

**Microsoft 收信自动探测顺序（摘要）**

1. refresh：空 scope → consumers 空 scope → IMAP scope → Graph `.default` → Graph `Mail.Read`
2. access token 探测 Graph / Outlook REST；opaque MSA 再试 IMAP XOAUTH2
3. 验证码轮询按探测结果调用对应 API

导入的 `M.C…` 消费 refresh 经常无法用 Graph；日志应出现 `api=imap` 或 REST 失败后的 IMAP 回落。

### 1.2 注册后端与人机

| 设置项 | 说明 | 建议 |
|--------|------|------|
| 注册传输后端 | `browser` / `protocol` / `auto` | **长跑用 protocol**；调试用 browser |
| 协议 Turnstile 来源 | 仅外置 / 外置优先可回退 / 仅注册浏览器 | 协议部署用 **仅外置** |
| 本地 Turnstile Solver | Camoufox HTTP API，默认 `:5072` | 协议路径推荐 |
| YesCaptcha Key | 云端打码 | 无本地浏览器二进制时用 |
| 浏览器模式 | 有头 / 无头 | 仅 browser 路径；Linux 无桌面用 Xvfb |
| 出口代理 | `http://…` 或 `socks5://…` | 注册 / 邮箱 / Solver 任务共用 |

`auto` 当前映射到 `protocol`。

`turnstile_provider=external` 时**不允许**回退注册 Chrome；本地 Solver 仍可使用。`auto` 才允许浏览器回退。

### 1.3 并发与节奏

| 设置项 | 说明 | 建议 |
|--------|------|------|
| 注册并发 Worker 数 | 同时运行的 worker | **`1`** |
| 每轮注册间隔（秒） | 一轮结束后再等待 | 风控紧时加大；`0` 表示不等待 |

### 1.4 grok2api 接入

| 设置项 | 说明 | 默认/建议 |
|--------|------|-----------|
| 注册成功后上传到 grok2api | Web 导入 + Build 转换 | 默认关；进号池时开 |
| Chat 可用性探测 | 上传前 mint 并测 chat | 默认关；要「有 chat 才入库」时开 |
| grok2api 地址 | 管理端 URL | 如 `http://127.0.0.1:21434` |
| 管理员用户名 / 密码 | 管理端登录 | 自建实例凭据 |
| Probe 代理 / 延迟 / 重试 | chat probe 网络与节奏 | 可与注册代理一致 |
| 注册后打开 grok.com 做 Web 激活 | 仅 **browser** 路径 | **关** |

瞬时失败会自动重试并走 durable 补传。probe 无权限/限流时跳过导入，**本地仍保留 SSO**。

### 1.5 CPA 接入与补号

| 键 / 设置 | 含义 | 默认 |
|-----------|------|------|
| `cpa_auto_export` | 注册成功后导出到 CPA | 关 |
| `cpa_probe_chat` | mint 后 chat probe | 开（开启 CPA 时建议保持） |
| `cpa_auth_dir` | 热池目录 | `/cpa/auths` |
| `cpa_dead_dir` | chat 失败归档 | `/cpa/auths-chat-dead` |
| `cpa_proxy` | mint/probe 代理；空则回落 `browser_proxy` | 空 |
| `cpa_probe_delay_sec` / `retries` / `retry_gap_sec` | 新号权限延迟与重试 | `45` / `2` / `60` |
| `cpa_pool_enabled` | 热池自动补号（外部 timer 读） | 关 |
| `cpa_pool_min` / `max` / `register_rounds` | 阈值与每次注册轮数 | `5` / `5` / `8` |

热池自动补号**不会**在本进程内轮询；需外部 timer（如 systemd）读设置并调 `/api/register/start|resume|pause`。  
容器使用时把宿主机 CPA `auths/` 挂进 `cpa_auth_dir`；凭证文件模式建议 `0600`。

### 1.6 导出与存储

| 设置项 | 说明 | 建议 |
|--------|------|------|
| 数据导出格式 | TXT / JSON | 批量粘贴用 TXT；程序消费用 JSON |
| 数据导出目录 | 落盘路径 | 默认 `./data`；勿提交敏感导出 |

### 1.7 双交付规则

入口：`upload_registered_sso`（`core/grok2api_client.py`）。

| 组合 | 行为 |
|------|------|
| 都关 | 只本地保存结果 |
| 只开 grok2api | Web 导入 → Build 转换；失败记状态并补传 |
| 只开 CPA | mint + 可选 probe；probe 失败写 `cpa_dead_dir` 并抛错 |
| 都开 | CPA 成功后，grok2api 失败**只警告**；整轮交付仍算成功（CPA 为主） |

---

## 2. 环境变量

### 2.1 进程与监听

| 变量 | 含义 |
|------|------|
| （CLI）`--host` / `--port` | 监听地址端口；非本机需 `--allow-remote` |
| `GROK_REGISTER_BROWSER_PATH` | Windows 上显式指定 `chrome.exe` |
| `GROK_REGISTER_BROWSER_HEADLESS` | 是否无头（浏览器路径） |
| `GROK_REGISTER_CONCURRENCY` | 并发（Xvfb 脚本会强制 `1`） |
| `GROK_REGISTER_BROWSER_START_TIMEOUT` | 浏览器启动超时秒数，默认 `90` |

### 2.2 注册后端与代理

| 变量 | 含义 |
|------|------|
| `GROK_REGISTER_BACKEND` | `protocol` / `browser` / `auto` |
| `GROK_PROXY` / 设置中的出口代理 | 注册与相关 HTTP 出口 |
| `GROK_REGISTER_TURNSTILE_PROVIDER` | `auto` / `external` / `browser` |
| `GROK_REGISTER_ALLOW_BROWSER_FALLBACK` | 是否允许回退注册浏览器 |
| `TURNSTILE_SOLVER_URL` | 本地 Solver URL |
| `YESCAPTCHA_KEY` / `GROK_REGISTER_YESCAPTCHA_KEY` | 云端打码；配置后优先云端 |

### 2.3 本地 Solver

| 变量 | 含义 |
|------|------|
| `GROK_REGISTER_SOLVER_BROWSER` | `camoufox`（默认）/ `chromium` / `chrome` / `msedge` |
| `GROK_REGISTER_SOLVER_THREADS` | 浏览器线程数，默认 `1` |
| `GROK_REGISTER_SOLVER_PROXY` | `true` 时 Solver 接受任务级 proxy |

应用在「需要本地 Solver」时会后台拉起子进程：未填 YesCaptcha、Turnstile 非「仅浏览器」、URL 为本机回环。退出时回收本应用拉起的子进程。

```bash
# 单独前台调试 Solver
python services/turnstile_solver/start.py --browser_type camoufox --thread 2 --port 5072 --debug
curl http://127.0.0.1:5072/
```

`scripts/mock_turnstile_solver.py` 只返回假 token，不能过真实 Cloudflare。

### 2.4 协议路径示例

```text
GROK_REGISTER_BACKEND=protocol
GROK_PROXY=http://127.0.0.1:7897
TURNSTILE_SOLVER_URL=http://127.0.0.1:5072
GROK_REGISTER_TURNSTILE_PROVIDER=external
# YESCAPTCHA_KEY=...
GROK_REGISTER_ALLOW_BROWSER_FALLBACK=false
```

相关模块：`core/registration/backend.py`、`turnstile.py`、`protocol_worker.py`、`services/solver_manager.py`。

---

## 3. 推荐组合

### A. 本机协议长跑（默认推荐）

```text
registration_backend = protocol
turnstile_provider   = external
turnstile_solver_url = http://127.0.0.1:5072
concurrency          = 1
web activation       = off
grok2api / CPA       = 按需
proxy                = 与 Solver 一致（需要时）
```

### B. 本机浏览器调试

```text
registration_backend = browser
browser              = 有头
concurrency          = 1
web activation       = off（除非你要观察 grok.com）
```

Linux 无桌面：

```bash
bash scripts/run_with_xvfb.sh --host 0.0.0.0 --port 5000 --allow-remote
```

脚本会强制有头 + 并发 1 + 虚拟显示器。

### C. 协议 + YesCaptcha（无本地浏览器二进制）

```text
registration_backend = protocol
turnstile_provider   = external
YESCAPTCHA_KEY       = <key>
# 不依赖本地 Camoufox
```

### D. CPA 热池 + 外部 timer

```text
cpa_auto_export = true
cpa_probe_chat  = true
cpa_auth_dir    = <挂载的 auths>
cpa_pool_*      = 按热池策略
```

外部 timer 读取池水位后调用 `/api/register/*`；本仓库不附带 systemd unit。

### E. 保守复现基线（先跑通 1 个号）

| 项目 | 推荐值 |
|------|--------|
| 注册传输后端 | `protocol` |
| 协议 Turnstile | `external` + 本地 Solver |
| 并发 | `1` |
| JS stealth | 默认关闭 |
| 注册后 Web 激活 | 关 |
| 自动上传 / CPA | 先关，确认出号后再开 |

不要直接复制他人的 `data/*.db`。

---

## 4. 与 grok2api 的对接约定

1. **管理端**：填写可从注册机访问的 base URL + 管理员账号  
2. **Web 导入**：SSO 以 `grok_web` 账号写入  
3. **Build 转换**：`POST .../accounts/web/convert-to-build`；瞬时 `failed` 会客户端重试一次  
4. **出口节点**：转换需要可用的 **`grok_web` 出口**；注册侧会维护 `grok-register-web` 上下文（UA + CF cookies）  
5. **chat probe**（可选）：上传前 mint Build 凭证并测 chat；403/限流时跳过导入，SSO 仍本地  
6. **补传**：`pending` / `uploading` / `success` / `failed`；后台 durable worker 重启后续跑  

常见交付错误的处理见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)。

---

## 5. Docker / SOCKS 要点

- SOCKS 写 `socks5://host:port`（`socks5h://` 会规范为 `socks5://`）  
- SOCKS **不**走 DrissionPage `set_proxy`，统一 Chromium `--proxy-server`  
- 容器/root 会加 `--no-sandbox`、`--disable-dev-shm-usage` 等  
- 容器内不要用 `127.0.0.1` 指宿主机代理；用网关 / `host.docker.internal` / Compose 服务名  
- 本仓库当前不附带官方 `docker-compose.yml`

---

## 6. 注册后端架构（摘要）

```text
RegistrationEngine
├── browser   浏览器流程（调试可选）
└── protocol  HTTP/gRPC-Web Worker（推荐）
    ├── curl_cffi Session
    ├── SignupParameterDiscovery
    ├── ProtocolRegistrationBackend
    ├── ExternalTurnstileProvider（YesCaptcha / 本地 Solver）
    └── BrowserTurnstileProvider（仅允许回退时）
```

协议路径要点：

1. 优先纯 HTTP + impersonate  
2. Turnstile 外置优先  
3. 协议路径用 HTTP 做 TOS/生日；「打开 grok.com 激活」仅 browser  
4. CF 硬拦截时 abort 且不消耗 alias 重试  
5. 批跑每轮重建 session，避免 duplicate SSO  
6. 成功日志：`transport=http|browser turnstile=... sso_follow=...`

设计细节见 [`core/DESIGN.md`](../core/DESIGN.md)。
