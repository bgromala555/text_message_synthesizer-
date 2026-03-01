/* search.js -- Client-side search/filter for contact, event, and device lists */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const DEBOUNCE_MS = 300;

const SEARCH_TARGETS = [
    { listId: 'devices-list', panelId: 'panel-devices', placeholder: 'Filter devices...', itemSelector: '.device-card' },
    { listId: 'contacts-list', panelId: 'panel-contacts', placeholder: 'Filter contacts...', itemSelector: '.card.device-card, .card' },
    { listId: 'events-list', panelId: 'panel-eventslinks', placeholder: 'Filter events...', itemSelector: '.card.event-card, .card' },
    { listId: 'personalities-list', panelId: 'panel-personalities', placeholder: 'Filter personalities...', itemSelector: '.card.personality-card, .card' },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function debounce(fn, ms) {
    let timer;
    return function (...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), ms);
    };
}

/**
 * Wrap every occurrence of `term` inside `textNode` with a <mark> highlight.
 * Operates on a single text node; splits it into fragments.
 */
function highlightTextNode(textNode, term) {
    const text = textNode.nodeValue;
    const lower = text.toLowerCase();
    const tLower = term.toLowerCase();
    let idx = lower.indexOf(tLower);
    if (idx === -1) return;

    const frag = document.createDocumentFragment();
    let cursor = 0;
    while (idx !== -1) {
        if (idx > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, idx)));
        const mark = document.createElement('mark');
        mark.className = 'search-highlight';
        mark.textContent = text.slice(idx, idx + term.length);
        frag.appendChild(mark);
        cursor = idx + term.length;
        idx = lower.indexOf(tLower, cursor);
    }
    if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)));
    textNode.parentNode.replaceChild(frag, textNode);
}

function walkTextNodes(root, term) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const n of nodes) highlightTextNode(n, term);
}

function clearHighlights(container) {
    for (const mark of container.querySelectorAll('mark.search-highlight')) {
        const parent = mark.parentNode;
        parent.replaceChild(document.createTextNode(mark.textContent), mark);
        parent.normalize();
    }
}

// ---------------------------------------------------------------------------
// Core filter logic - hides non-matching DOM elements
// ---------------------------------------------------------------------------

function filterList(listEl, itemSelector, term) {
    if (!listEl) return;
    clearHighlights(listEl);

    const items = listEl.querySelectorAll(itemSelector);
    if (!term) {
        for (const item of items) item.style.display = '';
        return;
    }

    const lowerTerm = term.toLowerCase();
    for (const item of items) {
        const text = item.textContent.toLowerCase();
        const match = text.includes(lowerTerm);
        item.style.display = match ? '' : 'none';
        if (match) walkTextNodes(item, term);
    }
}

// ---------------------------------------------------------------------------
// Public init
// ---------------------------------------------------------------------------

export function initSearch() {
    for (const target of SEARCH_TARGETS) {
        const panel = document.getElementById(target.panelId);
        const listEl = document.getElementById(target.listId);
        if (!panel || !listEl) continue;

        const existing = panel.querySelector('.search-filter-input');
        if (existing) continue;

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
            if (list) filterList(list, target.itemSelector, input.value.trim());
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
            listEl.parentNode.insertBefore(wrapper, listEl);
        }
    }
}
