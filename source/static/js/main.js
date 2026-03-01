/* main.js -- Entry point: imports all modules, initializes on DOMContentLoaded */

// Core
import { scenario, setScenario, syncScenario, esc, uid, registerExistingPhoneNumbers, syncThemeDropdown, _usedPhoneNumbers,
         updateTheme, updateCulture, updateLanguage, THEME_LABELS } from './state.js';
import { api } from './api.js';
import { showToast } from './toast.js';
import { initTabs } from './tabs.js';
import { initKeyboard } from './keyboard.js';
import { initFileBrowser, openFileBrowser, closeFileBrowser, loadScenarioFromBrowser } from './filebrowser.js';

// New feature modules
import { initSearch } from './search.js';
import { initUndo, pushSnapshot, undo, redo } from './undo.js';
import { initMobileNav } from './mobile.js';

// Feature modules
import { addDevice, removeDevice, updateDeviceField, setDeviceGenerationMode,
         setContactCount, renderDevices, aiGenerateOwnerName, aiGenerateAllOwnerNames } from './devices.js';
import { renderContacts, addContactToDevice, removeContact, updateContact,
         setMutualCount, addSharedContactManual, unlinkShared, countSharedBetween,
         aiGenerateNamesForDevice, aiGenerateAllNames } from './contacts.js';
import { renderPersonalities, togglePersonality, setPersonalityField,
         setTextingStyleField, addTag, removeTag,
         aiGeneratePersonality, aiGenerateAllPersonalities } from './personalities.js';
import { renderStoryArc, updateContactStoryArc,
         aiGenerateStoryArc, closeStoryArcModal, applyArcPreset,
         confirmAiGenerateStoryArc, aiGenerateCharacterArcs } from './storyarc.js';
import { renderEvents, renderGroupChats, toggleEventParticipant, scrollToEvent,
         addEvent, removeEvent, updateEventField, updateEventDeviceImpact,
         aiSuggestEvents, closeEventsModal, confirmAiSuggestEvents, aiSuggestEventDetails,
         toggleGroupMember, addGroupChat, removeGroupChat, updateGroupChat,
         aiSuggestGroupChats } from './events.js';
import { renderGenerate, updateSettings, runGeneration, runQualityCheck,
         checkGenerationProgress } from './generate.js';
import { renderLinkChart, hideNodeContextMenu,
         contextToggleParticipant, contextAddEventWithPerson,
         startConnectFromNode, showNodeInspector, hideNodeInspector,
         extractLocations } from './linkchart.js';

// -----------------------------------------------------------------------
// API Key management
// -----------------------------------------------------------------------

async function checkApiKey() {
    try {
        const res = await api('GET', '/api/apikey/status');
        updateApiKeyBadge(res.available, res.masked);
    } catch (e) {
        updateApiKeyBadge(false, '');
    }
}

function updateApiKeyBadge(available, masked) {
    const badge = document.getElementById('apikey-status');
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

function toggleApiKeyInput() {
    const wrap = document.getElementById('apikey-input-wrap');
    wrap.classList.toggle('hidden');
    if (!wrap.classList.contains('hidden')) {
        document.getElementById('apikey-input').focus();
    }
}

async function submitApiKey() {
    const input = document.getElementById('apikey-input');
    const key = input.value.trim();
    if (!key) { showToast('Enter an API key'); return; }
    try {
        await api('POST', '/api/apikey/set', { key });
        input.value = '';
        document.getElementById('apikey-input-wrap').classList.add('hidden');
        await checkApiKey();
        showToast('API key saved');
    } catch (e) {
        showToast('Error saving API key');
    }
}

// -----------------------------------------------------------------------
// Scenario operations
// -----------------------------------------------------------------------

async function loadScenarioFromServer() {
    const data = await api('GET', '/api/scenario');
    if (data && data.id) setScenario(data);
    registerExistingPhoneNumbers();
    syncThemeDropdown();
    renderDevices();
}

async function saveScenario() {
    await syncScenario();
    const res = await api('POST', '/api/scenario/save');
    if (res.status === 'saved') showToast('Scenario saved');
}

function loadScenarioPrompt() {
    const id = prompt('Enter scenario ID to load:');
    if (id) {
        api('POST', '/api/scenario/load/' + id).then(data => {
            if (data && !data.error) {
                setScenario(data);
                registerExistingPhoneNumbers();
                syncThemeDropdown();
                renderDevices();
                showToast('Scenario loaded');
            }
        }).catch(() => {});
    }
}

async function newScenario() {
    if (!confirm('Start a brand new scenario? Any unsaved changes will be lost.')) return;
    try {
        const data = await api('POST', '/api/scenario/new');
        if (data && data.scenario) {
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
    } catch (e) { /* toast shown by api() */ }
}

// -----------------------------------------------------------------------
// Init
// -----------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initKeyboard();
    initFileBrowser();
    initSearch();
    initUndo();
    initMobileNav();
    checkApiKey();
    loadScenarioFromServer();
    document.addEventListener('click', function(e) {
        const menu = document.getElementById('node-context-menu');
        if (menu && !menu.contains(e.target)) hideNodeContextMenu();
    });
});

