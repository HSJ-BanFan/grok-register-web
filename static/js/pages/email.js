import { api } from '../api.js';
import { showToast } from '../components/toast.js';
import { createTable } from '../components/table.js';

let accountTable = null;
let oauthPollTimer = null;
let oauthPollTimeout = null;

export async function render(container) {
    container.innerHTML = `
        <div class="card card-md">
            <div class="card-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
                Microsoft OAuth2 授权
            </div>
            <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px;line-height:1.6;">
                请前往 Azure 门户 → 应用注册，将重定向 URI 设置为 <code>http://localhost:53682</code>。
            </p>
            <div class="form-container-md">
                <div class="form-group" style="margin-bottom:0;">
                    <label>Client ID</label>
                    <div class="input-action-group">
                        <input type="text" class="form-input" id="oauth-client-id" placeholder="粘贴 Azure 应用程序的 Client ID">
                        <button class="btn btn-primary" id="oauth-start-btn">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
                            开始授权
                        </button>
                    </div>
                </div>
            </div>
            <div class="oauth-status" id="oauth-status">状态: 正在检查...</div>
        </div>

        <div class="card">
            <div class="card-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                批量导入账号
            </div>
            <p style="font-size:13px;color:var(--text-secondary);margin-bottom:14px;line-height:1.6;">
                此处仅导入 Microsoft 邮箱。格式: <code>邮箱----密码----ClientID----Token</code> (一行一个)。临时邮箱服务请在「系统设置」选择，注册时会自动创建并进入账号库。
            </p>

            <div class="import-two-column">
                <div class="import-column-left">
                    <!-- Drag & Drop Zone -->
                    <div id="drop-zone" class="drop-zone">
                        <div class="drop-zone-icon">
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                        </div>
                        <div class="drop-zone-title">拖放 .txt 文件到此处，或者 <span style="color:var(--accent);text-decoration:underline;">点击浏览文件</span></div>
                        <div class="drop-zone-desc">支持批量解析以 "----" 分割的账号凭证</div>
                    </div>

                    <!-- File Info Badge -->
                    <div id="file-info-container" style="display:none;margin-top:16px;align-items:center;gap:12px;">
                        <span class="file-info-badge" id="file-info-badge">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                            <span id="file-info-name">filename.txt</span>
                            <span id="file-info-size" style="color:var(--text-secondary);font-size:11px;">(0 KB)</span>
                        </span>
                        <button class="btn btn-sm btn-secondary" id="clear-file-btn" style="padding:4px 10px;border-radius:20px;font-size:11px;">清除文件</button>
                    </div>
                </div>

                <div class="import-column-right">
                    <!-- Manual Input Panel -->
                    <div class="form-group" id="manual-input-container" style="margin-bottom:0;">
                        <label style="display:flex;justify-content:space-between;align-items:center;">
                            <span>手动粘贴文本数据</span>
                            <button class="copy-btn" id="toggle-manual-input" style="font-size:11px;padding:2px 6px;">隐藏</button>
                        </label>
                        <textarea class="form-textarea" id="import-text" rows="5" placeholder="your@hotmail.com----password----client-id----refresh-token"></textarea>
                    </div>
                </div>
            </div>

            <div class="btn-group" style="margin-top:20px;">
                <button class="btn btn-success" id="import-confirm-btn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
                    解析并预览
                </button>
                <input type="file" id="import-file-input" accept=".txt" style="display:none">
            </div>

            <div class="import-preview" id="import-preview" style="display:none"></div>
        </div>

        <div class="card">
            <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
                <span style="display:flex;align-items:center;gap:8px;">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                    系统账号库
                </span>
                <div class="btn-group">
                    <select class="form-select" id="account-filter" style="width:120px;padding:6px 12px;font-size:13px;height:32px;">
                        <option value="all">全部账号</option>
                        <option value="ready">可用账号</option>
                        <option value="done">已用完</option>
                        <option value="disabled">已禁用</option>
                    </select>
                    <button class="btn btn-sm btn-secondary" id="refresh-accounts-btn" style="height:32px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                        刷新
                    </button>
                    <button class="btn btn-sm btn-danger" id="batch-delete-btn" style="height:32px;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        批量删除
                    </button>
                </div>
            </div>
            <div id="accounts-table"></div>
        </div>
    `;

    // Bind OAuth events
    document.getElementById('oauth-start-btn').addEventListener('click', startOAuth);
    checkOAuthStatus();

    // Bind Import Events
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('import-file-input');
    const clearFileBtn = document.getElementById('clear-file-btn');
    const toggleManualBtn = document.getElementById('toggle-manual-input');
    const manualInputContainer = document.getElementById('manual-input-container');
    const importTextarea = document.getElementById('import-text');

    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', handleFileSelect);

    // Drag and drop event listeners
    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.add('dragover');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
            dropZone.classList.remove('dragover');
        }, false);
    });

    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length) {
            handleFile(files[0]);
        }
    });

    clearFileBtn.addEventListener('click', () => {
        fileInput.value = '';
        importTextarea.value = '';
        document.getElementById('file-info-container').style.display = 'none';
        document.getElementById('import-preview').style.display = 'none';
        showToast('已清除加载的文件', 'info');
    });

    toggleManualBtn.addEventListener('click', () => {
        const textareaGroup = manualInputContainer.querySelector('.form-textarea');
        if (textareaGroup.style.display === 'none') {
            textareaGroup.style.display = '';
            toggleManualBtn.textContent = '隐藏';
        } else {
            textareaGroup.style.display = 'none';
            toggleManualBtn.textContent = '展开';
        }
    });

    importTextarea.addEventListener('input', () => {
        document.getElementById('import-preview').style.display = 'none';
    });

    document.getElementById('import-confirm-btn').addEventListener('click', showImportPreview);

    // Bind Accounts events
    document.getElementById('account-filter').addEventListener('change', loadAccounts);
    document.getElementById('refresh-accounts-btn').addEventListener('click', loadAccounts);
    document.getElementById('batch-delete-btn').addEventListener('click', batchDeleteAccounts);

    await loadAccounts();
}

