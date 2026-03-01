/**
 * Snapshot-based undo/redo system with a ring buffer.
 *
 * Captures deep clones of the scenario state after each mutation and stores
 * them in a fixed-size ring buffer. Undo/redo move a cursor through the
 * buffer, restoring previous states and re-rendering all panels.
 * Keyboard shortcuts (Ctrl+Z / Ctrl+Y) are bound on initialization.
 * @module
 */

import type { Scenario } from '../shared/types.js';

import { registerSnapshotCallback, scenario, setScenario, syncScenario } from '../core/AppState.js';

import { renderContacts } from './ContactManager.js';
import { renderDevices } from './DeviceManager.js';
import { renderEvents, renderGroupChats } from './EventManager.js';
import { renderGenerate } from './GenerateManager.js';
import { renderLinkChart } from './LinkChartManager.js';
import { renderPersonalities } from './PersonalityManager.js';
import { renderStoryArc } from './StoryArcManager.js';

/** Maximum number of snapshots retained in the ring buffer. */
const MAX_SNAPSHOTS = 50;

/**
 * Create a deep clone of a plain JSON-serializable object.
 *
 * Uses JSON round-trip which is safe for scenario data that contains
 * only primitives, arrays, and plain objects (no functions, Dates, etc.).
 *
 * @param obj - The object to clone.
 * @returns A new deeply-cloned copy of the object.
 */
function deepClone(obj: Scenario): Scenario {
  return JSON.parse(JSON.stringify(obj)) as Scenario;
}

/**
 * Manages undo/redo state using JSON snapshots stored in a ring buffer.
 *
 * Each mutation triggers a snapshot via {@link pushSnapshot}. The cursor
 * tracks the current position in the buffer. Undo decrements the cursor;
 * redo increments it. The buffer automatically discards the oldest snapshot
 * when the maximum capacity is reached.
 */
export class UndoManager {
  /** Ring buffer of scenario snapshots. */
  private readonly snapshots: Scenario[] = [];

  /** Current position within the snapshot buffer. */
  private cursor = -1;

  /**
   * Capture the current scenario state into the snapshot buffer.
   *
   * Discards any redo history beyond the current cursor position before
   * appending. When the buffer exceeds {@link MAX_SNAPSHOTS}, the oldest
   * entry is shifted off and the cursor is adjusted.
   */
  public pushSnapshot(): void {
    const snap = deepClone(scenario);
    this.cursor += 1;

    if (this.cursor < this.snapshots.length) {
      this.snapshots.length = this.cursor;
    }

    this.snapshots.push(snap);

    if (this.snapshots.length > MAX_SNAPSHOTS) {
      this.snapshots.shift();
      this.cursor = this.snapshots.length - 1;
    }

    this.updateButtonStates();
  }

  /**
   * Restore the previous snapshot (undo one step).
   *
   * Does nothing if the cursor is already at the beginning of the buffer.
   */
  public undo(): void {
    if (this.cursor <= 0) return;
    this.cursor -= 1;
    const snap = this.snapshots[this.cursor];
    if (snap) this.restore(snap);
  }

  /**
   * Restore the next snapshot (redo one step).
   *
   * Does nothing if the cursor is already at the end of the buffer.
   */
  public redo(): void {
    if (this.cursor >= this.snapshots.length - 1) return;
    this.cursor += 1;
    const snap = this.snapshots[this.cursor];
    if (snap) this.restore(snap);
  }

  /**
   * Apply a snapshot to the global scenario state.
   *
   * Sets the scenario to a deep clone of the snapshot, re-renders all
   * panels, syncs to the server, and updates the undo/redo button states.
   *
   * @param snap - The scenario snapshot to restore.
   */
  private restore(snap: Scenario): void {
    setScenario(deepClone(snap));
    this.reRenderAll();
    void syncScenario();
    this.updateButtonStates();
  }

  /**
   * Re-render every feature panel after a state restoration.
   *
   * The link chart render is wrapped in a try/catch because the chart
   * canvas may not be visible when the events/links tab is inactive.
   */
  private reRenderAll(): void {
    renderDevices();
    renderContacts();
    renderPersonalities();
    renderStoryArc();
    renderEvents();
    renderGroupChats();
    renderGenerate();
    try {
      renderLinkChart();
    } catch {
      /* chart may not be visible */
    }
  }

  /**
   * Enable or disable the undo/redo toolbar buttons based on cursor position.
   */
  private updateButtonStates(): void {
    const undoBtn = document.getElementById('undo-btn') as HTMLButtonElement | null;
    const redoBtn = document.getElementById('redo-btn') as HTMLButtonElement | null;
    if (undoBtn) undoBtn.disabled = this.cursor <= 0;
    if (redoBtn) redoBtn.disabled = this.cursor >= this.snapshots.length - 1;
  }

  /**
   * Bind keyboard shortcuts and toolbar button click handlers.
   *
   * Ctrl+Z (or Cmd+Z) triggers undo. Ctrl+Y (or Cmd+Y) and
   * Ctrl+Shift+Z (or Cmd+Shift+Z) trigger redo. Shortcuts are
   * suppressed when focus is inside an input, textarea, or select.
   */
  private bindKeyboardShortcuts(): void {
    document.addEventListener('keydown', (e: KeyboardEvent) => {
      const tag = ((e.target as HTMLElement | null)?.tagName ?? '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        this.undo();
      }

      if (
        ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') ||
        ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === 'z')
      ) {
        e.preventDefault();
        this.redo();
      }
    });
  }

  /**
   * Initialize the undo system.
   *
   * Takes an initial snapshot, registers the snapshot callback for
   * automatic capture on sync, binds keyboard shortcuts, and wires
   * up the toolbar undo/redo buttons.
   */
  public init(): void {
    this.pushSnapshot();
    registerSnapshotCallback(() => {
      this.pushSnapshot();
    });

    this.bindKeyboardShortcuts();

    const undoBtn = document.getElementById('undo-btn');
    const redoBtn = document.getElementById('redo-btn');
    if (undoBtn)
      undoBtn.addEventListener('click', () => {
        this.undo();
      });
    if (redoBtn)
      redoBtn.addEventListener('click', () => {
        this.redo();
      });

    this.updateButtonStates();
  }
}

// ---------------------------------------------------------------------------
// Singleton + convenience exports
// ---------------------------------------------------------------------------

/** Default singleton used by the convenience exports. */
const defaultManager = new UndoManager();

/**
 * Capture the current scenario state into the snapshot buffer.
 * Delegates to the default {@link UndoManager} singleton.
 */
export function pushSnapshot(): void {
  defaultManager.pushSnapshot();
}

/**
 * Restore the previous snapshot (undo).
 * Delegates to the default {@link UndoManager} singleton.
 */
export function undo(): void {
  defaultManager.undo();
}

/**
 * Restore the next snapshot (redo).
 * Delegates to the default {@link UndoManager} singleton.
 */
export function redo(): void {
  defaultManager.redo();
}

/**
 * Initialize the undo system using the default manager instance.
 * Delegates to the default {@link UndoManager} singleton.
 */
export function initUndo(): void {
  defaultManager.init();
}