// -----------------------------------------------------------------------
// Expose functions to window for inline event handlers in HTML
// -----------------------------------------------------------------------

// Scenario & API key
window.scenario = scenario;
window.toggleApiKeyInput = toggleApiKeyInput;
window.submitApiKey = submitApiKey;
window.newScenario = newScenario;
window.saveScenario = saveScenario;
window.loadScenarioPrompt = loadScenarioPrompt;
window.syncScenario = syncScenario;

// Theme / culture / language
window.updateTheme = updateTheme;
window.updateCulture = updateCulture;
window.updateLanguage = updateLanguage;

// Devices
window.addDevice = addDevice;
window.removeDevice = removeDevice;
window.updateDeviceField = updateDeviceField;
window.setDeviceGenerationMode = setDeviceGenerationMode;
window.setContactCount = setContactCount;
window.aiGenerateOwnerName = aiGenerateOwnerName;

// Contacts
window.addContactToDevice = addContactToDevice;
window.removeContact = removeContact;
window.updateContact = updateContact;
window.setMutualCount = setMutualCount;
window.addSharedContactManual = addSharedContactManual;
window.unlinkShared = unlinkShared;
window.aiGenerateNamesForDevice = aiGenerateNamesForDevice;
window.aiGenerateAllNames = aiGenerateAllNames;

// Personalities
window.togglePersonality = togglePersonality;
window.setPersonalityField = setPersonalityField;
window.setTextingStyleField = setTextingStyleField;
window.addTag = addTag;
window.removeTag = removeTag;
window.aiGeneratePersonality = aiGeneratePersonality;
window.aiGenerateAllPersonalities = aiGenerateAllPersonalities;

// Story arc
window.aiGenerateStoryArc = aiGenerateStoryArc;
window.closeStoryArcModal = closeStoryArcModal;
window.applyArcPreset = applyArcPreset;
window.confirmAiGenerateStoryArc = confirmAiGenerateStoryArc;
window.aiGenerateCharacterArcs = aiGenerateCharacterArcs;
window.updateContactStoryArc = updateContactStoryArc;

// Events & group chats
window.toggleEventParticipant = toggleEventParticipant;
window.scrollToEvent = scrollToEvent;
window.addEvent = addEvent;
window.removeEvent = removeEvent;
window.updateEventField = updateEventField;
window.updateEventDeviceImpact = updateEventDeviceImpact;
window.aiSuggestEvents = aiSuggestEvents;
window.closeEventsModal = closeEventsModal;
window.confirmAiSuggestEvents = confirmAiSuggestEvents;
window.aiSuggestEventDetails = aiSuggestEventDetails;
window.toggleGroupMember = toggleGroupMember;
window.addGroupChat = addGroupChat;
window.removeGroupChat = removeGroupChat;
window.updateGroupChat = updateGroupChat;
window.aiSuggestGroupChats = aiSuggestGroupChats;

// Generate
window.updateSettings = updateSettings;
window.runGeneration = runGeneration;
window.runQualityCheck = runQualityCheck;

// Undo / Redo
window.undo = undo;
window.redo = redo;
window.pushSnapshot = pushSnapshot;

// Link chart context menu
window.contextToggleParticipant = contextToggleParticipant;
window.contextAddEventWithPerson = contextAddEventWithPerson;
window.startConnectFromNode = startConnectFromNode;

// File browser
window.openFileBrowser = openFileBrowser;
window.closeFileBrowser = closeFileBrowser;
window.loadScenarioFromBrowser = loadScenarioFromBrowser;
