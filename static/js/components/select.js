/**
 * Custom select (listbox) for blue-white aurora UI.
 * Keeps a real <select> in the DOM so existing getElementById().value / change listeners work.
 */

const CHEVRON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>`;

const CHECK = `<svg class="ui-select-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`;

function prefersReducedMotion() {
    return typeof matchMedia === 'function'
        && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function closeAll(except = null) {
    document.querySelectorAll('.ui-select.is-open').forEach((wrap) => {
        if (wrap !== except) closeSelect(wrap);
    });
}

function clearMenuPosition(menu) {
    if (!menu) return;
    menu.style.top = '';
    menu.style.bottom = '';
    menu.style.left = '';
    menu.style.right = '';
    menu.style.width = '';
    menu.style.maxHeight = '';
}

function closeSelect(wrap) {
    if (!wrap) return;
    wrap.classList.remove('is-open', 'is-drop-up');
    const trigger = wrap.querySelector('.ui-select-trigger');
    const menu = wrap.querySelector('.ui-select-menu');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    if (menu) {
        menu.hidden = true;
        clearMenuPosition(menu);
        menu.querySelectorAll('.ui-select-option.is-active').forEach((el) => {
            el.classList.remove('is-active');
        });
    }
}

function openSelect(wrap) {
    if (!wrap || wrap.classList.contains('is-disabled')) return;
    closeAll(wrap);
    const trigger = wrap.querySelector('.ui-select-trigger');
    const menu = wrap.querySelector('.ui-select-menu');
    const select = wrap.querySelector('select');
    if (!trigger || !menu) return;

    wrap.classList.add('is-open');
    trigger.setAttribute('aria-expanded', 'true');
    menu.hidden = false;

    // Align active highlight with current value
    const current = select?.value ?? '';
    let active = null;
    menu.querySelectorAll('.ui-select-option').forEach((opt) => {
        const selected = opt.dataset.value === current;
        opt.classList.toggle('is-selected', selected);
        opt.setAttribute('aria-selected', selected ? 'true' : 'false');
        if (selected) active = opt;
    });
    if (active) {
        active.classList.add('is-active');
        // scroll into view without jumping the page
        try {
            active.scrollIntoView({ block: 'nearest' });
        } catch {
            /* ignore */
        }
    }

    positionMenu(wrap);
}

function positionMenu(wrap) {
    const menu = wrap.querySelector('.ui-select-menu');
    const trigger = wrap.querySelector('.ui-select-trigger');
    if (!menu || !trigger) return;

    // Fixed to viewport so parent card overflow can't clip the list.
    const rect = trigger.getBoundingClientRect();
    const gutter = 8;
    const width = rect.width;
    const left = Math.min(
        Math.max(gutter, rect.left),
        Math.max(gutter, window.innerWidth - width - gutter),
    );

    menu.style.left = `${left}px`;
    menu.style.width = `${width}px`;
    menu.style.right = 'auto';
    menu.style.bottom = 'auto';

    // Measure natural height with a temporary max so scrollHeight is accurate.
    menu.style.top = '0px';
    menu.style.maxHeight = 'none';
    const natural = menu.scrollHeight || 220;
    const preferred = Math.min(280, natural);

    const spaceBelow = window.innerHeight - rect.bottom - gutter;
    const spaceAbove = rect.top - gutter;
    const openUp = spaceBelow < preferred && spaceAbove > spaceBelow;

    if (openUp) {
        wrap.classList.add('is-drop-up');
        const maxH = Math.max(120, Math.min(280, spaceAbove));
        menu.style.maxHeight = `${maxH}px`;
        const height = Math.min(natural, maxH);
        menu.style.top = `${Math.max(gutter, rect.top - height - 6)}px`;
    } else {
        wrap.classList.remove('is-drop-up');
        const maxH = Math.max(120, Math.min(280, spaceBelow));
        menu.style.maxHeight = `${maxH}px`;
        menu.style.top = `${rect.bottom + 6}px`;
    }
}

function setValue(wrap, value, { emit = true } = {}) {
    const select = wrap.querySelector('select');
    const triggerValue = wrap.querySelector('.ui-select-value');
    const menu = wrap.querySelector('.ui-select-menu');
    if (!select) return;

    const next = String(value ?? '');
    const option = Array.from(select.options).find((o) => o.value === next);
    if (!option) return;

    const changed = select.value !== next;
    select.value = next;
    if (triggerValue) triggerValue.textContent = option.textContent;

    menu?.querySelectorAll('.ui-select-option').forEach((el) => {
        const selected = el.dataset.value === next;
        el.classList.toggle('is-selected', selected);
        el.setAttribute('aria-selected', selected ? 'true' : 'false');
    });

    if (emit && changed) {
        select.dispatchEvent(new Event('change', { bubbles: true }));
        select.dispatchEvent(new Event('input', { bubbles: true }));
    }
}

function moveActive(wrap, delta) {
    const menu = wrap.querySelector('.ui-select-menu');
    if (!menu) return;
    const options = Array.from(menu.querySelectorAll('.ui-select-option'));
    if (!options.length) return;

    let idx = options.findIndex((o) => o.classList.contains('is-active'));
    if (idx < 0) idx = options.findIndex((o) => o.classList.contains('is-selected'));
    if (idx < 0) idx = 0;
    else idx = (idx + delta + options.length) % options.length;

    options.forEach((o) => o.classList.remove('is-active'));
    const next = options[idx];
    next.classList.add('is-active');
    try {
        next.scrollIntoView({ block: 'nearest' });
    } catch {
        /* ignore */
    }
}

function bindSelect(wrap) {
    if (!wrap || wrap.dataset.bound === '1') return;
    wrap.dataset.bound = '1';

    const select = wrap.querySelector('select');
    const trigger = wrap.querySelector('.ui-select-trigger');
    const menu = wrap.querySelector('.ui-select-menu');
    if (!select || !trigger || !menu) return;

    // Sync label if native value changed programmatically
    select.addEventListener('change', () => {
        const opt = select.selectedOptions[0];
        const label = wrap.querySelector('.ui-select-value');
        if (label && opt) label.textContent = opt.textContent;
        menu.querySelectorAll('.ui-select-option').forEach((el) => {
            const selected = el.dataset.value === select.value;
            el.classList.toggle('is-selected', selected);
            el.setAttribute('aria-selected', selected ? 'true' : 'false');
        });
    });

    trigger.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (wrap.classList.contains('is-open')) closeSelect(wrap);
        else openSelect(wrap);
    });

    trigger.addEventListener('keydown', (event) => {
        const key = event.key;
        if (key === 'ArrowDown' || key === 'ArrowUp') {
            event.preventDefault();
            if (!wrap.classList.contains('is-open')) openSelect(wrap);
            else moveActive(wrap, key === 'ArrowDown' ? 1 : -1);
            return;
        }
        if (key === 'Enter' || key === ' ') {
            event.preventDefault();
            if (!wrap.classList.contains('is-open')) {
                openSelect(wrap);
                return;
            }
            const active = menu.querySelector('.ui-select-option.is-active')
                || menu.querySelector('.ui-select-option.is-selected');
            if (active) {
                setValue(wrap, active.dataset.value);
                closeSelect(wrap);
                trigger.focus();
            }
            return;
        }
        if (key === 'Escape') {
            if (wrap.classList.contains('is-open')) {
                event.preventDefault();
                closeSelect(wrap);
            }
            return;
        }
        if (key === 'Home' || key === 'End') {
            if (!wrap.classList.contains('is-open')) return;
            event.preventDefault();
            const options = Array.from(menu.querySelectorAll('.ui-select-option'));
            if (!options.length) return;
            options.forEach((o) => o.classList.remove('is-active'));
            const target = key === 'Home' ? options[0] : options[options.length - 1];
            target.classList.add('is-active');
            try { target.scrollIntoView({ block: 'nearest' }); } catch { /* ignore */ }
        }
    });

    menu.addEventListener('click', (event) => {
        const option = event.target.closest('.ui-select-option');
        if (!option || !menu.contains(option)) return;
        event.preventDefault();
        setValue(wrap, option.dataset.value);
        closeSelect(wrap);
        trigger.focus();
    });

    menu.addEventListener('mousemove', (event) => {
        const option = event.target.closest('.ui-select-option');
        if (!option || !menu.contains(option)) return;
        menu.querySelectorAll('.ui-select-option.is-active').forEach((el) => {
            el.classList.remove('is-active');
        });
        option.classList.add('is-active');
    });
}

