import logging
import os
import time

from core.account_activation import activate_grok_web, clear_sso_cookies
from core.grok2api_client import Grok2APIClient, Grok2APIError
from core.runtime import resolve_browser_headless


logger = logging.getLogger('register')


class BatchActivationEngine:
    """Re-activate historical success SSO accounts one by one.

    For each account:
      1. Switch SSO only (preserve Cloudflare clearance when possible)
      2. Accept TOS + set birth date + Web health probe
      3. Refresh shared grok2api Web egress (UA + Cloudflare cookies)
      4. Do NOT re-convert Build accounts (preserve Web ↔ Build links)
    """

    def __init__(self, db, browser_mgr, socketio, state):
        self.db = db
        self.browser = browser_mgr
        self.socketio = socketio
        self.state = state

    def run(self, limit=0, ids=None):
        self.state.status = 'running'
        settings = self.db.get_settings()
        headless = resolve_browser_headless(settings)

        records = self._load_records(ids=ids, limit=limit)
        total = len(records)
        logger.info(
            'Batch Web reactivation started for %s historical SSO account(s)%s',
            total,
            f' (limit={limit})' if limit else '',
        )

        if total == 0:
            logger.warning('No historical success SSO records found for reactivation')
            self.state.status = 'stopped'
            self._emit_status()
            return

        try:
            self.browser.headless = headless
            # Use auto-port temp profile. Within one batch run we preserve
            # cf_clearance by only clearing SSO cookies between accounts.
            self.browser.user_data_path = None
            self.browser.proxy = (settings.get('browser_proxy', '') or '').strip()
            self.browser.start()
        except Exception as exc:
            logger.error(f'Failed to start browser for reactivation: {exc}')
            self.state.status = 'stopped'
            self._emit_status()
            return

        last_context = None
        try:
            for index, record in enumerate(records, start=1):
                if self.state.should_stop():
                    logger.info('Stop flag detected, finishing batch reactivation')
                    break

                self.state.check_pause()
                self.state.current_round = index
                self.state.current_email = record.get('email', '')
                self._emit_status()

                try:
                    context = self._activate_one(record, settings)
                    if context and context.ready:
                        last_context = context
                        self.state.success += 1
                    else:
                        self.state.failed += 1
                except Exception as exc:
                    self.state.failed += 1
                    logger.error(
                        'Batch reactivation failed for %s: %s',
                        record.get('email', ''),
                        exc,
                    )
                finally:
                    self.state.completed += 1
                    self._emit_status()
                    if not self.state.should_stop():
                        # Only drop identity cookies between accounts.
                        # Keep cf_clearance so later accounts skip the CF wall.
                        self._clear_identity_only()

            if last_context and last_context.ready:
                self._sync_egress(settings, last_context)
        finally:
            self.state.status = 'stopped'
            try:
                self.browser.stop()
            except Exception:
                pass
            self._emit_status()
            logger.info(
                'Batch Web reactivation ended. Completed: %s, Success: %s, Failed: %s',
                self.state.completed,
                self.state.success,
                self.state.failed,
            )

    def _load_records(self, ids=None, limit=0):
        rows = self.db.get_registrations('sso')
        # get_registrations returns newest first; process oldest first so CF context
        # ends on a more recent account after the full pass.
        rows = list(reversed(rows))
        if ids:
            id_set = {int(item) for item in ids}
            rows = [row for row in rows if int(row.get('id', 0)) in id_set]
        if limit and limit > 0:
            rows = rows[:limit]
        return rows

    def _activate_one(self, record, settings):
        email = record.get('email', '')
        sso = (record.get('sso_value') or '').strip()
        reg_id = record.get('id')
        logger.info('Reactivating historical account [%s] %s', reg_id, email)

        if not sso:
            logger.warning('Skip empty SSO for %s', email)
            return None

        self._clear_identity_only()
        logger.info('Activating Grok Web for historical SSO...')
        # timeout=0 => wait indefinitely for manual Cloudflare completion.
        # After the first account clears CF, later accounts reuse cf_clearance.
        activation = activate_grok_web(
            self.browser,
            sso,
            timeout=0,
            reuse_cloudflare=True,
            proxy_url=(settings.get('browser_proxy', '') or '').strip(),
        )
        if activation.ready:
            logger.info('Grok Web activation completed for %s: %s', email, activation.message)
        else:
            logger.warning('Grok Web activation incomplete for %s: %s', email, activation.message)
            return activation

        # Refresh shared egress after every success so partial runs still leave a usable CF context.
        try:
            self._sync_egress(settings, activation)
        except Exception as exc:
            logger.warning('grok2api egress update failed for %s: %s', email, exc)

        self.socketio.emit('round_complete', {
            'round': self.state.current_round,
            'email': email,
            'success': activation.ready,
            'sso': (sso[:50] + '...') if len(sso) > 50 else sso,
            'duration': 0,
            'mode': 'reactivate',
        })
        return activation

    def _sync_egress(self, settings, activation):
        if settings.get('grok2api_auto_upload', 'false') != 'true':
            logger.info('grok2api auto upload disabled; skip egress update')
            return None
        if not activation.user_agent or not activation.cloudflare_cookies:
            raise Grok2APIError('Missing User-Agent or Cloudflare cookies for egress update')

        base_url = settings.get('grok2api_url', '').strip()
        username = settings.get('grok2api_username', '').strip()
        password = settings.get('grok2api_password', '')
        if not base_url or not username or not password:
            raise Grok2APIError('grok2api auto upload is enabled but URL/username/password is incomplete')

        logger.info('Updating grok2api Grok Web egress Cloudflare context...')
        client = Grok2APIClient(base_url, username, password)
        result = client.upsert_web_egress_context(activation.user_agent, activation.cloudflare_cookies)
        logger.info('grok2api egress context updated for node grok-register-web')
        return result

    def _clear_identity_only(self):
        """Clear only SSO identity; keep Cloudflare cookies and browser profile."""
        try:
            page = self.browser.page
            if page:
                clear_sso_cookies(page)
        except Exception:
            pass
        try:
            # Soft tab recycle without wiping cookies/cache.
            if self.browser.browser:
                new_page = self.browser.browser.new_tab('about:blank')
                try:
                    if self.browser.page:
                        self.browser.page.close()
                except Exception:
                    pass
                self.browser._page = new_page
        except Exception:
            pass
        time.sleep(0.3)

    def _emit_status(self):
        try:
            self.socketio.emit('status_update', self.state.get_snapshot())
        except Exception:
            pass
