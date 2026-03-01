/* keyboard.js -- Keyboard navigation for tabs and modals */

import { closeModal } from './modals.js';

const MODAL_IDS = ['ai-events-modal', 'ai-storyarc-modal', 'file-browser-modal'];

function getTabButtons() {
    return Array.from(document.querySelectorAll('.tab-bar .tab-btn'));
}

function handleTabArrowKeys(e) {
    const tabs = getTabButtons();
    const idx = tabs.indexOf(document.activeElement);
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
        tabs[next].focus();
    }
}

function handleTabActivation(e) {
    if (e.key === 'Enter' || e.key === ' ') {
        const tabs = getTabButtons();
        if (tabs.includes(document.activeElement)) {
            e.preventDefault();
            document.activeElement.click();
        }
    }
}

function handleEscapeKey(e) {
    if (e.key !== 'Escape') return;
    for (const id of MODAL_IDS) {
        const modal = document.getElementById(id);
        if (modal && !modal.classList.contains('hidden')) {
            closeModal(id);
            return;
        }
    }
}

export function initKeyboard() {
    document.querySelectorAll('.tab-bar .tab-btn').forEach(btn => {
        btn.setAttribute('tabindex', '0');
    });

    document.addEventListener('keydown', (e) => {
        handleEscapeKey(e);
        handleTabArrowKeys(e);
        handleTabActivation(e);
    });
}
