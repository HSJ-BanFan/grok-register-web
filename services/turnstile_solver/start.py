"""Entry point for the local Turnstile Solver subprocess.

Run directly:
  python services/turnstile_solver/start.py --browser_type camoufox --thread 2 --port 5072
"""

from __future__ import annotations

import os
import sys

# Prefer UTF-8 I/O before importing Rich/Quart so Windows GBK consoles
# do not abort on banner text when stdout/stderr are redirected.
os.environ.setdefault('PYTHONUTF8', '1')
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
for stream_name in ('stdout', 'stderr'):
    stream = getattr(sys, stream_name, None)
    try:
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Allow `python services/turnstile_solver/start.py` without installing a package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api_solver import create_app, parse_args  # noqa: E402


def main() -> None:
    args = parse_args()
    browser_types = ('chromium', 'chrome', 'msedge', 'camoufox')
    if args.browser_type not in browser_types:
        raise SystemExit(
            f'Unknown browser type: {args.browser_type}. '
            f'Available: {", ".join(browser_types)}'
        )
    app = create_app(
        headless=not args.no_headless,
        useragent=args.useragent,
        debug=args.debug,
        browser_type=args.browser_type,
        thread=args.thread,
        proxy_support=args.proxy,
        use_random_config=args.random,
        browser_name=args.browser,
        browser_version=args.version,
    )
    app.run(host=args.host, port=int(args.port))


if __name__ == '__main__':
    main()
