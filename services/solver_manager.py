"""Manage the vendored local Turnstile Solver as a child process.

Lifecycle B (app boot):
  - When settings need a local solver (no YesCaptcha, provider not browser-only)
    and the configured URL points at loopback, start the child on app boot.
  - Settings UI / API can still stop and start it manually.
  - On app exit, stop only the process we spawned.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_SOLVER_PORT = 5072
DEFAULT_SOLVER_URL = f'http://127.0.0.1:{DEFAULT_SOLVER_PORT}'
_MAX_CONSECUTIVE_FAILURES = 3
_READY_TIMEOUT_SEC = 45

_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_consecutive_failures = 0
_last_failure_reason = ''
_managed_port = DEFAULT_SOLVER_PORT
_managed_url = DEFAULT_SOLVER_URL
_owned_by_us = False


def _services_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _solver_start_script() -> str:
    return os.path.join(_services_dir(), 'turnstile_solver', 'start.py')


def parse_solver_endpoint(url: str | None) -> dict[str, Any]:
    """Parse a solver URL into host/port and whether we may manage it."""
    raw = (url or '').strip() or DEFAULT_SOLVER_URL
    try:
        parsed = urlparse(raw)
    except Exception:
        return {
            'url': raw,
            'host': '',
            'port': DEFAULT_SOLVER_PORT,
            'manageable': False,
            'reason': 'invalid_url',
        }
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return {
            'url': raw,
            'host': '',
            'port': DEFAULT_SOLVER_PORT,
            'manageable': False,
            'reason': 'invalid_url',
        }
    if parsed.username or parsed.password:
        return {
            'url': raw,
            'host': parsed.hostname,
            'port': parsed.port or DEFAULT_SOLVER_PORT,
            'manageable': False,
            'reason': 'credentials_not_allowed',
        }
    host = parsed.hostname.lower()
    port = int(parsed.port or (443 if parsed.scheme == 'https' else DEFAULT_SOLVER_PORT))
    manageable = host in {'127.0.0.1', 'localhost', '::1', '0.0.0.0'}
    return {
        'url': raw.rstrip('/'),
        'host': host,
        'port': port,
        'manageable': manageable,
        'reason': 'ok' if manageable else 'remote_url',
    }


def should_auto_start(settings: dict | None = None) -> bool:
    """Return True when boot should try to launch the local solver."""
    settings = settings or {}
    mode = str(settings.get('turnstile_provider', '') or 'auto').strip().lower() or 'auto'
    if mode in {'browser', 'none', 'off', 'disabled'}:
        return False
    yescaptcha = str(settings.get('yescaptcha_key', '') or '').strip()
    if yescaptcha:
        return False
    endpoint = parse_solver_endpoint(settings.get('turnstile_solver_url'))
    if not endpoint['manageable']:
        return False
    return mode in {
        'auto', 'external', 'strict_external', 'strict',
        'solver', 'yescaptcha', 'local',
    }


def configure_from_settings(settings: dict | None = None) -> dict[str, Any]:
    """Update the managed endpoint from settings (port / URL)."""
    global _managed_port, _managed_url
    settings = settings or {}
    endpoint = parse_solver_endpoint(settings.get('turnstile_solver_url'))
    if endpoint['manageable']:
        _managed_port = int(endpoint['port'])
        # Always probe loopback even if the stored URL used 0.0.0.0
        _managed_url = f'http://127.0.0.1:{_managed_port}'
    return endpoint


def is_running(url: str | None = None) -> bool:
    target = (url or _managed_url).rstrip('/')
    try:
        session = requests.Session()
        session.trust_env = False
        response = session.get(f'{target}/', timeout=2, allow_redirects=False)
        return response.status_code < 500
    except Exception:
        return False


def get_status(url: str | None = None) -> dict[str, Any]:
    """Detailed status for API / UI."""
    endpoint = parse_solver_endpoint(url or _managed_url)
    probe_url = (
        f'http://127.0.0.1:{endpoint["port"]}'
        if endpoint['manageable']
        else endpoint['url']
    )
    running = is_running(probe_url)
    info: dict[str, Any] = {
        'running': running,
        'online': running,
        'managed': _owned_by_us and _proc is not None and _proc.poll() is None,
        'pid': _proc.pid if (_proc is not None and _proc.poll() is None) else None,
        'url': probe_url if endpoint['manageable'] else endpoint['url'],
        'port': endpoint['port'],
        'manageable': endpoint['manageable'],
        'consecutive_failures': _consecutive_failures,
    }
    if not running and _last_failure_reason:
        info['last_error'] = _last_failure_reason
    if not running and _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
        info['stopped_retrying'] = True
        info['message'] = (
            f'连续 {_consecutive_failures} 次启动失败，已停止自动重试。'
            '请排查依赖后手动启动。'
        )
    return info


def _ensure_camoufox_browser() -> bool:
    """Ensure Camoufox browser binary is present (best-effort)."""
    try:
        from camoufox.pkgman import CamoufoxNotInstalled, installed_verstr
    except Exception as exc:
        logger.warning('[Solver] camoufox import failed: %s', exc)
        return False

    try:
        ver = installed_verstr()
        logger.info('[Solver] Camoufox browser ready (v%s)', ver)
        return True
    except CamoufoxNotInstalled:
        pass
    except Exception as exc:
        logger.warning('[Solver] Camoufox probe failed, will try install: %s', exc)

    logger.info('[Solver] Camoufox browser missing — downloading (~100MB)…')
    try:
        from camoufox.pkgman import CamoufoxFetcher

        CamoufoxFetcher().install()
        logger.info('[Solver] Camoufox browser download complete')
        return True
    except Exception as exc:
        logger.error('[Solver] Camoufox browser download failed: %s', exc)
        return False


def _build_cmd(port: int, *, browser_type: str, thread: int, proxy: bool) -> list[str]:
    script = _solver_start_script()
    if not os.path.isfile(script):
        raise FileNotFoundError(f'solver start script missing: {script}')
    cmd = [
        sys.executable,
        script,
        '--browser_type', browser_type,
        '--thread', str(max(1, int(thread))),
        '--host', '127.0.0.1',
        '--port', str(int(port)),
    ]
    if proxy:
        cmd.append('--proxy')
    return cmd


def start(
    settings: dict | None = None,
    *,
    browser_type: str | None = None,
    thread: int | None = None,
    proxy: bool | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Start the local solver if needed. Returns a status dict."""
    global _proc, _consecutive_failures, _last_failure_reason, _owned_by_us
    global _managed_port, _managed_url

    settings = settings or {}
    endpoint = configure_from_settings(settings)
    if not endpoint['manageable'] and not force:
        reason = (
            f'Solver URL 不在本机回环地址，无法由本进程托管'
            f'（当前: {endpoint.get("url") or "?"}）'
        )
        _last_failure_reason = reason
        logger.warning('[Solver] %s', reason)
        return get_status()

    port = int(endpoint['port'] if endpoint['manageable'] else _managed_port)
    _managed_port = port
    _managed_url = f'http://127.0.0.1:{port}'

    browser_type = (
        browser_type
        or str(os.environ.get('GROK_REGISTER_SOLVER_BROWSER', '') or '').strip()
        or 'camoufox'
    )
    try:
        thread = int(
            thread
            if thread is not None
            else os.environ.get('GROK_REGISTER_SOLVER_THREADS', '1')
        )
    except (TypeError, ValueError):
        thread = 1
    if proxy is None:
        proxy_env = str(os.environ.get('GROK_REGISTER_SOLVER_PROXY', '') or '').strip().lower()
        proxy = proxy_env in {'1', 'true', 'yes', 'on'}
        # Default: enable proxy flag when registration uses a proxy, so tasks can pass one.
        if not proxy and str(settings.get('browser_proxy', '') or '').strip():
            proxy = True

    with _lock:
        if is_running(_managed_url):
            logger.info('[Solver] already online at %s', _managed_url)
            _consecutive_failures = 0
            _last_failure_reason = ''
            return get_status(_managed_url)

        if not force and _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            logger.error(
                '[Solver] giving up after %s consecutive failures',
                _consecutive_failures,
            )
            return get_status(_managed_url)

        if browser_type == 'camoufox' and not _ensure_camoufox_browser():
            _consecutive_failures += 1
            _last_failure_reason = 'Camoufox 浏览器不可用（未安装或下载失败）'
            logger.error('[Solver] skip start: %s', _last_failure_reason)
            return get_status(_managed_url)

        try:
            cmd = _build_cmd(port, browser_type=browser_type, thread=thread, proxy=proxy)
        except FileNotFoundError as exc:
            _consecutive_failures += 1
            _last_failure_reason = str(exc)
            logger.error('[Solver] %s', exc)
            return get_status(_managed_url)

        logger.info('[Solver] starting: %s', ' '.join(cmd))
        try:
            child_env = {
                **os.environ,
                'PYTHONUTF8': '1',
                'PYTHONIOENCODING': 'utf-8',
                # Local health checks must not ride the system HTTP proxy.
                'NO_PROXY': ','.join(filter(None, [
                    os.environ.get('NO_PROXY', ''),
                    '127.0.0.1', 'localhost', '::1',
                ])),
                'no_proxy': ','.join(filter(None, [
                    os.environ.get('no_proxy', ''),
                    '127.0.0.1', 'localhost', '::1',
                ])),
            }
            _proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=child_env,
                cwd=os.path.dirname(_solver_start_script()),
            )
            _owned_by_us = True
        except Exception as exc:
            _consecutive_failures += 1
            _last_failure_reason = f'无法创建子进程: {exc}'
            logger.exception('[Solver] Popen failed')
            _proc = None
            _owned_by_us = False
            return get_status(_managed_url)

        for _ in range(_READY_TIMEOUT_SEC):
            time.sleep(1)
            if _proc.poll() is not None:
                stderr_msg = ''
                try:
                    if _proc.stderr:
                        stderr_msg = _proc.stderr.read().decode(
                            'utf-8', errors='replace',
                        )[:800]
                except Exception:
                    pass
                _consecutive_failures += 1
                _last_failure_reason = stderr_msg or f'进程退出 code={_proc.returncode}'
                logger.error(
                    '[Solver] child exited code=%s (fail %s/%s)%s',
                    _proc.returncode,
                    _consecutive_failures,
                    _MAX_CONSECUTIVE_FAILURES,
                    f' stderr={stderr_msg}' if stderr_msg else '',
                )
                _proc = None
                _owned_by_us = False
                return get_status(_managed_url)
            if is_running(_managed_url):
                logger.info('[Solver] online PID=%s url=%s', _proc.pid, _managed_url)
                _consecutive_failures = 0
                _last_failure_reason = ''
                try:
                    if _proc.stderr:
                        _proc.stderr.close()
                except Exception:
                    pass
                return get_status(_managed_url)

        _consecutive_failures += 1
        stderr_msg = ''
        try:
            if _proc and _proc.stderr:
                stderr_msg = _proc.stderr.read(2000).decode('utf-8', errors='replace')
                _proc.stderr.close()
        except Exception:
            pass
        _last_failure_reason = f'启动超时（{_READY_TIMEOUT_SEC}s） {stderr_msg}'.strip()
        logger.error('[Solver] %s', _last_failure_reason)
        return get_status(_managed_url)


