# Grok 自动注册平台

Web 可视化平台，用于批量管理 Hotmail/Outlook 邮箱别名并自动化 Grok (x.ai) 账号注册。  
支持验证码自动获取、浏览器自动化注册、SSO 采集，以及注册成功后自动写入 [grok2api](https://github.com/)（Web 导入 + Build 转换）。

> **仅供个人学习与自用测试。** 请遵守 xAI / Microsoft 服务条款与当地法律。  
> 本仓库默认不包含任何真实账号、Token 或数据库。

## 功能概述

- **批量账号导入** — 文本粘贴或文件上传，格式：`邮箱----密码----ClientID----RefreshToken`
- **全自动注册** — 打开注册页 → 填邮箱 → 收验证码 → 填资料 → 过 Turnstile → 提取 SSO
- **Hotmail 别名** — 每主邮箱最多 N 个成功别名（`邮箱` / `邮箱+1@域名` …）
- **失败重试** — 单个别名失败自动重试；耗尽后创建替换别名
- **英文/中文 UI 兼容** — 适配 `Sign up with email` / `Complete sign up` 等英文文案
- **实时日志** — WebSocket 推送进度与级别
- **结果管理** — SSO / 账号密码查看、复制、导出
- **grok2api 自动上传** — 注册成功后：
  1. 导入 Grok Web（SSO）
  2. 自动 convert 为 Grok Build  
  日志：`grok2api auto pipeline completed: web_created=1 build_created=1 ...`
- **浏览器代理** — 可选 `http://127.0.0.1:7897` 等，降低注册页风控概率
- **旧账号批量补激活（可选）** — 需要 Cloudflare 出口上下文时，对历史 success SSO 批量补 TOS / 生日 / CF；**同一次任务里通常只需手点第一道 CF**
- **OAuth2 邮箱授权** — Azure 一键授权（⚠️ 未充分测试）
- **Mail.tm 降级** — IMAP 连续失败时切换临时邮箱（⚠️ 未充分测试）

## 推荐用法（重要）

```
1) 日常批量注册（默认推荐）
   关闭「注册后打开 grok.com 做 Web 激活」
   开启「自动导入 Web 并转换 Build」
   可选：配置浏览器代理
   → 注册 → SSO → 上传 Web → Build 转换
   → 不打开 grok.com，一般不弹 Cloudflare 人机

2) 需要 Web / CF 出口时
   「结果管理」或「注册控制」→ 批量补激活
   → 你只处理第一道 Cloudflare
   → 同一浏览器会话内后续账号复用 cf_clearance

3) 中途失败 / 浏览器被重启
   可能还要再点一次 CF，不是每个账号都点
```

**说明：** grok.com 的托管挑战（`Verify you are human`）无法稳定全自动过。  
因此默认 **不在每轮注册后打开 grok.com**；上传与 Build 转换不依赖浏览器 CF Cookie。

## 环境要求

| 依赖 | 版本要求 |
|------|----------|
| Python | 3.10+ |
| Chrome / Chromium | 最新稳定版 |
| 操作系统 | Windows / macOS / Linux |

可选：本地 HTTP 代理（如 Clash `7897`），用于浏览器出口。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动平台

```bash
python app.py
```

默认打开 `http://localhost:5000`。

```bash
# 指定端口
python app.py --port 8080

# 局域网（仅可信网络）
python app.py --host 0.0.0.0 --port 5000
```

### 3. 使用流程

1. **导入账号** — `邮箱----密码----ClientID----RefreshToken`
2. **设置**（建议）
   - 开启：自动导入 Web 并转换 Build
   - 关闭：注册后打开 grok.com 做 Web 激活
   - 可选：浏览器代理，例如 `http://127.0.0.1:7897`
   - 填写 grok2api 地址 / 管理员账号密码
3. **注册控制** → 启动注册任务，看实时日志
4. **结果管理** → 导出 SSO / 账号

上传失败不会丢本地注册结果，错误会打到日志。

### 4. 旧账号批量补激活

适用于：历史 SSO 缺 TOS/生日，或共享 CF 出口失效。

1. 读取本地 `registrations` 中 success SSO  
2. 逐个切换 SSO → TOS → 生日 → Web 健康检查  
3. 刷新 grok2api 节点 `grok-register-web`（User-Agent + Cloudflare Cookie）  
4. **不**重新注册，**不**重新 convert Build  

日志示例：

```
Batch Web reactivation started for N historical SSO account(s)
Switching SSO cookie before Grok Web activation...
Opening grok.com / Reusing existing Cloudflare clearance...
Grok Web activation completed for user@example.com: ...
Updating grok2api Grok Web egress Cloudflare context...
```

## 项目结构

```
grok-register/
├── app.py
├── config.py
├── requirements.txt
├── README.md
│
├── core/
│   ├── register.py              # 注册引擎
│   ├── account_activation.py    # Web 激活（TOS/生日/CF）
│   ├── batch_activation.py      # 历史账号批量补激活
│   ├── grok2api_client.py       # Web 导入 / Build 转换 / 出口同步
│   ├── email_manager.py         # IMAP / OAuth2 收信
│   ├── browser.py               # DrissionPage + 可选代理
│   ├── oauth.py
│   └── database.py
│
├── api/
│   ├── accounts.py
│   ├── register.py              # 含 /api/register/reactivate
│   ├── results.py
│   ├── settings.py
│   └── websocket.py
│
├── static/                      # 原生 JS SPA
├── templates/index.html
├── turnstilePatch/              # Turnstile 辅助扩展
├── tests/
└── data/                        # 运行时生成（勿提交）
    ├── grok.db
    └── ...
```

## 数据库（SQLite）

- **accounts** — 主邮箱与 OAuth2 凭证  
- **aliases** — 别名与使用状态  
- **registrations** — 每轮注册结果（邮箱、密码、SSO、耗时）  
- **settings** — 系统配置键值  

别名：`邮箱`（index 0）、`邮箱+1@域名` …；只计成功数，失败可被替换。

## 主要设置项

| 设置项 | 说明 | 建议 |
|--------|------|------|
| 每账号最大别名数 | 每主邮箱成功上限 | 5 |
| 每别名最大重试 | 单个别名失败重试 | 2–3 |
| 浏览器模式 | 有头 / 无头 | 调试用有头 |
| 浏览器代理 | 如 `http://127.0.0.1:7897` | 有代理建议填 |
| Turnstile | 自动 / 手动 | 自动 |
| 注册后打开 grok.com 做 Web 激活 | 每轮是否做人机激活 | **关闭（推荐）** |
| 自动导入 Web 并转换 Build | 写 grok2api | **开启** |
| grok2api 地址 / 账号 / 密码 | 管理端 | 本地或自建实例 |

## 成功日志示例

```
SSO cookie found (152 chars)
grok2api auto pipeline completed: web_created=1 web_updated=0 build_created=1 linked=0 skipped=0 failed=0
Round N SUCCESS! Duration: 28.0s
```

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python Flask |
| 实时 | Flask-SocketIO（threading） |
| 浏览器 | DrissionPage |
| 数据库 | SQLite（WAL） |
| 前端 | 原生 ES Module，无构建 |
| 邮箱 | OAuth2 + IMAP（XOAUTH2） |

## 测试

```bash
python -m unittest discover -s tests -v
```

## 开源前请注意

**不要提交：**

- `data/`（含 `grok.db`、浏览器配置、导出 SSO）
- 任何 `*outlook*.txt` / 含 RefreshToken 的导入文件
- 真实 grok2api 密码、代理账号

建议仓库根目录 `.gitignore` 至少包含：

```gitignore
data/
__pycache__/
*.pyc
.env
*.db
*.db-shm
*.db-wal
*outlook*.txt
accounts_*.txt
sso.txt
registered_accounts.*
```

本项目当前运行时数据与密钥仅在本机 `data/` 与设置库中；开源请使用干净工作区或重新 `git init` 后只添加源码。

## 注意事项

- 默认绑定 `127.0.0.1`；`--host 0.0.0.0` 仅限可信网络  
- 需要本机已安装 Chrome/Chromium  
- 敏感数据默认只存本地 SQLite  
- Cloudflare **托管挑战**无法保证全自动；请用「推荐用法」分流  
- OAuth2 / Mail.tm 未充分测试  

## License

[MIT](LICENSE)
