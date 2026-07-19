# 文档 / 关于文案刷新草稿

> 状态：**已落地（README / meta / 侧栏 / 设置页 / 关于弹窗 / 文档拆分 / CHANGELOG）**  
> 背景：协议注册 + 本地 Solver + grok2api（Web/Build）+ chat probe + durable 补传已可稳定长跑；需同步自述、使用配置说明与 UI「关于」文案。  
> 日期：2026-07-19  
> 已决：关于正文 = **方案 A**（2026-07-19）  
> 文档拆分：`docs/USAGE.md` · `docs/CONFIGURATION.md` · `docs/TROUBLESHOOTING.md`  
> 未做：chat probe UI 默认值未改

---

## 0. 改写原则

1. **默认路径改成「协议注册」**：浏览器路径保留为调试 / 回退，不再写成唯一主路径。  
2. **交付链路写清楚**：本地 SSO →（可选）chat probe → grok2api Web 导入 → Build 转换 → durable 补传。  
3. **区分三类成功**：注册成功 ≠ 即时上传成功 ≠ chat 可用。  
4. **关于文案短、准、不吹**：不写「全自动无失败」；强调「可稳定长跑 + 失败可补传」。  
5. **版本对齐**：UI 顶栏 `v1.0` 与 CHANGELOG `v0.4.x` 不一致，建议在合入时统一（见文末待决）。

---

## 1. UI「关于」文案（对应截图弹窗）

### 1.1 当前文案（问题）

```text
Grok 自动注册 Web平台: Outlook别名批量注册、SSO 采集, 注册成功后自动导入 grok2api (Web/Build)
```

问题：

- 只提 Outlook，漏了临时邮箱 / 协议注册 / Solver / chat 探测 / 补传  
- 「自动导入」像必定发生，实际默认可能关，且会有瞬时失败  
- 标点与空格不统一  

### 1.2 已定稿主文案（弹窗正文）

**方案 A（已选定）**

```text
本地 Web 控制台：邮箱别名 / 临时邮箱批量注册 Grok，采集 SSO；可选交付到 grok2api（Web 导入 + Build 转换，失败自动补传）和/或 CPA（CLIProxyAPI 热载，chat 探测后入库）。
```

备选（未采用，仅留档）：

- **A′**：`…可选上传 grok2api（Web/Build，失败补传）或导出到 CPA 热池。`
- **B**：产品向一句，含双路径 + grok2api + CPA
- **C**：最短交付一句

### 1.3 弹窗完整结构建议

| 区块 | 文案 |
|------|------|
| 标题 | 关于 |
| 产品名 | Grok Register |
| 副标题 | 本地运维控制台 · Ops Console |
| 正文 | 方案 A |
| 能力点（3–4 条小字） | 见下 |
| 版本 | 与 CHANGELOG 对齐，例如 `v0.4.0`（或发布时的下一版号） |
| 许可 | MIT |
| 仓库 | `https://github.com/HSJ-BanFan/grok-register-web` |
| 免责 | 仅供个人学习与自用测试，请遵守 xAI 与邮箱服务商条款 |

**能力点（弹窗 bullets，可选）**

```text
• 邮箱：Microsoft 别名 / 多种临时邮箱
• 注册：协议 HTTP（推荐）或浏览器自动化
• 人机：本地 Camoufox Solver 或 YesCaptcha
• 交付：SSO 落库 · grok2api（Web/Build + 补传）· CPA（mint + chat probe 热载）
```

### 1.4 其它 UI 短文案同步

