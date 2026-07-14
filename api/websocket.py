import logging
from datetime import datetime


class SocketIOHandler(logging.Handler):
    # Map Python logging levels to frontend expected levels
    LEVEL_MAP = {
        'debug': 'debug',
        'info': 'info',
        'warning': 'warn',
        'error': 'error',
        'critical': 'error',
    }

    def __init__(self, socketio):
        super().__init__()
        self.socketio = socketio
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record):
        try:
            level = self.LEVEL_MAP.get(record.levelname.lower(), 'info')
            log_entry = {
                'level': level,
                'message': self.format(record),
                'timestamp': datetime.now().isoformat(),
            }
            self.socketio.emit('log', log_entry)
        except Exception:
            pass


def init_websocket(socketio, state_getter=None):
    @socketio.on('connect')
    def handle_connect():
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

    return SocketIOHandler(socketio)
