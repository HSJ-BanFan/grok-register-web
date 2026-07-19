import { renderSidebar } from './components/sidebar.js';
import { initAboutTrigger } from './components/about.js';
import { connectSocket } from './websocket.js';
import * as EmailPage from './pages/email.js';
import * as RegisterPage from './pages/register.js';
import * as ResultsPage from './pages/results.js';
import * as SettingsPage from './pages/settings.js';

const routes = {
    '#/email': EmailPage,
    '#/register': RegisterPage,
    '#/results': ResultsPage,
    '#/settings': SettingsPage,
};

const PAGE_META = {
    '#/email': { title: '邮箱', eyebrow: 'Account Intake' },
    '#/register': { title: '注册', eyebrow: 'Task Console' },
    '#/results': { title: '结果', eyebrow: 'SSO & Stats' },
    '#/settings': { title: '设置', eyebrow: 'System Config' },
};

const prefersReducedMotion = () =>
    typeof window !== 'undefined'
    && window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let currentHash = '';
let navToken = 0;

function clearPageMotion(el) {
    if (!el) return;
    el.classList.remove('is-page-exit', 'is-page-hold', 'is-page-enter');
}

function waitAnimation(el, fallbackMs = 280) {
    return new Promise((resolve) => {
        if (!el || prefersReducedMotion()) {
            resolve();
            return;
        }
        let done = false;
        const finish = () => {
            if (done) return;
            done = true;
            el.removeEventListener('animationend', onEnd);
            resolve();
        };
        const onEnd = (event) => {
            if (event.target === el) finish();
        };
        el.addEventListener('animationend', onEnd);
        window.setTimeout(finish, fallbackMs);
    });
}

function updatePageChrome(hash) {
    const meta = PAGE_META[hash] || PAGE_META['#/email'];
    const titleEl = document.getElementById('page-title');
    const eyebrowEl = document.getElementById('page-eyebrow');
    document.title = `${meta.title} · Grok 自动注册工具`;
    if (titleEl) {
        titleEl.classList.remove('is-swapping');
        titleEl.textContent = meta.title;
    }
    if (eyebrowEl) {
        eyebrowEl.classList.remove('is-swapping');
        eyebrowEl.textContent = meta.eyebrow;
    }
}

async function mountPage(hash, mainContent, { animateEnter = true } = {}) {
    const page = routes[hash] || routes['#/email'];
    const resolved = routes[hash] ? hash : '#/email';
    updatePageChrome(resolved);

    // Keep the shell fully transparent while swapping DOM so the new
    // page never paints at opacity:1 before the enter animation starts.
    mainContent.classList.remove('is-page-exit', 'is-page-enter');
    if (animateEnter && !prefersReducedMotion()) {
        mainContent.classList.add('is-page-hold');
    } else {
        mainContent.classList.remove('is-page-hold');
    }

    await page.render(mainContent);

    if (animateEnter && !prefersReducedMotion()) {
        // hold → enter in one frame: reflow then swap classes
        void mainContent.offsetWidth;
        mainContent.classList.remove('is-page-hold');
        mainContent.classList.add('is-page-enter');
        await waitAnimation(mainContent, 340);
        mainContent.classList.remove('is-page-enter');
    }
}

async function navigate() {
    const hash = location.hash || '#/email';
    const mainContent = document.getElementById('main-content');
    if (!mainContent) return;

    if (hash === currentHash && mainContent.childElementCount) {
        ++navToken;
        clearPageMotion(mainContent);
        return;
    }

    const token = ++navToken;
    const reduced = prefersReducedMotion();

    // First paint or reduced motion: mount without exit/enter flash path
    if (!currentHash || reduced) {
        try {
            clearPageMotion(mainContent);
            await mountPage(hash, mainContent, { animateEnter: Boolean(currentHash) && !reduced });
            if (token === navToken) currentHash = hash;
        } catch (err) {
            console.error('navigate failed', err);
        }
        return;
    }

    try {
        // 1) Fade old page out and stay at opacity 0 (fill-mode: forwards + hold class)
        mainContent.classList.remove('is-page-enter', 'is-page-hold');
        mainContent.classList.add('is-page-exit');
        await waitAnimation(mainContent, 240);
        if (token !== navToken) return;

        // 2) Lock transparent before removing exit (prevents opacity snap-back flash)
        mainContent.classList.add('is-page-hold');
        mainContent.classList.remove('is-page-exit');

        // 3) Replace DOM while invisible, then enter
        await mountPage(hash, mainContent, { animateEnter: true });
        if (token === navToken) currentHash = hash;
    } catch (err) {
        console.error('navigate failed', err);
        clearPageMotion(mainContent);
    }
}

function initMobileNav() {
    const menuBtn = document.getElementById('mobile-menu');
    const scrim = document.getElementById('sidebar-scrim');
    const sidebar = document.getElementById('sidebar');
    if (!menuBtn || !scrim || !sidebar) return;

    const sync = (open, { focusMenu = false, restoreFocus = false } = {}) => {
        const mobile = window.innerWidth <= 960;
        const expanded = mobile && open;
        document.body.classList.toggle('sidebar-open', expanded);
        menuBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        menuBtn.setAttribute('aria-label', expanded ? '关闭导航' : '打开导航');
        sidebar.setAttribute('aria-hidden', mobile && !expanded ? 'true' : 'false');
        sidebar.toggleAttribute('inert', mobile && !expanded);
        scrim.setAttribute('aria-hidden', expanded ? 'false' : 'true');
        if (expanded && focusMenu) {
            sidebar.querySelector('.nav-item')?.focus();
        } else if (!expanded && restoreFocus && mobile) {
            menuBtn.focus();
        }
    };
    const close = (restoreFocus = false) => sync(false, { restoreFocus });

    menuBtn.addEventListener('click', () => {
        const open = !document.body.classList.contains('sidebar-open');
        sync(open, { focusMenu: open, restoreFocus: !open });
    });
    scrim.addEventListener('click', () => close(true));
    window.addEventListener('hashchange', () => close(true));
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
            close(true);
        }
    });
    window.addEventListener('resize', () => {
        sync(document.body.classList.contains('sidebar-open'));
    });
    sync(false);
}

function init() {
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
        renderSidebar(sidebar);
    }

    if (!location.hash) {
        location.hash = '#/email';
    }

    const toggleBtn = document.getElementById('theme-toggle');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            const isDark = document.documentElement.classList.toggle('dark-theme');
            const theme = isDark ? 'dark' : 'light';
            localStorage.setItem('theme', theme);
            document.documentElement.dataset.theme = theme;
        });
    }

    initMobileNav();
    initAboutTrigger();
    window.addEventListener('hashchange', () => {
        navigate();
    });
    navigate();

    connectSocket({
        onLog: () => {},
        onStatusUpdate: () => {},
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
