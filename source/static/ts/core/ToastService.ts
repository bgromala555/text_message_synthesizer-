/**
 * Toast notification service with typed variants and auto-dismiss.
 *
 * Displays brief, self-dismissing notifications to provide visual
 * feedback for user actions. Supports success, error, info, and
 * warning variants with matching CSS classes.
 * @module
 */

import type { ToastType } from '../shared/types.js';

/** Duration in milliseconds before a toast begins its dismiss animation. */
const AUTO_DISMISS_MS = 4000;

/** Fallback removal delay if the CSS transition event never fires. */
const TRANSITION_FALLBACK_MS = 500;

/** DOM ID for the shared toast container element. */
const CONTAINER_ID = 'toast-container';

/**
 * Manages the lifecycle of toast notification elements.
 *
 * Lazily creates a shared container on first use and appends individual
 * toast elements that auto-dismiss after a configurable delay.
 */
export class ToastService {
  /**
   * Retrieve the toast container element, creating it if absent.
   *
   * @returns The container `<div>` that holds all active toasts.
   */
  private getOrCreateContainer(): HTMLDivElement {
    let container = document.getElementById(CONTAINER_ID);
    if (!container) {
      container = document.createElement('div');
      container.id = CONTAINER_ID;
      container.className = 'toast-container';
      document.body.appendChild(container);
    }
    return container as HTMLDivElement;
  }

  /**
   * Display a toast notification.
   *
   * Creates a toast element, appends it to the container, and schedules
   * automatic removal after {@link AUTO_DISMISS_MS} milliseconds. A
   * fallback timer ensures removal even if the CSS transition never fires.
   *
   * @param message - Text content to display inside the toast.
   * @param type - Visual variant controlling the CSS class applied.
   *   Defaults to `"info"`.
   */
  public show(message: string, type: ToastType = 'info'): void {
    const container = this.getOrCreateContainer();
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    el.setAttribute('role', 'alert');
    el.setAttribute('aria-live', 'assertive');
    container.appendChild(el);

    setTimeout(() => {
      el.classList.add('toast-dismiss');
      el.addEventListener(
        'transitionend',
        () => {
          el.remove();
        },
        { once: true }
      );
      setTimeout(() => {
        el.remove();
      }, TRANSITION_FALLBACK_MS);
    }, AUTO_DISMISS_MS);
  }
}

/** Default singleton used by the convenience export. */
const defaultService = new ToastService();

/**
 * Display a toast notification using the default service instance.
 *
 * Convenience wrapper that preserves the original module's public API
 * for backward compatibility with existing callers.
 *
 * @param msg - Text content to display inside the toast.
 * @param type - Visual variant. Defaults to `"info"`.
 */
export function showToast(msg: string, type: ToastType = 'info'): void {
  defaultService.show(msg, type);
}
