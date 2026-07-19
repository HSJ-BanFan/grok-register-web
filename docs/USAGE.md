# 使用指南

日常操作手册：导入邮箱、配置推荐路径、跑注册、读日志、导出结果与补传。

相关文档：

- 快速安装与项目概览：[README.md](../README.md)
- 设置项与环境变量：[CONFIGURATION.md](CONFIGURATION.md)
- 排障：[TROUBLESHOOTING.md](TROUBLESHOOTING.md)

> **仅供个人学习与自用测试。** 请遵守 xAI、邮箱服务商条款与当地法律。

---

## 1. 启动控制台

```bash
pip install -r requirements.txt
# 协议路径建议同时安装本地 Solver
pip install -r requirements-solver.txt
python -m camoufox fetch

python app.py
```

默认打开 `http://localhost:5000`（或 `http://127.0.0.1:5000`）。

```bash
python app.py --port 8080
python app.py --host 0.0.0.0 --port 5000 --allow-remote   # 仅限可信网络
```

Git 仓库**不包含**作者本机的 `data/`、SQLite 设置、邮箱 Token、SSO、浏览器 Profile 或 grok2api 密码。克隆后必须自行导入邮箱并完成设置。

---

## 2. 导入 Microsoft 账号 / 配置临时邮箱

### Microsoft（别名批量）

1. 打开 **邮箱** 页
2. 粘贴或上传账号，格式：

```text
邮箱----密码----ClientID----RefreshToken
```

3. 确认解析报告中有效行数，再导入账号库

Microsoft 会按「每账号最大别名数」生成 `邮箱` / `邮箱+1@域名` … 等别名。

### 临时邮箱

在 **设置 → 邮箱服务** 选择 DuckMail / YYDS / Cloudflare / Cloud Mail，并填好 API 凭证。  
临时邮箱通常 `max_aliases=1`：同一地址失败会重试，耗尽后自动换下一个。

邮箱 provider **只负责创建地址和读验证码**；注册页、OTP、资料、Turnstile、SSO 与交付始终走同一套注册管线。

---

## 3. 选择注册路径

| 路径 | 适用 | 说明 |
|------|------|------|
| **协议（推荐）** | 日常长跑、无界面 | HTTP 发码/验码/提交/SSO；业务 Chrome 不需要 |
| **浏览器** | 本机调试、观察页面 | 完整页面自动化；Linux 无桌面用 Xvfb，勿硬开无头 |
| **自动** | 当前映射到协议 | 见设置页说明 |

协议路径强烈建议：

- Cloudflare 人机验证 = **仅外置**
- 本地求解器 URL = `http://127.0.0.1:5072`（安装 `requirements-solver.txt` 后由应用托管）
- 并发注册数 = **1**

两层「浏览器」不要混：

| 层 | 作用 | 协议 + 本地 Solver |
|----|------|-------------------|
| 注册浏览器 | 打开账号注册页的业务 Chrome | **不需要** |
| Solver 浏览器 | Camoufox 只解 Turnstile | **需要**（子进程） |

---

## 4. 配置 Cloudflare 人机验证（Turnstile）求解器

设置页 **注册后端与人机**：

1. 协议路径下选 **仅外置**
2. 填本地求解器地址（默认 `http://127.0.0.1:5072`）
3. 点 **测试连接**；需要时可 **启动 / 停止**

也可使用 YesCaptcha：填 Key 后优先云端打码，一般不再自动起本地求解器。

不要把本地求解器暴露到公网。

---

## 5. 配置交付（可选）

注册成功后的交付入口是 `upload_registered_sso`，可独立或同时启用：

| 后端 | 作用 | 默认 |
|------|------|------|
| **grok2api** | Web 导入 SSO → Build 转换；失败 durable 补传 | 关 |
| **CPA** | mint OAuth →（可选）chat probe（硬门=chat 权限；`model` 为首字段软标记）→ 热载 `xai-*.json` | 关 |

建议：

- 要进 grok2api 号池：开「自动导入 Web 并转换 Build」
- 只要有对话权限的号再入库：开 **对话可用性探测**
- 要热载 CLIProxyAPI：开 CPA；probe 建议开，避免污染热池
- 两个都开时：CPA 成功后 grok2api 失败**只记警告**，整轮交付仍算成功（CPA 为主）

填写 grok2api 管理端地址 / 账号密码，并确认本机可访问。  
详细键值见 [CONFIGURATION.md](CONFIGURATION.md)。

---

## 6. 启动注册任务与实时日志

1. **注册** 页设置目标轮数 / 启动任务
2. 看 WebSocket 实时日志
3. 成功轮次常见形态：

```text
SSO cookie found (...)
grok2api auto pipeline completed: web_created=1 build_created=1 ...
Round N SUCCESS! Duration: 28.0s transport=http turnstile=local_solver
```

### 推荐长跑组合

