/**
 * Link chart visualization manager using vis-network.
 *
 * Renders an interactive graph of devices, contacts, shared contacts,
 * connections, and event co-presence edges. Provides context menus,
 * node inspection, and connect-mode for creating linked events between
 * nodes. Uses the callback registry to invoke EventManager render
 * functions without creating a direct module dependency.
 * @module
 */

import type { Contact, DeviceContactRef, Device, EncounterType, Personality, TimelineEvent } from '../shared/types.js';

import { DEVICE_COLORS, scenario, syncScenario, uid } from '../core/AppState.js';
import { showToast } from '../core/ToastService.js';

import { esc } from '../shared/html-utils.js';
import { getCallback, registerCallback } from '../shared/render-callbacks.js';

// -----------------------------------------------------------------------
// Constants
// -----------------------------------------------------------------------

/** Vis-network node shapes assigned to device owners in creation order. */
const OWNER_SHAPES = ['diamond', 'star', 'triangle', 'square', 'triangleDown'] as const;

/** Valid encounter type values for type-guarding user input. */
const VALID_ENCOUNTER_TYPES: readonly EncounterType[] = ['planned', 'chance_encounter', 'near_miss'];

// -----------------------------------------------------------------------
// Local types
// -----------------------------------------------------------------------

/** Vis-network context event callback parameters. */
interface VisContextParams {
  readonly event: MouseEvent;
  readonly pointer: { readonly DOM: { readonly x: number; readonly y: number } };
}

/** Vis-network click event callback parameters. */
interface VisClickParams {
  readonly nodes: ReadonlyArray<string | number>;
}

/** Flags tracking which edge categories are present in the chart. */
interface LegendFlags {
  hasShared: boolean;
  hasLocation: boolean;
  hasNearMiss: boolean;
  hasEvent: boolean;
}

/** Working state for building the graph during a render pass. */
interface GraphBuilder {
  readonly nodes: Record<string, unknown>[];
  readonly edges: Record<string, unknown>[];
  readonly flags: LegendFlags;
}

/** Data gathered for the node inspector panel. */
interface InspectorData {
  readonly name: string;
  readonly role: string;
  readonly color: string;
  readonly devIdx: number;
  readonly deviceLabel: string;
  readonly isShared: boolean;
  readonly profile: Personality | null | undefined;
  readonly eventsIn: ReadonlyArray<{ readonly idx: number; readonly ev: TimelineEvent }>;
  readonly locations: readonly string[];
  readonly crossLinks: ReadonlyArray<CrossLink>;
}

/** A cross-device link entry for the inspector panel. */
interface CrossLink {
  readonly type: 'shared' | 'event';
  readonly label: string;
  readonly name: string;
}

// -----------------------------------------------------------------------
// LinkChartManager
// -----------------------------------------------------------------------

/**
 * Manages the vis-network link chart visualization, context menus,
 * node inspection panel, and connect-mode event creation.
 *
 * Uses the callback registry to invoke EventManager render functions
 * without creating a direct module dependency. Registers its own
 * `renderLinkChart` callback at construction time.
 */
export class LinkChartManager {
  /** The current vis.Network instance, or null when no chart is rendered. */
  private network: vis.Network | null = null;

  /** Maps "deviceId::contactId" keys to vis node IDs. */
  private chartNodeMap: Record<string, number> = {};

  /** Maps stringified vis node IDs back to device/contact references. */
  private chartReverseMap: Record<string, DeviceContactRef> = {};

  /** Source node reference while connect-mode is active. */
  private pendingLinkConnect: DeviceContactRef | null = null;

  /** Auto-incrementing counter for vis node IDs within a render pass. */
  private nextNodeId = 1;

  constructor() {
    registerCallback('renderLinkChart', () => {
      this.renderLinkChart();
    });
  }

  // -------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------

  /**
   * Build and render the complete link chart from the current scenario.
   *
   * Constructs vis-network nodes and edges for all devices, contacts,
   * connections, and event co-presence links, then initializes the
   * network visualization with physics and event handlers.
   */
  public renderLinkChart(): void {
    const container = document.getElementById('link-chart-container');
    if (!container) return;

    this.resetState();

    if (scenario.devices.length === 0) {
      container.innerHTML =
        '<p style="text-align:center;color:var(--text-muted);padding:2.5rem;">Add devices to see the link chart.</p>';
      return;
    }

    const builder: GraphBuilder = {
      nodes: [],
      edges: [],
      flags: {
        hasShared: false,
        hasLocation: false,
        hasNearMiss: false,
        hasEvent: false,
      },
    };

    this.buildDeviceGraph(builder);
    this.buildConnectionEdges(builder);
    this.buildEventEdges(builder);
    this.renderLegend(builder.flags);
    this.setupNetwork(container, builder);
  }

