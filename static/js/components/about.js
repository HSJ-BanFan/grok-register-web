const APP_VERSION = 'v0.4.0';

const ABOUT_BODY =
    '本地 Web 控制台：邮箱别名 / 临时邮箱批量注册 Grok，采集 SSO；可选交付到 grok2api（Web 导入 + Build 转换，失败自动补传）和/或 CPA（CLIProxyAPI 热载，chat 探测后入库）。';

const ABOUT_POINTS = [
    '邮箱：Microsoft 别名 / 多种临时邮箱',
    '注册：协议 HTTP（推荐）或浏览器自动化',
    '人机：本地 Camoufox Solver 或 YesCaptcha',
    '交付：SSO 落库 · grok2api（Web/Build + 补传）· CPA（mint + chat probe 热载）',
];

let aboutRoot = null;

function ensureAboutRoot() {
    if (aboutRoot && document.body.contains(aboutRoot)) {
        return aboutRoot;
    }
    aboutRoot = document.createElement('div');
    aboutRoot.id = 'about-modal-root';
    aboutRoot.hidden = true;
    aboutRoot.innerHTML = `
        <div class="about-scrim" data-about-close="1" aria-hidden="true"></div>
        <div class="about-dialog" role="dialog" aria-modal="true" aria-labelledby="about-title">
            <div class="about-head">
                <div>
                    <p class="about-eyebrow">Ops Console</p>
                    <h2 id="about-title">关于</h2>
                </div>
                <button type="button" class="about-close" data-about-close="1" aria-label="关闭关于">×</button>
            </div>
            <div class="about-brand">
                <strong>Grok Register</strong>
                <span>本地运维控制台 · ${APP_VERSION}</span>
            </div>
            <p class="about-body">${ABOUT_BODY}</p>
            <ul class="about-points">
                ${ABOUT_POINTS.map((item) => `<li>${item}</li>`).join('')}
            </ul>
            <div class="about-meta">
                <span>MIT License</span>
                <a href="https://github.com/HSJ-BanFan/grok-register-web" target="_blank" rel="noopener noreferrer">仓库</a>
            </div>
            <p class="about-disclaimer">仅供个人学习与自用测试，请遵守 xAI 与邮箱服务商条款。</p>
        </div>
    `;
    document.body.appendChild(aboutRoot);
    aboutRoot.addEventListener('click', (event) => {
        if (event.target?.dataset?.aboutClose === '1') {
            closeAbout();
        }
    });
    return aboutRoot;
}

export function openAbout() {
    const root = ensureAboutRoot();
    root.hidden = false;
    document.body.classList.add('about-open');
    root.querySelector('.about-close')?.focus();
}

export function closeAbout() {
    if (!aboutRoot) return;
    aboutRoot.hidden = true;
    document.body.classList.remove('about-open');
}

export function initAboutTrigger() {
    const versionEl = document.querySelector('.version');
    if (!versionEl) return;
    versionEl.textContent = APP_VERSION;
    versionEl.setAttribute('role', 'button');
    versionEl.setAttribute('tabindex', '0');
    versionEl.setAttribute('title', '关于');
    versionEl.setAttribute('aria-label', '打开关于');
    versionEl.classList.add('version-btn');
    const open = (event) => {
        event.preventDefault();
        openAbout();
    };
    versionEl.addEventListener('click', open);
    versionEl.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            open(event);
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && document.body.classList.contains('about-open')) {
            closeAbout();
        }
    });
}
