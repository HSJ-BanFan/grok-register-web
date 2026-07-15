import { api } from '../api.js';
import { showToast } from '../components/toast.js';
import { createLogPanel } from '../components/log-panel.js';
import { connectSocket } from '../websocket.js';

let logPanel = null;

export async function render(container) {
    container.innerHTML = `
        <div class="card">
            <div class="card-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                注册任务控制台
            </div>
            

            
            <div class="btn-group" style="margin: 20px 0 24px 0;">
                <button class="btn btn-success" id="start-btn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    启动注册任务
                </button>
                <button class="btn btn-primary" id="reactivate-btn" title="对历史成功账号补做 TOS/生日/Cloudflare 激活">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                    批量补激活旧账号
                </button>
                <button class="btn btn-warning" id="pause-btn" disabled>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
                    暂停
                </button>
                <button class="btn btn-danger" id="stop-btn" disabled>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
                    停止任务
                </button>
            </div>
            
            <!-- Sleek Dashboard Status Grid -->
            <div id="reg-status">
                <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:10px;">
                    <span style="font-size:13.5px;color:var(--text-secondary);font-weight:600;">注册任务状态仪表盘</span>
                    <span class="badge badge-stopped">已停止</span>
                </div>
                <div class="mini-stats-grid">
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">当前进程</span>
                        <span class="mini-stat-val" style="color:var(--accent);">等待中</span>
                    </div>
                    <div class="mini-stat-card" style="grid-column: span 2;">
                        <span class="mini-stat-label">活跃处理账号</span>
                        <span class="mini-stat-val" style="font-size:14px;word-break:break-all;">无活跃账号</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">完成轮数</span>
                        <span class="mini-stat-val">0 轮</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">成功别名</span>
                        <span class="mini-stat-val" style="color:var(--success);">0</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">已失败别名</span>
                        <span class="mini-stat-val" style="color:var(--error);">0</span>
                    </div>
                </div>
            </div>
        </div>

        <div class="card log-panel" id="log-panel-container"></div>
    `;

    logPanel = createLogPanel(document.getElementById('log-panel-container'));

    // Connect WebSocket for updates
    connectSocket({
        onLog: (data) => logPanel.addLog(data),
        onStatusUpdate: (data) => updateStatus(data),
        onRoundComplete: (data) => {
            if (data.success) showToast(`第 ${data.round} 轮注册成功! 耗时: ${data.duration}秒`, 'success');
        },
        onError: (data) => showToast(data.message, 'error'),
    });

    // Load current status
    const statusRes = await api('GET', '/api/register/status');
    if (statusRes.success) updateStatus(statusRes.data);

    // Button event listeners
    document.getElementById('start-btn').addEventListener('click', startRegistration);
    document.getElementById('reactivate-btn').addEventListener('click', startReactivation);
    document.getElementById('pause-btn').addEventListener('click', pauseRegistration);
    document.getElementById('stop-btn').addEventListener('click', stopRegistration);
}

async function startRegistration() {
    const settingsRes = await api('GET', '/api/settings');
    const maxRetries = settingsRes.success ? parseInt(settingsRes.data.max_retries_per_alias) || 3 : 3;
    const concurrency = settingsRes.success ? parseInt(settingsRes.data.registration_concurrency) || 1 : 1;
    const res = await api('POST', '/api/register/start', {
        max_rounds: 0,
        max_retries: maxRetries,
        concurrency,
    });
    if (res.success) {
        showToast(`任务已启动，正在拉起 ${concurrency} 个浏览器 Worker...`, 'success');
        document.getElementById('start-btn').disabled = true;
        document.getElementById('reactivate-btn').disabled = true;
        document.getElementById('pause-btn').disabled = false;
        document.getElementById('stop-btn').disabled = false;
    } else {
        showToast(res.message, 'error');
    }
}

