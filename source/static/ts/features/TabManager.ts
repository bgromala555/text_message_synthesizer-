/**
 * Tab switching logic and status indicators.
 *
 * Manages the tab bar UI by toggling active states on tab buttons and
 * their corresponding panels. When a tab becomes active, the appropriate
 * feature render function is invoked to populate the panel content.
 * @module
 */

import { renderContacts } from './ContactManager.js';
import { renderEvents, renderGroupChats } from './EventManager.js';
import { renderGenerate } from './GenerateManager.js';
import { renderLinkChart } from './LinkChartManager.js';
import { renderPersonalities } from './PersonalityManager.js';
import { renderStoryArc } from './StoryArcManager.js';

/** Known tab panel identifiers that can be activated. */
type TabName = 'devices' | 'contacts' | 'personalities' | 'storyarc' | 'eventslinks' | 'generate';

/**
 * Manages tab switching and panel activation in the main navigation.
 *
 * Binds click handlers to all `.tab-btn` elements so that clicking a tab
 * deactivates all siblings, activates the clicked tab and its panel, then
 * invokes the corresponding render function to refresh content.
 */
export class TabManager {
  /**
   * Handle a tab switch by invoking the render functions for the activated panel.
   *
   * Each panel maps to one or more render calls that refresh
   * the DOM content for that feature area.
   *
   * @param tab - The tab name that was just activated.
   */
  private onTabSwitch(tab: TabName): void {
    if (tab === 'contacts') renderContacts();
    if (tab === 'personalities') renderPersonalities();
    if (tab === 'storyarc') renderStoryArc();
    if (tab === 'eventslinks') {
      renderLinkChart();
      renderEvents();
      renderGroupChats();
    }
    if (tab === 'generate') renderGenerate();
  }

  /**
   * Initialize tab click handlers on all `.tab-btn` elements.
   *
   * Each tab button must have a `data-tab` attribute whose value matches
   * the suffix of the panel element's DOM ID (e.g. `data-tab="contacts"`
   * activates `#panel-contacts`).
   */
  public init(): void {
    const buttons = document.querySelectorAll<HTMLElement>('.tab-btn');

    for (const btn of buttons) {
      btn.addEventListener('click', () => {
        for (const b of buttons) {
          b.classList.remove('active');
        }
        for (const p of document.querySelectorAll<HTMLElement>('.tab-panel')) {
          p.classList.remove('active');
        }

        btn.classList.add('active');
        const tabName = btn.dataset['tab'] as TabName | undefined;
        if (!tabName) return;

        const panel = document.getElementById(`panel-${tabName}`);
        if (panel) panel.classList.add('active');

        this.onTabSwitch(tabName);
      });
    }
  }
}

/** Default singleton used by the convenience export. */
const defaultManager = new TabManager();

/**
 * Initialize tab switching using the default manager instance.
 *
 * Convenience wrapper that preserves the original module's public API.
 */
export function initTabs(): void {
  defaultManager.init();
}

/**
 * Programmatically switch to a tab by name.
 *
 * Finds the `.tab-btn` with a matching `data-tab` attribute and
 * simulates a click to trigger the full tab-switch flow.
 *
 * @param tab - The tab name to activate.
 */
export function switchTab(tab: TabName): void {
  const btn = document.querySelector<HTMLElement>(`.tab-btn[data-tab="${tab}"]`);
  if (btn) btn.click();
}
