/**
 * Application entry point — imports all feature managers, initializes them
 * on DOMContentLoaded, and wires DOM event handlers via `addEventListener`.
 * @module
 */

import type { Culture, Language, Scenario, Theme } from './shared/types.js';

import { api, configureToast } from './core/ApiClient.js';
import {
  _usedPhoneNumbers,
  registerExistingPhoneNumbers,
  scenario,
  setScenario,
  syncScenario,
  syncThemeDropdown,
  updateCulture,
  updateLanguage,
  updateTheme,
} from './core/AppState.js';
import { showToast } from './core/ToastService.js';

import {
  addContactToDevice,
  addSharedContactManual,
  aiGenerateAllNames,
  aiGenerateNamesForDevice,
  removeContact,
  renderContacts,
  setMutualCount,
  unlinkShared,
  updateContact,
} from './features/ContactManager.js';
import {
  addDevice,
  aiGenerateOwnerName,
  removeDevice,
  renderDevices,
  setContactCount,
  setDeviceGenerationMode,
  updateDeviceField,
} from './features/DeviceManager.js';
import {
  addEvent,
  addGroupChat,
  aiSuggestEventDetails,
  aiSuggestEvents,
  aiSuggestGroupChats,
  closeEventsModal,
  confirmAiSuggestEvents,
  removeEvent,
  removeGroupChat,
  renderEvents,
  scrollToEvent,
  toggleEventParticipant,
  toggleGroupMember,
  updateEventDeviceImpact,
  updateEventField,
  updateGroupChat,
} from './features/EventManager.js';
import {
  closeFileBrowser,
  initFileBrowser,
  loadScenarioFromBrowser,
  openFileBrowser,
} from './features/FileBrowserManager.js';
import { renderGenerate, runGeneration, runQualityCheck, updateSettings } from './features/GenerateManager.js';
import { initKeyboard } from './features/KeyboardManager.js';
import {
  contextAddEventWithPerson,
  contextToggleParticipant,
  hideNodeContextMenu,
  startConnectFromNode,
} from './features/LinkChartManager.js';
import { initMobileNav } from './features/MobileNavManager.js';
import {
  addTag,
  aiGenerateAllPersonalities,
  aiGeneratePersonality,
  removeTag,
  renderPersonalities,
  setPersonalityField,
  setTextingStyleField,
  togglePersonality,
} from './features/PersonalityManager.js';
import { initSearch } from './features/SearchManager.js';
import {
  aiGenerateCharacterArcs,
  aiGenerateStoryArc,
  applyArcPreset,
  closeStoryArcModal,
  confirmAiGenerateStoryArc,
  renderStoryArc,
  updateContactStoryArc,
} from './features/StoryArcManager.js';
import { initTabs } from './features/TabManager.js';
import { initUndo } from './features/UndoManager.js';

// ---------------------------------------------------------------------------
// Local response types for API endpoints used by entry-point functions
// ---------------------------------------------------------------------------

/** Response from `GET /api/apikey/status`. */
interface ApiKeyStatusResponse {
  available: boolean;
  masked: string;
}

/** Response from `POST /api/scenario/save`. */
interface ScenarioSaveResponse {
  status: string;
}

/** Response from `POST /api/scenario/new`. */
interface NewScenarioResponse {
  scenario: Scenario;
}

// ---------------------------------------------------------------------------
// API Key management
// ---------------------------------------------------------------------------

/**
 * Update the API key status badge in the toolbar.
 *
 * Toggles between a green "key set" badge and an orange "no key" badge
 * based on whether the server reports an available API key.
 *
 * @param available - Whether an API key is currently configured.
 * @param masked - A masked representation of the key (e.g. "sk-...abc").
 */
function updateApiKeyBadge(available: boolean, masked: string): void {
  const badge = document.getElementById('apikey-status');
  if (!badge) return;

  if (available) {
    badge.className = 'badge badge-green';
    badge.textContent = masked || 'Key Set';
    badge.title = 'API key is configured. Click to change.';
  } else {
    badge.className = 'badge badge-orange';
    badge.textContent = 'No API Key';
    badge.title = 'Click to set your OpenAI API key';
  }
}

