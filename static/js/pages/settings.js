import { api } from '../api.js';
import { showToast } from '../components/toast.js';
import { initSelects, selectFieldMarkup } from '../components/select.js';

const ICONS = {
    gear: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 1v2m0 18v2m-9-11h2m18 0h2m-4.22-5.78-1.42 1.42M6.34 17.66l-1.42 1.42m0-13.84 1.42 1.42m11.32 11.32 1.42 1.42"/></svg>`,
    mail: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>`,
    pulse: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>`,
    browser: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`,
    user: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`,
    upload: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
    server: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/></svg>`,
    folder: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,
    save: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>`,
    reset: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>`,
};

function esc(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function field(id, label, value, {
    type = 'number', min, max, placeholder = '', readonly = false,
    helper = '', mono = false,
} = {}) {
    const attrs = [
        `type="${type}"`,
        `class="form-input${mono ? ' mono' : ''}"`,
        `id="${id}"`,
        `value="${esc(value)}"`,
    ];
    if (min != null) attrs.push(`min="${min}"`);
    if (max != null) attrs.push(`max="${max}"`);
    if (placeholder) attrs.push(`placeholder="${esc(placeholder)}"`);
    if (readonly) attrs.push('readonly');
    if (type === 'password') attrs.push('autocomplete="new-password"');
    return `
        <div class="form-group">
            <label for="${id}">${label}</label>
            <input ${attrs.join(' ')}>
            ${helper ? `<div class="helper-text">${helper}</div>` : ''}
        </div>
    `;
}

function selectField(id, label, current, options, { helper = '', mono = false } = {}) {
    return selectFieldMarkup(id, label, current, options, { helper, mono });
}

function choiceGroup(name, options, current) {
    return `
        <div class="choice-group" role="radiogroup" aria-label="${esc(name)}">
            ${options.map((option) => {
                const checked = String(current) === String(option.value);
                return `
                    <label class="choice-card${checked ? ' is-selected' : ''}${option.recommend ? ' is-recommend' : ''}">
                        <input type="radio" name="${name}" value="${esc(option.value)}" ${checked ? 'checked' : ''}>
                        <span class="choice-indicator" aria-hidden="true"></span>
                        <span class="choice-copy">
                            <span class="choice-title">
                                ${option.title}
                                ${option.recommend ? '<span class="choice-badge">推荐</span>' : ''}
                            </span>
                            ${option.desc ? `<span class="choice-desc">${option.desc}</span>` : ''}
                        </span>
                    </label>
                `;
            }).join('')}
        </div>
    `;
}

function section(icon, title, desc, body, extraClass = '') {
    return `
        <section class="card settings-section ${extraClass}">
            <div class="settings-section-head">
                <div class="settings-section-icon" aria-hidden="true">${icon}</div>
                <div class="settings-section-copy">
                    <h2 class="settings-section-title">${title}</h2>
                    ${desc ? `<p class="settings-section-desc">${desc}</p>` : ''}
                </div>
            </div>
            <div class="settings-section-body">${body}</div>
        </section>
    `;
}

function providerPanel(provider, body) {
    return `<div class="mail-provider-settings" data-provider="${esc(provider)}" hidden>${body}</div>`;
}

export async function render(container) {
    const res = await api('GET', '/api/settings');
    if (!res.success || !res.data) {
        const error = document.createElement('div');
        error.className = 'card';
        const title = document.createElement('div');
        title.className = 'card-title';
        title.textContent = '系统设置加载失败';
        const message = document.createElement('p');
        message.className = 'card-desc';
        message.textContent = '当前配置未被修改。请检查服务状态并刷新页面后重试。';
        error.append(title, message);
        container.replaceChildren(error);
        showToast(res.message || '设置加载失败', 'error');
        return;
    }
    const s = res.data;
    const emailProvider = s.email_provider || 'microsoft';
    const registrationBackend = s.registration_backend || 'browser';
    const turnstileProvider = ['external', 'strict_external'].includes(s.turnstile_provider)
        ? 'external'
        : (s.turnstile_provider || 'auto');
    const headless = s.browser_headless === 'true' ? 'true' : 'false';
    const turnstile = s.turnstile_auto === 'false' ? 'false' : 'true';
    const randomName = s.random_name_enabled === 'false' ? 'false' : 'true';
    const extractNumbers = s.extract_numbers_enabled === 'true' ? 'true' : 'false';
    const passwordMode = s.password_mode === 'manual' ? 'manual' : 'auto';
    const grok2apiUpload = s.grok2api_auto_upload === 'true' ? 'true' : 'false';
    const grok2apiProbe = s.grok2api_probe_chat === 'true' ? 'true' : 'false';
    const cpaAuto = s.cpa_auto_export === 'true' ? 'true' : 'false';
    const cpaProbe = s.cpa_probe_chat === 'false' ? 'false' : 'true';
    const cpaPool = s.cpa_pool_enabled === 'true' ? 'true' : 'false';
    const webActivation = s.grok_web_activation === 'true' ? 'true' : 'false';
    const exportFormat = s.export_format === 'json' ? 'json' : 'txt';
    const sub2Auto = s.sub2api_auto_upload === 'true' ? 'true' : 'false';
    const sub2AutoPause = s.sub2api_auto_pause_on_expired === 'false' ? 'false' : 'true';

    const markup = `
        <div class="settings-page">
            <div class="card settings-hero">
                <div class="settings-hero-main">
                    <div class="card-title">${ICONS.gear} 系统设置中心</div>
                    <p class="card-desc settings-hero-desc">
                        配置邮箱、注册路径、人机求解与交付后端（grok2api / CPA）。分区保存后对后续任务立即生效。
                    </p>
                </div>
                <div class="settings-hero-meta">
                    <span class="settings-pill"><span class="status-dot success"></span>本地生效</span>
                    <span class="settings-pill muted">保存后无需重启服务</span>
                </div>
            </div>

            ${section(ICONS.mail, '邮箱服务', '邮箱服务商只负责创建邮箱和读取验证码；注册页面、OTP、资料与 SSO 始终复用同一流程。', `
                <div class="settings-grid settings-grid-1">
                    ${selectField('s-email-provider', '注册邮箱服务', emailProvider, [
                        { value: 'microsoft', label: 'Microsoft Outlook / Hotmail（导入账号与别名）' },
                        { value: 'duckmail', label: 'DuckMail（自动创建临时邮箱）' },
                        { value: 'yyds', label: 'YYDS Mail（自动创建临时邮箱）' },
                        { value: 'cloudflare', label: 'Cloudflare Temp Email（自动创建）' },
                        { value: 'cloud_mail', label: 'Cloud Mail API（自动创建）' },
                    ], { helper: 'Microsoft 使用账号库 OAuth 凭证；临时邮箱会在注册轮次开始前自动创建并入库。' })}
                </div>

                ${providerPanel('duckmail', `
                    <div class="settings-grid">
                        ${field('s-duckmail-api-base', 'DuckMail API Base', s.duckmail_api_base || 'https://api.duckmail.sbs', { type: 'text', mono: true })}
                        ${field('s-duckmail-api-key', 'DuckMail API Key（可选）', s.duckmail_api_key || '', { type: 'password' })}
                    </div>
                `)}
                ${providerPanel('yyds', `
                    <div class="settings-grid">
                        ${field('s-yyds-api-base', 'YYDS API Base', s.yyds_api_base || 'https://maliapi.215.im/v1', { type: 'text', mono: true })}
                        ${field('s-yyds-api-key', 'YYDS API Key', s.yyds_api_key || '', { type: 'password' })}
                        ${field('s-yyds-jwt', 'YYDS JWT（与 API Key 二选一）', s.yyds_jwt || '', { type: 'password' })}
                    </div>
                `)}
                ${providerPanel('cloudflare', `
                    <div class="settings-grid">
                        ${field('s-cloudflare-api-base', 'Cloudflare 邮箱 API Base', s.cloudflare_api_base || '', { type: 'text', mono: true, placeholder: 'https://temp-mail.example.com' })}
                        ${selectField('s-cloudflare-auth-mode', '鉴权方式', s.cloudflare_auth_mode || 'none', [
                            { value: 'none', label: 'none' },
                            { value: 'query-key', label: 'query-key' },
                            { value: 'bearer', label: 'bearer' },
                            { value: 'x-api-key', label: 'x-api-key' },
                            { value: 'x-admin-auth', label: 'x-admin-auth' },
                        ], { mono: true })}
                        ${field('s-cloudflare-api-key', 'API Key / Admin Password', s.cloudflare_api_key || '', { type: 'password' })}
                        ${field('s-cloudflare-default-domains', '默认域名（逗号分隔）', s.cloudflare_default_domains || '', { type: 'text', mono: true, placeholder: 'mail.example.com, mail2.example.com' })}
                        ${field('s-cloudflare-path-domains', '域名路径', s.cloudflare_path_domains || '/api/domains', { type: 'text', mono: true })}
                        ${field('s-cloudflare-path-accounts', '创建邮箱路径', s.cloudflare_path_accounts || '/api/new_address', { type: 'text', mono: true })}
                        ${field('s-cloudflare-path-token', 'Token 路径', s.cloudflare_path_token || '/api/token', { type: 'text', mono: true })}
                        ${field('s-cloudflare-path-messages', '邮件列表路径', s.cloudflare_path_messages || '/api/mails', { type: 'text', mono: true })}
                    </div>
                `)}
                ${providerPanel('cloud_mail', `
                    <div class="settings-grid">
                        ${field('s-cloud-mail-api-base', 'Cloud Mail API Base', s.cloud_mail_api_base || 'https://mail.meilunaria.dpdns.org', { type: 'text', mono: true })}
                        ${field('s-cloud-mail-api-key', 'Cloud Mail API Key（优先）', s.cloud_mail_api_key || '', { type: 'password' })}
                        ${field('s-cloud-mail-admin-email', '管理员邮箱（无 API Key 时）', s.cloud_mail_admin_email || '', { type: 'text' })}
                        ${field('s-cloud-mail-admin-password', '管理员密码', s.cloud_mail_admin_password || '', { type: 'password' })}
                    </div>
                `)}
            `)}

            ${section(ICONS.pulse, '注册节奏', '控制别名额度、超时与重试，避免打爆邮箱或卡在验证码轮询。', `
                <div class="settings-grid">
                    ${field('s-max-aliases', '每账号最大别名数', s.max_aliases_per_account || 5, { min: 1, helper: '仅 Microsoft 主邮箱使用；临时邮箱固定为 1。' })}
                    ${field('s-code-retries', '验证码轮询次数', s.max_code_retries || 10, { min: 1 })}
                    ${field('s-timeout', '注册超时限制 (秒)', s.registration_timeout || 300, { min: 30 })}
                    ${field('s-registration-interval', '每轮注册间隔 (秒)', s.registration_interval_seconds ?? 300, { min: 0, helper: '一轮结束后再等待此时间领取下一个邮箱；300 秒即每 5 分钟一轮，0 表示不等待。' })}
                    ${field('s-confirm-retries', '确认邮箱重试次数', s.max_confirm_retries || 3, { min: 1 })}
                    ${field('s-alias-retries', '每别名最大重试次数', s.max_retries_per_alias || 3, { min: 1 })}
                    ${field('s-registration-concurrency', '并发注册 Worker 数', 1, { min: 1, max: 1, readonly: true, helper: '稳定模式固定为 1。' })}
                </div>
            `)}

            ${section(ICONS.browser, '注册后端与人机', '选择谁跑注册主流程；相关选项会随路径自动显示。', `
                <div class="settings-field-block">
                    <div class="settings-field-label">注册传输后端</div>
                    ${choiceGroup('registration-backend', [
                        {
                            value: 'browser',
                            title: '浏览器注册',
                            desc: '本机 Chrome / Xvfb 打开注册页，Turnstile 在页面内完成。适合本机调试。',
                            recommend: true,
                        },
                        {
                            value: 'protocol',
                            title: '协议注册',
                            desc: 'HTTP 完成注册，不启业务 Chrome。Turnstile 走本地 Solver 或 YesCaptcha。',
                        },
                        {
                            value: 'auto',
                            title: '自动（优先协议）',
                            desc: '先走协议路径；外置 Turnstile 失败时可按下方策略回退浏览器。',
                        },
                    ], registrationBackend)}
                    <input type="hidden" id="s-registration-backend" value="${esc(registrationBackend)}">
                </div>

                <div class="backend-settings" data-backend-panel="browser">
                    <div class="settings-field-block">
                        <div class="settings-field-label">浏览器运行模式</div>
                        ${choiceGroup('headless', [
                            { value: 'false', title: '有头 / Xvfb', desc: '真实渲染，更不容易被 Cloudflare 拦截。', recommend: true },
                            { value: 'true', title: '无头模式', desc: '占用更低，但更容易被拦截。' },
                        ], headless)}
                    </div>
                    <div class="settings-field-block">
                        <div class="settings-field-label">页面内 Turnstile</div>
                        ${choiceGroup('turnstile', [
                            { value: 'true', title: '自动过验证', desc: '页面内自动求解，适合无人值守。', recommend: true },
                            { value: 'false', title: '手动过验证', desc: '弹窗出现时由人工点击完成。' },
                        ], turnstile)}
                    </div>
                </div>

                <div class="backend-settings" data-backend-panel="protocol">
                    <div class="settings-field-block">
                        <div class="settings-field-label">Turnstile 来源</div>
                        ${choiceGroup('turnstile-provider', [
                            {
                                value: 'external',
                                title: '仅外置求解',
                                desc: '本地 Solver 或 YesCaptcha；失败即退出，不回退注册 Chrome。',
                                recommend: true,
                            },
                            {
                                value: 'auto',
                                title: '外置优先，可回退',
                                desc: '先走本地 Solver / YesCaptcha，失败后再用本机注册浏览器。',
                            },
                            {
                                value: 'browser',
                                title: '仅注册浏览器',
                                desc: '不用外置求解，直接用本机 Chrome 过 Turnstile。',
                            },
                        ], turnstileProvider)}
                        <input type="hidden" id="s-turnstile-provider" value="${esc(turnstileProvider)}">
                    </div>
                    <div class="settings-grid">
                        ${field('s-turnstile-solver-url', '本地 Turnstile Solver', s.turnstile_solver_url || 'http://127.0.0.1:5072', {
                            type: 'text',
                            mono: true,
                            helper: 'Camoufox 仅解验证码，不是注册浏览器。协议部署推荐优先用它。',
                        })}
                        ${field('s-yescaptcha-key', 'YesCaptcha Key', s.yescaptcha_key || '', {
                            type: 'password',
                            helper: '可选云打码。机器不能跑任何浏览器时再填。',
                        })}
                    </div>
                    <div class="solver-health-card" id="solver-health-card" data-state="checking">
                        <div class="solver-health-state">
                            <span class="solver-health-dot" aria-hidden="true"></span>
                            <span id="solver-health-status">检测中</span>
                        </div>
                        <div class="solver-health-detail" id="solver-health-detail" aria-live="polite">正在检查 Solver 连接…</div>
                        <div class="solver-health-actions">
                            <button type="button" class="btn btn-sm btn-secondary" id="test-solver-btn">测试连接</button>
                            <button type="button" class="btn btn-sm btn-primary" id="start-solver-btn">启动本地 Solver</button>
                            <button type="button" class="btn btn-sm btn-secondary" id="stop-solver-btn">停止</button>
                        </div>
                    </div>
                </div>

                <div class="settings-grid settings-grid-1">
                    ${field('s-browser-proxy', '出口代理', s.browser_proxy || '', {
                        type: 'text',
                        mono: true,
                        placeholder: 'http://127.0.0.1:7897 （留空=直连）',
                        helper: '注册、邮箱 API 与本地 Solver 任务共用；协议路径会把代理传给 Solver。',
                    })}
                </div>
            `)}

            ${section(ICONS.user, '身份与密码', '注册资料生成策略，以及别名登录密码如何分配。', `
                <div class="settings-field-block">
                    <div class="settings-field-label">随机姓名生成</div>
                    ${choiceGroup('random-name', [
                        { value: 'true', title: '开启随机生成', desc: '每次注册使用随机姓名，降低指纹重复。', recommend: true },
                        { value: 'false', title: '关闭随机生成', desc: '使用固定/默认姓名策略。' },
                    ], randomName)}
                </div>
                <div class="settings-two-col">
                    <div class="settings-field-block">
                        <div class="settings-field-label">页面数字智能提取</div>
                        ${choiceGroup('extract-numbers', [
                            { value: 'false', title: '禁用提取', desc: '默认关闭，避免误读页面数字。', recommend: true },
                            { value: 'true', title: '启用提取', desc: '尝试从页面文案中智能抽取数字。' },
                        ], extractNumbers)}
                    </div>
                    <div class="settings-field-block">
                        <div class="settings-field-label">别名密码分配模式</div>
                        ${choiceGroup('password-mode', [
                            { value: 'auto', title: '自动随机生成', desc: '每个别名独立随机密码。', recommend: true },
                            { value: 'manual', title: '统一自定义密码', desc: '所有别名使用同一登录密码。' },
                        ], passwordMode)}
                    </div>
                </div>
                <div class="settings-reveal ${passwordMode === 'manual' ? 'is-open' : ''}" id="manual-password-group">
                    <div class="settings-grid settings-grid-1">
                        ${field('s-manual-password', '自定义统一密码', s.manual_password || '', { type: 'password', mono: true })}
                    </div>
                </div>
            `)}

            ${section(ICONS.server, 'CPA 接入与补号', '可选交付到 CLIProxyAPI：mint OAuth、chat 探测后热载；号池低于下限可自动补号（需外部 timer）。', `
                <div class="settings-two-col">
                    <div class="settings-field-block">
                        <div class="settings-field-label">注册成功后导出到 CPA</div>
                        ${choiceGroup('cpa-auto', [
                            { value: 'false', title: '关闭', desc: '不写 CPA 凭证文件（默认）。', recommend: true },
                            { value: 'true', title: '开启 CPA 热载', desc: 'SSO → device OAuth → chat probe → auths/。' },
                        ], cpaAuto)}
                    </div>
                    <div class="settings-field-block">
                        <div class="settings-field-label">Chat 可用性探测</div>
                        ${choiceGroup('cpa-probe', [
                            { value: 'true', title: '开启 probe', desc: 'chat 403 进 dead，避免污染热池。', recommend: true },
                            { value: 'false', title: '跳过 probe', desc: 'mint 成功即热载（可能含不能聊的号）。' },
                        ], cpaProbe)}
                    </div>
                </div>
                <div class="settings-grid">
                    ${field('s-cpa-auth-dir', '热池目录 (cpa/auths)', s.cpa_auth_dir || '/cpa/auths', { type: 'text', mono: true })}
                    ${field('s-cpa-dead-dir', 'Dead 目录', s.cpa_dead_dir || '/cpa/auths-chat-dead', { type: 'text', mono: true })}
                    ${field('s-cpa-proxy', 'CPA / probe 代理', s.cpa_proxy || s.browser_proxy || '', { type: 'text', mono: true, placeholder: 'socks5://warp:1080' })}
                    ${field('s-cpa-probe-delay', 'Probe 前延迟 (秒)', s.cpa_probe_delay_sec || 45, { min: 0, helper: '新号 mint 后常需等待 chat 权限生效。' })}
                    ${field('s-cpa-probe-retries', 'Probe 失败重试次数', s.cpa_probe_retries || 2, { min: 0 })}
                    ${field('s-cpa-probe-gap', '重试间隔 (秒)', s.cpa_probe_retry_gap_sec || 60, { min: 0 })}
                </div>
                <div class="settings-field-block">
                    <div class="settings-field-label">热池自动补号（systemd pool keeper 读取）</div>
                    ${choiceGroup('cpa-pool', [
                        { value: 'false', title: '关闭', desc: '仅手动注册（默认）。', recommend: true },
                        { value: 'true', title: '开启自动补号', desc: '低于下限自动注册；达到上限自动暂停（需外部 timer 调 /api/register/*）。' },
                    ], cpaPool)}
                </div>
                <div class="settings-grid">
                    ${field('s-cpa-pool-min', '热池下限 (低于则补)', s.cpa_pool_min || 5, { min: 0, helper: '统计未禁用且 JWT 未过期的 xai-*.json' })}
                    ${field('s-cpa-pool-max', '热池上限 (达到则暂停)', s.cpa_pool_max || 5, { min: 0 })}
                    ${field('s-cpa-pool-rounds', '每次自动注册轮数', s.cpa_pool_register_rounds || 8, { min: 1, max: 30 })}
                </div>
            `)}

            ${section(ICONS.upload, 'Sub2API 接入', '可选交付后端：把注册成功的 SSO 原文推到 Sub2API，由其完成 SSO→OAuth mint 与建号（v0.1.162+ 支持 Grok 平台 + SSO 导入）。', `
                <div class="settings-two-col">
                    <div class="settings-field-block">
                        <div class="settings-field-label">注册成功后导入 Sub2API</div>
                        ${choiceGroup('sub2api-auto', [
                            { value: 'false', title: '不自动导入', desc: '默认。SSO 仅本地保存，不推送。', recommend: true },
                            { value: 'true', title: '自动导入', desc: '注册成功即 POST 到 /api/v1/admin/grok/sso-to-oauth；失败由后台补传。' },
                        ], sub2Auto)}
                    </div>
                    <div class="settings-field-block">
                        <div class="settings-field-label">过期自动暂停账号</div>
                        ${choiceGroup('sub2api-auto-pause', [
                            { value: 'true', title: '开启', desc: 'Sub2API 侧过期/配额耗尽自动置 disabled（默认）。', recommend: true },
                            { value: 'false', title: '关闭', desc: '不过问账号健康度。' },
                        ], sub2AutoPause)}
                    </div>
                </div>
                <div class="settings-grid">
                    ${field('s-sub2api-url', 'Sub2API 地址', s.sub2api_url || 'http://127.0.0.1:8080', { type: 'text', mono: true })}
                    ${field('s-sub2api-email', '管理员邮箱（与 API Token 二选一）', s.sub2api_email || '', { type: 'text', mono: true })}
                    ${field('s-sub2api-password', '管理员密码', s.sub2api_password || '', { type: 'password' })}
                    ${field('s-sub2api-token', '管理员 API Key（优先于邮箱密码）', s.sub2api_api_token || '', {
                        type: 'password',
                        helper: '粘贴 sub2api 后台「设置 → 管理员 API Key」生成的值。本字段会以 <code>x-api-key</code> header 发送,不会走邮箱密码登录流程。',
                    })}
                    ${field('s-sub2api-group-ids', '导入到 Group（ID 列表）', s.sub2api_group_ids || '', { type: 'text', mono: true, placeholder: '2 或 2,3' })}
                    ${field('s-sub2api-proxy-id', '出口代理 ID（留空 = 不用代理）', s.sub2api_proxy_id || '', { min: 0, type: 'number' })}
                    ${field('s-sub2api-concurrency', '并发 (Sub2API 端 3 worker 固定)', s.sub2api_concurrency || 1, { min: 0, max: 3 })}
                    ${field('s-sub2api-priority', '优先级', s.sub2api_priority || 1, { min: 0 })}
                    ${field('s-sub2api-name-prefix', '账号名前缀（留空用邮箱）', s.sub2api_name_prefix || '', { type: 'text', mono: true })}
                    ${field('s-sub2api-timeout', 'HTTP 超时（秒）', s.sub2api_timeout_sec || 180, { min: 30 })}
                </div>
                <div class="settings-field-block">
                    <div class="settings-field-label">连通性诊断</div>
                    <div class="settings-action-row">
                        <button class="btn btn-secondary" id="test-sub2api-btn" type="button">
                            <span aria-hidden="true">⚡</span> 测试连接
                        </button>
                        <div class="helper-text" id="test-sub2api-result">未测试。</div>
                    </div>
                </div>
            `)}

            ${section(ICONS.upload, 'grok2api 接入', '注册成功后的可选交付：Web 导入、Build 转换、chat 探测与后台补传。', `
                <div class="settings-two-col">
                    <div class="settings-field-block">
                        <div class="settings-field-label">注册成功后上传到 grok2api</div>
                        ${choiceGroup('grok2api-upload', [
                            { value: 'false', title: '不自动上传', desc: '仅本地保存结果，手动处理。', recommend: true },
                            { value: 'true', title: '自动导入 Web 并转换 Build', desc: '导入 Web SSO 并转换 Build；瞬时失败会自动重试并后台补传。' },
                        ], grok2apiUpload)}
                    </div>
                    <div class="settings-field-block">
                        <div class="settings-field-label">Chat 可用性探测</div>
                        ${choiceGroup('grok2api-probe', [
                            { value: 'false', title: '关闭', desc: '保持原上传流程，不额外等待。', recommend: true },
                            { value: 'true', title: '开启 probe', desc: '上传前探测 chat。无权限/限流时跳过导入，本地仍保留 SSO。' },
                        ], grok2apiProbe)}
                    </div>
                </div>
                <div class="settings-grid">
                    ${field('s-grok2api-url', 'grok2api 地址', s.grok2api_url || 'http://127.0.0.1:21434', { type: 'text', mono: true })}
                    ${field('s-grok2api-username', '管理员用户名', s.grok2api_username || 'admin', { type: 'text', mono: true })}
                    ${field('s-grok2api-password', '管理员密码', s.grok2api_password || '', { type: 'password' })}
                    ${field('s-grok2api-probe-proxy', 'Probe 代理', s.grok2api_probe_proxy || s.browser_proxy || '', { type: 'text', mono: true, placeholder: 'http://127.0.0.1:7897' })}
                    ${field('s-grok2api-probe-delay', 'Probe 前延迟 (秒)', s.grok2api_probe_delay_sec || 45, { min: 0 })}
                    ${field('s-grok2api-probe-retries', 'Probe 失败重试次数', s.grok2api_probe_retries || 2, { min: 0 })}
                    ${field('s-grok2api-probe-gap', 'Probe 重试间隔 (秒)', s.grok2api_probe_retry_gap_sec || 60, { min: 0 })}
                </div>
                <div class="settings-field-block">
                    <div class="settings-field-label">注册后打开 grok.com 做 Web 激活</div>
                    ${choiceGroup('web-activation', [
                        { value: 'false', title: '关闭', desc: '推荐。避免浏览器流程每轮再过 Cloudflare。', recommend: true },
                        { value: 'true', title: '开启', desc: '仅浏览器注册路径生效，可能需要人工验证。' },
                    ], webActivation)}
                    <div class="helper-text">协议路径使用 HTTP 完成 TOS/生日；此开关不会改变协议流程。</div>
                </div>
            `)}

            ${section(ICONS.folder, '导出与存储', '结果导出格式与落盘目录。', `
                <div class="settings-two-col">
                    <div class="settings-field-block">
                        <div class="settings-field-label">数据导出格式</div>
                        ${choiceGroup('export-format', [
                            { value: 'txt', title: 'TXT 文本格式', desc: '便于批量粘贴或导入其它工具。', recommend: true },
                            { value: 'json', title: 'JSON 数据格式', desc: '结构化，适合程序消费。' },
                        ], exportFormat)}
                    </div>
                    ${field('s-export-dir', '数据导出目录', s.export_dir || './data', { type: 'text', mono: true })}
                </div>
            `)}

            <div class="settings-actions card">
                <div class="settings-actions-copy">
                    <div class="settings-actions-title">配置变更</div>
                    <div class="settings-actions-desc">保存会立即写入并应用到后续注册任务。</div>
                </div>
                <div class="btn-group">
                    <button class="btn btn-primary" id="save-settings-btn">${ICONS.save} 保存当前配置</button>
                    <button class="btn btn-secondary" id="reset-settings-btn">${ICONS.reset} 恢复默认值</button>
                </div>
            </div>
        </div>
    `;

    const parsed = new DOMParser().parseFromString(markup, 'text/html');
    container.replaceChildren(...Array.from(parsed.body.childNodes));
    initSelects(container);
    bindChoiceCards(container);
    updateProviderSettings(container);
    updateBackendSettings(container);
    bindBackendChoices(container);

    container.querySelector('#save-settings-btn')?.addEventListener('click', saveSettings);
    container.querySelector('#reset-settings-btn')?.addEventListener('click', resetSettings);
    container.querySelector('#test-solver-btn')?.addEventListener('click', () => {
        void testSolverConnection({ notify: true });
    });
    container.querySelector('#start-solver-btn')?.addEventListener('click', () => {
        void controlLocalSolver('start');
    });
    container.querySelector('#stop-solver-btn')?.addEventListener('click', () => {
        void controlLocalSolver('stop');
    });
    container.querySelector('#test-sub2api-btn')?.addEventListener('click', () => {
        void testSub2apiConnection();
    });
    container.querySelector('#s-turnstile-solver-url')?.addEventListener('input', () => {
        setSolverHealth('idle', '待检测', 'URL 已修改，点击“测试连接”检查当前地址。');
    });
    container.querySelector('#s-email-provider')?.addEventListener('change', () => {
        updateProviderSettings(container);
    });
    container.querySelectorAll('input[name="password-mode"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            const group = container.querySelector('#manual-password-group');
            const manual = container.querySelector('input[name="password-mode"]:checked')?.value === 'manual';
            group?.classList.toggle('is-open', manual);
        });
    });
    void testSolverConnection();
}

function bindChoiceCards(root) {
    root.querySelectorAll('.choice-group').forEach((group) => {
        const sync = () => {
            group.querySelectorAll('.choice-card').forEach((card) => {
                const input = card.querySelector('input[type="radio"]');
                card.classList.toggle('is-selected', Boolean(input?.checked));
            });
        };
        group.addEventListener('change', sync);
        sync();
    });
}

function updateProviderSettings(root) {
    const provider = root.querySelector('#s-email-provider')?.value || 'microsoft';
    root.querySelectorAll('.mail-provider-settings').forEach((panel) => {
        panel.hidden = panel.dataset.provider !== provider;
    });
}

function currentRegistrationBackend(root = document) {
    return root.querySelector('input[name="registration-backend"]:checked')?.value
        || root.querySelector('#s-registration-backend')?.value
        || 'browser';
}

function currentTurnstileProvider(root = document) {
    return root.querySelector('input[name="turnstile-provider"]:checked')?.value
        || root.querySelector('#s-turnstile-provider')?.value
        || 'auto';
}

function updateBackendSettings(root) {
    const backend = currentRegistrationBackend(root);
    const usesBrowser = backend === 'browser' || backend === 'auto';
    const usesProtocol = backend === 'protocol' || backend === 'auto';
    const hiddenBackend = root.querySelector('#s-registration-backend');
    if (hiddenBackend) hiddenBackend.value = backend;

    root.querySelectorAll('[data-backend-panel="browser"]').forEach((panel) => {
        panel.hidden = !usesBrowser;
    });
    root.querySelectorAll('[data-backend-panel="protocol"]').forEach((panel) => {
        panel.hidden = !usesProtocol;
    });
}

function bindBackendChoices(root) {
    root.querySelectorAll('input[name="registration-backend"]').forEach((radio) => {
        radio.addEventListener('change', () => updateBackendSettings(root));
    });
    root.querySelectorAll('input[name="turnstile-provider"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            const hidden = root.querySelector('#s-turnstile-provider');
            if (hidden) hidden.value = currentTurnstileProvider(root);
        });
    });
}

function setSolverHealth(state, statusText, detailText) {
    const card = document.getElementById('solver-health-card');
    const status = document.getElementById('solver-health-status');
    const detail = document.getElementById('solver-health-detail');
    if (card) card.dataset.state = state;
    if (status) status.textContent = statusText;
    if (detail) detail.textContent = detailText;
}

function solverOfflineDetail(data) {
    const reason = data?.reason || 'request_error';
    if (reason === 'invalid_url') return 'Solver URL 无效，仅支持不含账号密码的 HTTP/HTTPS 地址。';
    if (reason === 'timeout') return `连接超时 · ${data.latency_ms ?? 0} ms`;
    if (reason === 'connection_error') return '无法连接到 Solver，请确认服务和端口已经启动。';
    if (reason === 'http_error') return `Solver 响应 HTTP ${data.status_code ?? '5xx'}。`;
    return 'Solver 状态检测失败，请检查地址和服务日志。';
}

async function testSolverConnection({ notify = false } = {}) {
    const input = document.getElementById('s-turnstile-solver-url');
    const button = document.getElementById('test-solver-btn');
    const url = input?.value.trim() || '';
    setSolverHealth('checking', '检测中', '正在检查 Solver 连接…');
    if (button) button.disabled = true;
    try {
        const res = await api('POST', '/api/settings/turnstile-solver/test', { url });
        const data = res.data || {};
        if (res.success && data.online) {
            const detail = `HTTP ${data.status_code} · ${data.latency_ms ?? 0} ms`;
            setSolverHealth('online', '在线', detail);
            if (notify) showToast(`Solver 连接成功：${detail}`, 'success');
            return;
        }
        const detail = res.success ? solverOfflineDetail(data) : (res.message || '检测请求失败');
        setSolverHealth('offline', '离线', detail);
        if (notify) showToast(detail, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function testSub2apiConnection() {
    const button = document.getElementById('test-sub2api-btn');
    const result = document.getElementById('test-sub2api-result');
    const setResult = (cls, label, detail) => {
        if (!result) return;
        result.className = `helper-text ${cls || ''}`.trim();
        result.textContent = detail ? `${label} · ${detail}` : label;
    };
    const formatDiag = (diag) => {
        if (!diag || typeof diag !== 'object') return '';
        const flags = [];
        if (diag.has_whitespace) flags.push('含空白');
        if (diag.has_control) flags.push('含控制字符');
        if (diag.has_non_ascii) flags.push('含非 ASCII');
        const safe = flags.length ? `（⚠ ${flags.join('、')}）` : '';
        return `len=${diag.length}${safe}`;
    };
    setResult('', '检测中…', '正在检查 Sub2API 连接…');
    if (button) button.disabled = true;
    const payload = {
        sub2api_url: document.getElementById('s-sub2api-url')?.value.trim() || '',
        sub2api_email: document.getElementById('s-sub2api-email')?.value.trim() || '',
        sub2api_password: document.getElementById('s-sub2api-password')?.value || '',
        sub2api_api_token: document.getElementById('s-sub2api-token')?.value.trim() || '',
        sub2api_group_ids: document.getElementById('s-sub2api-group-ids')?.value.trim() || '',
        sub2api_proxy_id: document.getElementById('s-sub2api-proxy-id')?.value || '0',
        sub2api_concurrency: document.getElementById('s-sub2api-concurrency')?.value || '1',
        sub2api_priority: document.getElementById('s-sub2api-priority')?.value || '1',
        sub2api_name_prefix: document.getElementById('s-sub2api-name-prefix')?.value.trim() || '',
        sub2api_auto_pause_on_expired:
            document.querySelector('input[name="sub2api-auto-pause"]:checked')?.value || 'true',
        sub2api_timeout_sec: document.getElementById('s-sub2api-timeout')?.value || '180',
    };
    try {
        const res = await api('POST', '/api/settings/sub2api/test', payload);
        const data = res.data || {};
        if (res.success && data.ok) {
            const authLabel = data.auth === 'api_key'
                ? 'api_key (x-api-key)'
                : data.auth === 'login'
                    ? 'login (Bearer)'
                    : data.auth || '?';
            const detail = [
                data.base_url,
                `auth=${authLabel}`,
                `groups=${data.group_count ?? 0}`,
                `grok=${data.grok_group_count ?? 0}`,
                formatDiag(data.token_diag),
            ].filter(Boolean).join(' · ');
            setResult('success', '已连接', detail);
            showToast(`Sub2API 连接成功：${detail}`, 'success');
        } else {
            const reason = (res.message || data.error || '检测请求失败');
            const diagHint = data.token_diag ? ` ${formatDiag(data.token_diag)}` : '';
            setResult('error', '连接失败', reason + diagHint);
            showToast(reason, 'error');
        }
    } catch (exc) {
        const reason = String(exc && exc.message ? exc.message : exc);
        setResult('error', '连接失败', reason);
        showToast(reason, 'error');
    } finally {
        if (button) button.disabled = false;
    }
}

async function controlLocalSolver(action) {
    const input = document.getElementById('s-turnstile-solver-url');
    const startBtn = document.getElementById('start-solver-btn');
    const stopBtn = document.getElementById('stop-solver-btn');
    const url = input?.value.trim() || '';
    const busyLabel = action === 'stop' ? '停止中…' : '启动中…';
    setSolverHealth('checking', busyLabel, action === 'stop'
        ? '正在停止本地 Solver 子进程…'
        : '正在拉起本地 Solver（首次可能下载 Camoufox，需数十秒）…');
    if (startBtn) startBtn.disabled = true;
    if (stopBtn) stopBtn.disabled = true;
    try {
        const res = await api(
            'POST',
            `/api/settings/turnstile-solver/${action}`,
            { url },
        );
        const data = res.data || {};
        if (action === 'stop') {
            // After stop, re-probe so an externally managed solver is not
            // falsely marked offline if it is still serving.
            const stillUp = await probeSolverOnline(url);
            if (stillUp?.online) {
                setSolverHealth(
                    'online',
                    '在线',
                    '停止请求已发送，但该地址仍可访问（可能由外部进程托管）。',
                );
                showToast('Solver 仍在线（外部进程）', 'success');
            } else {
                setSolverHealth('offline', '已停止', res.message || '本地 Solver 已请求停止');
                showToast(res.message || '本地 Solver 已停止', res.success === false ? 'error' : 'success');
            }
            return;
        }
        if (res.success && data.online) {
            const detail = data.pid
                ? `PID ${data.pid} · ${data.url || url || 'http://127.0.0.1:5072'}`
                : (data.url || '本地 Solver 在线');
            setSolverHealth('online', '在线', detail);
            showToast(res.message || '本地 Solver 已启动', 'success');
            return;
        }

        // Start API missing/failed (e.g. old app process without new routes)
        // must not paint the card offline when the solver is already healthy.
        const probe = await probeSolverOnline(url);
        if (probe?.online) {
            const latency = probe.latency_ms != null ? ` · ${probe.latency_ms} ms` : '';
            setSolverHealth(
                'online',
                '在线',
                `Solver 已在线（HTTP ${probe.status_code ?? 200}${latency}）。`
                    + (res.message ? ` 启动接口：${res.message}` : ''),
            );
            showToast(
                res.message && /not\s*found/i.test(String(res.message))
                    ? 'Solver 已在线。启动接口 404：请重启 python app.py 以加载新路由。'
                    : 'Solver 已在线，无需重复启动。',
                'success',
            );
            return;
        }

        const detail = data.last_error || res.message || '本地 Solver 启动失败';
        setSolverHealth('offline', '离线', detail);
        showToast(detail, 'error');
    } catch (err) {
        const detail = err?.message || '请求失败';
        const probe = await probeSolverOnline(url);
        if (probe?.online) {
            setSolverHealth('online', '在线', `Solver 仍在线；启动请求异常：${detail}`);
            showToast(`Solver 仍在线：${detail}`, 'error');
        } else {
            setSolverHealth('offline', '离线', detail);
            showToast(detail, 'error');
        }
    } finally {
        if (startBtn) startBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = false;
    }
}

async function probeSolverOnline(url) {
    try {
        const res = await api('POST', '/api/settings/turnstile-solver/test', { url: url || '' });
        if (res.success && res.data?.online) return res.data;
    } catch (_) {
        /* ignore */
    }
    return null;
}

function collectSettings() {
    const turnstileProvider = currentTurnstileProvider();
    return {
        email_provider: document.getElementById('s-email-provider').value,
        duckmail_api_base: document.getElementById('s-duckmail-api-base').value.trim(),
        duckmail_api_key: document.getElementById('s-duckmail-api-key').value.trim(),
        yyds_api_base: document.getElementById('s-yyds-api-base').value.trim(),
        yyds_api_key: document.getElementById('s-yyds-api-key').value.trim(),
        yyds_jwt: document.getElementById('s-yyds-jwt').value.trim(),
        cloudflare_api_base: document.getElementById('s-cloudflare-api-base').value.trim(),
        cloudflare_api_key: document.getElementById('s-cloudflare-api-key').value.trim(),
        cloudflare_auth_mode: document.getElementById('s-cloudflare-auth-mode').value,
        cloudflare_path_domains: document.getElementById('s-cloudflare-path-domains').value.trim(),
        cloudflare_path_accounts: document.getElementById('s-cloudflare-path-accounts').value.trim(),
        cloudflare_path_token: document.getElementById('s-cloudflare-path-token').value.trim(),
        cloudflare_path_messages: document.getElementById('s-cloudflare-path-messages').value.trim(),
        cloudflare_default_domains: document.getElementById('s-cloudflare-default-domains').value.trim(),
        cloud_mail_api_base: document.getElementById('s-cloud-mail-api-base').value.trim(),
        cloud_mail_api_key: document.getElementById('s-cloud-mail-api-key').value.trim(),
        cloud_mail_admin_email: document.getElementById('s-cloud-mail-admin-email').value.trim(),
        cloud_mail_admin_password: document.getElementById('s-cloud-mail-admin-password').value,
        max_aliases_per_account: document.getElementById('s-max-aliases').value,
        max_code_retries: document.getElementById('s-code-retries').value,
        registration_timeout: document.getElementById('s-timeout').value,
        registration_interval_seconds: document.getElementById('s-registration-interval').value,
        max_confirm_retries: document.getElementById('s-confirm-retries').value,
        max_retries_per_alias: document.getElementById('s-alias-retries').value,
        registration_concurrency: document.getElementById('s-registration-concurrency').value,
        registration_backend: currentRegistrationBackend(),
        browser_headless: document.querySelector('input[name="headless"]:checked')?.value || 'false',
        turnstile_auto: document.querySelector('input[name="turnstile"]:checked')?.value || 'true',
        browser_proxy: document.getElementById('s-browser-proxy').value.trim(),
        turnstile_provider: turnstileProvider,
        allow_browser_fallback: turnstileProvider === 'external' ? 'false' : 'true',
        yescaptcha_key: document.getElementById('s-yescaptcha-key').value.trim(),
        turnstile_solver_url: document.getElementById('s-turnstile-solver-url').value.trim(),
        random_name_enabled: document.querySelector('input[name="random-name"]:checked').value,
        extract_numbers_enabled: document.querySelector('input[name="extract-numbers"]:checked').value,
        password_mode: document.querySelector('input[name="password-mode"]:checked').value,
        manual_password: document.getElementById('s-manual-password').value,
        export_format: document.querySelector('input[name="export-format"]:checked').value,
        export_dir: document.getElementById('s-export-dir').value,
        grok2api_auto_upload: document.querySelector('input[name="grok2api-upload"]:checked').value,
        grok2api_probe_chat: document.querySelector('input[name="grok2api-probe"]:checked')?.value || 'false',
        grok2api_probe_proxy: document.getElementById('s-grok2api-probe-proxy')?.value.trim() || '',
        grok2api_probe_delay_sec: document.getElementById('s-grok2api-probe-delay')?.value || '45',
        grok2api_probe_retries: document.getElementById('s-grok2api-probe-retries')?.value || '2',
        grok2api_probe_retry_gap_sec: document.getElementById('s-grok2api-probe-gap')?.value || '60',
        sub2api_auto_upload: document.querySelector('input[name="sub2api-auto"]:checked')?.value || 'false',
        sub2api_auto_pause_on_expired: document.querySelector('input[name="sub2api-auto-pause"]:checked')?.value || 'true',
        sub2api_url: document.getElementById('s-sub2api-url')?.value.trim() || '',
        sub2api_email: document.getElementById('s-sub2api-email')?.value.trim() || '',
        sub2api_password: document.getElementById('s-sub2api-password')?.value || '',
        sub2api_api_token: document.getElementById('s-sub2api-token')?.value.trim() || '',
        sub2api_group_ids: document.getElementById('s-sub2api-group-ids')?.value.trim() || '',
        sub2api_proxy_id: document.getElementById('s-sub2api-proxy-id')?.value || '0',
        sub2api_concurrency: document.getElementById('s-sub2api-concurrency')?.value || '1',
        sub2api_priority: document.getElementById('s-sub2api-priority')?.value || '1',
        sub2api_name_prefix: document.getElementById('s-sub2api-name-prefix')?.value.trim() || '',
        sub2api_timeout_sec: document.getElementById('s-sub2api-timeout')?.value || '180',
        grok_web_activation: document.querySelector('input[name="web-activation"]:checked').value,
        grok2api_url: document.getElementById('s-grok2api-url').value,
        grok2api_username: document.getElementById('s-grok2api-username').value,
        grok2api_password: document.getElementById('s-grok2api-password').value,
        cpa_auto_export: document.querySelector('input[name="cpa-auto"]:checked')?.value || 'false',
        cpa_probe_chat: document.querySelector('input[name="cpa-probe"]:checked')?.value || 'true',
        cpa_auth_dir: document.getElementById('s-cpa-auth-dir')?.value.trim() || '/cpa/auths',
        cpa_dead_dir: document.getElementById('s-cpa-dead-dir')?.value.trim() || '/cpa/auths-chat-dead',
        cpa_proxy: document.getElementById('s-cpa-proxy')?.value.trim() || '',
        cpa_probe_delay_sec: document.getElementById('s-cpa-probe-delay')?.value || '45',
        cpa_probe_retries: document.getElementById('s-cpa-probe-retries')?.value || '2',
        cpa_probe_retry_gap_sec: document.getElementById('s-cpa-probe-gap')?.value || '60',
        cpa_pool_enabled: document.querySelector('input[name="cpa-pool"]:checked')?.value || 'false',
        cpa_pool_min: document.getElementById('s-cpa-pool-min')?.value || '5',
        cpa_pool_max: document.getElementById('s-cpa-pool-max')?.value || '5',
        cpa_pool_register_rounds: document.getElementById('s-cpa-pool-rounds')?.value || '8',
    };
}

async function saveSettings() {
    const button = document.getElementById('save-settings-btn');
    if (button) {
        button.disabled = true;
        button.classList.add('is-pressed');
    }
    try {
        const res = await api('PUT', '/api/settings', collectSettings());
        if (res.success) showToast('系统配置已成功保存并应用', 'success');
        else showToast(res.message || '保存失败', 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.classList.remove('is-pressed');
        }
    }
}

async function resetSettings() {
    if (!confirm('安全提示：确定恢复所有系统设置为初始默认值吗？')) return;
    const res = await api('PUT', '/api/settings', { _reset: true });
    if (res.success) {
        showToast('已成功恢复初始默认值', 'success');
        render(document.getElementById('main-content'));
    } else {
        showToast(res.message || '恢复失败', 'error');
    }
}
