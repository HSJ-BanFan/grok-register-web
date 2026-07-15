"""Registration failure classification shared by scheduling and persistence."""


FAILURE_CATEGORY_MAIL_FETCH = 'mail_fetch'
FAILURE_CATEGORY_REGISTRATION = 'registration'
FAILURE_CATEGORY_EXISTING_ACCOUNT = 'existing_account'

MAIL_FETCH_ERROR_MARKERS = (
    'failed to get verification code',
    'token refresh failed',
    'microsoft graph mail failed',
    'outlook rest mail failed',
    'imap ',
    'mail.tm',
)


def is_mail_fetch_error(error_msg: str) -> bool:
    """Return whether a registration failure came from mailbox/code retrieval."""
    text = (error_msg or '').lower()
    return any(marker in text for marker in MAIL_FETCH_ERROR_MARKERS)


def classify_failure(error_msg: str) -> str:
    """Map a registration exception to a stable category stored with the alias."""
    text = (error_msg or '').lower()
    if (
        '注册邮箱已存在' in text
        or 'existing account found' in text
        or 'an account already exists which is associated with this email address' in text
    ):
        return FAILURE_CATEGORY_EXISTING_ACCOUNT
    if is_mail_fetch_error(error_msg):
        return FAILURE_CATEGORY_MAIL_FETCH
    return FAILURE_CATEGORY_REGISTRATION


def account_disable_reason(consecutive_mail_fails: int, used_count: int,
                           total_count: int, max_aliases: int,
                           failure_budget: int) -> str:
    """Return the policy reason for disabling an account, or an empty string."""
    if consecutive_mail_fails >= 2:
        return (
            'mailbox unusable '
            f'({consecutive_mail_fails} consecutive verification-code alias failures)'
        )
    budget = max_aliases + failure_budget
    if used_count < max_aliases and total_count >= budget:
        return f'alias budget exhausted ({total_count}/{budget}, used={used_count})'
    return ''
