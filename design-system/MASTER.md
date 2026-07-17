# Design System: Grok Register

> Inspired by `astrbot_plugin_telegram_forwarder/web` blue-white aurora control plane
> Project: self-hosted Grok 自动注册 / 运维控制台
> Stack: Flask + vanilla HTML/CSS/JS (hash SPA)
> Last decided: 2026-07-17

---

## 1. Product Context

| 维度 | 结论 |
|------|------|
| 产品类型 | Developer Tool / Operations Dashboard |
| 用户 | 个人开发者 / 自建运维，长时间盯注册进度与日志 |
| 核心任务 | 邮箱导入 → 启动注册 → 实时日志/状态 → 结果导出 → grok2api 接入 |
| 使用场景 | 本地桌面；默认浅色蓝白控制台，可选深色 |
| 信息密度 | 中高：表格 + 日志流 + KPI |

---

## 2. Selected Direction

### 主风格：**Blue-White Aurora Control Plane**

| 项 | 选择 | 理由 |
|----|------|------|
| **Style** | 蓝白极光控制台 | 对齐参考项目 Telegram Forwarder Web Admin |
| **Layout** | sticky sidebar + topbar eyebrow/title | 运维台信息架构清晰 |
| **Primary** | `#229ed9` Telegram 蓝 | 品牌主色、主按钮、active nav |
| **Accent** | `#38bdf8` 天蓝 | 渐变第二色、活力提亮 |
| **Dark mode** | 保留，深 slate + 同色系蓝 | 暗光环境可用，但仍是蓝白体系而非霓虹 OLED |

### 明确不选

| 候选 | 不选原因 |
|------|----------|
| Cyberpunk / Neon Cyan OLED | 眩光、与参考控制台气质冲突 |
| 全站 Glassmorphism | 对比难控；仅 toast/header 轻 glass |
| Green run-accent 铺满 chrome | 成功态保留绿色，主交互用蓝 |

---

## 3. Color Tokens

### Light（默认）

| Role | Hex / Value | CSS Variable |
|------|-------------|--------------|
| Background | mesh + `#f4f8fc` | `--bg-base` |
| Surface / Card | `#ffffff` | `--bg-card` |
| Primary | `#229ed9` | `--primary` |
| Accent | `#38bdf8` | `--accent` |
| Ink | `#0f172a` | `--text-primary` |
| Text | `#334155` | `--text-secondary` |
| Muted | `#64748b` | `--text-muted` |
| Success | `#10b981` | `--success` |
| Danger | `#ef4444` | `--error` |
| Warning | `#f59e0b` | `--warning` |

### Dark

深 slate 底 (`#0b1220` / `#111827`)，主交互仍为天蓝系，成功/失败语义不变。

---

## 4. Typography

| 角色 | 字体 |
|------|------|
| UI body | **DM Sans** |
| Titles / KPI | **Space Grotesk** |
| Logs / tokens / mono | **JetBrains Mono** |

---

## 5. Layout

```
bg-aurora (fixed mesh blobs)
└─ app-shell
   ├─ sidebar (brand + grouped nav + status card)
   └─ content-shell
      ├─ topbar (eyebrow + page title + theme/version)
      └─ main-content (page cards)
```

### 组件语言

- Primary button = `linear-gradient(135deg, primary, accent)` + soft blue shadow
- Active nav = 同色渐变 + 白色文字
- Card title left bar = 4px primary→accent pill
- Metric cards = soft corner glow, hover lift 2px
- Log panel stays terminal-dark for contrast

---

## 6. Motion

| 类型 | 规范 |
|------|------|
| Easing | `--ease-out` / `--ease-soft` / `--ease-spring` |
| Duration | 140–320ms 交互；aurora 背景 26–32s 漂移 |
| Page transition | 路由切换 `page-exit` → render → `page-enter`（轻上移 + blur） |
| Stagger | 卡片 40–240ms；KPI tile 60–360ms；表格行 `--row-i * 22ms` |
| KPI count-up | 结果页指标 / 注册仪表盘数字 640–920ms ease-out-cubic；结束轻 `count-pop` |
| Toast / log | toast spring scale；新日志行 `log-in` 左滑入 |
| Fold panels | 大表默认收起；`grid-template-rows` 内敛滑动；展开后内部 `max-height` 滚动；状态记 localStorage |
| Settings | 分区卡片 + choice-card 选项磁贴；密码区 reveal；底部 sticky 操作条 |
| Select | 自定义 `ui-select` listbox；隐藏原生 select 保 `.value`/change；键盘与暗色支持 |
| Reduced motion | `prefers-reduced-motion` 关闭装饰动画、page transition 与 count-up |

---

## 7. Implementation Map

| 文件 | 焦点 |
|------|------|
| `templates/index.html` | app-shell、aurora、topbar、DM/Space fonts |
| `static/css/style.css` | blue-white tokens + shell + page utilities |
| `static/js/components/sidebar.js` | grouped nav + brand head |
| `static/js/app.js` | page eyebrow/title chrome + route transition + mobile drawer |
| `static/js/components/count-up.js` | KPI / 仪表盘数字 count-up |
| `static/js/components/fold.js` | 可收纳面板：结果表 / 账号库 |
| `static/js/components/select.js` | 自定义下拉：`selectFieldMarkup` + `initSelects` |
| `static/js/pages/settings.js` | 设置分区、choice-card、ui-select、collect/save |
| `static/js/pages/*.js` | card-header / card-desc / section-divider 等，少用 inline style |

### 页面工具类（pages 优先用这些，而不是内联 style）

| 类 | 用途 |
|----|------|
| `.card-header` | 标题行 + 右侧 toolbar |
| `.card-desc` | 卡片说明文字 |
| `.section-divider` | 表单分段 |
| `.helper-text` | 字段辅助说明 |
| `.status-dot` | OAuth / 状态点 |
| `.dashboard-head` | 注册仪表盘标题行 |
| `.control-actions` | 任务控制按钮组间距 |
| `.text-accent/success/error/...` | 语义色文字 |
| `.mono` / `.time-cell` / `.sso-cell` | 表格内容排版 |

**不引入** React/Vue/Tailwind 构建链。

---

## 8. One-line Prompt

> Build a blue-white aurora ops console for Grok registration: Telegram-blue primary `#229ed9`, sky accent `#38bdf8`, soft mesh background, sticky sidebar with gradient active nav, Space Grotesk titles, DM Sans UI, compact cards/tables/logs. No neon cyberpunk, no emoji icons.
