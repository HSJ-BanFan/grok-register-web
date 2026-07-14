export async function api(method, path, body = null) {
    const opts = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body !== null) {
        opts.body = JSON.stringify(body);
    }
    try {
        const res = await fetch(path, opts);
        if (res.headers.get('content-type')?.includes('application/json')) {
            return await res.json();
        }
        // For file downloads (blob)
        if (res.headers.get('content-type')?.includes('octet-stream') ||
            res.headers.get('content-disposition')) {
            const blob = await res.blob();
            const disposition = res.headers.get('content-disposition') || '';
            const match = disposition.match(/filename="?([^"]+)"?/);
            const filename = match ? match[1] : 'download';
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.click();
            URL.revokeObjectURL(url);
            return { success: true, data: null, message: 'File downloaded' };
        }
        return { success: res.ok, data: null, message: res.statusText };
    } catch (e) {
        return { success: false, data: null, message: e.message, code: 'NETWORK_ERROR' };
    }
}