let globalBound = false;

function ensureGlobalHandlers() {
    if (globalBound) return;
    globalBound = true;

    document.addEventListener('click', (event) => {
        if (event.target.closest?.('.ui-select')) return;
        closeAll();
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeAll();
    });

    window.addEventListener('resize', () => {
        document.querySelectorAll('.ui-select.is-open').forEach(positionMenu);
    });

    // Reposition while scrolling; close only if the trigger leaves the viewport.
    window.addEventListener('scroll', (event) => {
        if (event.target?.closest?.('.ui-select-menu')) return;
        document.querySelectorAll('.ui-select.is-open').forEach((wrap) => {
            const trigger = wrap.querySelector('.ui-select-trigger');
            if (!trigger) {
                closeSelect(wrap);
                return;
            }
            const rect = trigger.getBoundingClientRect();
            const visible = rect.bottom > 0
                && rect.top < window.innerHeight
                && rect.right > 0
                && rect.left < window.innerWidth;
            if (!visible) closeSelect(wrap);
            else positionMenu(wrap);
        });
    }, true);
}

/**
 * Enhance all [data-ui-select] wrappers inside root.
 */
export function initSelects(root = document) {
    ensureGlobalHandlers();
    root.querySelectorAll('[data-ui-select]').forEach(bindSelect);
}

