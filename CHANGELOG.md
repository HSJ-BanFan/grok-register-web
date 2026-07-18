# 更新日志

本文档记录 Grok 自动注册平台的重要功能变化、问题修复和验证结果。

版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

（暂无）

## [v0.4.0] - 2026-07-18

本版本把 **协议注册 + 本地 Camoufox Turnstile Solver** 收成可部署能力：Solver 入库托管、OTP/SSO 批跑稳定性、设置页按路径显隐，并纳入 CPA / IMAP 交付与注册节奏控制。

### 新增

- **本地 Turnstile Solver vendoring**：`services/turnstile_solver/`（Camoufox / Chromium HTTP API）+ `services/solver_manager.py` 子进程托管；默认 `http://127.0.0.1:5072`
- 应用启动按设置自动拉起本地 Solver（未填 YesCaptcha、Turnstile 非「仅浏览器」、URL 为本机回环）；退出时回收子进程
- 设置 API：`/api/settings/turnstile-solver/{status,start,stop,restart,test}`；设置页「测试连接 / 启动 / 停止」
- 可选依赖 `requirements-solver.txt`（主 `requirements.txt` 保持轻量）
- Solver 任务级代理：`/turnstile?proxy=` 与注册出口对齐；客户端对 loopback 使用 `trust_env=False`，避免系统代理劫持本机 Solver
- 设置页「注册后端与人机」按路径融合说明：浏览器 / 协议 / 自动三选一，相关字段随路径显隐
- **可配置注册间隔** `registration_interval_seconds`（轮次间等待）
- **grok2api 上传前 chat 可用性探测**（权限不足时跳过推送，本地仍保存 SSO）
- 可选 **CPA（CLIProxyAPI）** 交付：SSO → device OAuth mint → chat probe → 热载 `xai-*.json`；设置页默认关闭
- Microsoft **IMAP XOAUTH2** 收信：`M.C…` 消费令牌在 Graph/Outlook REST 不可用时回落 IMAP
- 注册交付可同时或独立启用 CPA 与 grok2api；CPA 成功后 grok2api 失败不拖垮整轮

### 修复

- Cloud Mail / xAI 验证码：优先从主题解析 `SpaceXAI confirmation code: XXX-XXX`，避免 HTML/CSS 伪码（如 `PER100`）抢码
- 协议批跑 **duplicate SSO**：curl_cffi 多域 cookie 清理 + 每轮纯 HTTP **重建 session**，避免上一号 SSO 残留
- Docker / headless 下 SOCKS 代理改走 Chromium `--proxy-server`，补充容器启动参数与可配置启动超时
- 单元测试对齐 IMAP refresh 顺序、cookie 清理与双交付语义

### 验证结果

| 项目 | 结果 |
|------|------|
| 协议 + Cloud Mail + 本地 Solver（仅外置） | 连续 `3/3` 成功；`transport=http turnstile=local_solver` |
| 同配置扩展批跑 | 可达 `10/10` 成功（无 duplicate SSO / 无错误 OTP） |
| 单轮耗时 | 约 25–30s（含 Solver ~10s） |
| grok2api | 无 chat 权限时 probe `403`，本地 SSO 仍入库（预期行为） |

### 部署说明（协议 + 本地 Solver）

推荐组合（无界面注册主流程）：

```text
registration_backend=protocol
turnstile_provider=external          # 禁止回退注册 Chrome；仍可用本地 Camoufox Solver
turnstile_solver_url=http://127.0.0.1:5072
browser_proxy=<与注册相同的出口>
```

说明：**本地 Solver 的 Camoufox 只解 Turnstile，不是注册浏览器。** 仅当机器完全不能运行任何浏览器二进制时，才需要 YesCaptcha 替代 Solver。

### 相关提交

- 本 tag 覆盖 `v0.3.0` 之后至发布点的协议硬化、Solver 托管、设置 UI 与交付能力
- 含已合入主干的：`feat: add configurable registration interval`、`feat: probe grok2api chat availability`，以及 CPA/IMAP（PR #12）等

## [v0.3.0] - 2026-07-17

蓝白 Aurora 运维台 UI，以及此前已合入主干的协议 HTTP 注册后端与多邮箱 Provider 能力的版本锚点。

- 协议 HTTP Worker（实验）、多临时邮箱 Provider
- 结果管理 KPI / 折叠面板与设置分区
- 详见 README「注册后端实验架构」与界面预览

## [v0.2.0] - 2026-07-15

