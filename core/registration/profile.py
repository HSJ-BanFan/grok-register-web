import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum


class ProfileSubmitStage(str, Enum):
    FORM_READY = 'form_ready'
    SUBMITTED = 'submitted'
    IN_FLIGHT = 'in_flight'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    STALLED = 'stalled'
    TIMED_OUT = 'timed_out'
    STOPPED = 'stopped'


@dataclass(frozen=True)
class ProfileSubmitSnapshot:
    loading: bool = False
    primary_disabled: bool = False
    primary_text: str = ''
    error_text: str = ''
    url: str = ''
    cf_length: int = -1
    turnstile_ok: bool = False

    @classmethod
    def from_mapping(cls, value):
        data = value or {}
        return cls(
            loading=bool(data.get('loading')),
            primary_disabled=bool(data.get('primaryDisabled')),
            primary_text=str(data.get('primaryText') or ''),
            error_text=str(data.get('errText') or ''),
            url=str(data.get('url') or ''),
            cf_length=int(data.get('cfLen', -1) or -1),
            turnstile_ok=bool(data.get('turnstileOk')),
        )

    @property
    def in_flight(self):
        return self.loading or self.primary_disabled


def classify_profile_submit(snapshot, *, left_signup=False, has_sso=False,
                            timed_out=False, stopped=False):
    snapshot = snapshot or ProfileSubmitSnapshot()
    if stopped:
        return ProfileSubmitStage.STOPPED
    if left_signup or has_sso:
        return ProfileSubmitStage.SUCCEEDED
    if snapshot.error_text:
        return ProfileSubmitStage.FAILED
    if snapshot.in_flight:
        return (
            ProfileSubmitStage.TIMED_OUT
            if timed_out else ProfileSubmitStage.IN_FLIGHT
        )
    if timed_out:
        return ProfileSubmitStage.STALLED
    return ProfileSubmitStage.SUBMITTED


def save_profile_diagnostics(page, stage, snapshot=None, reason='', directory=None):
    """Persist a compact state snapshot and best-effort screenshot."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    directory = directory or os.path.join(project_root, 'data', 'diagnostics')
    os.makedirs(directory, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    stage_value = stage.value if isinstance(stage, ProfileSubmitStage) else str(stage)
    base_name = f'profile-{timestamp}-{stage_value}'
    json_path = os.path.join(directory, f'{base_name}.json')
    png_path = os.path.join(directory, f'{base_name}.png')

    payload = {
        'stage': stage_value,
        'reason': str(reason or ''),
        'snapshot': asdict(snapshot or ProfileSubmitSnapshot()),
    }
    try:
        payload['page_title'] = str(getattr(page, 'title', '') or '')
    except Exception:
        payload['page_title'] = ''
    try:
        payload['page_url'] = str(getattr(page, 'url', '') or '')
    except Exception:
        payload['page_url'] = ''

    with open(json_path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    screenshot_path = ''
    try:
        result = page.get_screenshot(
            path=directory, name=f'{base_name}.png', full_page=True,
        )
        screenshot_path = str(result or png_path)
    except TypeError:
        try:
            result = page.get_screenshot(path=directory, name=f'{base_name}.png')
            screenshot_path = str(result or png_path)
        except Exception:
            screenshot_path = ''
    except Exception:
        screenshot_path = ''

    return {'json': json_path, 'screenshot': screenshot_path}