/**
 * Fetch the current API key status from the server and update the badge.
 *
 * On network or server errors, the badge falls back to the "no key" state.
 */
async function checkApiKey(): Promise<void> {
  try {
    const res = await api<ApiKeyStatusResponse>('GET', '/api/apikey/status');
    updateApiKeyBadge(res.available, res.masked);
  } catch {
    updateApiKeyBadge(false, '');
  }
}

/**
 * Toggle visibility of the API key input field.
 *
 * When the input becomes visible, it receives keyboard focus automatically.
 */
function toggleApiKeyInput(): void {
  const wrap = document.getElementById('apikey-input-wrap');
  if (!wrap) return;

  wrap.classList.toggle('hidden');
  if (!wrap.classList.contains('hidden')) {
    const input = document.getElementById('apikey-input') as HTMLInputElement | null;
    input?.focus();
  }
}

/**
 * Submit the API key entered in the input field to the server.
 *
 * Validates that the input is non-empty, sends it to the server,
 * clears the input, hides the input wrapper, refreshes the badge,
 * and shows a confirmation toast.
 */
async function submitApiKey(): Promise<void> {
  const input = document.getElementById('apikey-input') as HTMLInputElement | null;
  if (!input) return;

  const key = input.value.trim();
  if (!key) {
    showToast('Enter an API key');
    return;
  }

  try {
    await api('POST', '/api/apikey/set', { key });
    input.value = '';
    document.getElementById('apikey-input-wrap')?.classList.add('hidden');
    await checkApiKey();
    showToast('API key saved');
  } catch {
    showToast('Error saving API key');
  }
}

// ---------------------------------------------------------------------------
// Scenario operations
// ---------------------------------------------------------------------------

/**
 * Load the current scenario from the server and render the device list.
 *
 * Called during application bootstrap to populate the initial state.
 * Registers existing phone numbers and synchronises dropdown selections
 * after the scenario data is applied.
 */
async function loadScenarioFromServer(): Promise<void> {
  const data = await api<Scenario>('GET', '/api/scenario');
  if (data && data.id) setScenario(data);
  registerExistingPhoneNumbers();
  syncThemeDropdown();
  renderDevices();
}

/**
 * Persist the current scenario to the server and show a confirmation toast.
 *
 * Triggers a sync before saving to ensure the latest in-memory state
 * is written to the server.
 */
async function saveScenario(): Promise<void> {
  await syncScenario();
  const res = await api<ScenarioSaveResponse>('POST', '/api/scenario/save');
  if (res.status === 'saved') showToast('Scenario saved');
}

/**
 * Create a brand-new scenario after user confirmation.
 *
 * Prompts the user with a confirmation dialog, then creates a new
 * scenario on the server, clears local state, and re-renders all panels.
 */
async function newScenario(): Promise<void> {
  if (!confirm('Start a brand new scenario? Any unsaved changes will be lost.')) return;

  try {
    const data = await api<NewScenarioResponse>('POST', '/api/scenario/new');
    if (data.scenario) {
      setScenario(data.scenario);
      _usedPhoneNumbers.clear();
      syncThemeDropdown();
      renderDevices();
      renderContacts();
      renderPersonalities();
      renderStoryArc();
      renderEvents();
      renderGenerate();
      showToast('New scenario created');
    }
  } catch {
    /* toast shown by api() */
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  configureToast(showToast);
  initTabs();
  initKeyboard();
  initSearch();
  initMobileNav();
  initUndo();
  initFileBrowser();
  bindEventHandlers();
  void checkApiKey();
  void loadScenarioFromServer();

  // Close the link-chart context menu when clicking outside it
  document.addEventListener('click', (e: MouseEvent) => {
    const menu = document.getElementById('node-context-menu');
    if (menu && !menu.contains(e.target as Node)) hideNodeContextMenu();
  });
});