async function checkOAuthStatus() {
    const res = await api('GET', '/api/oauth/status');
    const el = document.getElementById('oauth-status');
    if (!el) return;
    if (res.success && res.data.authorized) {
        el.innerHTML = `状态: <span style="color:var(--success);margin-right:4px;">●</span> 已授权 <strong>${escapeHtml(res.data.email)}</strong> &nbsp; 授权时间: <span style="color:var(--text-secondary);font-size:12px;">${escapeHtml(res.data.time)}</span>`;
    } else {
        el.innerHTML = '状态: <span style="color:var(--error);margin-right:4px;">●</span> 未授权';
    }
}

function clearOAuthPoll() {
    if (oauthPollTimer) {
        clearInterval(oauthPollTimer);
        oauthPollTimer = null;
    }
    if (oauthPollTimeout) {
        clearTimeout(oauthPollTimeout);
        oauthPollTimeout = null;
    }
}

async function startOAuth() {
    const clientId = document.getElementById('oauth-client-id').value.trim();
    if (!clientId) { showToast('请输入 Client ID', 'warning'); return; }

    const res = await api('POST', '/api/oauth/start', { client_id: clientId });
    if (res.success) {
        window.open(res.data.auth_url, '_blank', 'width=500,height=700');
        showToast('授权窗口已打开，请在弹窗中完成登录', 'info');

        // Poll status
        clearOAuthPoll();
        oauthPollTimer = setInterval(async () => {
            if (!document.getElementById('oauth-status')) {
                clearOAuthPoll();
                return;
            }
            const status = await api('GET', '/api/oauth/status');
            if (status.success && status.data.authorized) {
                clearOAuthPoll();
                checkOAuthStatus();
                showToast(`微软账号授权成功: ${status.data.email}`, 'success');
            }
        }, 2000);
        oauthPollTimeout = setTimeout(clearOAuthPoll, 120000);
    } else {
        showToast(res.message, 'error');
    }
}