本次更新集中提升连续注册、Cloudflare 上下文复用、SSO 身份隔离、验证码邮件容错，以及 grok2api 导入转换的可恢复性。

### 新增

- 注册 Worker 会捕获并复用 `grok.com` 的 `cf_clearance`、`__cf_bm` 和浏览器 User-Agent
- 每轮注册结束后清理完整 Cookie、缓存和 xAI 身份存储，再仅恢复 Grok Cloudflare 上下文，避免旧 SSO 串号
- 新增持久化 `sso_identities` 指纹账本，并在成功提交事务内执行原子去重
- 新增 grok2api 交付状态：`pending`、`uploading`、`success`、`failed`
- 新增后台持久化补传 Worker；服务重启后会继续处理未完成的 Web 导入和 Build 转换
- 新增 Linux `scripts/run_with_xvfb.sh`，使用虚拟显示器运行普通有头 Chrome
- 新增注册入口 Cloudflare、代理错误页和普通挑战页分类及诊断文件

### 修复

- 重复 SSO 不再覆盖 grok2api Web 账号名称，也不会被注册器标记为成功
- 重复 SSO 和其他可恢复重试不再占用 `max_rounds` 的目标完成名额
- 并发 Worker 同时获得相同 SSO 时，由数据库事务阻止重复提交
- SSO 去重记录不再因删除注册结果或重置 alias 而丢失
- 验证码轮询安全下限统一为 10 次，覆盖 Outlook 邮件延迟到达场景
- grok2api Build 转换出现瞬时失败时自动重试一次
- grok2api 整体不可用或两次转换均失败时，记录失败原因并交由后台补传
- 最大 alias 数设置会同步到现有账号，并正确重新计算账号状态
- 注册流程跨标签页识别 SSO、成功 URL、Existing account 和资料页状态
- Linux/容器启动前检查代理可达性，日志自动移除代理用户名和密码
- 环境拦截会保存 `data/diagnostics/signup-*.json/.png`，停止任务并释放 alias，不消耗重试预算

相关问题：

