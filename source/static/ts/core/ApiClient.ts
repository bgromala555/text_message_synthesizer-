/**
 * Typed fetch wrapper for API communication.
 *
 * Handles JSON serialization, error reporting via toast notifications,
 * and provides convenience methods for common HTTP verbs.
 * The toast handler is configurable via {@link configureToast} so the
 * core layer stays decoupled from the UI notification implementation.
 */

import type { ToastType } from '../shared/types.js';

/**
 * Function signature for displaying toast notifications.
 * Configure an implementation via {@link configureToast} before
 * the first API call that might fail.
 */
export type ToastService = (message: string, type?: ToastType) => void;

/** HTTP methods supported by the API client. */
type HttpMethod = 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';

/** Module-level toast handler — defaults to a console.error fallback. */
let showToast: ToastService = (msg: string): void => {
  console.error('[API]', msg);
};

/**
 * Set the toast notification handler used by all API functions.
 *
 * Call this once during application bootstrap to wire the real
 * toast UI into the API layer.
 *
 * @param toast - A function that displays user-facing toast messages.
 */
export function configureToast(toast: ToastService): void {
  showToast = toast;
}

/**
 * Send an HTTP request with an optional JSON body and parse the JSON response.
 *
 * On non-2xx responses the server error message is surfaced as a toast
 * notification and an {@link Error} is thrown with that message.
 *
 * @typeParam T - Expected shape of the parsed response body.
 * @param method - HTTP verb to use.
 * @param url    - Request URL (absolute or relative to the current origin).
 * @param body   - Optional payload to serialize as JSON.
 * @returns Parsed JSON response body cast to `T`.
 * @throws {Error} When the server returns a non-2xx status code.
 */
export async function api<T = unknown>(method: HttpMethod, url: string, body?: unknown): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) {
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(url, opts);
  const data: unknown = await res.json();

  if (!res.ok) {
    const errorData = data as Record<string, unknown>;
    const msg = String(errorData['detail'] ?? errorData['error'] ?? 'Request failed');
    showToast(msg, 'error');
    throw new Error(msg);
  }

  return data as T;
}

/**
 * Convenience wrapper for HTTP GET requests.
 *
 * @typeParam T - Expected shape of the parsed response body.
 * @param url - Request URL.
 * @returns Parsed JSON response body.
 */
export async function fetchJSON<T = unknown>(url: string): Promise<T> {
  return api<T>('GET', url);
}

/**
 * Convenience wrapper for HTTP POST requests.
 *
 * @typeParam T - Expected shape of the parsed response body.
 * @param url  - Request URL.
 * @param body - Payload to serialize as JSON.
 * @returns Parsed JSON response body.
 */
export async function postJSON<T = unknown>(url: string, body: unknown): Promise<T> {
  return api<T>('POST', url, body);
}

/**
 * Convenience wrapper for HTTP PUT requests.
 *
 * @typeParam T - Expected shape of the parsed response body.
 * @param url  - Request URL.
 * @param body - Payload to serialize as JSON.
 * @returns Parsed JSON response body.
 */
export async function putJSON<T = unknown>(url: string, body: unknown): Promise<T> {
  return api<T>('PUT', url, body);
}

/**
 * Convenience wrapper for HTTP DELETE requests.
 *
 * @typeParam T - Expected shape of the parsed response body.
 * @param url - Request URL.
 * @returns Parsed JSON response body.
 */
export async function deleteJSON<T = unknown>(url: string): Promise<T> {
  return api<T>('DELETE', url);
}
