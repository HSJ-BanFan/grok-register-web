import { api } from '../api.js';
import { showToast } from '../components/toast.js';

function escapeAttr(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('"', '&quot;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
}

export async function render(container) {
    const res = await api('GET', '/api/settings');
    const s = res.success ? res.data : {};

    container.innerHTML = `
        <div class="card card-md">
            <div class="card-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2m0 18v2m-9-11h2m18 0h2m-4.22-5.78-1.42 1.42M6.34 17.66l-1.42 1.42m0-13.84 1.42 1.42m11.32 11.32 1.42 1.42"/></svg>
                系统设置中心
            </div>
            <div class="form-container-md">
                <div class="form-row">
                    <div class="form-group">
                        <label>注册邮箱服务</label>
                        <select class="form-input" id="s-email-provider">
                            <option value="microsoft" ${(s.email_provider || 'microsoft') === 'microsoft' ? 'selected' : ''}>Microsoft Outlook / Hotmail（导入账号与别名）</option>
                            <option value="duckmail" ${s.email_provider === 'duckmail' ? 'selected' : ''}>DuckMail（自动创建临时邮箱）</option>
                            <option value="yyds" ${s.email_provider === 'yyds' ? 'selected' : ''}>YYDS Mail（自动创建临时邮箱）</option>
                            <option value="cloudflare" ${s.email_provider === 'cloudflare' ? 'selected' : ''}>Cloudflare Temp Email（自动创建）</option>
                            <option value="cloud_mail" ${s.email_provider === 'cloud_mail' ? 'selected' : ''}>Cloud Mail API（自动创建）</option>
                        </select>
                        <div style="margin-top:6px;font-size:12px;color:var(--text-secondary);">Microsoft 使用账号库中的 OAuth 凭证和加号别名；其他服务会在每轮注册前创建一个独立邮箱并自动入库。</div>
                    </div>
                    <div class="form-group" style="visibility:hidden;"></div>
                </div>

                <div class="mail-provider-settings" data-provider="duckmail">
                    <div class="form-row">
                        <div class="form-group">
                            <label>DuckMail API Base</label>
                            <input type="text" class="form-input" id="s-duckmail-api-base" value="${escapeAttr(s.duckmail_api_base || 'https://api.duckmail.sbs')}">
                        </div>
                        <div class="form-group">
                            <label>DuckMail API Key（可选）</label>
                            <input type="password" class="form-input" id="s-duckmail-api-key" value="${escapeAttr(s.duckmail_api_key || '')}" autocomplete="new-password">
                        </div>
                    </div>
                </div>

                <div class="mail-provider-settings" data-provider="yyds">
                    <div class="form-row">
                        <div class="form-group">
                            <label>YYDS API Base</label>
                            <input type="text" class="form-input" id="s-yyds-api-base" value="${escapeAttr(s.yyds_api_base || 'https://maliapi.215.im/v1')}">
                        </div>
                        <div class="form-group">
                            <label>YYDS API Key</label>
                            <input type="password" class="form-input" id="s-yyds-api-key" value="${escapeAttr(s.yyds_api_key || '')}" autocomplete="new-password">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>YYDS JWT（与 API Key 二选一）</label>
                            <input type="password" class="form-input" id="s-yyds-jwt" value="${escapeAttr(s.yyds_jwt || '')}" autocomplete="new-password">
                        </div>
                        <div class="form-group" style="visibility:hidden;"></div>
                    </div>
                </div>

                <div class="mail-provider-settings" data-provider="cloudflare">
                    <div class="form-row">
                        <div class="form-group">
                            <label>Cloudflare 邮箱 API Base</label>
                            <input type="text" class="form-input" id="s-cloudflare-api-base" value="${escapeAttr(s.cloudflare_api_base || '')}" placeholder="https://temp-mail.example.com">
                        </div>
                        <div class="form-group">
                            <label>鉴权方式</label>
                            <select class="form-input" id="s-cloudflare-auth-mode">
                                ${['none', 'query-key', 'bearer', 'x-api-key', 'x-admin-auth'].map(mode => `<option value="${mode}" ${(s.cloudflare_auth_mode || 'none') === mode ? 'selected' : ''}>${mode}</option>`).join('')}
                            </select>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Cloudflare API Key / Admin Password</label>
                            <input type="password" class="form-input" id="s-cloudflare-api-key" value="${escapeAttr(s.cloudflare_api_key || '')}" autocomplete="new-password">
                        </div>
                        <div class="form-group">
                            <label>默认域名（逗号分隔，可选）</label>
                            <input type="text" class="form-input" id="s-cloudflare-default-domains" value="${escapeAttr(s.cloudflare_default_domains || '')}" placeholder="mail.example.com, mail2.example.com">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>域名 / 创建邮箱路径</label>
                            <div class="input-action-group">
                                <input type="text" class="form-input" id="s-cloudflare-path-domains" value="${escapeAttr(s.cloudflare_path_domains || '/api/domains')}">
                                <input type="text" class="form-input" id="s-cloudflare-path-accounts" value="${escapeAttr(s.cloudflare_path_accounts || '/api/new_address')}">
                            </div>
                        </div>
                        <div class="form-group">
                            <label>Token / 邮件列表路径</label>
                            <div class="input-action-group">
                                <input type="text" class="form-input" id="s-cloudflare-path-token" value="${escapeAttr(s.cloudflare_path_token || '/api/token')}">
                                <input type="text" class="form-input" id="s-cloudflare-path-messages" value="${escapeAttr(s.cloudflare_path_messages || '/api/mails')}">
                            </div>
                        </div>
                    </div>
                </div>

                <div class="mail-provider-settings" data-provider="cloud_mail">
                    <div class="form-row">
                        <div class="form-group">
                            <label>Cloud Mail API Base</label>
                            <input type="text" class="form-input" id="s-cloud-mail-api-base" value="${escapeAttr(s.cloud_mail_api_base || 'https://mail.meilunaria.dpdns.org')}">
                        </div>
                        <div class="form-group">
                            <label>Cloud Mail API Key（优先）</label>
                            <input type="password" class="form-input" id="s-cloud-mail-api-key" value="${escapeAttr(s.cloud_mail_api_key || '')}" autocomplete="new-password">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>管理员邮箱（无 API Key 时）</label>
                            <input type="text" class="form-input" id="s-cloud-mail-admin-email" value="${escapeAttr(s.cloud_mail_admin_email || '')}">
                        </div>
                        <div class="form-group">
                            <label>管理员密码</label>
                            <input type="password" class="form-input" id="s-cloud-mail-admin-password" value="${escapeAttr(s.cloud_mail_admin_password || '')}" autocomplete="new-password">
                        </div>
                    </div>
                </div>

                <hr style="border: 0; border-top: 1px solid var(--border); margin: 20px 0;" />

                <!-- ── 延时与重试数字设置 ── -->
                <div class="form-row">
                    <div class="form-group">
                        <label>每账号最大别名数</label>
                        <input type="number" class="form-input" id="s-max-aliases" value="${s.max_aliases_per_account || 5}" min="1">
                    </div>
                    <div class="form-group">
                        <label>验证码轮询次数</label>
                        <input type="number" class="form-input" id="s-code-retries" value="${s.max_code_retries || 3}" min="1">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>注册超时限制 (秒)</label>
                        <input type="number" class="form-input" id="s-timeout" value="${s.registration_timeout || 300}" min="30">
                    </div>
                    <div class="form-group">
                        <label>确认邮箱重试次数</label>
                        <input type="number" class="form-input" id="s-confirm-retries" value="${s.max_confirm_retries || 3}" min="1">
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>每别名最大重试次数</label>
                        <input type="number" class="form-input" id="s-alias-retries" value="${s.max_retries_per_alias || 3}" min="1">
                    </div>
                    <div class="form-group">
                        <label>并发注册 Worker 数（稳定模式固定为 1）</label>
                        <input type="number" class="form-input" id="s-registration-concurrency" value="1" min="1" max="1" readonly>
                    </div>
                </div>

                <hr style="border: 0; border-top: 1px solid var(--border); margin: 20px 0;" />

                <!-- ── 运行与防封模式单选 ── -->
                <div class="form-row">
                    <div class="form-group">
                        <label>注册传输后端</label>
                        <select class="form-input" id="s-registration-backend">
                            <option value="browser" ${(s.registration_backend || 'browser') === 'browser' ? 'selected' : ''}>浏览器（默认）</option>
                            <option value="protocol" ${s.registration_backend === 'protocol' ? 'selected' : ''}>HTTP 协议 Worker（实验）</option>
                            <option value="auto" ${s.registration_backend === 'auto' ? 'selected' : ''}>自动 → 协议（实验）</option>
                        </select>
                        <div style="margin-top:6px;font-size:12px;color:var(--text-secondary);">浏览器后端保持现有流程。协议 Worker 优先 curl_cffi 纯 HTTP（发现参数 / gRPC / Server Action / SSO）；Turnstile 优先 YesCaptcha 或本地 solver，失败再回退本机 Chrome。环境被 CF 拦截时不消耗 alias 重试。</div>
                    </div>
                    <div class="form-group">
                        <label>浏览器运行模式</label>
                        <div class="radio-group">
                            <label><input type="radio" name="headless" value="true" ${s.browser_headless === 'true' ? 'checked' : ''}> 无头模式（可能被 Cloudflare 拦截）</label>
                            <label><input type="radio" name="headless" value="false" ${s.browser_headless !== 'true' ? 'checked' : ''}> 有头 / Xvfb 模式（推荐）</label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Turnstile 人机验证</label>
                        <div class="radio-group">
                            <label><input type="radio" name="turnstile" value="true" ${s.turnstile_auto !== 'false' ? 'checked' : ''}> 自动过验证</label>
                            <label><input type="radio" name="turnstile" value="false" ${s.turnstile_auto === 'false' ? 'checked' : ''}> 手动过验证</label>
                        </div>
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label>浏览器代理 (降低 Cloudflare 验证概率)</label>
                        <input type="text" class="form-input" id="s-browser-proxy" value="${s.browser_proxy || ''}" placeholder="http://127.0.0.1:7897 （留空=直连）">
                    </div>
                    <div class="form-group">
                        <label>协议 Turnstile 提供方</label>
                        <select class="form-input" id="s-turnstile-provider">
                            <option value="auto" ${(s.turnstile_provider || 'auto') === 'auto' ? 'selected' : ''}>自动（外置优先，失败可回退浏览器）</option>
                            <option value="external" ${s.turnstile_provider === 'external' || s.turnstile_provider === 'strict_external' ? 'selected' : ''}>仅外置 / 零浏览器（失败即退出，不启 Chrome）</option>
                            <option value="browser" ${s.turnstile_provider === 'browser' ? 'selected' : ''}>仅本机浏览器</option>
                        </select>
                        <div style="margin-top:6px;font-size:12px;color:var(--text-secondary);">
                            服务器零浏览器请选「仅外置」并配置 YesCaptcha 或本地 solver；日志会拆分 transport / turnstile / sso_follow。
                        </div>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>YesCaptcha Key（协议外置打码）</label>
                        <input type="password" class="form-input" id="s-yescaptcha-key" value="${escapeAttr(s.yescaptcha_key || '')}" placeholder="留空则尝试本地 solver / 浏览器" autocomplete="new-password">
                    </div>
                    <div class="form-group">
                        <label>本地 Turnstile Solver URL</label>
                        <input type="text" class="form-input" id="s-turnstile-solver-url" value="${escapeAttr(s.turnstile_solver_url || 'http://127.0.0.1:5072')}" placeholder="http://127.0.0.1:5072">
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label>随机姓名生成</label>
                        <div class="radio-group">
                            <label><input type="radio" name="random-name" value="true" ${s.random_name_enabled !== 'false' ? 'checked' : ''}> 开启随机生成</label>
                            <label><input type="radio" name="random-name" value="false" ${s.random_name_enabled === 'false' ? 'checked' : ''}> 关闭随机生成</label>
                        </div>
                    </div>
                    <div class="form-group" style="visibility: hidden;"></div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label>页面数字智能提取</label>
                        <div class="radio-group">
                            <label><input type="radio" name="extract-numbers" value="true" ${s.extract_numbers_enabled === 'true' ? 'checked' : ''}> 启用提取</label>
                            <label><input type="radio" name="extract-numbers" value="false" ${s.extract_numbers_enabled !== 'true' ? 'checked' : ''}> 禁用提取</label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>别名密码分配模式</label>
                        <div class="radio-group">
                            <label><input type="radio" name="password-mode" value="auto" ${s.password_mode !== 'manual' ? 'checked' : ''}> 自动随机生成</label>
                            <label><input type="radio" name="password-mode" value="manual" ${s.password_mode === 'manual' ? 'checked' : ''}> 统一自定义密码</label>
                        </div>
                    </div>
                </div>

                <!-- ── 自定义密码输入 (保持对齐) ── -->
                <div class="form-row" id="manual-password-group" style="${s.password_mode === 'manual' ? '' : 'display:none'}">
                    <div class="form-group">
                        <label>自定义统一密码</label>
                        <input type="text" class="form-input" id="s-manual-password" value="${s.manual_password || ''}" placeholder="请输入别名账号统一登录密码">
                    </div>
                    <div class="form-group" style="visibility: hidden;"></div>
                </div>

                <hr style="border: 0; border-top: 1px solid var(--border); margin: 20px 0;" />

                <div class="form-row">
                    <div class="form-group">
                        <label>注册成功后上传到 grok2api</label>
                        <div class="radio-group">
                            <label><input type="radio" name="grok2api-upload" value="true" ${s.grok2api_auto_upload === 'true' ? 'checked' : ''}> 自动导入 Web 并转换 Build</label>
                            <label><input type="radio" name="grok2api-upload" value="false" ${s.grok2api_auto_upload !== 'true' ? 'checked' : ''}> 不自动上传</label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>注册后打开 grok.com 做 Web 激活</label>
                        <div class="radio-group">
                            <label><input type="radio" name="web-activation" value="false" ${s.grok_web_activation !== 'true' ? 'checked' : ''}> 关闭（推荐，避免每轮 Cloudflare 人机）</label>
                            <label><input type="radio" name="web-activation" value="true" ${s.grok_web_activation === 'true' ? 'checked' : ''}> 开启（可能要手点 Verify you are human）</label>
                        </div>
                        <div style="margin-top:6px;font-size:12px;color:var(--text-secondary);">
                            仅作用于<strong>浏览器注册</strong>路径：成功拿 SSO 后是否再打开 grok.com 做人机/CF 上下文。
                            关闭不影响 SSO 落库与 grok2api 上传/Build 转换。
                            协议路径用 pure-HTTP 做 TOS/生日（与本开关无关）；需要浏览器 CF 出口时仍用「批量补激活」。
                        </div>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>grok2api 地址</label>
                        <input type="text" class="form-input" id="s-grok2api-url" value="${s.grok2api_url || 'http://127.0.0.1:21434'}" placeholder="http://127.0.0.1:21434">
                    </div>
                    <div class="form-group" style="visibility: hidden;"></div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>grok2api 管理员用户名</label>
                        <input type="text" class="form-input" id="s-grok2api-username" value="${s.grok2api_username || 'admin'}">
                    </div>
                    <div class="form-group">
                        <label>grok2api 管理员密码</label>
                        <input type="password" class="form-input" id="s-grok2api-password" value="${s.grok2api_password || ''}" autocomplete="new-password">
                    </div>
                </div>

                <hr style="border: 0; border-top: 1px solid var(--border); margin: 20px 0;" />

                <!-- ── 导出文件格式与目录 ── -->
                <div class="form-row">
                    <div class="form-group">
                        <label>数据导出格式</label>
                        <div class="radio-group">
                            <label><input type="radio" name="export-format" value="txt" ${s.export_format !== 'json' ? 'checked' : ''}> TXT 文本格式</label>
                            <label><input type="radio" name="export-format" value="json" ${s.export_format === 'json' ? 'checked' : ''}> JSON 数据格式</label>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>数据导出目录</label>
                        <input type="text" class="form-input" id="s-export-dir" value="${s.export_dir || './data'}">
                    </div>
                </div>

                <div class="btn-group" style="margin-top:28px;">
                    <button class="btn btn-primary" id="save-settings-btn">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
                        保存当前配置
                    </button>
                    <button class="btn btn-secondary" id="reset-settings-btn">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
                        恢复默认值
                    </button>
                </div>
            </div>
        </div>
    `;

    document.getElementById('save-settings-btn').addEventListener('click', saveSettings);
    document.getElementById('reset-settings-btn').addEventListener('click', resetSettings);
    document.getElementById('s-email-provider').addEventListener('change', updateProviderSettings);
    updateProviderSettings();

    document.querySelectorAll('input[name="password-mode"]').forEach(radio => {
        radio.addEventListener('change', () => {
            const group = document.getElementById('manual-password-group');
            group.style.display = radio.value === 'manual' && radio.checked ? 'flex' : 'none';
        });
    });
}

function updateProviderSettings() {
    const provider = document.getElementById('s-email-provider')?.value || 'microsoft';
    document.querySelectorAll('.mail-provider-settings').forEach(section => {
        section.style.display = section.dataset.provider === provider ? 'block' : 'none';
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
    const settings = collectSettings();
    const res = await api('PUT', '/api/settings', settings);
    if (res.success) showToast('系统配置已成功保存并应用', 'success');
    else showToast(res.message, 'error');
}

async function resetSettings() {
    if (!confirm('安全提示：确定恢复所有系统设置为初始默认值吗？')) return;
    const res = await api('PUT', '/api/settings', { _reset: true });
    if (res.success) {
        showToast('已成功恢复初始默认值', 'success');
        render(document.getElementById('main-content'));
    } else {
        showToast(res.message, 'error');
    }
}
