import { renderSidebar } from './components/sidebar.js';
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

function navigate() {
    const hash = location.hash || '#/email';
    const page = routes[hash] || routes['#/email'];
    const mainContent = document.getElementById('main-content');
    if (mainContent && page) {
        page.render(mainContent);
    }
}

function init() {
    // Render sidebar
    const sidebar = document.getElementById('sidebar');
    if (sidebar) {
        renderSidebar(sidebar);
    }

    // Set default hash
    if (!location.hash) {
        location.hash = '#/email';
    }

    // Theme toggle
    const toggleBtn = document.getElementById('theme-toggle');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            const isDark = document.documentElement.classList.toggle('dark-theme');
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
        });
    }

    // Listen for hash changes
    window.addEventListener('hashchange', navigate);

    // Initial render
    navigate();

    // Connect WebSocket (global, for log/status updates)
    connectSocket({
        onLog: (data) => {
            // Logs are handled by register page's log panel when active
        },
        onStatusUpdate: (data) => {
            // Status updates are handled by register page when active
        },
    });
}

// Start when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
