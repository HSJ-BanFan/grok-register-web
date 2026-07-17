import { api } from '../api.js';
import { showToast } from '../components/toast.js';
import { createTable } from '../components/table.js';
import { animateCountNodes } from '../components/count-up.js';
import { FOLD_CHEVRON, initFoldCards, updateFoldCount } from '../components/fold.js';

let ssoTable = null;
let accTable = null;

const ICONS = {
    accounts: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
    used: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>`,
    unused: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`,
    done: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg>`,
    aliases: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
    ready: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
    failed: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`,
    sso: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`,
    today: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`,
    rate: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>`,
    duration: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
};


function svgNode(markup) {
    const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
    return document.importNode(parsed.documentElement, true);
}

function textCell(className, value) {
    const span = document.createElement('span');
    if (className) span.className = className;
    span.textContent = String(value ?? '');
    return span;
}

function metricNode(icon, value, label, tone = 'accent', valueClass = '') {
    const toneMap = {
        accent: 'var(--accent)',
        success: 'var(--success)',
        info: 'var(--info)',
        error: 'var(--error)',
        warning: 'var(--warning)',
        muted: 'var(--text-secondary)',
    };
    const color = toneMap[tone] || toneMap.accent;
    const display = value == null ? '0' : String(value);
    const card = document.createElement('div');
    card.className = 'stat-card';
    card.style.setProperty('--card-accent', color);

    const iconWrap = document.createElement('span');
    iconWrap.className = 'stat-icon';
    iconWrap.style.color = color;
    iconWrap.setAttribute('aria-hidden', 'true');
    iconWrap.appendChild(svgNode(icon));

    const metricValue = document.createElement('div');
    metricValue.className = `stat-value count-up${valueClass ? ` ${valueClass}` : ''}`;
    metricValue.style.color = color;
    metricValue.dataset.countValue = display;
    metricValue.textContent = '0';

    const metricLabel = document.createElement('div');
    metricLabel.className = 'stat-label';
    metricLabel.textContent = label;
    card.append(iconWrap, metricValue, metricLabel);
    return card;
}

export async function render(container) {
    container.innerHTML = `
        <div class="card">
            <div class="card-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>
                系统统计概览
            </div>
            <div class="stats-grid" id="stats-grid"></div>
        </div>

        <div class="card fold-card" data-fold="sso" id="fold-sso">
            <div class="card-header">
                <button type="button" class="fold-toggle" id="fold-sso-toggle" aria-expanded="false" aria-controls="fold-sso-body">
                    <span class="fold-chevron" aria-hidden="true">${FOLD_CHEVRON}</span>
                    <div class="card-title">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                        SSO 会话令牌 (已成功注册)
                    </div>
                    <span class="fold-meta">
                        <span class="fold-count" id="sso-count">0</span>
                        <span class="fold-hint">点击展开</span>
                    </span>
                </button>
                <div class="btn-group actions-tight fold-actions">
                    <button class="btn btn-sm btn-success" id="sso-reactivate-btn" title="对历史成功账号逐个补做 TOS/生日/Cloudflare 激活">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                        批量补激活
                    </button>
                    <button class="btn btn-sm btn-secondary" id="sso-copy-btn">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        全选复制
                    </button>
                    <button class="btn btn-sm btn-primary" id="sso-export-btn">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        导出为文本
                    </button>
                    <button class="btn btn-sm btn-danger" id="sso-clear-btn">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        清空记录
                    </button>
                </div>
            </div>
            <div class="fold-body" id="fold-sso-body" role="region" aria-labelledby="fold-sso-toggle">
                <div class="fold-body-inner">
                    <div class="fold-scroll" id="sso-table"></div>
                </div>
            </div>
        </div>

        <div class="card fold-card" data-fold="accounts" id="fold-accounts">
            <div class="card-header">
                <button type="button" class="fold-toggle" id="fold-acc-toggle" aria-expanded="false" aria-controls="fold-acc-body">
                    <span class="fold-chevron" aria-hidden="true">${FOLD_CHEVRON}</span>
                    <div class="card-title">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                        已成功注册 Grok 的别名邮箱账号
                    </div>
                    <span class="fold-meta">
                        <span class="fold-count" id="acc-count">0</span>
                        <span class="fold-hint">点击展开</span>
                    </span>
                </button>
                <div class="btn-group actions-tight fold-actions">
                    <button class="btn btn-sm btn-secondary" id="acc-copy-btn">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        全选复制
                    </button>
                    <button class="btn btn-sm btn-primary" id="acc-export-btn">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        导出为文本
                    </button>
                    <button class="btn btn-sm btn-danger" id="acc-clear-btn">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        清空记录
                    </button>
                </div>
            </div>
            <div class="fold-body" id="fold-acc-body" role="region" aria-labelledby="fold-acc-toggle">
                <div class="fold-body-inner">
                    <div class="fold-scroll" id="acc-table"></div>
                </div>
            </div>
        </div>
    `;

    initFoldCards(container);
    await Promise.all([loadStats(), loadSSO(), loadAccounts()]);

    document.getElementById('sso-reactivate-btn').addEventListener('click', startBatchReactivate);
    document.getElementById('sso-copy-btn').addEventListener('click', copyAllSSO);
    document.getElementById('sso-export-btn').addEventListener('click', () => exportData('sso'));
    document.getElementById('sso-clear-btn').addEventListener('click', () => clearData('sso'));
    document.getElementById('acc-copy-btn').addEventListener('click', copyAllAccounts);
    document.getElementById('acc-export-btn').addEventListener('click', () => exportData('accounts'));
    document.getElementById('acc-clear-btn').addEventListener('click', () => clearData('accounts'));
}

async function startBatchReactivate() {
    const statusRes = await api('GET', '/api/register/status');
    if (statusRes.success && statusRes.data && statusRes.data.status === 'running') {
        showToast('当前已有任务在运行，请先停止后再补激活', 'warning');
        return;
    }

    const ssoRes = await api('GET', '/api/results/sso');
    const total = (ssoRes.success && ssoRes.data) ? ssoRes.data.length : 0;
    if (!total) {
        showToast('没有可补激活的历史 SSO 记录', 'warning');
        return;
    }

    const confirmed = confirm(
        `将对 ${total} 个历史成功账号逐个补做 Web 激活：\n` +
        `· 注入 SSO\n· 接受 TOS\n· 设置生日\n· 刷新 Cloudflare 出口上下文\n\n` +
        `不会重新注册，也不会破坏已有 Web ↔ Build 关联。\n\n确定开始？`
    );
    if (!confirmed) return;

    const btn = document.getElementById('sso-reactivate-btn');
    if (btn) btn.disabled = true;
    const res = await api('POST', '/api/register/reactivate', { limit: 0 });
    if (res.success) {
        showToast(`已启动批量补激活（${total} 个账号）。请到「注册控制」查看实时日志。`, 'success');
    } else {
        showToast(res.message || '启动补激活失败', 'error');
        if (btn) btn.disabled = false;
    }
}

async function loadStats() {
    const res = await api('GET', '/api/accounts/stats');
    const grid = document.getElementById('stats-grid');
    if (!grid || !res.success) return;
    const s = res.data || {};
    grid.replaceChildren(
        metricNode(ICONS.accounts, s.total_accounts, '主账号总数', 'accent'),
        metricNode(ICONS.used, s.used_accounts, '已使用主账号', 'success'),
        metricNode(ICONS.unused, s.unused_accounts, '未使用主账号', 'info'),
        metricNode(ICONS.done, s.done_accounts, '额度已用完账号', 'muted'),
        metricNode(ICONS.aliases, s.total_aliases, '系统别名总数', 'accent'),
        metricNode(ICONS.used, s.used_aliases, '已使用别名数', 'success'),
        metricNode(ICONS.ready, s.ready_aliases, '待分配别名数', 'info'),
        metricNode(ICONS.failed, s.failed_aliases, '失败别名数', 'error'),
        metricNode(ICONS.sso, s.total_sso, '成功采集 SSO 数', 'accent'),
        metricNode(ICONS.today, s.today_sso, '今日采集 SSO 数', 'info'),
        metricNode(ICONS.rate, `${s.success_rate ?? 0}%`, '别名注册成功率', 'success'),
        metricNode(ICONS.duration, `${s.avg_duration ?? 0}s`, '平均注册耗时', 'warning'),
    );
    animateCountNodes(grid, { duration: 920, stagger: 40 });
}

async function loadSSO() {
    const res = await api('GET', '/api/results/sso');
    const el = document.getElementById('sso-table');
    if (!el) return;
    const rows = res.data || [];
    updateFoldCount('sso-count', rows.length);
    ssoTable = createTable(el, {
        columns: [
            { title: '#', width: '50px', render: (r, i) => `${i + 1}` },
            { title: '注册别名邮箱', key: 'email', render: (r) => textCell('font-medium', r.email) },
            { title: 'SSO Session Token', render: (r) => {
                const sso = String(r.sso_value || '');
                const container = document.createElement('div');
                container.className = 'sso-cell';

                const span = document.createElement('span');
                span.className = 'sso-text';
                span.textContent = sso.substring(0, 36) + '...';
                span.title = sso;

                const btn = document.createElement('button');
                btn.className = 'copy-btn';
                btn.type = 'button';
                btn.setAttribute('aria-label', '复制此 SSO 令牌');
                btn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
                btn.addEventListener('click', () => {
                    navigator.clipboard.writeText(sso);
                    showToast('SSO 令牌已复制到剪切板', 'success');
                });

                container.appendChild(span);
                container.appendChild(btn);
                return container;
            }},
            { title: '采集时间', key: 'created_at', width: '150px', render: (r) => textCell('time-cell', r.created_at ? String(r.created_at).substring(0, 16) : '') },
        ],
        data: rows,
        emptyText: '暂无已采收的 SSO Token 记录',
    });
}

async function loadAccounts() {
    const res = await api('GET', '/api/results/accounts');
    const el = document.getElementById('acc-table');
    if (!el) return;
    const rows = res.data || [];
    updateFoldCount('acc-count', rows.length);
    accTable = createTable(el, {
        columns: [
            { title: '#', width: '50px', render: (r, i) => `${i + 1}` },
            { title: '注册别名邮箱账号', key: 'email', render: (r) => textCell('font-medium', r.email) },
            { title: '登录密码', key: 'account_password', render: (r) => textCell('mono', r.account_password) },
            { title: '完成时间', key: 'created_at', width: '150px', render: (r) => textCell('time-cell', r.created_at ? String(r.created_at).substring(0, 16) : '') },
        ],
        data: rows,
        emptyText: '暂无已成功完成注册的邮箱别名记录',
    });
}

async function copyAllSSO() {
    const res = await api('GET', '/api/results/sso');
    if (res.success && res.data.length) {
        const text = res.data.map(r => r.sso_value).join('\n');
        await navigator.clipboard.writeText(text);
        showToast(`已批量复制 ${res.data.length} 条 SSO 会话 Token`, 'success');
    } else {
        showToast('没有可导出的 SSO 数据', 'warning');
    }
}

async function copyAllAccounts() {
    const res = await api('GET', '/api/results/accounts');
    if (res.success && res.data.length) {
        const text = res.data.map(r => `${r.email}----${r.account_password}`).join('\n');
        await navigator.clipboard.writeText(text);
        showToast(`已批量复制 ${res.data.length} 个账号密码凭证`, 'success');
    } else {
        showToast('没有可导出的账号数据', 'warning');
    }
}

async function exportData(type) {
    const res = await api('POST', `/api/results/${type}/export`, {});
    if (res.success) showToast(`数据导出成功，已保存至后台配置的数据目录`, 'success');
    else showToast(res.message || '数据导出失败', 'error');
}

async function clearData(type) {
    const label = type === 'sso' ? 'SSO Token' : '已注册账号';
    if (!confirm(`安全警告: 确定清空数据库中的所有 ${label} 记录吗？该操作不可逆！`)) return;
    const res = await api('DELETE', `/api/results/${type}`);
    if (res.success) {
        showToast('所有历史记录已安全清空', 'success');
        render(document.getElementById('main-content'));
    } else {
        showToast(res.message, 'error');
    }
}
