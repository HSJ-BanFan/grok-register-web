const NAV_GROUPS = [
    {
        label: '工作流',
        items: [
            {
                hash: '#/email',
                label: '邮箱',
                icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>`,
            },
            {
                hash: '#/register',
                label: '注册',
                icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
            },
            {
                hash: '#/results',
                label: '结果',
                icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>`,
            },
        ],
    },
    {
        label: '系统',
        items: [
            {
                hash: '#/settings',
                label: '设置',
                icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M12 1v2m0 18v2m-9-11h2m18 0h2m-4.22-5.78-1.42 1.42M6.34 17.66l-1.42 1.42m0-13.84 1.42 1.42m11.32 11.32 1.42 1.42"/></svg>`,
            },
        ],
    },
];

function currentHash() {
    return location.hash || '#/email';
}

function setActive(container) {
    const hash = currentHash();
    container.querySelectorAll('.nav-item').forEach((btn) => {
        const active = btn.dataset.hash === hash;
        btn.classList.toggle('active', active);
        if (active) btn.setAttribute('aria-current', 'page');
        else btn.removeAttribute('aria-current');
    });
}

export function renderSidebar(container) {
    container.innerHTML = `
        <div class="sidebar-head">
            <div class="brand-dot" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 12.5 11.4 15 15.5 9.5"/><path d="M4 8.5V7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v1.5"/><path d="M4 15.5V17a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-1.5"/></svg>
            </div>
            <div>
                <strong>Grok Register</strong>
                <span>Ops Console</span>
            </div>
        </div>
        <nav class="nav-list" aria-label="页面导航"></nav>
        <div class="sidebar-footer">
            <div class="sidebar-status-card">
                <span class="status-badge success">本地控制台</span>
                <span class="status-text">邮箱导入 · 注册任务 · SSO 导出</span>
            </div>
        </div>
    `;

    const nav = container.querySelector('.nav-list');
    NAV_GROUPS.forEach((group) => {
        const label = document.createElement('p');
        label.className = 'nav-group-label';
        label.textContent = group.label;
        nav.appendChild(label);

        group.items.forEach((item) => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'nav-item';
            btn.dataset.hash = item.hash;
            btn.innerHTML = `${item.icon}<span class="nav-label">${item.label}</span>`;
            btn.addEventListener('click', () => {
                if (location.hash !== item.hash) {
                    location.hash = item.hash;
                }
                document.body.classList.remove('sidebar-open');
            });
            nav.appendChild(btn);
        });
    });

    setActive(container);
    window.addEventListener('hashchange', () => setActive(container));
}
