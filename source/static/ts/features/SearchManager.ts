/**
 * Client-side search and filter for list panels.
 *
 * Injects filter input fields into configured panels and provides
 * real-time, debounced filtering of list items with text highlighting.
 * Observes DOM mutations to re-apply filters when list content changes.
 * @module
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** Debounce delay in milliseconds for the filter input handler. */
const DEBOUNCE_MS = 300;

/** Descriptor for a single searchable list panel. */
interface SearchTarget {
  /** DOM ID of the list element containing filterable items. */
  readonly listId: string;
  /** DOM ID of the parent panel wrapping the list. */
  readonly panelId: string;
  /** Placeholder text displayed in the filter input. */
  readonly placeholder: string;
  /** CSS selector for the individual items to show/hide. */
  readonly itemSelector: string;
}

/** Default set of panels that receive a search input on initialization. */
const SEARCH_TARGETS: readonly SearchTarget[] = [
  {
    listId: 'devices-list',
    panelId: 'panel-devices',
    placeholder: 'Filter devices...',
    itemSelector: '.device-card',
  },
  {
    listId: 'contacts-list',
    panelId: 'panel-contacts',
    placeholder: 'Filter contacts...',
    itemSelector: '.card.device-card, .card',
  },
  {
    listId: 'events-list',
    panelId: 'panel-eventslinks',
    placeholder: 'Filter events...',
    itemSelector: '.card.event-card, .card',
  },
  {
    listId: 'personalities-list',
    panelId: 'panel-personalities',
    placeholder: 'Filter personalities...',
    itemSelector: '.card.personality-card, .card',
  },
];

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/**
 * Create a debounced version of a zero-argument function.
 *
 * The wrapped function delays invocation until `ms` milliseconds have
 * elapsed since the last call. Each new call resets the timer.
 *
 * @param fn - The function to debounce.
 * @param ms - Delay in milliseconds.
 * @returns A wrapper that resets the timer on each call.
 */
function debounce(fn: () => void, ms: number): () => void {
  let timer: number | undefined;
  return (): void => {
    clearTimeout(timer);
    timer = window.setTimeout(fn, ms);
  };
}

/**
 * Wrap every occurrence of `term` inside a text node with a `<mark>` element.
 *
 * The original text node is replaced by a document fragment containing
 * interleaved text nodes and `<mark class="search-highlight">` elements.
 *
 * @param textNode - The text node to scan for occurrences.
 * @param term - The search term to highlight (case-insensitive match).
 */
function highlightTextNode(textNode: Text, term: string): void {
  const text = textNode.nodeValue;
  if (!text) return;

  const lower = text.toLowerCase();
  const tLower = term.toLowerCase();
  let idx = lower.indexOf(tLower);
  if (idx === -1) return;

  const frag = document.createDocumentFragment();
  let cursor = 0;

  while (idx !== -1) {
    if (idx > cursor) {
      frag.appendChild(document.createTextNode(text.slice(cursor, idx)));
    }
    const mark = document.createElement('mark');
    mark.className = 'search-highlight';
    mark.textContent = text.slice(idx, idx + term.length);
    frag.appendChild(mark);
    cursor = idx + term.length;
    idx = lower.indexOf(tLower, cursor);
  }

  if (cursor < text.length) {
    frag.appendChild(document.createTextNode(text.slice(cursor)));
  }

  const parent = textNode.parentNode;
  if (parent) {
    parent.replaceChild(frag, textNode);
  }
}

/**
 * Walk all text nodes under `root` and highlight occurrences of `term`.
 *
 * Collects text nodes first (via a TreeWalker snapshot) to avoid
 * mutating the tree while iterating.
 *
 * @param root - The subtree root to search within.
 * @param term - The search term to highlight.
 */
function walkTextNodes(root: HTMLElement, term: string): void {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let current = walker.nextNode();
  while (current) {
    nodes.push(current as Text);
    current = walker.nextNode();
  }
  for (const n of nodes) {
    highlightTextNode(n, term);
  }
}

/**
 * Remove all `<mark class="search-highlight">` elements inside a container,
 * replacing each with its original text content and normalizing adjacent
 * text nodes.
 *
 * @param container - The element to clear highlights within.
 */