| 位置 | 建议文案 |
|------|----------|
| `<meta name="description">` | `Grok 批量注册本地控制台：邮箱别名与多邮箱源、协议/浏览器注册、SSO 采集；可选交付 grok2api（Web/Build）与 CPA 热载。` |
| 浏览器标题后缀 | `Grok Register`（现为「Grok 自动注册工具」，可保留或统一） |
| 侧栏底部 `status-text` | `邮箱 · 注册 · SSO · 交付`（现：`邮箱导入 · 注册任务 · SSO 导出`） |
| 侧栏 badge | `本地控制台`（可保留） |
| 设置页 grok2api 开启项 desc | `Web 导入 + Build 转换；瞬时失败会后台补传。` |
| 设置页 chat probe 开启项 desc | `上传前探测 chat；无权限则跳过导入，SSO 仍本地保存。` |
| 顶栏 version | 与发布版本一致（见待决） |

---

## 2. README 自述 · 重写骨架（草稿）

> 目标：新用户 5 分钟能跑通「协议 + Solver + 可选 grok2api」；旧浏览器路径下沉到附录。

### 2.1 开篇（替换现有首段）

```markdown
# Grok Register

本地 Web 控制台：批量注册 Grok（xAI）账号、采集 SSO，并可选交付到 **grok2api**（Web 导入 + Build 转换，失败补传）和/或 **CPA（CLIProxyAPI）** 热载。

- **邮箱**：Microsoft Outlook/Hotmail 别名，以及 DuckMail / YYDS / Cloudflare Temp Email / Cloud Mail 等临时邮箱
- **注册路径**：HTTP **协议注册**（推荐，无业务 Chrome）或 **浏览器自动化**
- **人机**：本地 Camoufox Turnstile Solver，或 YesCaptcha
- **交付**：SSO 本地落库；可选 grok2api Web→Build（失败 durable 补传）；可选 CPA mint + chat probe 热载
- **状态**：协议长跑已在自用环境验证可稳定使用（含 chat 可用号池）

> **仅供个人学习与自用测试。** 请遵守 xAI、邮箱服务商条款与当地法律。
```

### 2.2 界面预览说明（替换 v0.4.0 旁注）

```markdown
> 当前推荐路径：协议注册 + 本地 Turnstile Solver；结果页提供 KPI、SSO 折叠与 chat 探测统计。
```

### 2.3 功能列表 · 建议增补 / 改写要点

保留现有条目，建议调整措辞：

| 原表述 | 建议 |
|--------|------|
| 全自动注册 · 打开注册页… | **协议注册（推荐）** — HTTP 发码/验码/提交/SSO；可选浏览器路径 |
| grok2api 自动接入 | **grok2api 交付（可选）** — Web 导入 → Build 转换；chat probe 可开；失败 durable 补传 |
| （缺失） | **注册 / 上传 / chat 解耦** — Round SUCCESS 表示出号成功；即时上传失败不推翻注册结果 |
| （缺失） | **日志可观测** — Web 导入、Build 转换、sync_failed、durable retry 分字段输出 |

### 2.4 快速开始 · 推荐顺序（新结构）

```markdown
## 快速开始（推荐：协议 + 本地 Solver）

### 1. 安装主依赖
pip install -r requirements.txt

### 2. 安装本地 Turnstile Solver（协议路径强烈建议）
pip install -r requirements-solver.txt
python -m camoufox fetch

### 3. 启动
python app.py
# 浏览器打开 http://127.0.0.1:5000

### 4. 首次配置（设置页）
1. 注册邮箱服务 + 导入 Microsoft 账号（或配置临时邮箱）
2. 注册传输后端 = 协议
3. Turnstile = 仅外置 + 本地 Solver URL（默认 http://127.0.0.1:5072）
4. 并发 Worker = 1
5. 可选：开启「自动导入 Web 并转换 Build」并填写 grok2api
6. 可选：开启 Chat 可用性探测（要「有 chat 再进池」时打开）

### 5. 跑一轮验证
注册控制 → 启动 1 轮 → 日志出现 Round SUCCESS 与（若开启）delivery 完成
```

浏览器路径、Xvfb、Docker 注意点保留，降级为：

```markdown
## 附录 A · 浏览器注册路径
## 附录 B · Linux Xvfb
## 附录 C · 代理与容器
```

### 2.5 推荐生产配置表（新增，放「主要设置」前）