// ---------------------------------------------------------------------------
// DOM event binding — replaces all former inline onclick/onchange/oninput
// handlers with addEventListener calls.
// ---------------------------------------------------------------------------

/**
 * Helper that queries a DOM element by ID and attaches an event listener.
 * Silently skips when the element is not found (the panel may not be
 * rendered yet for dynamically-created elements).
 *
 * @param id - The element's `id` attribute.
 * @param event - The DOM event name (e.g. "click", "change").
 * @param handler - The callback to invoke when the event fires.
 */
function on<K extends keyof HTMLElementEventMap>(
  id: string,
  event: K,
  handler: (e: HTMLElementEventMap[K]) => void
): void {
  document.getElementById(id)?.addEventListener(event, handler);
}

/**
 * Wire every static DOM element in `index.html` to its corresponding
 * feature-manager function via `addEventListener`.
 *
 * Called once during bootstrap, after the DOM is fully parsed.
 * Dynamically-rendered elements (inside device/contact/event cards)
 * continue to use delegated handlers set up by their respective managers.
 */
function bindEventHandlers(): void {
  // -- Header: API key -------------------------------------------------------
  on('apikey-status', 'click', () => toggleApiKeyInput());
  on('btn-submit-apikey', 'click', () => void submitApiKey());

  // -- Header: scenario actions ----------------------------------------------
  on('btn-new-scenario', 'click', () => void newScenario());
  on('btn-save-scenario', 'click', () => void saveScenario());
  on('btn-load-scenario', 'click', () => openFileBrowser());

  // -- Scenario setup: theme / culture / language ----------------------------
  on('theme-select', 'change', (e) => updateTheme((e.target as HTMLSelectElement).value as Theme));
  on('culture-select', 'change', (e) => updateCulture((e.target as HTMLSelectElement).value as Culture));
  on('scenario-language', 'change', (e) => updateLanguage((e.target as HTMLSelectElement).value as Language));

  // -- Devices ---------------------------------------------------------------
  on('btn-add-device', 'click', () => addDevice());

  // -- Contacts --------------------------------------------------------------
  on('btn-ai-generate-all-names', 'click', () => void aiGenerateAllNames());

  // -- Personalities ---------------------------------------------------------
  on('btn-ai-generate-all-personalities', 'click', () => void aiGenerateAllPersonalities());

  // -- Story arc -------------------------------------------------------------
  on('btn-ai-generate-story-arc', 'click', () => aiGenerateStoryArc());
  on('story-arc-global', 'input', (e) => {
    scenario.story_arc = (e.target as HTMLTextAreaElement).value;
    void syncScenario();
  });
  on('btn-ai-generate-character-arcs', 'click', () => void aiGenerateCharacterArcs());

  // -- AI Events modal -------------------------------------------------------
  on('ai-events-modal', 'click', (e) => {
    if (e.target === document.getElementById('ai-events-modal')) closeEventsModal();
  });
  on('btn-close-events-modal', 'click', () => closeEventsModal());
  on('btn-cancel-events-modal', 'click', () => closeEventsModal());
  on('btn-confirm-ai-suggest-events', 'click', () => void confirmAiSuggestEvents());

  // -- Events & group chats --------------------------------------------------
  on('btn-ai-suggest-events', 'click', () => aiSuggestEvents());
  on('btn-add-event', 'click', () => addEvent());
  on('btn-ai-suggest-group-chats', 'click', () => void aiSuggestGroupChats());
  on('btn-add-group-chat', 'click', () => addGroupChat());

  // -- Generate settings -----------------------------------------------------
  on('gen-date-start', 'change', () => updateSettings());
  on('gen-date-end', 'change', () => updateSettings());
  on('gen-msg-min', 'change', () => updateSettings());
  on('gen-msg-max', 'change', () => updateSettings());
  on('gen-batch-size', 'change', () => updateSettings());
  on('gen-provider', 'change', () => updateSettings());
  on('gen-temperature', 'change', () => updateSettings());

  // -- Generate actions ------------------------------------------------------
  on('gen-run-btn', 'click', () => void runGeneration(false));
  on('gen-resume-btn', 'click', () => void runGeneration(true));
  on('gen-quality-fix-btn', 'click', () => void runQualityCheck());

  // -- AI Story-arc modal ----------------------------------------------------
  on('ai-storyarc-modal', 'click', (e) => {
    if (e.target === document.getElementById('ai-storyarc-modal')) closeStoryArcModal();
  });
  on('btn-close-storyarc-modal', 'click', () => closeStoryArcModal());
  on('modal-arc-preset', 'change', (e) => applyArcPreset((e.target as HTMLSelectElement).value));
  on('btn-cancel-storyarc-modal', 'click', () => closeStoryArcModal());
  on('btn-confirm-ai-generate-storyarc', 'click', () => void confirmAiGenerateStoryArc());
}

