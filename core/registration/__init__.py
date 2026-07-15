"""Registration workflow primitives shared by the Web registration engine."""

from .state import (
    EMAIL_REQUEST_MIN_INTERVAL,
    RegistrationState,
    VerificationRequestError,
    email_request_slot,
    is_xai_permission_denied,
    submit_is_in_flight,
)
from .profile import (
    ProfileSubmitSnapshot,
    ProfileSubmitStage,
    classify_profile_submit,
    save_profile_diagnostics,
)

__all__ = [
    'EMAIL_REQUEST_MIN_INTERVAL',
    'RegistrationState',
    'VerificationRequestError',
    'email_request_slot',
    'is_xai_permission_denied',
    'submit_is_in_flight',
    'ProfileSubmitSnapshot',
    'ProfileSubmitStage',
    'classify_profile_submit',
    'save_profile_diagnostics',
]
