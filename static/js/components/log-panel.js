export function createLogPanel(container) {
    let locked = false;
    let logs = [];

    container.innerHTML = `
        <div class="log-header">
            <span class="log-title">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
                实时日志
            </span>
            <div class="log-actions">
                <button class="btn btn-sm btn-secondary" id="log-lock-btn">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                    锁定
                </button>
                <button class="btn btn-sm btn-secondary" id="log-clear-btn">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    清空
                </button>
            </div>
        </div>
        <div class="log-body" id="log-body"></div>
    `;

    const logBody = container.querySelector('#log-body');
    const lockBtn = container.querySelector('#log-lock-btn');
    const clearBtn = container.querySelector('#log-clear-btn');

    lockBtn.addEventListener('click', () => {
        locked = !locked;
        if (locked) {
            lockBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg> 解锁`;
        } else {
            lockBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> 锁定`;
        }
    });

    clearBtn.addEventListener('click', () => {
        logs = [];
        logBody.innerHTML = '';
    });

    function addLog(entry) {
        logs.push(entry);
        const line = document.createElement('div');
        line.className = `log-line log-${entry.level || 'info'}`;
        const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();
        line.textContent = `[${time}] ${entry.message}`;
        logBody.appendChild(line);

        if (!locked) {
            logBody.scrollTop = logBody.scrollHeight;
        }

        // Keep max 500 lines
        while (logBody.children.length > 500) {
            logBody.removeChild(logBody.firstChild);
        }
    }

    return { addLog };
}
