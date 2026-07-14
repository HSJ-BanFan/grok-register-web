const NAV_ITEMS = [
    { hash: '#/email',    icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>`, label: '邮箱' },
    { hash: '#/register', icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`, label: '注册' },
    { hash: '#/results',  icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>`, label: '结果' },
    { hash: '#/settings', icon: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 1v2m0 18v2m-9-11h2m18 0h2m-4.22-5.78-1.42 1.42M6.34 17.66l-1.42 1.42m0-13.84 1.42 1.42m11.32 11.32 1.42 1.42"/></svg>`, label: '设置' },
];

export function renderSidebar(container) {
    container.innerHTML = '';
    const ul = document.createElement('ul');
    ul.className = 'sidebar-nav';

    NAV_ITEMS.forEach(item => {
        const li = document.createElement('li');
        li.className = 'sidebar-item';
        if (location.hash === item.hash || (!location.hash && item.hash === '#/email')) {
            li.classList.add('active');
        }
        li.innerHTML = `<a href="${item.hash}"><span class="sidebar-icon">${item.icon}</span><span class="sidebar-label">${item.label}</span></a>`;
        ul.appendChild(li);
    });

    container.appendChild(ul);

    // Update active on hash change
    window.addEventListener('hashchange', () => {
        const items = container.querySelectorAll('.sidebar-item');
        items.forEach(li => {
            const a = li.querySelector('a');
            li.classList.toggle('active', a.getAttribute('href') === location.hash);
        });
    });
}
