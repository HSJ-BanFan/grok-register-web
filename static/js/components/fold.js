const FOLD_STORAGE_PREFIX = 'fold:';

export const FOLD_CHEVRON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>`;

export function readFoldOpen(key, fallback = false) {
    try {
        const raw = localStorage.getItem(FOLD_STORAGE_PREFIX + key);
        if (raw == null) return fallback;
        return raw === '1';
    } catch {
        return fallback;
    }
}

export function writeFoldOpen(key, open) {
    try {
        localStorage.setItem(FOLD_STORAGE_PREFIX + key, open ? '1' : '0');
    } catch {
        /* ignore quota / private mode */
    }
}

export function setFoldOpen(card, open, { persist = true } = {}) {
    if (!card) return;
    const key = card.dataset.fold;
    const toggle = card.querySelector('.fold-toggle');
    const hint = card.querySelector('.fold-hint');
    card.classList.toggle('is-open', open);
    if (toggle) {
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (hint) {
        hint.textContent = open ? '点击收起' : '点击展开';
    }
    if (persist && key) writeFoldOpen(key, open);
}

export function initFoldCards(root) {
    if (!root) return;
    root.querySelectorAll('.fold-card').forEach((card) => {
        const key = card.dataset.fold;
        const open = readFoldOpen(key, false);
        setFoldOpen(card, open, { persist: false });

        const toggle = card.querySelector('.fold-toggle');
        if (!toggle || toggle.dataset.bound === '1') return;
        toggle.dataset.bound = '1';
        toggle.addEventListener('click', () => {
            setFoldOpen(card, !card.classList.contains('is-open'));
        });
    });
}

export function updateFoldCount(id, count) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(count ?? 0);
}