/**
 * Build markup for a custom select field (used by settings page).
 * @param {string} id
 * @param {string} label
 * @param {string|number} current
 * @param {Array<{value:string|number,label:string}>} options
 * @param {{helper?:string, mono?:boolean}} [opts]
 */
export function selectFieldMarkup(id, label, current, options, { helper = '', mono = false } = {}) {
    const desired = String(current ?? '');
    const matched = options.find((o) => String(o.value) === desired);
    const active = matched || options[0];
    const activeValue = active ? String(active.value) : '';
    const activeLabel = active ? active.label : '';

    const nativeOptions = options.map((option) => {
        const value = String(option.value);
        return `<option value="${escapeAttr(value)}"${value === activeValue ? ' selected' : ''}>${escapeHtml(option.label)}</option>`;
    }).join('');

    const listOptions = options.map((option) => {
        const value = String(option.value);
        const selected = value === activeValue;
        return `
            <li class="ui-select-option${selected ? ' is-selected' : ''}"
                role="option"
                data-value="${escapeAttr(value)}"
                aria-selected="${selected ? 'true' : 'false'}"
                tabindex="-1">
                <span class="ui-select-option-label">${escapeHtml(option.label)}</span>
                ${CHECK}
            </li>
        `;
    }).join('');

    return `
        <div class="form-group">
            <label id="${escapeAttr(id)}-label" for="${escapeAttr(id)}-trigger">${label}</label>
            <div class="ui-select${mono ? ' is-mono' : ''}${prefersReducedMotion() ? ' is-reduced' : ''}" data-ui-select>
                <select class="ui-select-native" id="${escapeAttr(id)}" tabindex="-1" aria-hidden="true">
                    ${nativeOptions}
                </select>
                <button type="button"
                    class="ui-select-trigger"
                    id="${escapeAttr(id)}-trigger"
                    aria-haspopup="listbox"
                    aria-expanded="false"
                    aria-controls="${escapeAttr(id)}-menu"
                    aria-labelledby="${escapeAttr(id)}-label ${escapeAttr(id)}-value">
                    <span class="ui-select-value" id="${escapeAttr(id)}-value">${escapeHtml(activeLabel)}</span>
                    <span class="ui-select-chevron" aria-hidden="true">${CHEVRON}</span>
                </button>
                <ul class="ui-select-menu"
                    id="${escapeAttr(id)}-menu"
                    role="listbox"
                    aria-labelledby="${escapeAttr(id)}-label"
                    hidden>
                    ${listOptions}
                </ul>
            </div>
            ${helper ? `<div class="helper-text">${helper}</div>` : ''}
        </div>
    `;
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escapeAttr(value) {
    return escapeHtml(value).replace(/'/g, '&#39;');
}