def stop(*, kill_orphans: bool = False) -> dict[str, Any]:
    """Stop the child we spawned. Optionally clear anything on the managed port."""
    global _proc, _owned_by_us
    with _lock:
        if _proc is not None and _proc.poll() is None:
            logger.info('[Solver] terminating PID=%s', _proc.pid)
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _proc.kill()
                try:
                    _proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            logger.info('[Solver] child stopped')
        _proc = None
        _owned_by_us = False

        if kill_orphans and is_running(_managed_url):
            _kill_by_port(_managed_port)
            for _ in range(10):
                time.sleep(0.5)
                if not is_running(_managed_url):
                    break
            if is_running(_managed_url):
                logger.warning('[Solver] port %s still occupied after stop', _managed_port)
            else:
                logger.info('[Solver] orphan process on port %s cleared', _managed_port)
        return get_status(_managed_url)


def restart(settings: dict | None = None, **kwargs) -> dict[str, Any]:
    """Manual restart resets the failure counter."""
    global _consecutive_failures, _last_failure_reason
    _consecutive_failures = 0
    _last_failure_reason = ''
    stop(kill_orphans=True)
    for _ in range(10):
        if not is_running(_managed_url):
            break
        time.sleep(0.5)
    return start(settings, force=True, **kwargs)


def start_async(settings: dict | None = None, **kwargs) -> None:
    """Fire-and-forget start so app boot is not blocked for 30s+."""
    snapshot = dict(settings or {})

    def _run():
        try:
            start(snapshot, **kwargs)
        except Exception:
            logger.exception('[Solver] async start failed')

    threading.Thread(target=_run, name='turnstile-solver-boot', daemon=True).start()


def _kill_by_port(port: int) -> None:
    import platform

    try:
        if platform.system() == 'Windows':
            out = subprocess.check_output(
                ['netstat', '-ano', '-p', 'TCP'], text=True, timeout=5,
            )
            for line in out.splitlines():
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if pid > 0 and pid != os.getpid():
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except Exception:
                            subprocess.run(
                                ['taskkill', '/PID', str(pid), '/F'],
                                capture_output=True,
                                timeout=5,
                                check=False,
                            )
        else:
            out = subprocess.check_output(
                ['lsof', '-ti', f':{port}'], text=True, timeout=5,
            ).strip()
            for pid_str in out.splitlines():
                pid = int(pid_str.strip())
                if pid > 0 and pid != os.getpid():
                    os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
