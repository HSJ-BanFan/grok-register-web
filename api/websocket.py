import logging
from collections import deque
from datetime import datetime


class SocketIOHandler(logging.Handler):
    """Python logging → WebSocket 'log' + in-memory ring buffer for reconnect replay."""

    LEVEL_MAP = {
        'debug': 'debug',
        'info': 'info',
        'warning': 'warn',
        'error': 'error',
        'critical': 'error',
    }
    MAX_BUFFER = 300

    def __init__(self, socketio):
        super().__init__()
        self.socketio = socketio
        self._buffer = deque(maxlen=self.MAX_BUFFER)
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record):
        try:
            level = self.LEVEL_MAP.get(record.levelname.lower(), 'info')
            log_entry = {
                'level': level,
                'message': self.format(record),
                'timestamp': datetime.now().isoformat(),
            }
            self._buffer.append(log_entry)
            self.socketio.emit('log', log_entry)
        except Exception:
            pass

    def replay(self, namespace=None):
        """Return up to MAX_BUFFER entries for a freshly-connected client."""
        return list(self._buffer)


def init_websocket(socketio, state_getter=None):
    handler = SocketIOHandler(socketio)

    @socketio.on('connect')
    def handle_connect():
        # Replay recent log buffer on connect (no await needed inside socket event)
        entries = handler.replay()
        if entries:
            handler.socketio.emit('log_replay', {'entries': entries, 'total': len(entries)})

        if state_getter:
            state = state_getter()
            if state:
                socketio.emit('status_update', state.get_snapshot())
            else:
                socketio.emit('status_update', {
                    'status': 'stopped',
                    'current_round': 0,
                    'current_email': '',
                    'completed': 0,
                    'success': 0,
                    'failed': 0,
                })

    return handler
