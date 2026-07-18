"""Vendored local Turnstile solver (Camoufox / Chromium HTTP API).

HTTP surface (compatible with ExternalTurnstileProvider):
  GET /                          -> health
  GET /turnstile?url=&sitekey=   -> {taskId}
  GET /result?id=                -> {solution: {token}}
"""
