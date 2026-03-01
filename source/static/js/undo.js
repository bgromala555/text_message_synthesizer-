/* undo.js -- Snapshot-based undo/redo system with ring buffer */

import { scenario, setScenario, syncScenario, registerSnapshotCallback } from './state.js';
import { renderDevices } from './devices.js';
import { renderContacts } from './contacts.js';
import { renderPersonalities } from './personalities.js';
import { renderStoryArc } from './storyarc.js';
import { renderEvents, renderGroupChats } from './events.js';
import { renderGenerate } from './generate.js';
import { renderLinkChart } from './linkchart.js';

// ---------------------------------------------------------------------------
// Ring buffer
// ---------------------------------------------------------------------------

const MAX_SNAPSHOTS = 50;
const snapshots = [];
let cursor = -1;

function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Capture the current scenario state. Call after every mutation.
 * Discards any redo history beyond the current cursor.
 */
export function pushSnapshot() {
    const snap = deepClone(scenario);

    cursor += 1;

    if (cursor < snapshots.length) {
        snapshots.length = cursor;
    }

    snapshots.push(snap);

    if (snapshots.length > MAX_SNAPSHOTS) {
        snapshots.shift();
        cursor = snapshots.length - 1;
    }

    updateButtonStates();
}

/**
 * Restore the previous snapshot (undo).
 */
export function undo() {
    if (cursor <= 0) return;
    cursor -= 1;
    restore(snapshots[cursor]);
}

/**
 * Restore the next snapshot (redo).
 */
export function redo() {
    if (cursor >= snapshots.length - 1) return;
    cursor += 1;
    restore(snapshots[cursor]);
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function restore(snap) {
    setScenario(deepClone(snap));
    reRenderAll();
    syncScenario();
    updateButtonStates();
}

function reRenderAll() {
    renderDevices();
    renderContacts();
    renderPersonalities();
    renderStoryArc();
    renderEvents();
    renderGroupChats();
    renderGenerate();
    try { renderLinkChart(); } catch (_) { /* chart may not be visible */ }
}

function updateButtonStates() {
    const undoBtn = document.getElementById('undo-btn');
    const redoBtn = document.getElementById('redo-btn');
    if (undoBtn) undoBtn.disabled = cursor <= 0;
    if (redoBtn) redoBtn.disabled = cursor >= snapshots.length - 1;
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts & init
// ---------------------------------------------------------------------------

export function initUndo() {
    pushSnapshot();
    registerSnapshotCallback(pushSnapshot);

    document.addEventListener('keydown', (e) => {
        const tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

        if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z') {
            e.preventDefault();
            undo();
        }

        if (
            ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') ||
            ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'z')
        ) {
            e.preventDefault();
            redo();
        }
    });

    const undoBtn = document.getElementById('undo-btn');
    const redoBtn = document.getElementById('redo-btn');
    if (undoBtn) undoBtn.addEventListener('click', undo);
    if (redoBtn) redoBtn.addEventListener('click', redo);

    updateButtonStates();
}
