let socket = null;
let handlers = {};

export function connectSocket(h = {}) {
    Object.assign(handlers, h);
    if (socket) return socket;
    socket = io();

    socket.on('log', (data) => {
        if (handlers.onLog) handlers.onLog(data);
    });

    socket.on('status_update', (data) => {
        if (handlers.onStatusUpdate) handlers.onStatusUpdate(data);
    });

    socket.on('round_complete', (data) => {
        if (handlers.onRoundComplete) handlers.onRoundComplete(data);
    });

    socket.on('error', (data) => {
        if (handlers.onError) handlers.onError(data);
    });

    socket.on('connect', () => {
        console.log('WebSocket connected');
    });

    socket.on('disconnect', () => {
        console.log('WebSocket disconnected');
    });

    return socket;
}

export function getSocket() {
    return socket;
}