- [Issue #3](https://github.com/HSJ-BanFan/grok-register-web/issues/3)
- [Issue #4](https://github.com/HSJ-BanFan/grok-register-web/issues/4)

### 验证结果

| 项目 | 结果 |
|------|------|
| 自动化测试 | `100 passed` |
| 连续真实注册 | `5/5` 成功 |
| 新注册 SSO | 5 个指纹全部唯一 |
| grok2api Web/Build | 5 组账号全部双向关联并处于 `active` |
| 硬化后单账号回归 | 注册、Web 导入、Build 转换全部成功 |
| 持久化补传 | 人为标记失败后真实补传成功，状态恢复为 `success` |

### 相关提交

- `2aff19a` `feat: reuse registration Cloudflare context`
- `671c5bb` `fix registration SSO dedupe and delayed verification mail`
- `e20f939` `retry transient Build conversion failures`
- `649562c` `harden retry accounting identity ledger and delivery recovery`
- `405a030` `Merge registration hardening and durable recovery`

## [v0.1.0] - 2026-07-15

本次更新集中处理社区反馈的验证码发码 403、OTP 后资料页卡住、注册成功误判失败，以及 grok2api 导入和转换过程不可见等问题。

相关问题：[Issue #1](https://github.com/HSJ-BanFan/grok-register-web/issues/1)

### 修复

#### 验证码发码 403

- 默认关闭旧版自定义 JS stealth，不再伪造 `navigator.webdriver`、`navigator.languages` 和 `navigator.plugins`
- 明确识别注册页的 `[permission_denied] HTTP 403`、请求过多等拒绝状态
- 发码被拒绝时立即停止当前流程并保留 alias/retry 预算，不再继续等待一封不会产生的验证码邮件
- 强化并发任务的 alias 租约和发码节流，降低多个浏览器会话互相抢占账号的概率

对照验证中，关闭旧版 stealth 后，重启运行的 26 次发码请求均被 xAI 接受；修复前的验证日志中则没有成功接受的发码请求。公网 IP 是否在实验期间发生变化无法仅由仓库日志绝对证明，因此网络出口仍属于外部变量。

相关提交：

- `a82cfb8` `fix: handle verification 403 and concurrent registration safely`
- `252f7d5` `fix: disable xAI-incompatible stealth patches by default`
- `4cdab89` `fix: finish xAI 403 wrap-up and harden post-code diagnostics`

#### OTP 与资料提交

- 验证码邮件改为按目标 alias、发码时间和邮件 ID 精确匹配，避免读取旧邮件或其他 alias 的验证码
- OTP 确认后要求资料表单稳定存在，避免页面切换过程中提前进入填写逻辑
- Cookie banner 只处理一次，避免重复移除或点击导致 React 表单重新挂载
- 禁止强制启用 disabled 的资料提交按钮；只有 Turnstile token 有效且按钮真正 enabled 时才提交
- 识别 `Existing account found`，将邮箱已有 xAI 账号与普通资料页卡住区分开
- 识别 Turnstile 后由站点自动提交并跳转到 account/grok 页面，避免账号实际成功但程序继续等待按钮并判定失败

相关提交：

- `7bddc63` `fix: diagnose profile submit responses and require enabled button`
- `032ebe9` `fix: avoid repeated cookie-banner remounts during profile submit`
- `8a8def3` `fix: match verification emails to exact aliases`
- `1dde860` `fix: require stable profile form after OTP confirmation`
- `ff1057d` `fix: classify existing xAI accounts during profile flow`
- `b80eb4e` `fix: recognize automatic profile completion and continue SSO pipeline`

#### 成功状态与任务租约

- 注册成功、SSO 已提取后，上传 grok2api 期间不再因 alias 租约已完成清理而误报 `Alias lease was lost`
- 成功账号会正常写入 registration 结果并将 alias 标记为 `used`

相关提交：

- `c7163d8` `fix: suppress terminal alias lease false warnings`

#### grok2api 自动管线

- 增加 Web 导入开始、完成和账号定位日志
- 增加 Build 转换开始、完成、创建、失败和同步结果日志
- Web 即时同步失败与 Build 转换结果分开记录，避免 `sync_failed=1` 被误认为整轮注册失败

相关提交：

- `9df8910` `feat: log grok2api import and Build conversion outcomes`

### 新增诊断

资料提交阶段新增以下网络诊断日志：

```text
Profile network diagnostic: email=... method=POST status=... failed=... url=... body=...
```

该日志用于确认资料阶段是否真正发出了 POST、服务端返回了什么状态，以及页面跳转是否发生。单条 `failed=True` 不代表整轮注册必然失败：页面导航可能中断旧请求，最终仍应结合最终 URL、SSO cookie 和 `Round SUCCESS` 判断。

资料页异常时，程序还会在 `data/diagnostics/` 保存状态 JSON 和页面截图。提交公开 Issue 前必须删除邮箱 RefreshToken、验证码、SSO、Cookie 和服务密码。

### 验证结果

本次变更完成时的验证结果：

| 项目 | 结果 |
|------|------|
| 自动化测试 | `65 passed` |
| 注册并发 | 单 Worker |
| 验证码发码 | xAI 接受 |
| OTP 获取与填写 | 成功 |
| 资料提交 | 成功 |
| 最终状态 | `Round 1 SUCCESS` |
| SSO 提取 | 成功 |
| grok2api Web 导入 | `created=1` |
| grok2api Build 转换 | `created=1`、`failed=0`、`synced=1` |

真实单 Worker 验证耗时约 47.6 秒。验证使用的邮箱、验证码、SSO 和 grok2api 凭据未写入仓库。

### 已验证环境

| 项目 | 版本或配置 |
|------|------------|
| 操作系统 | Windows 11 x64 |
| Python | 3.12.10 |
| Chrome | 150.0.7871.101 |
| DrissionPage | 4.1.0.9 |
| Flask | 3.1.3 |
| Flask-SocketIO | 5.6.1 |
| requests | 2.33.1 |
| curl_cffi | 0.15.0 |
| 浏览器模式 | 有头 |
| 注册并发 | `1` |
| 浏览器 Profile | 临时隔离目录 |
| 网络 | 本地直连，无浏览器代理 |

这些是本次验证环境，不是所有环境的强制要求。公网 IP 信誉、xAI 页面版本、Turnstile 状态、Chrome 版本和邮箱 OAuth 状态仍会影响实际结果。

### 已知限制

- xAI 和 Cloudflare 的外部风控无法由仓库代码保证完全一致
- Render 等数据中心环境的 IP、无头 Chrome、Linux sandbox、临时磁盘和资源限制与已验证环境不同
- `requirements.txt` 中除 DrissionPage 外仍使用兼容版本范围，未来安装可能产生依赖漂移
- grok2api 的 `127.0.0.1` 地址只代表程序所在机器；部署到云容器后不会自动连接用户电脑上的服务
- Cloudflare 托管挑战仍可能需要人工处理

环境复现和常见错误处理方式见 [README.md](README.md)。
