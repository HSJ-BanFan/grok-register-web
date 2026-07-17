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
    const s = res.success ? res.data : {};
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
    const webActivation = s.grok_web_activation === 'true' ? 'true' : 'false';
    const exportFormat = s.export_format === 'json' ? 'json' : 'txt';

    const markup = `
        <div class="settings-page">
            <div class="card settings-hero">
                <div class="settings-hero-main">
                    <div class="card-title">${ICONS.gear} 系统设置中心</div>
                    <p class="card-desc settings-hero-desc">
                        配置邮箱服务、注册后端、浏览器策略与 grok2api 流程。分区保存，后续任务立即生效。
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
                    ${field('s-confirm-retries', '确认邮箱重试次数', s.max_confirm_retries || 3, { min: 1 })}
                    ${field('s-alias-retries', '每别名最大重试次数', s.max_retries_per_alias || 3, { min: 1 })}
                    ${field('s-registration-concurrency', '并发注册 Worker 数', 1, { min: 1, max: 1, readonly: true, helper: '稳定模式固定为 1。' })}
                </div>
            `)}

            ${section(ICONS.browser, '注册后端与人机', '浏览器是默认后端；协议模式为实验功能，并可使用外置 Turnstile。', `
                <div class="settings-grid">
                    ${selectField('s-registration-backend', '注册传输后端', registrationBackend, [
                        { value: 'browser', label: '浏览器（默认）' },
                        { value: 'protocol', label: 'HTTP 协议 Worker（实验）' },
                        { value: 'auto', label: '自动 → 协议（实验）' },
                    ])}
                    ${selectField('s-turnstile-provider', '协议 Turnstile 提供方', turnstileProvider, [
                        { value: 'auto', label: '自动（外置优先，可回退浏览器）' },
                        { value: 'external', label: '仅外置 / 零浏览器（失败即退出）' },
                        { value: 'browser', label: '仅本机浏览器' },
                    ])}
                </div>
                <div class="settings-field-block">
                    <div class="settings-field-label">浏览器运行模式</div>
                    ${choiceGroup('headless', [
                        { value: 'false', title: '有头 / Xvfb 模式', desc: '推荐。真实渲染环境，更不容易被 Cloudflare 拦。', recommend: true },
                        { value: 'true', title: '无头模式', desc: '资源占用更低，但可能被 Cloudflare 拦截。' },
                    ], headless)}
                </div>
                <div class="settings-field-block">
                    <div class="settings-field-label">Turnstile 人机验证</div>
                    ${choiceGroup('turnstile', [
                        { value: 'true', title: '自动过验证', desc: '走自动求解器，适合无人值守。', recommend: true },
                        { value: 'false', title: '手动过验证', desc: '浏览器弹窗时由人工点击完成。' },
                    ], turnstile)}
                </div>
                <div class="settings-grid">
                    ${field('s-browser-proxy', '浏览器 / 邮箱 API 代理', s.browser_proxy || '', { type: 'text', mono: true, placeholder: 'http://127.0.0.1:7897 （留空=直连）' })}
                    ${field('s-yescaptcha-key', 'YesCaptcha Key', s.yescaptcha_key || '', { type: 'password' })}
                    ${field('s-turnstile-solver-url', '本地 Turnstile Solver URL', s.turnstile_solver_url || 'http://127.0.0.1:5072', { type: 'text', mono: true })}
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
                        ${field('s-manual-password', '自定义统一密码', s.manual_password || '', { type: 'text', mono: true })}
                    </div>
                </div>
            `)}

            ${section(ICONS.upload, 'grok2api 接入', '注册成功后的自动上传、Web 激活与管理端凭证。', `
                <div class="settings-two-col">
                    <div class="settings-field-block">
                        <div class="settings-field-label">注册成功后上传到 grok2api</div>
                        ${choiceGroup('grok2api-upload', [
                            { value: 'false', title: '不自动上传', desc: '仅本地保存结果，手动处理。', recommend: true },
                            { value: 'true', title: '自动导入 Web 并转换 Build', desc: '成功后直接推到 grok2api。' },
                        ], grok2apiUpload)}
                    </div>
                    <div class="settings-field-block">
                        <div class="settings-field-label">注册后打开 grok.com 做 Web 激活</div>
                        ${choiceGroup('web-activation', [
                            { value: 'false', title: '关闭', desc: '推荐。避免浏览器流程每轮再过 Cloudflare。', recommend: true },
                            { value: 'true', title: '开启', desc: '仅浏览器注册路径生效，可能需要人工验证。' },
                        ], webActivation)}
                        <div class="helper-text">协议路径使用 HTTP 完成 TOS/生日；此开关不会改变协议流程。</div>
                    </div>
                </div>
                <div class="settings-grid">
                    ${field('s-grok2api-url', 'grok2api 地址', s.grok2api_url || 'http://127.0.0.1:21434', { type: 'text', mono: true })}
                    ${field('s-grok2api-username', '管理员用户名', s.grok2api_username || 'admin', { type: 'text', mono: true })}
                    ${field('s-grok2api-password', '管理员密码', s.grok2api_password || '', { type: 'password' })}
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

    container.querySelector('#save-settings-btn')?.addEventListener('click', saveSettings);
    container.querySelector('#reset-settings-btn')?.addEventListener('click', resetSettings);
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

function collectSettings() {
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
        max_confirm_retries: document.getElementById('s-confirm-retries').value,
        max_retries_per_alias: document.getElementById('s-alias-retries').value,
        registration_concurrency: document.getElementById('s-registration-concurrency').value,
        registration_backend: document.getElementById('s-registration-backend').value,
        browser_headless: document.querySelector('input[name="headless"]:checked').value,
        turnstile_auto: document.querySelector('input[name="turnstile"]:checked').value,
        browser_proxy: document.getElementById('s-browser-proxy').value.trim(),
        turnstile_provider: document.getElementById('s-turnstile-provider').value,
        yescaptcha_key: document.getElementById('s-yescaptcha-key').value.trim(),
        turnstile_solver_url: document.getElementById('s-turnstile-solver-url').value.trim(),
        random_name_enabled: document.querySelector('input[name="random-name"]:checked').value,
        extract_numbers_enabled: document.querySelector('input[name="extract-numbers"]:checked').value,
        password_mode: document.querySelector('input[name="password-mode"]:checked').value,
        manual_password: document.getElementById('s-manual-password').value,
        export_format: document.querySelector('input[name="export-format"]:checked').value,
        export_dir: document.getElementById('s-export-dir').value,
        grok2api_auto_upload: document.querySelector('input[name="grok2api-upload"]:checked').value,
        grok_web_activation: document.querySelector('input[name="web-activation"]:checked').value,
        grok2api_url: document.getElementById('s-grok2api-url').value,
        grok2api_username: document.getElementById('s-grok2api-username').value,
        grok2api_password: document.getElementById('s-grok2api-password').value,
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