```markdown
## 稳定长跑推荐配置

| 项 | 值 | 说明 |
|----|----|------|
| 注册传输后端 | `protocol` | 无业务 Chrome，批跑更稳 |
| Turnstile | `external` + 本地 Solver | 禁止回退注册浏览器 |
| 并发 Worker | `1` | 先稳再谈并发 |
| 每轮间隔 | `0`–`30`s | 风控紧时加大 |
| 注册后 Web 激活 | 关 | 仅 browser 路径有意义 |
| grok2api 自动上传 | 按需开 | 本地需可达管理端 |
| Chat probe | 要「有 chat 才入库」时开 | 无权限仍会本地存 SSO |
| 出口代理 | 与 Solver 一致 | Docker 勿写容器内 127.0.0.1 当宿主机代理 |
```

### 2.6 成功判读（新增短节，对应你这次运维经验）

```markdown
## 如何判断「这一轮算不算成功」

| 日志 | 含义 | 是否算出号成功 |
|------|------|----------------|
| `Round N SUCCESS` | SSO 已采集并落库 | **是** |
| `grok2api auto upload failed: ...` | 即时交付失败 | 否，但出号仍成功 |
| `Build conversion reported failed=1; retrying once` | 转换瞬时失败，正在重试 | 观察下一行 |
| `durable retry completed` | 后台补传成功 | 交付最终成功 |
| `chat probe passed` | 当前凭证可 chat | 号池质量 OK |
| `chat probe ... 403/429` | 无权限或限流 | SSO 仍在；稍后重试 probe/补传 |

常见根因（交付失败，不是注册失败）：

- `当前没有可用的 grok_web 出口节点` → 检查 grok2api 出口节点
- `error=rate_limited` / `slow_down` / HTTP 429 → 降频或换出口，等补传
- `resource-exhausted` → 上游模型拥塞，与注册无关
```

### 2.7 常见错误表 · 建议追加行

| 日志或现象 | 实际含义 | 建议处理 |
|------------|----------|----------|
| `Build conversion failed for Web account N` | Web→Build 转换失败（已自动重试） | 看 grok2api 日志；常为无出口节点或上游限流；等 durable 补传 |
| `当前没有可用的 grok_web 出口节点` | grok2api 无可用 Web 出口 | 检查 egress 启用状态与 `grok-register-web` 节点 |
| `device?error=rate_limited` / `slow_down` | Device OAuth 限流 | 降并发/加间隔；依赖 durable retry |
| `auto upload failed` + 随后 `Round SUCCESS` | 交付失败、注册成功 | **不要重注册**；等补传或手动导入 SSO |

---

## 3. 使用与配置文档 · 建议拆分

README 已偏长（约 500 行）。建议拆成：

| 文件 | 职责 | 读者 |
|------|------|------|
| `README.md` | 是什么、快速开始、推荐配置、链接 | 新用户 |
| `docs/USAGE.md` | 日常操作：导入邮箱、开任务、读日志、导出 | 操作者 |
| `docs/CONFIGURATION.md` | 设置项字典 + 环境变量 + 推荐组合 | 部署者 |
| `docs/TROUBLESHOOTING.md` | 从 README「常见错误」迁出并扩展 | 排障 |
| `CHANGELOG.md` | 版本记录（保持） | 全体 |
| `core/DESIGN.md` | 模块设计（保持） | 开发者 |

### 3.1 `docs/USAGE.md` 目录草稿

```markdown
# 使用指南

1. 启动与登录本地控制台
2. 导入 Microsoft 账号 / 配置临时邮箱
3. 选择注册路径（协议推荐）
4. 配置 Turnstile Solver
5. 配置 grok2api / CPA（可选）
6. 启动注册任务与实时日志
7. 结果页：SSO、账号、chat 探测
8. 失败与补传：何时等 durable，何时人工导入
9. 批量补激活（历史 SSO / CF 出口）
10. 备份与迁移（勿提交 data/）
```

