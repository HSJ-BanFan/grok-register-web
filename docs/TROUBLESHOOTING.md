# 排障手册

先分清 **注册失败** 与 **交付失败**，再对症处理。  
日常操作见 [USAGE.md](USAGE.md)，配置见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 1. 注册失败 vs 交付失败

| 现象 | 类型 | 处理原则 |
|------|------|----------|
| 没有 `Round N SUCCESS`，无可用 SSO | **注册失败** | 查发码、收信、Turnstile、环境拦截 |
| 有 `Round SUCCESS`，但有 `auto upload failed` | **交付失败** | **不要重注册**；等 durable 补传或手动导入 |
| `chat probe` 403/429 | **质量/权限** | SSO 已在本地；稍后 probe 或开权限后再入库 |

判读表：

| 日志 | 含义 | 出号成功？ |
|------|------|------------|
| `Round N SUCCESS` | SSO 已落库 | **是** |
| `grok2api auto upload failed` | 即时交付失败 | 出号仍成功 |
| `Build conversion reported failed=1; retrying once` | 转换瞬时失败，重试中 | 看下一行 |
| `durable retry completed` | 补传成功 | 交付最终成功 |
| `chat probe passed` | 可 chat | 号池质量 OK |

---

## 2. 验证码 403 / 收不到信

| 日志或现象 | 实际含义 | 建议处理 |
|------------|----------|----------|
| `[permission_denied] HTTP 403` | xAI 拒绝发码；邮箱里不会有新验证码 | 确认未恢复旧 stealth；降并发；等风控；检查公网出口。**不是** Outlook 收信故障 |
| Microsoft token 刷新成功但 Graph/Outlook 401 | opaque MSA token 无 REST 邮件权限 | 看是否落到 `api=imap`；确认邮箱与 refresh 同一 MSA |
| 一直无验证码、IMAP 也无 | 邮件延迟或别名不匹配 | 验证码轮询至少 10 次量级；确认 alias 精确匹配 |
| `invalid-validation-code` / 码像 `PER100` | 从 HTML/CSS 误抽码 | 使用 subject-first OTP 版本；Cloud Mail 主题形如 `SpaceXAI confirmation code: WKT-B4B` |

---

## 3. Turnstile / Solver

| 现象 | 含义 | 处理 |
|------|------|------|
| Solver 测试连接失败 | 本机 `:5072` 未起或被代理劫持 | 设置页启动 Solver；loopback 客户端应 `trust_env=False` |
| Solver 超时后回退浏览器 | `turnstile_provider=auto` 且外置失败 | 协议部署改为 `external`；确认 Solver 与任务代理 |
| 只有 YesCaptcha 仍失败 | 云端任务失败或站点变化 | 查 Key/余额；必要时本机 Solver |
| mock solver | 假 token | **不能**过真实 CF |

---

## 4. 代理与 Docker 网络

| 现象 | 含义 | 处理 |
|------|------|------|
| `Browser proxy is not reachable from this process/network namespace` | 进程连不上代理 | Docker 勿用容器内 `127.0.0.1` 指宿主机代理；用网关 / `host.docker.internal` / 服务名 |
| 浏览器启动超时 / SOCKS 下 Chrome 不起 | 沙箱、shm 或错误 `set_proxy` | 使用较新代码；SOCKS 写 `socks5://…`；增大 `GROK_REGISTER_BROWSER_START_TIMEOUT` |
| `SIGNUP_ENVIRONMENT_BLOCKED` | CF 硬拦截或代理错误页 | 释放 alias 不耗重试；Xvfb；检查代理命名空间；看 `data/diagnostics/signup-*.json/.png` |

---

## 5. Microsoft Graph → IMAP 回落

正常路径：Graph/REST 不可用时回落 **IMAP XOAUTH2**。

- 日志应出现 `api=imap` 或 REST 失败后的 IMAP 尝试  
- 若 refresh 与邮箱地址不匹配，IMAP 也会失败  
- 程序不会在一次已发码的注册中途切换邮箱 provider  

---

## 6. grok2api 转换 / 出口节点 / 限流

| 日志 | 含义 | 处理 |
|------|------|------|
| `sync_failed=1` 但后续 `build_created=1 failed=0` | Web 同步抖了一下，Build 已成功 | **整轮可算交付成功** |
| `Build conversion failed for Web account N` | Web→Build 失败（已重试） | 查 grok2api 日志；等 durable 补传 |
| `当前没有可用的 grok_web 出口节点` | 无可用 Web 出口 | 检查 egress 启用与 `grok-register-web` |
| `device?error=rate_limited` / `slow_down` | Device OAuth 限流 | 降并发/加间隔；依赖补传 |
| `resource-exhausted` / chat 429 | 上游模型拥塞 | 与注册无关；稍后 probe |
| `grok2api chat probe permission denied: HTTP 403` | 无 chat 权限 | **注册仍成功**；有权限后再上传 |
| `CPA chat probe failed` / 进 `cpa_dead_dir` | mint 成功但 chat 暂不可用 | 勿当热池号；延迟重试或回捞 |

