import { api } from '../api.js';
import { showToast } from '../components/toast.js';
import { createLogPanel } from '../components/log-panel.js';
import { connectSocket } from '../websocket.js';
import { countUp } from '../components/count-up.js';

let logPanel = null;

const PAUSE_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`;
const RESUME_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;

function setPauseButton(paused) {
    const btn = document.getElementById('pause-btn');
    if (!btn) return;
    const markup = paused ? RESUME_ICON : PAUSE_ICON;
    const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
    const icon = document.importNode(parsed.documentElement, true);
    btn.dataset.action = paused ? 'resume' : 'pause';
    btn.replaceChildren(icon, document.createTextNode(paused ? ' 继续任务' : ' 暂停'));
}

export async function render(container) {
    container.innerHTML = `
        <div class="card">
            <div class="card-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
                注册任务控制台
            </div>
            <p class="card-desc">启动 / 暂停 / 停止注册 Worker，并实时查看当前轮次与活跃账号。</p>

            <div class="btn-group control-actions">
                <button class="btn btn-success" id="start-btn">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                    启动注册任务
                </button>
                <button class="btn btn-primary" id="reactivate-btn" title="对历史成功账号补做 TOS/生日/Cloudflare 激活">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                    批量补激活旧账号
                </button>
                <button class="btn btn-warning" id="pause-btn" disabled>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>
                    暂停
                </button>
                <button class="btn btn-danger" id="stop-btn" disabled>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>
                    停止任务
                </button>
            </div>

            <div id="reg-status">
                <div class="dashboard-head">
                    <span class="dashboard-head-title">注册任务状态仪表盘</span>
                    <span class="badge badge-stopped">已停止</span>
                </div>
                <div class="mini-stats-grid">
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">当前进程</span>
                        <span class="mini-stat-val text-accent">等待中</span>
                    </div>
                    <div class="mini-stat-card span-2">
                        <span class="mini-stat-label">活跃处理账号</span>
                        <span class="mini-stat-val sm">无活跃账号</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">完成轮数</span>
                        <span class="mini-stat-val">0 轮</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">成功别名</span>
                        <span class="mini-stat-val text-success">0</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">已失败别名</span>
                        <span class="mini-stat-val text-error">0</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">Chat 通过</span>
                        <span class="mini-stat-val text-success">0</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">Chat 无权限</span>
                        <span class="mini-stat-val text-error">0</span>
                    </div>
                    <div class="mini-stat-card">
                        <span class="mini-stat-label">Chat 未检/失败</span>
                        <span class="mini-stat-val">0</span>
                    </div>
                </div>
            </div>
        </div>

        <div class="card log-panel" id="log-panel-container"></div>
    `;

    logPanel = createLogPanel(document.getElementById('log-panel-container'));

    connectSocket({
        onLog: (data) => logPanel.addLog(data),
        onLogReplay: (data) => {
            if (data && Array.isArray(data.entries)) {
                for (const entry of data.entries) {
                    logPanel.addLog(entry);
                }
            }
        },
        onStatusUpdate: (data) => updateStatus(data),
        onRoundComplete: (data) => {
            if (data.success) showToast(`第 ${data.round} 轮注册成功! 耗时: ${data.duration}秒`, 'success');
        },
        onError: (data) => showToast(data.message, 'error'),
    });

    const statusRes = await api('GET', '/api/register/status');
    if (statusRes.success) updateStatus(statusRes.data);

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
        setPauseButton(false);
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
        setPauseButton(false);
    } else {
        showToast(res.message, 'error');
    }
}

async function pauseRegistration() {
    const btn = document.getElementById('pause-btn');
    if (btn.dataset.action !== 'resume') {
        const res = await api('POST', '/api/register/pause');
        if (!res.success) {
            showToast(res.message || '暂停任务失败', 'error');
            return;
        }
        setPauseButton(true);
        showToast('任务已发起暂停指令', 'warning');
    } else {
        const res = await api('POST', '/api/register/resume');
        if (!res.success) {
            showToast(res.message || '继续任务失败', 'error');
            return;
        }
        setPauseButton(false);
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
        setPauseButton(false);
    }
}

function ensureStatusShell(root, labels) {
    if (root.dataset.ready === '1') {
        const successLabel = root.querySelector('[data-role="success-label"]');
        const failedLabel = root.querySelector('[data-role="failed-label"]');
        if (successLabel) successLabel.textContent = labels.success;
        if (failedLabel) failedLabel.textContent = labels.failed;
        return;
    }

    root.innerHTML = `
        <div class="dashboard-head">
            <span class="dashboard-head-title" id="dashboard-title">注册任务状态仪表盘</span>
            <span class="badge badge-stopped" id="status-badge">已停止</span>
        </div>
        <div class="mini-stats-grid">
            <div class="mini-stat-card">
                <span class="mini-stat-label">当前进程</span>
                <span class="mini-stat-val text-accent" id="cur-round">等待中</span>
            </div>
            <div class="mini-stat-card span-2">
                <span class="mini-stat-label">活跃处理账号</span>
                <span class="mini-stat-val sm" id="cur-email">无活跃账号</span>
            </div>
            <div class="mini-stat-card">
                <span class="mini-stat-label">完成轮数</span>
                <span class="mini-stat-val count-up" id="completed">0 轮</span>
            </div>
            <div class="mini-stat-card">
                <span class="mini-stat-label" data-role="success-label">${labels.success}</span>
                <span class="mini-stat-val text-success count-up" id="success-count">0</span>
            </div>
            <div class="mini-stat-card">
                <span class="mini-stat-label" data-role="failed-label">${labels.failed}</span>
                <span class="mini-stat-val text-error count-up" id="failed-count">0</span>
            </div>
            <div class="mini-stat-card" title="上传前 chat probe 返回 2xx 的账号数（本任务会话）">
                <span class="mini-stat-label">Chat 通过</span>
                <span class="mini-stat-val text-success count-up" id="chat-passed">0</span>
            </div>
            <div class="mini-stat-card" title="chat probe 403/401 无权限的账号数（本任务会话）">
                <span class="mini-stat-label">Chat 无权限</span>
                <span class="mini-stat-val text-error count-up" id="chat-denied">0</span>
            </div>
            <div class="mini-stat-card" title="未开启 probe / 探测失败 / 未走上传的成功注册">
                <span class="mini-stat-label">Chat 未检/失败</span>
                <span class="mini-stat-val count-up" id="chat-other">0</span>
            </div>
        </div>
    `;
    root.dataset.ready = '1';
}

function updateStatus(data) {
    const statusMap = {
        running: ['badge-running', '正在运行'],
        waiting: ['badge-paused', '等待下一轮'],
        paused: ['badge-paused', '任务已暂停'],
        stopped: ['badge-stopped', '任务已停止'],
    };
    const [cls, text] = statusMap[data.status] || statusMap.stopped;

    const isReactivate = data.mode === 'reactivate';
    const currentRoundText = data.status === 'waiting' && data.next_round_in > 0
        ? `${data.next_round_in} 秒后开始`
        : (data.current_round !== undefined && data.current_round > 0 ? `第 ${data.current_round} 轮` : '等待中');
    const activeWorkers = Array.isArray(data.active_workers) ? data.active_workers : [];
    const currentEmailText = activeWorkers.length
        ? activeWorkers.map(worker => `${worker.worker_id}: ${worker.email}`).join(' | ')
        : (data.current_email || '无活跃账号');
    const dashboardTitle = isReactivate ? '旧账号补激活状态仪表盘' : '注册任务状态仪表盘';
    const successLabel = isReactivate ? '补激活成功' : '成功别名';
    const failedLabel = isReactivate ? '补激活失败' : '已失败别名';

    const root = document.getElementById('reg-status');
    if (!root) return;
    ensureStatusShell(root, { success: successLabel, failed: failedLabel });

    const titleEl = document.getElementById('dashboard-title');
    const badgeEl = document.getElementById('status-badge');
    const roundEl = document.getElementById('cur-round');
    const emailEl = document.getElementById('cur-email');
    const completedEl = document.getElementById('completed');
    const successEl = document.getElementById('success-count');
    const failedEl = document.getElementById('failed-count');
    const chatPassedEl = document.getElementById('chat-passed');
    const chatDeniedEl = document.getElementById('chat-denied');
    const chatOtherEl = document.getElementById('chat-other');

    if (titleEl) titleEl.textContent = dashboardTitle;
    if (badgeEl) {
        const prev = badgeEl.dataset.status || '';
        badgeEl.className = `badge ${cls}`;
        badgeEl.textContent = text;
        if (prev && prev !== (data.status || 'stopped')) {
            badgeEl.classList.remove('is-flash');
            void badgeEl.offsetWidth;
            badgeEl.classList.add('is-flash');
        }
        badgeEl.dataset.status = data.status || 'stopped';
    }
    if (roundEl) roundEl.textContent = currentRoundText;
    if (emailEl) {
        emailEl.textContent = currentEmailText;
        emailEl.title = currentEmailText;
    }

    // Animate numeric tiles from previous displayed value → new value
    if (completedEl) countUp(completedEl, `${data.completed || 0} 轮`, { duration: 640 });
    if (successEl) countUp(successEl, data.success || 0, { duration: 640 });
    if (failedEl) countUp(failedEl, data.failed || 0, { duration: 640 });

    const chatPassed = Number(data.chat_probe_passed || 0);
    const chatDenied = Number(data.chat_probe_denied || 0);
    const chatFailed = Number(data.chat_probe_failed || 0);
    const chatSkipped = Number(data.chat_probe_skipped || 0);
    // "未检/失败" = skipped + non-permission failures. Successful regs without upload
    // do not increment probe counters (upload path only).
    const chatOther = chatFailed + chatSkipped;
    if (chatPassedEl) countUp(chatPassedEl, chatPassed, { duration: 640 });
    if (chatDeniedEl) countUp(chatDeniedEl, chatDenied, { duration: 640 });
    if (chatOtherEl) countUp(chatOtherEl, chatOther, { duration: 640 });

    if (data.status === 'stopped') {
        document.getElementById('start-btn').disabled = false;
        document.getElementById('reactivate-btn').disabled = false;
        document.getElementById('pause-btn').disabled = true;
        document.getElementById('stop-btn').disabled = true;
        setPauseButton(false);
    } else {
        document.getElementById('start-btn').disabled = true;
        document.getElementById('reactivate-btn').disabled = true;
        document.getElementById('pause-btn').disabled = false;
        document.getElementById('stop-btn').disabled = false;
        setPauseButton(data.status === 'paused');
    }
}