### 3.2 `docs/CONFIGURATION.md` 目录草稿

```markdown
# 配置参考

## 设置页字典
### 邮箱服务
### 注册后端与人机
### 并发与节奏
### grok2api 接入
### CPA 接入
### 导出与存储

## 环境变量
（合并 README 中 Solver / Backend / Proxy / Browser Path 表）

## 推荐组合
### A. 本机协议长跑（默认推荐）
### B. 本机浏览器调试
### C. 协议 + YesCaptcha（无本地浏览器二进制）
### D. CPA 热池 + 外部 timer

## 与 grok2api 的对接约定
- 管理端地址 / 账号
- Web 导入 → convert-to-build
- 出口节点 scope=grok_web
- chat probe 语义
```

### 3.3 `docs/TROUBLESHOOTING.md` 目录草稿

```markdown
# 排障手册

1. 注册失败 vs 交付失败
2. 验证码 403 / 收不到信
3. Turnstile / Solver
4. 代理与 Docker 网络
5. Microsoft Graph → IMAP 回落
6. grok2api 转换 / 出口节点 / 限流
7. duplicate SSO
8. 需要提交 Issue 时的脱敏清单
```

---

## 4. 设置页文案微调草稿（可选合入）

| 位置 | 现文案 | 建议 |
|------|--------|------|
| 页眉 helper | `配置邮箱服务、注册后端…` | `配置邮箱、注册路径、人机求解与交付后端。分区保存后对后续任务立即生效。` |
| grok2api 分区说明 | `注册成功后的自动上传…` | `注册成功后的可选交付：Web 导入、Build 转换、chat 探测与后台补传。` |
| 「自动导入…」desc | `成功后直接推到 grok2api。` | `导入 Web SSO 并转换 Build；瞬时失败会自动重试并后台补传。` |
| probe 开启 desc | `上传前 mint…` | `上传前探测 chat。无权限/限流时跳过导入，本地仍保留 SSO。` |
| 默认推荐标记 | 上传默认关、probe 默认关 | **长跑要进 grok2api 号池时**：上传开；**只要有 chat 的号**：probe 开。文档里写清，UI recommend 可改为场景化说明而非唯一默认 |

---

## 5. CHANGELOG 条目草稿（文档版本发布时用）

```markdown
## [Unreleased] 或下一 patch

### 文档

- 同步「协议注册已可稳定长跑」定位：README 默认路径改为协议 + 本地 Solver
- 补充注册成功 / 上传失败 / durable 补传的判读说明
- 补充 grok2api 出口节点缺失、Device OAuth 限流等交付类错误
- 刷新 UI「关于」与 meta 描述，覆盖多邮箱源与可选交付

### UI 文案

- 关于弹窗 / meta / 侧栏状态文案与当前能力对齐
- 版本号与 CHANGELOG 对齐（若本次一并修正）
```

---

## 6. 待你拍板的选项

| # | 问题 | 选项 |
|---|------|------|
| 1 | 「关于」正文 | **已定：A** |
| 2 | 是否拆分 `USAGE` / `CONFIGURATION` / `TROUBLESHOOTING` | **已拆** |
| 3 | UI 版本号 | **已改为 `v0.4.0`** |
| 4 | 协议路径在 README 是否去掉「实验」字样 | **已去掉**，改为「推荐」 |
| 5 | chat probe 是否写成「有 chat 号池」默认推荐 | 文档已写推荐场景；**UI 默认值未改** |

---

## 7. 落地记录

1. ~~定稿「关于」+ meta + 侧栏短文案~~  
2. ~~README 开篇 + 推荐配置 + 成功判读 + 错误表追加~~  
3. ~~拆分 USAGE / CONFIGURATION / TROUBLESHOOTING~~  
4. ~~设置页 desc 微调~~  
5. ~~CHANGELOG + 版本号对齐~~  
6. ~~关于弹窗：点击顶栏版本号打开，正文方案 A~~
