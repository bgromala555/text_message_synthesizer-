/**
 * Keyboard navigation for tab bars and modal dismissal.
 *
 * Enables arrow-key navigation between tab buttons, Enter/Space activation,
 * and Escape-key dismissal of open modals. Designed for WAI-ARIA compliant
 * keyboard interaction within the tab bar component.
 * @module
 */

import { closeModal } from '../core/ModalService.js';

/** DOM IDs of modals that can be dismissed with the Escape key. */
const MODAL_IDS = ['ai-events-modal', 'ai-storyarc-modal', 'file-browser-modal'] as const;

/**
 * Manages keyboard navigation for tab bars and global keyboard shortcuts.
 *
 * Listens for keydown events on the document and delegates to handlers
 * for tab arrow-key navigation, tab activation, and modal dismissal.
 */
export class KeyboardManager {
  /**
   * Collect all tab button elements from the tab bar.
   *
   * @returns An ordered array of tab button elements.
   */
  private getTabButtons(): HTMLElement[] {
    return Array.from(document.querySelectorAll<HTMLElement>('.tab-bar .tab-btn'));
  }

  /**
   * Navigate between tab buttons using arrow keys, Home, and End.
   *
   * ArrowRight/ArrowDown advance to the next tab, wrapping at the end.
   * ArrowLeft/ArrowUp move to the previous tab, wrapping at the start.
   * Home jumps to the first tab; End jumps to the last.
   *
   * @param e - The keyboard event to inspect.
   */
  private handleTabArrowKeys(e: KeyboardEvent): void {
    const tabs = this.getTabButtons();
    const idx = tabs.indexOf(document.activeElement as HTMLElement);
    if (idx < 0) return;

    let next = -1;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      next = (idx + 1) % tabs.length;
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      next = (idx - 1 + tabs.length) % tabs.length;
    } else if (e.key === 'Home') {
      next = 0;
    } else if (e.key === 'End') {
      next = tabs.length - 1;
    }

    if (next >= 0) {
      e.preventDefault();
      tabs[next]?.focus();
    }
  }

  /**
   * Activate the focused tab button when Enter or Space is pressed.
   *
   * Prevents the default scroll behavior for Space and programmatically
   * clicks the focused tab button.
   *
   * @param e - The keyboard event to inspect.
   */
  private handleTabActivation(e: KeyboardEvent): void {
    if (e.key !== 'Enter' && e.key !== ' ') return;

    const tabs = this.getTabButtons();
    const active = document.activeElement as HTMLElement;
    if (tabs.includes(active)) {
      e.preventDefault();
      active.click();
    }
  }

  /**
   * Dismiss the first visible modal when Escape is pressed.
   *
   * Iterates {@link MODAL_IDS} in order and closes the first modal
   * whose element exists and is not hidden.
   *
   * @param e - The keyboard event to inspect.
   */
  private handleEscapeKey(e: KeyboardEvent): void {
    if (e.key !== 'Escape') return;
    for (const id of MODAL_IDS) {
      const modal = document.getElementById(id);
      if (modal && !modal.classList.contains('hidden')) {
        closeModal(id);
        return;
      }
    }
  }

  /**
   * Initialize keyboard event listeners and tab accessibility attributes.
   *
   * Sets `tabindex="0"` on all tab buttons so they are focusable, then
   * registers a single document-level keydown listener that delegates
   * to the escape, arrow-key, and activation handlers.
   */
  public init(): void {
    for (const btn of document.querySelectorAll<HTMLElement>('.tab-bar .tab-btn')) {
      btn.setAttribute('tabindex', '0');
    }

    document.addEventListener('keydown', (e: KeyboardEvent) => {
      this.handleEscapeKey(e);
      this.handleTabArrowKeys(e);
      this.handleTabActivation(e);
    });
  }
}

/** Default singleton used by the convenience export. */
const defaultManager = new KeyboardManager();

/**
 * Initialize keyboard navigation using the default manager instance.
 *
 * Convenience wrapper that preserves the original module's public API
 * by delegating to the default {@link KeyboardManager} instance.
 */
export function initKeyboard(): void {
  defaultManager.init();
}