即时转换失败但 `Round SUCCESS`：**不要重注册**。

---

## 7. duplicate SSO

| 现象 | 含义 | 处理 |
|------|------|------|
| 连续轮次 `duplicate SSO` | 上一号 SSO cookie 残留 | 使用含 session 重建 / jar 级清理的协议 Worker |
| 重复 SSO 被拒绝提交 | 身份账本去重 | 属预期；不占用成功名额（视版本策略） |

---

## 8. 浏览器注册页文案类错误

| 现象 | 含义 | 处理 |
|------|------|------|
| `未找到邮箱注册入口按钮` | 页面未到预期状态 | 有头观察；确认可访问 `accounts.x.ai`；保存诊断 |
| `未找到最终注册表单或完成注册按钮` | OTP 后表单不稳，或已自动完成 / Existing account | 看最终 URL 与诊断；新版本会识别自动完成 |
| `Profile network diagnostic: ... failed=...` | 捕获到的网络事件，**不等于**整轮失败 | 以最终 URL 与是否拿到 SSO 为准 |

---

## 9. 环境差异与云平台

自动化结果还取决于：

1. xAI 页面版本、Turnstile、公网 IP 信誉  
2. Chrome / Python / 依赖版本  
3. 桌面会话、沙箱、内存  
4. 邮箱 OAuth 与 alias 占用状态  
5. SQLite 中的并发、代理、交付设置  
6. grok2api 是否从本机可达  

**同一份源码 ≠ 同一运行环境。** 排障时先用单 Worker 基线复现，不要同时改代理、并发、浏览器和邮箱。

### Render / 普通云函数

**不推荐**跑注册 Worker：

- 数据中心 IP 更易 403 / 风控  
- 无头 Chrome、sandbox、shm 与已验证 Windows 有头环境不同  
- 临时文件系统不适合长期 SQLite / Profile  
- 容器内 `127.0.0.1:21434` 不是宿主机 grok2api  

优先：可控制出口、Chrome 与持久磁盘的 Windows 主机。

---

## 10. 提交 Issue 时的脱敏清单

删除或打码：

- 邮箱 RefreshToken、密码、ClientID  
- 验证码、SSO、Cookie  
- grok2api / CPA 密码与完整凭证文件  
- 代理用户名密码  

建议提供：

- 系统 / Python / Chrome 版本  
- 注册路径（protocol / browser）、Worker 数、是否有头  
- 公网出口类型（直连 / 家宽 / 机房）  
- 完整错误前后日志  
- `data/diagnostics/` 中已脱敏的 JSON / 截图  

---

## 11. 快速对照总表

| 日志或现象 | 实际含义 | 建议处理 |
|------------|----------|----------|
| `[permission_denied] HTTP 403` | xAI 拒发码 | 降并发、换/等出口；非收信故障 |
| `SIGNUP_ENVIRONMENT_BLOCKED` | CF / 代理错误页 | 诊断文件；Xvfb；代理命名空间 |
| `Browser proxy is not reachable...` | 代理不可达 | 修正 Docker/宿主机地址 |
| 浏览器启动超时 / SOCKS 挂起 | 沙箱或 set_proxy | `socks5://`；增大启动超时 |
| Graph/Outlook 401 无验证码 | 应用 IMAP 回落 | 看 `api=imap` |
| `CPA chat probe failed` | chat 暂不可用 | dead 目录回捞 |
| `grok2api chat probe ... 403` | 无 chat 权限 | 本地 SSO 保留 |
| `invalid-validation-code` / `PER100` | 误抽码 | subject-first OTP |
| `duplicate SSO` | session 串号 | 升级协议 session 重建 |
| Solver 超时回退浏览器 | auto + 外置失败 | 改 `external` |
| `sync_failed=1` | Web 同步瞬时失败 | 看 Build 是否成功 |
| `Build conversion failed...` | 转换失败 | 出口/限流；等补传 |
| `当前没有可用的 grok_web 出口节点` | 无 Web 出口 | 检查 egress |
| `rate_limited` / `slow_down` | OAuth 限流 | 降频；补传 |
| `auto upload failed` + `Round SUCCESS` | 仅交付失败 | **勿重注册** |
| Render 运行 `-1` | 容器运行环境问题 | 先本地 Windows 复现 |