// ---------------------------------------------------------------------------
// Window type extension for dynamic inline handlers in feature managers.
// Feature managers (DeviceManager, ContactManager, EventManager, etc.)
// generate HTML at runtime with inline onclick/onchange/oninput attributes
// that call these global functions.  These bindings will be removed when
// those managers are migrated to event delegation.
// ---------------------------------------------------------------------------

declare global {
  /**
   * Extends the Window interface so that dynamically-generated inline
   * handlers in feature-manager templates can call global functions.
   */
  // eslint-disable-next-line @typescript-eslint/consistent-type-definitions
  interface Window {
    [key: string]: unknown;
  }
}

// -- DeviceManager -----------------------------------------------------------
window['removeDevice'] = removeDevice;
window['updateDeviceField'] = updateDeviceField;
window['setDeviceGenerationMode'] = setDeviceGenerationMode;
window['setContactCount'] = setContactCount;
window['aiGenerateOwnerName'] = aiGenerateOwnerName;

// -- ContactManager ----------------------------------------------------------
window['addContactToDevice'] = addContactToDevice;
window['removeContact'] = removeContact;
window['updateContact'] = updateContact;
window['setMutualCount'] = setMutualCount;
window['addSharedContactManual'] = addSharedContactManual;
window['unlinkShared'] = unlinkShared;
window['aiGenerateNamesForDevice'] = aiGenerateNamesForDevice;

// -- PersonalityManager ------------------------------------------------------
window['togglePersonality'] = togglePersonality;
window['setPersonalityField'] = setPersonalityField;
window['setTextingStyleField'] = setTextingStyleField;
window['addTag'] = addTag;
window['removeTag'] = removeTag;
window['aiGeneratePersonality'] = aiGeneratePersonality;

// -- StoryArcManager ---------------------------------------------------------
window['updateContactStoryArc'] = updateContactStoryArc;

// -- EventManager ------------------------------------------------------------
window['scrollToEvent'] = scrollToEvent;
window['removeEvent'] = removeEvent;
window['updateEventField'] = updateEventField;
window['updateEventDeviceImpact'] = updateEventDeviceImpact;
window['toggleEventParticipant'] = toggleEventParticipant;
window['aiSuggestEventDetails'] = aiSuggestEventDetails;
window['removeGroupChat'] = removeGroupChat;
window['updateGroupChat'] = updateGroupChat;
window['toggleGroupMember'] = toggleGroupMember;

// -- LinkChartManager --------------------------------------------------------
window['contextToggleParticipant'] = contextToggleParticipant;
window['contextAddEventWithPerson'] = contextAddEventWithPerson;
window['startConnectFromNode'] = startConnectFromNode;

// -- FileBrowserManager ------------------------------------------------------
window['closeFileBrowser'] = closeFileBrowser;
window['loadScenarioFromBrowser'] = loadScenarioFromBrowser;
