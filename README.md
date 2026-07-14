# Grok 自动注册平台

Web 可视化平台，用于批量管理 Hotmail/Outlook 邮箱别名并自动化 Grok (x.ai) 账号注册。  
支持验证码自动获取、浏览器自动化注册、SSO 采集，以及注册成功后自动写入 grok2api（Web 导入 + Build 转换），导入后可直接在 grok2api 中使用。

> **仅供个人学习与自用测试。** 请遵守 xAI / Microsoft 服务条款与当地法律。

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
python app.py --host 0.0.0.0 --port 5000   # 仅限可信网络
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

- 默认绑定 `127.0.0.1`；`--host 0.0.0.0` 仅限可信网络  
- 需要本机已安装 Chrome / Chromium  
- 账号、Token、密码仅存本地 `data/`，请勿把运行时数据提交到公开仓库  
- Cloudflare 托管挑战无法保证全自动，请用「推荐用法」分流  

## License

[MIT](LICENSE)