function clearHighlights(container: HTMLElement): void {
  for (const mark of container.querySelectorAll('mark.search-highlight')) {
    const parent = mark.parentNode;
    if (!parent) continue;
    parent.replaceChild(document.createTextNode(mark.textContent ?? ''), mark);
    parent.normalize();
  }
}

/**
 * Show or hide list items based on whether they match the search term.
 *
 * Clears existing highlights, then hides items whose text content does
 * not include `term` (case-insensitive). Matching items receive inline
 * `<mark>` highlights around each occurrence.
 *
 * @param listEl - The list container element.
 * @param itemSelector - CSS selector for individual list items.
 * @param term - The search term to match against item text.
 */
function filterList(listEl: HTMLElement, itemSelector: string, term: string): void {
  clearHighlights(listEl);

  const items = listEl.querySelectorAll<HTMLElement>(itemSelector);
  if (!term) {
    for (const item of items) {
      item.style.display = '';
    }
    return;
  }

  const lowerTerm = term.toLowerCase();
  for (const item of items) {
    const text = (item.textContent ?? '').toLowerCase();
    const match = text.includes(lowerTerm);
    item.style.display = match ? '' : 'none';
    if (match) {
      walkTextNodes(item, term);
    }
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Manages client-side search/filter inputs for list panels.
 *
 * On initialization, injects a text input into each configured panel.
 * User keystrokes are debounced and applied as a case-insensitive text
 * filter that hides non-matching items and highlights matches. A
 * MutationObserver re-applies the filter when list content changes.
 */
export class SearchManager {
  /** The panel configurations this manager operates on. */
  private readonly targets: readonly SearchTarget[];

  /**
   * Create a new SearchManager.
   *
   * @param targets - Panel configurations to set up search inputs for.
   *   Defaults to the built-in {@link SEARCH_TARGETS} list.
   */
  constructor(targets: readonly SearchTarget[] = SEARCH_TARGETS) {
    this.targets = targets;
  }

  /**
   * Inject filter inputs and wire up event listeners for each target panel.
   *
   * Skips panels whose DOM elements are missing or that already contain
   * a filter input. Attaches a MutationObserver to each list so that
   * the filter is re-applied when list content changes dynamically.
   */
  public init(): void {
    for (const target of this.targets) {
      this.setupTarget(target);
    }
  }

  /**
   * Set up a single search target by creating its input and observers.
   *
   * @param target - The search target configuration to initialize.
   */
  private setupTarget(target: SearchTarget): void {
    const panel = document.getElementById(target.panelId);
    const listEl = document.getElementById(target.listId);
    if (!panel || !listEl) return;

    if (panel.querySelector('.search-filter-input')) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'search-filter-wrap';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'search-filter-input';
    input.placeholder = target.placeholder;
    input.setAttribute('aria-label', target.placeholder);
    wrapper.appendChild(input);

    const debouncedFilter = debounce(() => {
      const list = document.getElementById(target.listId);
      if (list) {
        filterList(list, target.itemSelector, input.value.trim());
      }
    }, DEBOUNCE_MS);

    input.addEventListener('input', debouncedFilter);

    const observer = new MutationObserver(() => {
      const list = document.getElementById(target.listId);
      if (list && input.value.trim()) {
        filterList(list, target.itemSelector, input.value.trim());
      }
    });
    observer.observe(listEl, { childList: true, subtree: true });

    const cardParent = listEl.closest('.card');
    if (cardParent) {
      const header = cardParent.querySelector('.card-header');
      if (header) {
        header.after(wrapper);
      } else {
        cardParent.insertBefore(wrapper, listEl);
      }
    } else {
      listEl.parentNode?.insertBefore(wrapper, listEl);
    }
  }
}

/** Default singleton used by the convenience export. */
const defaultManager = new SearchManager();

/**
 * Initialize search/filter inputs in all configured list panels.
 *
 * Convenience wrapper that preserves the original module's public API
 * by delegating to the default {@link SearchManager} instance.
 */
export function initSearch(): void {
  defaultManager.init();
}
