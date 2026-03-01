/**
 * Hamburger menu and slide-out mobile navigation.
 *
 * Creates a slide-out panel cloned from the desktop tab bar, an overlay
 * backdrop, and a hamburger toggle button. Keeps the active-tab state
 * in sync with the desktop tab bar via a MutationObserver.
 * @module
 */

/**
 * Manages the mobile hamburger menu and slide-out navigation panel.
 *
 * On initialization, the manager creates overlay and slide-panel DOM
 * elements, clones tab buttons from the desktop tab bar, and wires up
 * click handlers and a MutationObserver for active-state synchronization.
 */
export class MobileNavManager {
  /** The hamburger toggle button element. */
  private hamburgerBtn: HTMLElement | null = null;

  /** The slide-out navigation panel element. */
  private slidePanel: HTMLElement | null = null;

  /** The semi-transparent overlay backdrop element. */
  private overlay: HTMLElement | null = null;

  /**
   * Slide the navigation panel open and show the overlay.
   *
   * Sets the `open` CSS class on both the panel and overlay, and
   * updates the hamburger button's `aria-expanded` attribute.
   */
  private openPanel(): void {
    if (!this.slidePanel || !this.overlay || !this.hamburgerBtn) return;
    this.slidePanel.classList.add('open');
    this.overlay.classList.add('open');
    this.hamburgerBtn.setAttribute('aria-expanded', 'true');
  }

  /**
   * Slide the navigation panel closed and hide the overlay.
   *
   * Removes the `open` CSS class from both the panel and overlay,
   * and updates the hamburger button's `aria-expanded` attribute.
   */
  private closePanel(): void {
    if (!this.slidePanel || !this.overlay || !this.hamburgerBtn) return;
    this.slidePanel.classList.remove('open');
    this.overlay.classList.remove('open');
    this.hamburgerBtn.setAttribute('aria-expanded', 'false');
  }

  /**
   * Toggle the panel between open and closed states.
   */
  private togglePanel(): void {
    if (this.slidePanel?.classList.contains('open')) {
      this.closePanel();
    } else {
      this.openPanel();
    }
  }

  /**
   * Initialize the mobile navigation system.
   *
   * Locates the hamburger button and desktop tab bar in the DOM,
   * creates the overlay and slide panel elements, clones all tab
   * buttons into the slide panel, and sets up event listeners and
   * a MutationObserver to keep active-tab state synchronized.
   *
   * Does nothing if the hamburger button or tab bar is not present
   * in the DOM (e.g. on desktop viewports without a hamburger button).
   */
  public init(): void {
    this.hamburgerBtn = document.getElementById('hamburger-btn');
    if (!this.hamburgerBtn) return;

    this.overlay = document.createElement('div');
    this.overlay.className = 'mobile-nav-overlay';
    document.body.appendChild(this.overlay);

    this.slidePanel = document.createElement('nav');
    this.slidePanel.className = 'mobile-slide-panel';
    this.slidePanel.setAttribute('aria-label', 'Mobile navigation');

    const tabBar = document.querySelector<HTMLElement>('.tab-bar');
    if (!tabBar) return;

    const buttons = tabBar.querySelectorAll<HTMLElement>('.tab-btn');
    for (const btn of buttons) {
      const clone = btn.cloneNode(true) as HTMLElement;
      clone.addEventListener('click', () => {
        btn.click();
        this.closePanel();
      });
      this.slidePanel.appendChild(clone);
    }

    document.body.appendChild(this.slidePanel);

    this.hamburgerBtn.addEventListener('click', () => {
      this.togglePanel();
    });

    this.overlay.addEventListener('click', () => {
      this.closePanel();
    });

    // Synchronize cloned button active-states with the real tab bar
    const slidePanel = this.slidePanel;
    const observer = new MutationObserver(() => {
      const realBtns = tabBar.querySelectorAll('.tab-btn');
      const cloneBtns = slidePanel.querySelectorAll('.tab-btn');
      for (let i = 0; i < realBtns.length && i < cloneBtns.length; i++) {
        cloneBtns[i].classList.toggle('active', realBtns[i].classList.contains('active'));
      }
    });
    observer.observe(tabBar, {
      attributes: true,
      subtree: true,
      attributeFilter: ['class'],
    });
  }
}

/** Default singleton used by the convenience export. */
const defaultManager = new MobileNavManager();

/**
 * Initialize mobile navigation using the default manager instance.
 *
 * Convenience wrapper that preserves the original module's public API.
 */
export function initMobileNav(): void {
  defaultManager.init();
}