  /**
   * Show the node context menu at the specified screen coordinates.
   *
   * Renders event participation toggles and action buttons for the
   * node identified by the vis-network ID.
   *
   * @param visNodeId - The vis-network node ID.
   * @param x - Horizontal pixel position for the menu.
   * @param y - Vertical pixel position for the menu.
   */
  public showNodeContextMenu(visNodeId: string | number, x: number, y: number): void {
    const ref = this.chartReverseMap[String(visNodeId)];
    if (!ref) return;

    const dev = scenario.devices.find((d) => d.id === ref.device_id);
    if (!dev) return;

    const isOwner = ref.contact_id === '__owner__';
    const contact = isOwner ? null : dev.contacts.find((c) => c.id === ref.contact_id);
    const personName = isOwner
      ? dev.owner_name || `${dev.device_label} Owner`
      : contact?.name || contact?.actor_id || '?';

    const html = this.buildContextMenuHtml(ref, personName);
    const menu = document.getElementById('node-context-menu');
    if (!menu) return;

    menu.innerHTML = html;
    menu.classList.remove('hidden');
    menu.style.left = `${String(Math.min(x, window.innerWidth - 340))}px`;
    menu.style.top = `${String(Math.min(y, window.innerHeight - 380))}px`;
  }

  /**
   * Hide the node context menu by adding the `hidden` CSS class.
   */
  public hideNodeContextMenu(): void {
    const menu = document.getElementById('node-context-menu');
    if (menu) menu.classList.add('hidden');
  }

  /**
   * Toggle a participant in an event via the context menu, then hide the menu.
   *
   * Delegates to the EventManager's toggleEventParticipant callback.
   *
   * @param evIdx - Zero-based index of the timeline event.
   * @param deviceId - The device ID of the participant.
   * @param contactId - The contact ID of the participant.
   */
  public contextToggleParticipant(evIdx: number, deviceId: string, contactId: string): void {
    getCallback('toggleEventParticipant')?.(evIdx, deviceId, contactId);
    this.hideNodeContextMenu();
  }

