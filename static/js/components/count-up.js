/**
 * Animated number count-up for KPI / dashboard values.
 * Respects prefers-reduced-motion.
 */

const DEFAULTS = {
    duration: 900,
    decimals: null, // auto-detect from target
    prefix: '',
    suffix: '',
    easing: easeOutCubic,
    stagger: 45,
};

const running = new WeakMap();

function prefersReducedMotion() {
    return typeof matchMedia === 'function'
        && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function easeOutCubic(t) {
    return 1 - Math.pow(1 - t, 3);
}

function parseNumeric(value) {
    if (typeof value === 'number' && Number.isFinite(value)) {
        return { num: value, prefix: '', suffix: '', decimals: Number.isInteger(value) ? 0 : 1 };
    }
    const raw = String(value ?? '').trim();
    if (!raw) return null;
    const match = raw.match(/^([^0-9+\-]*)([+\-]?\d+(?:\.\d+)?)(.*)$/);
    if (!match) return null;
    const num = parseFloat(match[2]);
    if (!Number.isFinite(num)) return null;
    const decimals = match[2].includes('.') ? (match[2].split('.')[1] || '').length : 0;
    return { num, prefix: match[1], suffix: match[3], decimals };
}

function formatNumber(num, decimals) {
    if (decimals <= 0) return String(Math.round(num));
    return num.toFixed(decimals);
}

/**
 * Animate a single element from its current displayed number (or 0) to target.
 * @param {HTMLElement} el
 * @param {number|string} target
 * @param {object} [opts]
 */
export function countUp(el, target, opts = {}) {
    if (!el) return;

    const options = { ...DEFAULTS, ...opts };
    const parsed = parseNumeric(target);
    if (!parsed) {
        el.textContent = String(target ?? '');
        return;
    }

    const decimals = options.decimals != null ? options.decimals : parsed.decimals;
    const prefix = options.prefix || parsed.prefix;
    const suffix = options.suffix || parsed.suffix;
    const end = parsed.num;

    // cancel previous rAF on this element
    const prev = running.get(el);
    if (prev) cancelAnimationFrame(prev);

    el.dataset.countTarget = String(end);
    el.classList.add('is-counting');

    if (prefersReducedMotion() || options.duration <= 0) {
        el.textContent = `${prefix}${formatNumber(end, decimals)}${suffix}`;
        el.classList.remove('is-counting');
        el.classList.add('count-pop');
        setTimeout(() => el.classList.remove('count-pop'), 280);
        return;
    }

    const fromParsed = parseNumeric(el.textContent);
    const start = fromParsed && Number.isFinite(fromParsed.num) ? fromParsed.num : 0;
    // skip tiny deltas (same value refresh)
    if (Math.abs(end - start) < 1e-9) {
        el.textContent = `${prefix}${formatNumber(end, decimals)}${suffix}`;
        el.classList.remove('is-counting');
        return;
    }

    const t0 = performance.now();
    const duration = options.duration;
    const ease = options.easing || easeOutCubic;

    const tick = (now) => {
        const t = Math.min(1, (now - t0) / duration);
        const current = start + (end - start) * ease(t);
        el.textContent = `${prefix}${formatNumber(current, decimals)}${suffix}`;
        if (t < 1) {
            const id = requestAnimationFrame(tick);
            running.set(el, id);
        } else {
            el.textContent = `${prefix}${formatNumber(end, decimals)}${suffix}`;
            el.classList.remove('is-counting');
            el.classList.add('count-pop');
            running.delete(el);
            setTimeout(() => el.classList.remove('count-pop'), 280);
        }
    };

    const id = requestAnimationFrame(tick);
    running.set(el, id);
}

/**
 * Animate all matching elements under a root (staggered).
 * Each element needs data-count / data-count-value, or pass getValue.
 * @param {ParentNode} root
 * @param {string} selector
 * @param {(el: HTMLElement, index: number) => number|string} getValue
 * @param {object} [opts]
 */
export function countUpAll(root, selector, getValue, opts = {}) {
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll(selector));
    const stagger = opts.stagger ?? DEFAULTS.stagger;
    nodes.forEach((el, i) => {
        const value = typeof getValue === 'function' ? getValue(el, i) : (el.dataset.countValue ?? el.textContent);
        const delay = (opts.delay || 0) + i * stagger;
        if (delay <= 0) {
            countUp(el, value, opts);
        } else {
            setTimeout(() => countUp(el, value, opts), delay);
        }
    });
}

/**
 * Convenience: mark values with data-count-value then animate .count-up nodes.
 */
export function animateCountNodes(root, opts = {}) {
    if (!root) return;
    countUpAll(root, '.count-up', (el) => el.dataset.countValue ?? el.textContent, opts);
}
