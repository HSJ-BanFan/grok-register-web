import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum


class SignupPageStage(str, Enum):
    READY = 'ready'
    CHALLENGE = 'challenge'
    BLOCKED = 'blocked'
    PROXY_ERROR = 'proxy_error'
    STALLED = 'stalled'


BLOCKED_TITLE_MARKERS = (
    'attention required',
    'access denied',
    'website blocked',
)

BLOCKED_BODY_MARKERS = (
    'sorry, you have been blocked',
    'you are unable to access x.ai',
    'why have i been blocked',
    'the action you just performed triggered the security solution',
)

PROXY_ERROR_MARKERS = (
    'err_proxy_connection_failed',
    'err_tunnel_connection_failed',
    'proxy connection failed',
    'the proxy server is refusing connections',
)

CHALLENGE_MARKERS = (
    'just a moment',
    'verifying you are human',
    'performing security verification',
)


@dataclass(frozen=True)
class SignupPageSnapshot:
    url: str = ''
    title: str = ''
    ready_state: str = ''
    body_text: str = ''
    user_agent: str = ''
    has_email_field: bool = False
    has_email_signup: bool = False
    has_challenge_dom: bool = False
    button_labels: tuple = ()
    capture_error: str = ''

    @classmethod
    def from_mapping(cls, value):
        data = value or {}
        labels = data.get('buttonLabels') or data.get('button_labels') or []
        return cls(
            url=str(data.get('href') or data.get('url') or ''),
            title=str(data.get('title') or ''),
            ready_state=str(data.get('readyState') or data.get('ready_state') or ''),
            body_text=str(data.get('bodyText') or data.get('body_text') or ''),
            user_agent=str(data.get('userAgent') or data.get('user_agent') or ''),
            has_email_field=bool(
                data.get('hasEmailField') or data.get('has_email_field')
            ),
            has_email_signup=bool(
                data.get('hasEmailSignup') or data.get('has_email_signup')
            ),
            has_challenge_dom=bool(
                data.get('hasChallengeDom') or data.get('has_challenge_dom')
            ),
            button_labels=tuple(str(item) for item in labels[:20]),
            capture_error=str(
                data.get('captureError') or data.get('capture_error') or ''
            ),
        )

    @property
    def stage(self):
        return classify_signup_page(self)

    @property
    def ray_id(self):
        match = re.search(
            r'(?i)cloudflare\s+ray\s+id\s*:\s*([a-f0-9]+)',
            self.body_text,
        )
        return match.group(1) if match else ''


class SignupEnvironmentError(RuntimeError):
    """Signup is unavailable because of the runtime browser/network environment."""

    def __init__(self, reason, snapshot=None, diagnostics=None):
        self.reason = str(reason or 'signup_environment_blocked')
        self.snapshot = snapshot or SignupPageSnapshot()
        self.diagnostics = diagnostics or {}
        title = self.snapshot.title or 'unknown title'
        ray = f', ray_id={self.snapshot.ray_id}' if self.snapshot.ray_id else ''
        super().__init__(f'{self.reason}: title={title}, url={self.snapshot.url}{ray}')


def classify_signup_page(snapshot):
    snapshot = snapshot or SignupPageSnapshot()
    title = snapshot.title.lower()
    body = snapshot.body_text.lower()
    if any(marker in body or marker in title for marker in PROXY_ERROR_MARKERS):
        return SignupPageStage.PROXY_ERROR
    if (
        any(marker in title for marker in BLOCKED_TITLE_MARKERS)
        or any(marker in body for marker in BLOCKED_BODY_MARKERS)
        or ('cloudflare ray id' in body and 'blocked' in body)
    ):
        return SignupPageStage.BLOCKED
    if snapshot.has_email_field or snapshot.has_email_signup:
        return SignupPageStage.READY
    if (
        snapshot.has_challenge_dom
        or any(marker in title or marker in body for marker in CHALLENGE_MARKERS)
    ):
        return SignupPageStage.CHALLENGE
    return SignupPageStage.STALLED


def _redact_diagnostic_text(value):
    text = str(value or '')
    text = re.sub(
        r'(?i)\b((?:https?|socks4|socks5)://)[^\s/@:]+:[^\s/@]+@',
        r'\1<redacted>@',
        text,
    )
    text = re.sub(
        r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b',
        '<redacted-email>',
        text,
    )
    return text[:3000]


def save_signup_diagnostics(page, stage, snapshot=None, reason='', directory=None):
    """Persist a secret-safe signup state snapshot and best-effort screenshot."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    directory = directory or os.path.join(project_root, 'data', 'diagnostics')
    os.makedirs(directory, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    stage_value = stage.value if isinstance(stage, SignupPageStage) else str(stage)
    base_name = f'signup-{timestamp}-{stage_value}'
    json_path = os.path.join(directory, f'{base_name}.json')

    current = snapshot or SignupPageSnapshot()
    payload_snapshot = asdict(current)
    for key, value in list(payload_snapshot.items()):
        if isinstance(value, str):
            payload_snapshot[key] = _redact_diagnostic_text(value)
    payload_snapshot['button_labels'] = [
        _redact_diagnostic_text(label) for label in current.button_labels
    ]
    payload = {
        'stage': stage_value,
        'reason': _redact_diagnostic_text(reason),
        'snapshot': payload_snapshot,
        'ray_id': current.ray_id,
    }

    screenshot_name = f'{base_name}.png'
    screenshot_path = os.path.join(directory, screenshot_name)
    try:
        result = page.get_screenshot(
            path=directory, name=screenshot_name, full_page=True,
        )
        screenshot_path = str(result or screenshot_path)
    except TypeError:
        try:
            result = page.get_screenshot(path=directory, name=screenshot_name)
            screenshot_path = str(result or screenshot_path)
        except Exception:
            screenshot_path = ''
    except Exception:
        screenshot_path = ''

    payload['screenshot'] = screenshot_path
    with open(json_path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return {'json': json_path, 'screenshot': screenshot_path}
