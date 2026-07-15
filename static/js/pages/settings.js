import { api } from '../api.js';
import { showToast } from '../components/toast.js';

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
                    <div class="form-group" style="visibility: hidden;"></div>
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
                        <div style="margin-top:6px;font-size:12px;color:var(--text-secondary);">关闭不影响 SSO 注册与 grok2api 上传/Build 转换。需要 CF 出口时用「批量补激活」单独处理。</div>
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

    document.querySelectorAll('input[name="password-mode"]').forEach(radio => {
        radio.addEventListener('change', () => {
            const group = document.getElementById('manual-password-group');
            group.style.display = radio.value === 'manual' && radio.checked ? 'flex' : 'none';
        });
    });
}

function collectSettings() {
    return {
        max_aliases_per_account: document.getElementById('s-max-aliases').value,
        max_code_retries: document.getElementById('s-code-retries').value,
        registration_timeout: document.getElementById('s-timeout').value,
        max_confirm_retries: document.getElementById('s-confirm-retries').value,
        max_retries_per_alias: document.getElementById('s-alias-retries').value,
        registration_concurrency: document.getElementById('s-registration-concurrency').value,
        browser_headless: document.querySelector('input[name="headless"]:checked').value,
        turnstile_auto: document.querySelector('input[name="turnstile"]:checked').value,
        browser_proxy: document.getElementById('s-browser-proxy').value.trim(),
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