  /**
   * Create a new event pre-populated with a single participant from the context menu.
   *
   * Pushes a new timeline event, re-renders events and link chart,
   * syncs the scenario, and scrolls the new event card into view.
   *
   * @param deviceId - The device ID of the initial participant.
   * @param contactId - The contact ID of the initial participant.
   */
  public contextAddEventWithPerson(deviceId: string, contactId: string): void {
    scenario.timeline_events.push({
      id: uid(),
      date: scenario.generation_settings.date_start,
      time: null,
      description: '',
      encounter_type: 'planned',
      device_impacts: {},
      involved_contacts: {},
      participants: [{ device_id: deviceId, contact_id: contactId }],
    });

    getCallback('renderEvents')?.();
    this.renderLinkChart();
    void syncScenario();
    this.hideNodeContextMenu();

    const idx = scenario.timeline_events.length - 1;
    const el = document.getElementById(`event-${String(idx)}`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  /**
   * Enter connect mode starting from a specific node.
   *
   * The next left-click on a different node will create a linked event
   * (and optionally a group chat) between the two nodes.
   *
   * @param deviceId - The device ID of the starting node.
   * @param contactId - The contact ID of the starting node.
   */
  public startConnectFromNode(deviceId: string, contactId: string): void {
    this.pendingLinkConnect = {
      device_id: deviceId,
      contact_id: contactId,
    };
    this.hideNodeContextMenu();
    showToast('Connect mode: now left-click another node to create a linked event.');
  }

  /**
   * Display the node inspector panel for a given vis-network node.
   *
   * Gathers contextual data (events, locations, cross-device links)
   * and renders an HTML summary panel.
   *
   * @param visNodeId - The vis-network internal node ID.
   */
  public showNodeInspector(visNodeId: string | number): void {
    const data = this.gatherInspectorData(visNodeId);
    if (!data) return;

    const html = this.buildInspectorHtml(data);
    const panel = document.getElementById('node-inspector');
    if (!panel) return;

    panel.innerHTML = html;
    panel.classList.remove('hidden');
  }

  /**
   * Hide the node inspector panel by adding the `hidden` CSS class.
   */
  public hideNodeInspector(): void {
    const panel = document.getElementById('node-inspector');
    if (panel) panel.classList.add('hidden');
  }

  /**
   * Extract location names from free-text descriptions.
   *
   * Uses regex patterns to find proper nouns following location
   * prepositions and known NYC neighborhood names.
   *
   * @param text - The text to scan for location references.
   * @returns An array of unique location strings.
   */
  public extractLocations(text: string): string[] {
    if (!text) return [];

    const locations: string[] = [];
    const patterns: RegExp[] = [
      /(?:at|near|in|on|from|visit(?:ing)?|went to|headed to|going to|arrives? at|meet(?:ing)? at)\s+([A-Z][A-Za-z\u2019']+(?:\s+[A-Z][A-Za-z\u2019']+){0,4})/g,
      /(?:Central|Prospect|Bryant|Washington Square|Madison Square|Union Square|Times Square|Brooklyn Bridge|Coney Island|Rockaway|Williamsburg|Bushwick|Astoria|Harlem|Chelsea|SoHo|TriBeCa|Midtown|Greenpoint|Dumbo|Flatbush|Park Slope|Bed-Stuy|Fort Greene|Crown Heights|Sunset Park|Bay Ridge|Red Hook|Cobble Hill|Boerum Hill|Carroll Gardens|Jackson Heights|Flushing|Long Island City|Upper East Side|Upper West Side|Lower East Side|East Village|West Village|Greenwich Village|Nolita|NoHo|Financial District|Battery Park|Hell's Kitchen|Murray Hill|Gramercy|Kips Bay|Flatiron|Koreatown)\b/gi,
    ];

    for (const re of patterns) {
      let m: RegExpExecArray | null;
      while ((m = re.exec(text)) !== null) {
        const loc = (m[1] ?? m[0]).trim().replace(/[.,;:!?]+$/, '');
        if (loc.length >= 4 && loc.length <= 60) locations.push(loc);
      }
    }

    return [...new Set(locations)];
  }

  // -------------------------------------------------------------------
  // Private: Graph building
  // -------------------------------------------------------------------

  /** Reset internal state for a fresh chart render. */
  private resetState(): void {
    this.chartNodeMap = {};
    this.chartReverseMap = {};
    this.nextNodeId = 1;
  }

  /** Allocate the next sequential node ID. */
  private allocNodeId(): number {
    return this.nextNodeId++;
  }

  /**
   * Build vis nodes and edges for all devices, their owners, and contacts.
   *
   * @param builder - The graph builder accumulating nodes, edges, and legend flags.
   */
  private buildDeviceGraph(builder: GraphBuilder): void {
    for (const [devIdx, dev] of scenario.devices.entries()) {
      const color = DEVICE_COLORS[devIdx % DEVICE_COLORS.length];
      const ownerShape = OWNER_SHAPES[devIdx % OWNER_SHAPES.length];
      const ownerId = this.addOwnerNode(builder, dev, color, ownerShape);

      for (const contact of dev.contacts) {
        this.addContactNode(builder, dev, contact, ownerId, color);
      }
    }
  }

  /**
   * Add a device owner node to the graph and register it in the lookup maps.
   *
   * @param builder - The graph builder to append the node to.
   * @param dev - The device whose owner to create a node for.
   * @param color - The palette colour assigned to this device.
   * @param shape - The vis-network shape for the owner node.
   * @returns The allocated vis node ID for the owner.
   */
  private addOwnerNode(builder: GraphBuilder, dev: Device, color: string, shape: string): number {
    const ownerId = this.allocNodeId();
    const mapKey = `${dev.id}::__owner__`;
    this.chartNodeMap[mapKey] = ownerId;
    this.chartReverseMap[String(ownerId)] = {
      device_id: dev.id,
      contact_id: '__owner__',
    };

    builder.nodes.push({
      id: ownerId,
      label: dev.owner_name || dev.device_label,
      color: {
        background: color,
        border: '#fff',
        highlight: { background: '#fff', border: color },
      },
      font: { color: '#fff', size: 14, bold: { color: '#fff' } },
      shape,
      size: 30,
      borderWidth: 3,
      title: `${dev.device_label} (Owner)\nRight-click for options`,
    });

    return ownerId;
  }

  /**
   * Add a contact node, its ownership edge, and any shared-contact edges.
   *
   * @param builder - The graph builder to append nodes and edges to.
   * @param dev - The parent device containing the contact.
   * @param contact - The contact to create a node for.
   * @param ownerId - The vis node ID of the device owner.
   * @param color - The palette colour assigned to this device.
   */
  private addContactNode(builder: GraphBuilder, dev: Device, contact: Contact, ownerId: number, color: string): void {
    const cNodeId = this.allocNodeId();
    const cKey = `${dev.id}::${contact.id}`;
    this.chartNodeMap[cKey] = cNodeId;
    this.chartReverseMap[String(cNodeId)] = {
      device_id: dev.id,
      contact_id: contact.id,
    };

    const isShared = contact.shared_with.length > 0;
    const label = (contact.name || contact.actor_id) + (contact.role ? `\n${contact.role}` : '');

    builder.nodes.push({
      id: cNodeId,
      label,
      color: {
        background: color,
        border: isShared ? '#ef4444' : color,
        highlight: { background: '#fff', border: color },
      },
      font: { color: '#e8e8e8', size: 12, multi: 'md' },
      shape: 'dot',
      size: isShared ? 22 : 16,
      borderWidth: isShared ? 3 : 2,
      title:
        `${dev.device_label} \u2014 ${contact.role || 'contact'}` +
        (isShared ? '\n\uD83D\uDD17 SHARED across devices' : '') +
        '\nRight-click for options',
    });

    builder.edges.push({
      from: ownerId,
      to: cNodeId,
      color: { color: `${color}55`, highlight: color },
      width: 1.5,
      dashes: [4, 4],
      arrows: '',
    });

    this.addSharedEdges(builder, contact, cNodeId);
  }

  /**
   * Add "SAME PERSON" edges for shared contacts already present in the chart.
   *
   * @param builder - The graph builder to append edges to.
   * @param contact - The contact whose shared links to check.
   * @param fromNodeId - The vis node ID of the source contact.
   */
  private addSharedEdges(builder: GraphBuilder, contact: Contact, fromNodeId: number): void {
    for (const link of contact.shared_with) {
      const targetKey = `${link.device_id}::${link.contact_id}`;
      const targetNodeId = this.chartNodeMap[targetKey];
      if (targetNodeId !== undefined) {
        builder.flags.hasShared = true;
        builder.edges.push({
          from: fromNodeId,
          to: targetNodeId,
          color: { color: '#ef4444', highlight: '#ff6b6b' },
          width: 4,
          label: 'SAME PERSON',
          font: {
            color: '#ef4444',
            size: 10,
            strokeWidth: 2,
            strokeColor: '#000',
          },
          smooth: { type: 'curvedCW', roundness: 0.2 },
          title: 'SHARED CONTACT \u2014 same real person on both phones. They text with both owners directly.',
        });
      }
    }
  }

  /**
   * Build location and near-miss connection edges between mapped nodes.
   *
   * @param builder - The graph builder to append edges to.
   */
  private buildConnectionEdges(builder: GraphBuilder): void {
    for (const conn of scenario.connections) {
      const fromKey = `${conn.source_device_id}::${conn.source_contact_id}`;
      const toKey = `${conn.target_device_id}::${conn.target_contact_id}`;
      const fromId = this.chartNodeMap[fromKey];
      const toId = this.chartNodeMap[toKey];
      if (fromId === undefined || toId === undefined) continue;

      let edgeColor = '#f59e0b';
      let tipText = 'LOCATION LINK \u2014 both devices reference this place.';

      if (conn.connection_type === 'near_miss') {
        edgeColor = '#a855f7';
        tipText = 'NEAR MISS \u2014 same place, same time, unknowingly. Forensic breadcrumb.';
        builder.flags.hasNearMiss = true;
      } else {
        builder.flags.hasLocation = true;
      }

      builder.edges.push({
        from: fromId,
        to: toId,
        color: { color: edgeColor, highlight: '#fff' },
        width: 3,
        label: conn.label ?? conn.connection_type,
        font: {
          color: edgeColor,
          size: 10,
          strokeWidth: 2,
          strokeColor: '#000',
        },
        smooth: { type: 'curvedCW', roundness: 0.2 },
        title: tipText,
      });
    }
  }

  /**
   * Build event co-presence edges between participants on different devices.
   *
   * De-duplicates edges so each participant pair appears at most once,
   * even if they share multiple events.
   *
   * @param builder - The graph builder to append edges to.
   */
  private buildEventEdges(builder: GraphBuilder): void {
    const seen = new Set<string>();

    for (const ev of scenario.timeline_events) {
      if (ev.participants.length < 2) continue;

      const desc = ev.description || 'Event';
      const shortDesc = desc.substring(0, 30) + (desc.length > 30 ? '\u2026' : '');

      for (let i = 0; i < ev.participants.length; i++) {
        for (let j = i + 1; j < ev.participants.length; j++) {
          const a = ev.participants[i];
          const b = ev.participants[j];
          if (a.device_id === b.device_id) continue;

          const aKey = `${a.device_id}::${a.contact_id}`;
          const bKey = `${b.device_id}::${b.contact_id}`;
          const pairKey = [aKey, bKey].sort().join('|');
          if (seen.has(pairKey)) continue;
          seen.add(pairKey);

          const fromId = this.chartNodeMap[aKey];
          const toId = this.chartNodeMap[bKey];
          if (fromId === undefined || toId === undefined) continue;

          builder.flags.hasEvent = true;
          builder.edges.push({
            from: fromId,
            to: toId,
            color: { color: '#38bdf8', highlight: '#7dd3fc' },
            width: 2.5,
            dashes: [8, 5],
            label: shortDesc,
            font: {
              color: '#38bdf8',
              size: 9,
              strokeWidth: 2,
              strokeColor: '#000',
            },
            smooth: { type: 'curvedCCW', roundness: 0.15 },
            title: `EVENT CO-PRESENCE \u2014 both at this event. Does NOT mean they know each other.\n\n${ev.description || ''}`,
          });
        }
      }
    }
  }

  /**
   * Update the chart legend element based on which edge types are present.
   *
   * @param flags - Flags indicating which edge categories exist.
   */
  private renderLegend(flags: LegendFlags): void {
    const el = document.getElementById('link-chart-legend');
    if (!el) return;

    let html = '';
    if (flags.hasShared)
      html += '<span><span class="legend-line" style="background:#ef4444;"></span> Shared Contact</span>';
    if (flags.hasEvent)
      html +=
        '<span><span class="legend-line legend-dashed" style="border-color:#38bdf8;"></span> Event Co-presence</span>';
    if (flags.hasLocation)
      html += '<span><span class="legend-line" style="background:#f59e0b;"></span> Location Link</span>';
    if (flags.hasNearMiss)
      html += '<span><span class="legend-line" style="background:#a855f7;"></span> Near Miss</span>';

    for (const [idx, dev] of scenario.devices.entries()) {
      const c = DEVICE_COLORS[idx % DEVICE_COLORS.length];
      html += `<span><span class="legend-line" style="background:${c};"></span> ${esc(dev.device_label)}</span>`;
    }

    el.innerHTML = html;
  }

  /**
   * Create the vis.Network instance and attach event handlers.
   *
   * @param container - The DOM element to render the network into.
   * @param builder - The graph builder containing nodes and edges.
   */
  private setupNetwork(container: HTMLElement, builder: GraphBuilder): void {
    const data = {
      nodes: new vis.DataSet(builder.nodes),
      edges: new vis.DataSet(builder.edges),
    };

    const options: vis.NetworkOptions = {
      physics: {
        forceAtlas2Based: {
          gravitationalConstant: -40,
          centralGravity: 0.005,
          springLength: 160,
          springConstant: 0.04,
        },
        solver: 'forceAtlas2Based',
        stabilization: { iterations: 120 },
      },
      interaction: { hover: true, multiselect: true, tooltipDelay: 150 },
      edges: { smooth: { enabled: true } },
    };

    this.network = new vis.Network(container, data, options);

    this.network.on('oncontext', (rawParams) => {
      const params = rawParams as unknown as VisContextParams;
      params.event.preventDefault();
      const nodeVisId = this.network?.getNodeAt(params.pointer.DOM);
      if (nodeVisId !== undefined) {
        this.showNodeContextMenu(nodeVisId, params.event.clientX, params.event.clientY);
      } else {
        this.hideNodeContextMenu();
      }
    });

    this.network.on('click', (rawParams) => {
      const params = rawParams as unknown as VisClickParams;
      this.hideNodeContextMenu();

      if (this.pendingLinkConnect && params.nodes.length > 0) {
        this.handleLinkConnectSelection(params.nodes[0]);
        return;
      }

      if (params.nodes.length > 0) {
        this.showNodeInspector(params.nodes[0]);
      } else {
        this.hideNodeInspector();
      }
    });
  }

  // -------------------------------------------------------------------
  // Private: Context menu HTML
  // -------------------------------------------------------------------

  /**
   * Build the HTML content for the node context menu.
   *
   * @param ref - The device/contact reference for the selected node.
   * @param personName - The display name of the person.
   * @returns Complete HTML string for the context menu interior.
   */
  private buildContextMenuHtml(ref: DeviceContactRef, personName: string): string {
    let html = `<div class="context-menu-header">${esc(personName)}</div>`;
    html += '<div class="context-menu-section">Events</div>';

    if (scenario.timeline_events.length === 0) {
      html += '<div class="context-menu-item" style="color:var(--text-muted);cursor:default;">No events yet</div>';
    } else {
      for (const [idx, ev] of scenario.timeline_events.entries()) {
        const isIn = ev.participants.some((p) => p.device_id === ref.device_id && p.contact_id === ref.contact_id);
        const evLabel = (ev.description || `Event ${String(idx + 1)}`).substring(0, 40);
        const dateLabel = ev.date ? ` (${ev.date.substring(5)})` : '';
        html += `<div class="context-menu-item" onclick="contextToggleParticipant(${String(idx)},'${ref.device_id}','${ref.contact_id}')">
          <span class="${isIn ? 'cm-check' : 'cm-empty'}">${isIn ? '\u2705' : ''}</span>
          <span>${esc(evLabel)}${dateLabel}</span>
        </div>`;
      }
    }

    html += '<div class="context-menu-section">Actions</div>';
    html += `<div class="context-menu-item" onclick="startConnectFromNode('${ref.device_id}','${ref.contact_id}')">
      <span class="cm-empty">\uD83D\uDD17</span> Start connect from ${esc(personName)}</div>`;
    html += `<div class="context-menu-item" onclick="contextAddEventWithPerson('${ref.device_id}','${ref.contact_id}')">
      <span class="cm-empty">+</span> Create new event with ${esc(personName)}</div>`;

    return html;
  }

  // -------------------------------------------------------------------
  // Private: Connect mode
  // -------------------------------------------------------------------

  /**
   * Resolve a chart node reference to a human-readable label.
   *
   * @param ref - The device/contact reference.
   * @returns Display name for the referenced person.
   */
  private labelForRef(ref: DeviceContactRef): string {
    const dev = scenario.devices.find((d) => d.id === ref.device_id);
    if (!dev) return '?';
    if (ref.contact_id === '__owner__') return dev.owner_name || dev.device_label;
    const c = dev.contacts.find((x) => x.id === ref.contact_id);
    return c ? c.name || c.actor_id : '?';
  }

  /**
   * Check whether a string is a valid encounter type.
   *
   * @param value - The string to validate.
   * @returns True if the value matches a known encounter type.
   */
  private isEncounterType(value: string): value is EncounterType {
    return (VALID_ENCOUNTER_TYPES as readonly string[]).includes(value);
  }

  /**
   * Handle the second click in connect mode to create a linked event.
   *
   * Prompts the user for event type and description, creates the
   * timeline event and optional group chat, then re-renders.
   *
   * @param targetVisNodeId - The vis-network ID of the clicked target node.
   */
  private handleLinkConnectSelection(targetVisNodeId: string | number): void {
    if (!this.pendingLinkConnect) return;

    const targetRef = this.chartReverseMap[String(targetVisNodeId)];
    const srcRef = this.pendingLinkConnect;
    this.pendingLinkConnect = null;
    if (!targetRef) return;

    if (srcRef.device_id === targetRef.device_id && srcRef.contact_id === targetRef.contact_id) {
      showToast('Select a different node.');
      return;
    }

    const encounterType = this.promptEncounterType();
    const srcLabel = this.labelForRef(srcRef);
    const tgtLabel = this.labelForRef(targetRef);

    const descDefault =
      encounterType === 'near_miss'
        ? `${srcLabel} and ${tgtLabel} were at the same location around the same time but did not directly interact.`
        : `${srcLabel} and ${tgtLabel} were present at the same event.`;

    const desc = prompt('Event description', descDefault) ?? descDefault;
    const createGroup = confirm('Also seed a group chat from this event?');

    this.createLinkedEvent(srcRef, targetRef, encounterType, desc);
    if (createGroup) {
      this.createLinkedGroupChat(srcRef, targetRef, srcLabel, tgtLabel, encounterType);
    }

    getCallback('renderEvents')?.();
    getCallback('renderGroupChats')?.();
    this.renderLinkChart();
    void syncScenario();
    showToast(`Created linked event${createGroup ? ' and seeded group chat.' : '.'}`);
  }

  /**
   * Prompt the user to select an encounter type via browser dialog.
   *
   * @returns A valid EncounterType value, defaulting to 'near_miss'.
   */
  private promptEncounterType(): EncounterType {
    const raw = prompt('Event type? planned / chance_encounter / near_miss', 'near_miss');
    const trimmed = (raw ?? 'near_miss').trim();
    return this.isEncounterType(trimmed) ? trimmed : 'near_miss';
  }

  /**
   * Push a new timeline event for a linked connection between two nodes.
   *
   * @param src - Source participant reference.
   * @param target - Target participant reference.
   * @param encounterType - The type of encounter.
   * @param description - Free-text event description.
   */
  private createLinkedEvent(
    src: DeviceContactRef,
    target: DeviceContactRef,
    encounterType: EncounterType,
    description: string
  ): void {
    scenario.timeline_events.push({
      id: uid(),
      date: scenario.generation_settings.date_start,
      time: null,
      description,
      encounter_type: encounterType,
      device_impacts: {},
      involved_contacts: {},
      participants: [src, target],
    });
  }

  /**
   * Push a new group chat seeded from a linked event between two nodes.
   *
   * @param src - Source participant reference.
   * @param target - Target participant reference.
   * @param srcLabel - Display name of the source.
   * @param tgtLabel - Display name of the target.
   * @param encounterType - The type of encounter.
   */
  private createLinkedGroupChat(
    src: DeviceContactRef,
    target: DeviceContactRef,
    srcLabel: string,
    tgtLabel: string,
    encounterType: EncounterType
  ): void {
    const members: DeviceContactRef[] = [src, target];
    if (!members.some((m) => m.contact_id === '__owner__')) {
      members.push({ device_id: src.device_id, contact_id: '__owner__' });
    }

    const lastEvent = scenario.timeline_events[scenario.timeline_events.length - 1];

    scenario.group_chats.push({
      id: uid(),
      name: `${srcLabel} + ${tgtLabel}`,
      members,
      origin_event_id: lastEvent?.id ?? '',
      start_date: lastEvent?.date || scenario.generation_settings.date_start,
      end_date: '',
      message_volume: 'light',
      vibe:
        encounterType === 'near_miss'
          ? 'post-event comparison and indirect references'
          : 'event follow-up coordination',
      activation_mode: 'event_time',
      auto_pair_threads: true,
      quality_score: 1.0,
    });
  }

  // -------------------------------------------------------------------
  // Private: Node inspector
  // -------------------------------------------------------------------

  /**
   * Gather contextual data for the node inspector panel.
   *
   * @param visNodeId - The vis-network node ID to inspect.
   * @returns Collected data for rendering, or null if the node cannot be resolved.
   */
  private gatherInspectorData(visNodeId: string | number): InspectorData | null {
    const ref = this.chartReverseMap[String(visNodeId)];
    if (!ref) return null;

    const dev = scenario.devices.find((d) => d.id === ref.device_id);
    if (!dev) return null;

    const isOwner = ref.contact_id === '__owner__';
    const contact = isOwner ? null : (dev.contacts.find((c) => c.id === ref.contact_id) ?? null);
    const devIdx = scenario.devices.indexOf(dev);

    const eventsIn = this.findEventsForRef(ref);
    const locations = this.extractLocationsForEvents(eventsIn, dev.id);
    const crossLinks = this.buildCrossLinks(ref, isOwner, contact, eventsIn);

    return {
      name: isOwner ? dev.owner_name || dev.device_label : contact?.name || '?',
      role: isOwner ? 'Phone Owner' : contact?.role || 'contact',
      color: DEVICE_COLORS[devIdx % DEVICE_COLORS.length],
      devIdx,
      deviceLabel: dev.device_label,
      isShared: !isOwner && (contact?.shared_with?.length ?? 0) > 0,
      profile: isOwner ? dev.owner_personality : contact?.personality,
      eventsIn,
      locations,
      crossLinks,
    };
  }

  /**
   * Find all timeline events that include a given participant.
   *
   * @param ref - The device/contact reference to search for.
   * @returns Array of matching events with their scenario indices.
   */
  private findEventsForRef(ref: DeviceContactRef): Array<{ idx: number; ev: TimelineEvent }> {
    const result: Array<{ idx: number; ev: TimelineEvent }> = [];
    for (const [idx, ev] of scenario.timeline_events.entries()) {
      const inEvent = ev.participants.some((p) => p.device_id === ref.device_id && p.contact_id === ref.contact_id);
      if (inEvent) result.push({ idx, ev });
    }
    return result;
  }

  /**
   * Extract unique locations from event descriptions and device impacts.
   *
   * @param eventsIn - Events the person participates in.
   * @param deviceId - The device ID to look up per-device impacts.
   * @returns Deduplicated array of location strings.
   */
  private extractLocationsForEvents(eventsIn: ReadonlyArray<{ ev: TimelineEvent }>, deviceId: string): string[] {
    const locations = new Set<string>();
    for (const { ev } of eventsIn) {
      for (const loc of this.extractLocations(ev.description || '')) locations.add(loc);
      const impact = ev.device_impacts[deviceId];
      if (impact) {
        for (const loc of this.extractLocations(impact)) locations.add(loc);
      }
    }
    return [...locations];
  }

  /**
   * Build cross-device link entries for the inspector panel.
   *
   * Includes both shared-contact links and event co-presence links.
   *
   * @param ref - The inspected node's device/contact reference.
   * @param isShared - Whether the contact is a shared contact.
   * @param contact - The contact object, or null for owners.
   * @param eventsIn - Events the person participates in.
   * @returns Array of cross-link entries for display.
   */
  private buildCrossLinks(
    ref: DeviceContactRef,
    isShared: boolean,
    contact: Contact | null,
    eventsIn: ReadonlyArray<{ ev: TimelineEvent }>
  ): CrossLink[] {
    const links: CrossLink[] = [];

    if (isShared && contact?.shared_with) {
      for (const link of contact.shared_with) {
        const od = scenario.devices.find((d) => d.id === link.device_id);
        const oc = od?.contacts.find((x) => x.id === link.contact_id);
        links.push({
          type: 'shared',
          label: `Same person on ${od?.device_label ?? '?'}`,
          name: oc?.name ?? '?',
        });
      }
    }

    for (const { ev } of eventsIn) {
      for (const p of ev.participants) {
        if (p.device_id === ref.device_id) continue;
        const od = scenario.devices.find((d) => d.id === p.device_id);
        if (!od) continue;
        const pName =
          p.contact_id === '__owner__'
            ? od.owner_name || od.device_label
            : (od.contacts.find((x) => x.id === p.contact_id)?.name ?? '?');
        links.push({
          type: 'event',
          label: (ev.description || '').substring(0, 50),
          name: `${pName} (${od.device_label})`,
        });
      }
    }

    return links;
  }

  /**
   * Build the complete HTML for the node inspector panel.
   *
   * @param data - Pre-gathered inspector data.
   * @returns HTML string for the inspector content.
   */
  private buildInspectorHtml(data: InspectorData): string {
    let html = `<div class="inspector-header">
      <span class="device-number" style="background:${data.color}">${String(data.devIdx + 1)}</span>
      <span class="inspector-name">${esc(data.name)}</span>
      <span class="badge badge-blue">${esc(data.role)}</span>
      ${data.isShared ? '<span class="badge badge-orange">Shared</span>' : ''}
      <span style="font-size:0.6875rem;color:var(--text-muted);">${esc(data.deviceLabel)}</span>
    </div>`;

    html += '<div class="inspector-grid">';
    html += this.buildPersonalitySection(data.profile);
    html += this.buildEventsSection(data.eventsIn);
    html += this.buildLocationsSection(data.locations);
    html += this.buildCrossLinksSection(data.crossLinks);
    html += '</div>';

    return html;
  }

  /**
   * Build the personality section HTML for the node inspector.
   *
   * @param profile - The personality profile, or null/undefined.
   * @returns HTML string for the personality section.
   */
  private buildPersonalitySection(profile: Personality | null | undefined): string {
    let html = '<div class="inspector-section"><h5>Personality</h5>';
    if (profile?.personality_summary) {
      html += `<p>${esc(profile.personality_summary)}</p>`;
    } else {
      html += '<p style="color:var(--text-muted);">No profile set</p>';
    }
    if (profile?.neighborhood)
      html += `<p style="margin-top:0.25rem;">Neighborhood: <strong>${esc(profile.neighborhood)}</strong></p>`;
    if (profile?.job_details) html += `<p>Job: ${esc(profile.job_details)}</p>`;
    html += '</div>';
    return html;
  }

  /**
   * Build the events section HTML for the node inspector.
   *
   * @param eventsIn - Events the inspected person participates in.
   * @returns HTML string for the events section.
   */
  private buildEventsSection(
    eventsIn: ReadonlyArray<{
      readonly idx: number;
      readonly ev: TimelineEvent;
    }>
  ): string {
    let html = `<div class="inspector-section"><h5>Events (${String(eventsIn.length)})</h5>`;

    if (eventsIn.length > 0) {
      html += '<ul>';
      for (const { ev } of eventsIn) {
        const desc = (ev.description || '').substring(0, 80);
        const ellipsis = (ev.description?.length ?? 0) > 80 ? '\u2026' : '';
        html += `<li><strong>${esc(ev.date || '?')}</strong>: ${esc(desc)}${ellipsis}</li>`;
      }
      html += '</ul>';
    } else {
      html += '<p style="color:var(--text-muted);">Not in any events</p>';
    }

    html += '</div>';
    return html;
  }

  /**
   * Build the locations section HTML for the node inspector.
   *
   * @param locations - Deduplicated location strings.
   * @returns HTML string for the locations section.
   */
  private buildLocationsSection(locations: readonly string[]): string {
    let html = '<div class="inspector-section"><h5>Locations</h5>';

    if (locations.length > 0) {
      html += '<div>' + locations.map((l) => `<span class="inspector-location">${esc(l)}</span>`).join(' ') + '</div>';
    } else {
      html += '<p style="color:var(--text-muted);">No locations extracted</p>';
    }

    html += '</div>';
    return html;
  }

  /**
   * Build the cross-device links section HTML for the node inspector.
   *
   * @param crossLinks - Array of cross-link entries.
   * @returns HTML string for the cross-device links section.
   */
  private buildCrossLinksSection(crossLinks: ReadonlyArray<CrossLink>): string {
    let html = `<div class="inspector-section"><h5>Cross-Device Links (${String(crossLinks.length)})</h5>`;

    if (crossLinks.length > 0) {
      html += '<ul>';
      for (const link of crossLinks) {
        const icon = link.type === 'shared' ? '\uD83D\uDD17' : '\uD83D\uDCC5';
        html += `<li>${icon} ${esc(link.name)} \u2014 ${esc(link.label)}</li>`;
      }
      html += '</ul>';
    } else {
      html += '<p style="color:var(--text-muted);">No cross-device links</p>';
    }

    html += '</div>';
    return html;
  }
}

// -----------------------------------------------------------------------
// Module singleton & convenience exports
// -----------------------------------------------------------------------

/** Default singleton used by the convenience exports. */
const defaultManager = new LinkChartManager();

/**
 * Render the link chart from the current scenario.
 * Delegates to the default {@link LinkChartManager} singleton.
 */
export function renderLinkChart(): void {
  defaultManager.renderLinkChart();
}

/**
 * Hide the node context menu.
 * Delegates to the default {@link LinkChartManager} singleton.
 */
export function hideNodeContextMenu(): void {
  defaultManager.hideNodeContextMenu();
}

/**
 * Toggle event participant from the context menu.
 * Delegates to the default {@link LinkChartManager} singleton.
 *
 * @param evIdx - Zero-based index of the timeline event.
 * @param deviceId - The device ID of the participant.
 * @param contactId - The contact ID of the participant.
 */
export function contextToggleParticipant(evIdx: number, deviceId: string, contactId: string): void {
  defaultManager.contextToggleParticipant(evIdx, deviceId, contactId);
}

/**
 * Create a new event with a person from the context menu.
 * Delegates to the default {@link LinkChartManager} singleton.
 *
 * @param deviceId - The device ID of the initial participant.
 * @param contactId - The contact ID of the initial participant.
 */
export function contextAddEventWithPerson(deviceId: string, contactId: string): void {
  defaultManager.contextAddEventWithPerson(deviceId, contactId);
}

/**
 * Enter connect mode from a specific node.
 * Delegates to the default {@link LinkChartManager} singleton.
 *
 * @param deviceId - The device ID of the starting node.
 * @param contactId - The contact ID of the starting node.
 */
export function startConnectFromNode(deviceId: string, contactId: string): void {
  defaultManager.startConnectFromNode(deviceId, contactId);
}

/**
 * Display the node inspector panel for a vis-network node.
 * Delegates to the default {@link LinkChartManager} singleton.
 *
 * @param visNodeId - The vis-network internal node ID.
 */
export function showNodeInspector(visNodeId: string | number): void {
  defaultManager.showNodeInspector(visNodeId);
}

/**
 * Hide the node inspector panel.
 * Delegates to the default {@link LinkChartManager} singleton.
 */
export function hideNodeInspector(): void {
  defaultManager.hideNodeInspector();
}

/**
 * Extract location names from free-text descriptions.
 * Delegates to the default {@link LinkChartManager} singleton.
 *
 * @param text - The text to scan for location references.
 * @returns An array of unique location strings.
 */
export function extractLocations(text: string): string[] {
  return defaultManager.extractLocations(text);
}
