/* api.js -- Fetch wrapper for API calls */

import { showToast } from './toast.js';

export async function api(method, url, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    const data = await res.json();
    if (!res.ok) {
        const msg = data.detail || data.error || 'Request failed';
        showToast(msg);
        throw new Error(msg);
    }
    return data;
}

export async function fetchJSON(url) {
    return api('GET', url, undefined);
}

export async function postJSON(url, body) {
    return api('POST', url, body);
}

export async function putJSON(url, body) {
    return api('PUT', url, body);
}

export async function deleteJSON(url) {
    return api('DELETE', url, undefined);
}