function handleFileSelect(e) {
    const files = e.target.files;
    if (files.length) {
        handleFile(files[0]);
    }
}

function handleFile(file) {
    if (!file.name.toLowerCase().endsWith('.txt')) {
        showToast('仅支持导入 .txt 文本格式文件', 'warning');
        return;
    }
    const reader = new FileReader();
    reader.onload = (ev) => {
        document.getElementById('import-text').value = ev.target.result;

        // Show file info badge
        document.getElementById('file-info-name').textContent = file.name;
        document.getElementById('file-info-size').textContent = `(${formatBytes(file.size)})`;
        document.getElementById('file-info-container').style.display = 'flex';

        showToast(`成功读取文件: ${file.name}`, 'success');
    };
    reader.onerror = () => {
        showToast(`读取文件失败: ${file.name}`, 'error');
    };
    reader.readAsText(file);
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function showImportPreview() {
    const text = document.getElementById('import-text').value.trim();
    if (!text) { showToast('请输入或粘贴账号数据，或拖放账号文件', 'warning'); return; }

    const lines = text.split('\n').filter(l => l.trim());
    const previewDiv = document.getElementById('import-preview');

    let validCount = 0;
    let invalidCount = 0;
    const PREVIEW_LIMIT = 200;
    let previewRows = 0;
    let html = '<div class="import-preview-scroll"><table style="width:100%;font-size:13px;border-collapse:collapse;"><thead><tr><th style="text-align:left;padding:8px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;border-bottom:2px solid var(--border)">邮箱账号</th><th style="text-align:left;padding:8px;color:var(--text-secondary);font-size:11px;text-transform:uppercase;border-bottom:2px solid var(--border)">格式校验</th></tr></thead><tbody>';

    lines.forEach(line => {
        const parts = line.split('----');
        const isValid = parts.length >= 4 && parts[0].trim() && parts[2].trim() && parts[3].trim();

        if (isValid) {
            validCount++;
        } else {
            invalidCount++;
        }

        if (previewRows < PREVIEW_LIMIT) {
            previewRows++;
            if (isValid) {
                html += `<tr><td style="padding:8px;border-bottom:1px solid var(--border);">${escapeHtml(parts[0].trim())}</td><td style="padding:8px;border-bottom:1px solid var(--border);color:var(--success);font-weight:600;">✓ 格式正确</td></tr>`;
            } else {
                html += `<tr><td style="padding:8px;border-bottom:1px solid var(--border);color:var(--text-muted);font-style:italic;">${escapeHtml(line.substring(0, 45))}...</td><td style="padding:8px;border-bottom:1px solid var(--border);color:var(--error);font-weight:600;">✗ 格式错误</td></tr>`;
            }
        }
    });

    html += '</tbody></table></div>';
    const hiddenCount = lines.length > PREVIEW_LIMIT ? lines.length - PREVIEW_LIMIT : 0;
    html += `<div class="import-preview-footer">
        <span>解析报告: <strong style="color:var(--success)">${validCount}</strong> 个有效账号，<strong style="color:var(--error)">${invalidCount}</strong> 个错误行${hiddenCount > 0 ? ` (仅显示前${PREVIEW_LIMIT}行)` : ''}</span>
        <button class="btn btn-success btn-sm" id="do-import-btn" ${validCount === 0 ? 'disabled' : ''}>确认导入 ${validCount} 个账号</button>
    </div>`;

    previewDiv.innerHTML = html;
    previewDiv.style.display = 'flex';

    document.getElementById('do-import-btn').addEventListener('click', confirmImport);
}

async function confirmImport() {
    const text = document.getElementById('import-text').value.trim();
    if (!text) { showToast('未检测到需要导入的数据', 'warning'); return; }
    const lines = text.split('\n').filter(l => l.trim());
    const res = await api('POST', '/api/accounts/import', { lines });
    if (res.success) {
        showToast(res.message, 'success');
        document.getElementById('import-text').value = '';
        document.getElementById('import-preview').style.display = 'none';
        document.getElementById('file-info-container').style.display = 'none';
        document.getElementById('import-file-input').value = '';
        loadAccounts();
    } else {
        showToast(res.message, 'error');
    }
}

export async function loadAccounts() {
    const filter = document.getElementById('account-filter')?.value || 'all';
    const res = await api('GET', `/api/accounts?status=${filter}`);
    const tableContainer = document.getElementById('accounts-table');
    if (!tableContainer) return;

    if (!res.success) { tableContainer.innerHTML = '<div class="table-empty">加载失败，请检查网络连接</div>'; return; }

    accountTable = createTable(tableContainer, {
        columns: [
            { title: '#', key: 'id', width: '50px', render: (r, i) => `${i + 1}` },
            { title: '邮箱账号', key: 'email', render: (r) => `<span style="font-weight:500;">${r.email}</span>` },
            { title: '服务', key: 'provider', width: '105px', render: (r) => {
                const names = { microsoft: 'Microsoft', duckmail: 'DuckMail', yyds: 'YYDS', cloudflare: 'Cloudflare', cloud_mail: 'Cloud Mail' };
                return `<span style="font-size:12.5px;">${names[r.provider] || r.provider || 'Microsoft'}</span>`;
            }},
            { title: '状态', key: 'status', width: '110px', render: (r) => {
                const map = { ready: ['badge-ready', '可用别名'], done: ['badge-done', '已用完'], disabled: ['badge-disabled', '已禁用'] };
                const [cls, text] = map[r.status] || ['', r.status];
                return `<span class="badge ${cls}">${text}</span>`;
            }},
            { title: '已用别名', width: '90px', render: (r) => `<span style="font-family:'JetBrains Mono',monospace;font-size:12.5px;font-weight:500;">${r.used_count || 0} / ${r.max_aliases}</span>` },
            { title: '注册成功数', width: '90px', render: (r) => `<span style="color:var(--success);font-weight:700;font-size:13.5px;">${r.success_count || 0}</span>` },
            { title: '操作选项', width: '130px', render: (r) => {
                const div = document.createElement('div');
                div.className = 'btn-group';
                if (r.status === 'done') {
                    const resetBtn = document.createElement('button');
                    resetBtn.className = 'btn btn-sm btn-warning';
                    resetBtn.textContent = '重置';
                    resetBtn.onclick = () => resetAccount(r.id);
                    div.appendChild(resetBtn);
                }
                const delBtn = document.createElement('button');
                delBtn.className = 'btn btn-sm btn-danger';
                delBtn.textContent = '删除';
                delBtn.onclick = () => deleteAccount(r.id);
                div.appendChild(delBtn);
                return div;
            }},
        ],
        data: res.data,
        emptyText: '暂无账号；可导入 Microsoft 邮箱，或在设置中选择临时邮箱服务后开始注册',
    });
}

async function deleteAccount(id) {
    if (!confirm('确定删除该账号？此操作不可撤销。')) return;
    const res = await api('DELETE', `/api/accounts/${id}`);
    if (res.success) { showToast('账号已安全删除', 'success'); loadAccounts(); }
    else showToast(res.message, 'error');
}

async function resetAccount(id) {
    const res = await api('POST', `/api/accounts/${id}/reset`);
    if (res.success) { showToast('账号使用状态已重置为初始状态', 'success'); loadAccounts(); }
    else showToast(res.message, 'error');
}

async function batchDeleteAccounts() {
    if (!accountTable) return;
    const ids = accountTable.getSelectedIds();
    if (!ids.length) { showToast('请先在左侧勾选要删除的账号', 'warning'); return; }
    if (!confirm(`确定要批量删除已选中的 ${ids.length} 个账号吗？`)) return;
    const res = await api('DELETE', '/api/accounts', { ids });
    if (res.success) { showToast(res.message, 'success'); loadAccounts(); }
    else showToast(res.message, 'error');
}
