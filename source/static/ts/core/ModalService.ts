/**
 * Modal dialog management service with accessible focus trapping.
 *
 * Provides open/close lifecycle for modal elements identified by DOM ID.
 * While a modal is open, keyboard focus is trapped within the modal to
 * satisfy WAI-ARIA dialog accessibility requirements.
 * @module
 */

/** CSS selector matching all elements that can receive keyboard focus. */
const FOCUSABLE_SELECTOR = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Manages modal dialog visibility and focus trapping.
 *
 * Tracks the currently active modal, saves and restores the previously
 * focused element, and installs a keydown listener that traps Tab
 * navigation within the modal boundary.
 */
export class ModalService {
  /** Element that held focus before the modal opened. */
  private previousFocus: HTMLElement | null = null;

  /** DOM ID of the currently open modal, or `null` when no modal is active. */
  private activeModalId: string | null = null;

  /** Bound keydown handler for focus trapping — created once and reused. */
  private readonly trapHandler: (e: KeyboardEvent) => void;

  constructor() {
    this.trapHandler = (e: KeyboardEvent): void => {
      this.handleTrapFocus(e);
    };
  }

  /**
   * Collect all focusable, visible, enabled elements within a modal.
   *
   * Filters out disabled form elements and elements hidden via
   * `display: none` (detected through `offsetParent`).
   *
   * @param modal - The modal container element to search within.
   * @returns An ordered array of focusable elements inside the modal.
   */
  private getFocusableElements(modal: HTMLElement): HTMLElement[] {
    return Array.from(modal.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter((el) => {
      if ('disabled' in el && (el as HTMLButtonElement).disabled) {
        return false;
      }
      return el.offsetParent !== null;
    });
  }

  /**
   * Keyboard handler that wraps Tab focus at modal boundaries.
   *
   * When the user presses Tab on the last focusable element, focus wraps
   * to the first. Shift+Tab on the first element wraps to the last.
   *
   * @param e - The keyboard event to inspect.
   */
  private handleTrapFocus(e: KeyboardEvent): void {
    if (e.key !== 'Tab' || !this.activeModalId) return;

    const modal = document.getElementById(this.activeModalId);
    if (!modal) return;

    const focusable = this.getFocusableElements(modal);
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  /**
   * Open a modal dialog by its DOM ID.
   *
   * Removes the `hidden` CSS class, moves focus to the first focusable
   * child element, and activates the Tab-trapping keydown listener.
   * The previously focused element is saved so it can be restored on close.
   *
   * @param id - The `id` attribute of the modal element to open.
   */
  public open(id: string): void {
    const el = document.getElementById(id);
    if (!el) return;

    this.previousFocus = document.activeElement as HTMLElement | null;
    this.activeModalId = id;
    el.classList.remove('hidden');

    const focusable = this.getFocusableElements(el);
    if (focusable.length > 0) {
      requestAnimationFrame(() => {
        focusable[0].focus();
      });
    }

    document.removeEventListener('keydown', this.trapHandler);
    document.addEventListener('keydown', this.trapHandler);
  }

  /**
   * Close a modal dialog by its DOM ID.
   *
   * Adds the `hidden` CSS class, removes the focus trap listener,
   * and restores focus to the element that was active before the
   * modal was opened.
   *
   * @param id - The `id` attribute of the modal element to close.
   */
  public close(id: string): void {
    const el = document.getElementById(id);
    if (el) {
      el.classList.add('hidden');
    }

    if (this.activeModalId === id) {
      this.activeModalId = null;
      document.removeEventListener('keydown', this.trapHandler);

      if (this.previousFocus && typeof this.previousFocus.focus === 'function') {
        this.previousFocus.focus();
      }
      this.previousFocus = null;
    }
  }
}

/** Default singleton used by the convenience exports. */
const defaultService = new ModalService();

/**
 * Open a modal dialog by DOM ID using the default service instance.
 *
 * @param id - The `id` attribute of the modal element to open.
 */
export function openModal(id: string): void {
  defaultService.open(id);
}

/**
 * Close a modal dialog by DOM ID using the default service instance.
 *
 * @param id - The `id` attribute of the modal element to close.
 */
export function closeModal(id: string): void {
  defaultService.close(id);
}