```text
注册后端 = 协议
Cloudflare 人机验证 = 仅外置 + 本地求解器
并发注册数 = 1
关闭「注册后打开 grok.com 做 Web 激活」
需要时开启 grok2api 和/或 CPA
```

| 项 | 推荐值 |
|----|--------|
| 注册传输后端 | `protocol` |
| Cloudflare 人机验证 | `external` + 本地求解器 |
| 并发注册数 | `1` |
| 每轮间隔 | `0`–`30` 秒（风控偏紧时加大） |
| 注册后 Web 激活 | 关 |
| grok2api 上传 | 需要时开启 |
| 对话可用性探测 | 仅在「有对话权限才入库」时开启 |
| CPA | 需要时开启 |

---

## 7. 结果页：SSO、账号、chat 探测

**结果** 页可：

- 查看 KPI（成功数、今日 SSO 等）
- 复制 / 导出 SSO 与账号密码
- 查看 chat 探测无权限记录（探测失败不等于注册失败）

导出格式与目录在设置页 **导出与存储** 配置。

---

## 8. 如何判断「这一轮算不算成功」

| 日志 | 含义 | 是否算出号成功 |
|------|------|----------------|
| `Round N SUCCESS` | SSO 已采集并落库 | **是** |
| `grok2api auto upload failed: ...` | 即时交付失败 | 否，但出号仍成功 |
| `Build conversion reported failed=1; retrying once` | 转换瞬时失败，正在重试 | 观察下一行 |
| `durable retry completed` | 后台补传成功 | 交付最终成功 |
| `chat probe passed` | chat 权限可用；若 `model` 为 `grok-4.5*-build-free` 则 model_ok | 号池质量 OK |
| `chat probe ... 403/429` | 无权限或限流 | SSO 仍在；稍后重试 |
| `unexpected model: ...` | chat 可用但 model 首字段不是 free Build | **不挡上传**；仅质量告警 |

**注册成功 ≠ 即时上传成功 ≠ chat 可用。**

交付类失败常见原因：

- `当前没有可用的 grok_web 出口节点` → 检查 grok2api 出口
- `rate_limited` / `slow_down` / HTTP 429 → 降频，等 durable 补传
- `resource-exhausted` → 上游拥塞，与注册无关

即时上传失败时：**不要重注册**；等 durable 补传或手动导入 SSO。

---

## 9. 失败与补传

| 状态 | 含义 | 你要做什么 |
|------|------|------------|
| 注册失败（无 `Round SUCCESS`） | 本轮未拿到可用 SSO | 看日志分类；必要时换出口 / 降并发 |
| 注册成功 + 上传失败 | SSO 已在本地 | 等后台 durable retry；或结果页导出后手动导入 |
| chat probe 拒绝 | 无 chat 权限或限流 | SSO 仍本地保存；有权限后再开上传 / 重试 probe |
| CPA 进 dead 目录 | mint 成功但 chat 暂不可用 | 勿当热池号；稍后回捞 |

服务重启后，未完成的 grok2api 交付会由补传 Worker 继续处理。

---

## 10. 批量补激活（历史 SSO / CF 出口）

适用于历史 SSO 缺少 TOS/生日，或共享 CF 出口失效：

1. **结果** 或 **注册** 页 → 批量补激活  
2. 流程：注入 SSO → TOS → 生日 → Web 健康检查  
3. 刷新 grok2api 出口节点 `grok-register-web`  
4. **不**重新注册，**不**重新 convert Build  

说明：

- 同一次任务里通常只需手点第一道 Cloudflare  
- 注册 Worker 会在同一出口上复用已建立的 `cf_clearance` 等上下文；不会伪造或跨出口复用  
- 设置里的「注册后打开 grok.com 做 Web 激活」**仅 browser 路径**；协议路径用 HTTP 完成 TOS/生日  
- grok.com 托管挑战无法保证全自动，故默认不在每轮注册后打开 grok.com

---

## 11. 备份与迁移

| 路径 | 内容 | 注意 |
|------|------|------|
| `data/` | SQLite、日志、诊断、导出 | **勿提交公开仓库** |
| 设置中的导出目录 | SSO / 账号导出文件 | 含敏感信息 |
| CPA `cpa_auth_dir` | 热载凭证 | 权限建议 `0600`；勿提交 git |

**不要**直接复制别人的数据库文件。其中可能含 Token、SSO、已占用 alias 与不适合当前机器的设置。

迁移本机时：打包 `data/`（加密保管）+ 记录 Python/Chrome/代理/设置截图，在新机器按 [CONFIGURATION.md](CONFIGURATION.md) 重建环境。

---

## 12. Linux / 代理补充

- 无物理桌面时用 `scripts/run_with_xvfb.sh` 跑有头 Chrome（浏览器路径），不要硬开无头  
- Docker 内代理不要写容器自己的 `127.0.0.1` 当宿主机代理；用网关 / `host.docker.internal` / 服务名  
- 更细的代理与环境变量见 [CONFIGURATION.md](CONFIGURATION.md)；故障表见 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
