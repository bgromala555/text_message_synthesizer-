/* modals.js -- Modal open/close management with focus trapping */

const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

let _previousFocus = null;
let _activeModalId = null;
let _trapHandler = null;

function getFocusableElements(modal) {
    return Array.from(modal.querySelectorAll(FOCUSABLE)).filter(
        el => !el.disabled && el.offsetParent !== null
    );
}

function trapFocus(e) {
    if (e.key !== 'Tab' || !_activeModalId) return;
    const modal = document.getElementById(_activeModalId);
    if (!modal) return;

    const focusable = getFocusableElements(modal);
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

export function openModal(id) {
    const el = document.getElementById(id);
    if (!el) return;

    _previousFocus = document.activeElement;
    _activeModalId = id;
    el.classList.remove('hidden');

    const focusable = getFocusableElements(el);
    if (focusable.length > 0) {
        requestAnimationFrame(() => focusable[0].focus());
    }

    if (_trapHandler) document.removeEventListener('keydown', _trapHandler);
    _trapHandler = trapFocus;
    document.addEventListener('keydown', _trapHandler);
}

export function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('hidden');

    if (_activeModalId === id) {
        _activeModalId = null;
        if (_trapHandler) {
            document.removeEventListener('keydown', _trapHandler);
            _trapHandler = null;
        }
        if (_previousFocus && typeof _previousFocus.focus === 'function') {
            _previousFocus.focus();
        }
        _previousFocus = null;
    }
}
