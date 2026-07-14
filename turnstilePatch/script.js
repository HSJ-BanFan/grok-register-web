// Turnstile helper for registration only.
// IMPORTANT: do NOT repeatedly click during verification — that resets the widget
// and forces the user to click again (observed on grok.com managed challenges).

(function() {
    'use strict';

    function isTurnstileFrame() {
        try {
            return window.location.href.includes('challenges.cloudflare.com') ||
                   window.location.href.includes('turnstile');
        } catch(e) {
            return false;
        }
    }

    if (!isTurnstileFrame()) {
        return;
    }

    let clickedOnce = false;

    function alreadyChecked(box) {
        if (!box) return false;
        if (box.checked) return true;
        const aria = (box.getAttribute && box.getAttribute('aria-checked')) || '';
        return aria === 'true';
    }

    function autoSolve() {
        if (clickedOnce) return;
        const checkbox = document.querySelector('input[type="checkbox"]') ||
                         document.querySelector('.cb-i') ||
                         document.querySelector('[role="checkbox"]');
        if (!checkbox || alreadyChecked(checkbox)) {
            return;
        }
        clickedOnce = true;
        try { checkbox.click(); } catch (e) {}
    }

    // Single delayed attempt only — never re-click while Cloudflare is verifying.
    setTimeout(autoSolve, 1500);
})();
