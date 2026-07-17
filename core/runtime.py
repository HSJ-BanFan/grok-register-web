import logging
import os


logger = logging.getLogger('register')


TRUE_VALUES = {'1', 'true', 'yes', 'on'}
FALSE_VALUES = {'0', 'false', 'no', 'off'}
VALID_REGISTRATION_BACKENDS = {'browser', 'protocol', 'auto'}


def _environment_bool(name):
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    logger.warning('Ignoring invalid %s=%r; expected true or false', name, value)
    return None


def resolve_browser_headless(settings):
    """Resolve browser mode, allowing server launchers to force headful mode."""
    override = _environment_bool('GROK_REGISTER_BROWSER_HEADLESS')
    if override is not None:
        return override
    return str((settings or {}).get('browser_headless', 'false')).lower() == 'true'


def resolve_registration_concurrency(value):
    """Resolve worker count with an optional deployment-level override."""
    override = os.environ.get('GROK_REGISTER_CONCURRENCY')
    candidate = override if override is not None else value
    try:
        concurrency = int(candidate or 1)
    except (TypeError, ValueError):
        if override is not None:
            logger.warning(
                'Ignoring invalid GROK_REGISTER_CONCURRENCY=%r; using one worker',
                override,
            )
        concurrency = 1
    return max(1, min(10, concurrency))


def resolve_registration_backend(settings):
    """Resolve the registration transport, defaulting to the legacy browser."""
    candidate = os.environ.get('GROK_REGISTER_BACKEND')
    if candidate is None:
        candidate = (settings or {}).get('registration_backend', 'browser')
    backend = str(candidate or 'browser').strip().lower()
    if backend not in VALID_REGISTRATION_BACKENDS:
        logger.warning(
            'Ignoring invalid registration backend %r; using browser', candidate,
        )
        return 'browser'
    return backend