async function startReactivation() {
    const ssoRes = await api('GET', '/api/results/sso');
    const total = (ssoRes.success && ssoRes.data) ? ssoRes.data.length : 0;
    if (!total) {
        showToast('没有可补激活的历史 SSO 记录', 'warning');
        return;
    }
    if (!confirm(`将对 ${total} 个历史成功账号逐个补做 Web 激活（TOS/生日/Cloudflare）。\n不会重新注册，也不会破坏已有 Web ↔ Build 关联。\n\n确定开始？`)) {
        return;
    }
    const res = await api('POST', '/api/register/reactivate', { limit: 0 });
    if (res.success) {
        showToast(`批量补激活已启动（${total} 个账号）`, 'success');
        document.getElementById('start-btn').disabled = true;
        document.getElementById('reactivate-btn').disabled = true;
        document.getElementById('pause-btn').disabled = false;
        document.getElementById('stop-btn').disabled = false;
    } else {
        showToast(res.message, 'error');
    }
}

async function pauseRegistration() {
    const btn = document.getElementById('pause-btn');
    if (btn.textContent.includes('暂停')) {
        await api('POST', '/api/register/pause');
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> 继续任务`;
        showToast('任务已发起暂停指令', 'warning');
    } else {
        await api('POST', '/api/register/resume');
        btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> 暂停`;
        showToast('任务已继续运行', 'info');
    }
}

async function stopRegistration() {
    const res = await api('POST', '/api/register/stop');
    if (res.success) {
        showToast('已发送中止任务请求，正在安全关闭进程...', 'info');
        document.getElementById('start-btn').disabled = false;
        document.getElementById('reactivate-btn').disabled = false;
        document.getElementById('pause-btn').disabled = true;
        document.getElementById('stop-btn').disabled = true;
    }
}

function updateStatus(data) {
    const statusMap = {
        running: ['badge-running', '正在运行'],
        paused: ['badge-paused', '任务已暂停'],
        stopped: ['badge-stopped', '任务已停止'],
    };
    const [cls, text] = statusMap[data.status] || statusMap.stopped;

    const isReactivate = data.mode === 'reactivate';
    const currentRoundText = data.current_round !== undefined && data.current_round > 0 ? `第 ${data.current_round} 轮` : '等待中';
    const activeWorkers = Array.isArray(data.active_workers) ? data.active_workers : [];
    const currentEmailText = activeWorkers.length
        ? activeWorkers.map(worker => `${worker.worker_id}: ${worker.email}`).join(' | ')
        : (data.current_email || '无活跃账号');
    const dashboardTitle = isReactivate ? '旧账号补激活状态仪表盘' : '注册任务状态仪表盘';
    const successLabel = isReactivate ? '补激活成功' : '成功别名';
    const failedLabel = isReactivate ? '补激活失败' : '已失败别名';

    document.getElementById('reg-status').innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:10px;">
            <span style="font-size:13.5px;color:var(--text-secondary);font-weight:600;">${dashboardTitle}</span>
            <span class="badge ${cls}">${text}</span>
        </div>
        <div class="mini-stats-grid">
            <div class="mini-stat-card">
                <span class="mini-stat-label">当前进程</span>
                <span class="mini-stat-val" id="cur-round" style="color:var(--accent);">${currentRoundText}</span>
            </div>
            <div class="mini-stat-card" style="grid-column: span 2;">
                <span class="mini-stat-label">活跃处理账号</span>
                <span class="mini-stat-val" id="cur-email" style="font-size:14px;word-break:break-all;" title="${currentEmailText}">${currentEmailText}</span>
            </div>
            <div class="mini-stat-card">
                <span class="mini-stat-label">完成轮数</span>
                <span class="mini-stat-val" id="completed">${data.completed || 0} 轮</span>
            </div>
            <div class="mini-stat-card">
                <span class="mini-stat-label">${successLabel}</span>
                <span class="mini-stat-val" id="success-count" style="color:var(--success);">${data.success || 0}</span>
            </div>
            <div class="mini-stat-card">
                <span class="mini-stat-label">${failedLabel}</span>
                <span class="mini-stat-val" id="failed-count" style="color:var(--error);">${data.failed || 0}</span>
            </div>
        </div>
    `;

    if (data.status === 'stopped') {
        document.getElementById('start-btn').disabled = false;
        document.getElementById('reactivate-btn').disabled = false;
        document.getElementById('pause-btn').disabled = true;
        document.getElementById('stop-btn').disabled = true;
        document.getElementById('pause-btn').innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> 暂停`;
    } else {
        document.getElementById('start-btn').disabled = true;
        document.getElementById('reactivate-btn').disabled = true;
        document.getElementById('pause-btn').disabled = false;
        document.getElementById('stop-btn').disabled = false;
    }
}
