/**
 * Generation panel UI, progress display, quality checks, and SSE stream handling.
 *
 * Renders the scenario summary grid, manages generation settings form fields,
 * streams Server-Sent Events during dataset generation, and drives the
 * AI quality-fix workflow. All DOM mutations target elements identified by
 * well-known IDs in the generation tab.
 * @module
 */

import type { Language, MessageVolume, SpamDensity } from '../shared/types.js';

import { api } from '../core/ApiClient.js';
import { LANGUAGE_LABELS, THEME_LABELS, scenario, syncScenario } from '../core/AppState.js';
import { extractLocations } from '../shared/text-utils.js';

// ---------------------------------------------------------------------------
// Local types — SSE events, progress, quality responses
// ---------------------------------------------------------------------------

/** CSS class applied to progress log lines for visual severity. */
type LogClass = 'success' | 'error';

/** Callback for appending a line to the progress log container. */
type LogFn = (text: string, cls?: LogClass) => void;

/** Discriminated SSE event type literals emitted by the generation endpoint. */
type SseEventType =
  | 'device_start'
  | 'device_skipped'
  | 'contact_start'
  | 'contact_skipped'
  | 'contact_done'
  | 'contact_error'
  | 'quota_exhausted'
  | 'group_start'
  | 'group_done'
  | 'pairwise_done'
  | 'spam_done'
  | 'quality_warning'
  | 'device_done'
  | 'device_event_validation'
  | 'resume_blocked'
  | 'stopped'
  | 'complete'
  | 'error';

/**
 * Parsed payload from a single SSE `data:` frame.
 *
 * Every frame carries a `type` discriminator. The remaining fields are
 * optional and populated only by the event types that use them.
 */
interface SseEvent {
  type: SseEventType;
  label?: string;
  resuming?: boolean;
  reason?: string;
  name?: string;
  messages?: number;
  error?: string;
  group_name?: string;
  threads?: number;
  entity_id?: string;
  check_id?: string;
  severity?: string;
  message?: string;
  suggestion?: string;
  path?: string;
  partial?: boolean;
  passed?: boolean;
  issues_before?: number;
  issues_after?: number;
  warnings_after?: number;
  repaired_threads?: number;
  repair_details?: RepairDetail[];
  remaining_findings?: QualityFinding[];
  top_findings?: QualityFinding[];
  blocking_findings?: QualityFinding[];
  quality_report_path?: string;
  quality_summary?: QualitySummary;
  run_log_path?: string;
}

/** Mutable counters shared across the SSE processing loop. */
interface StreamState {
  doneWork: number;
  totalWork: number;
}

/** Per-device progress information returned by the progress endpoint. */
interface DeviceProgressInfo {
  label: string;
  complete: boolean;
  has_output: boolean;
  contacts_done: number;
  contacts_total: number;
  total_messages: number;
}

/** Top-level progress response from `GET /api/generate/progress`. */
interface ProgressResponse {
  has_partial: boolean;
  all_complete: boolean;
  devices: DeviceProgressInfo[];
}

/** Aggregate quality score and finding counts. */
interface QualitySummary {
  overall_score: number;
  overall_severity: string;
  findings_total: number;
  critical_count: number;
  warning_count: number;
}

/** A single quality finding with optional remediation advice. */
interface QualityFinding {
  severity: string;
  message: string;
  check_id?: string;
  entity_id?: string;
  suggestion?: string;
}

/** Detail record for a single repair action taken during generation. */
interface RepairDetail {
  thread?: string;
  outcome: string;
  reason?: string;
  issues?: string[];
  device?: string;
  messages_produced?: number;
}

/** Section of the quality-check response (before / after). */
interface QualityReportSection {
  summary?: QualitySummary;
  top_findings?: QualityFinding[];
}

/** A single issue item in the resolution write-up. */
interface ResolutionItem {
  issue: string;
  action: string;
  result: string;
  repair_details?: RepairDetail[];
}

/** Detailed write-up of issues resolved by the quality-fix pass. */
interface ResolutionWriteup {
  before_problem_count: number;
  after_problem_count: number;
  resolved_estimate: number;
  items: ResolutionItem[];
}

/** Readiness summary returned alongside the quality-check response. */
interface ReadinessInfo {
  personality_complete: string;
  arc_complete: string;
  events_with_participants: string;
}

/** Full response from `POST /api/generate/quality-check`. */
interface QualityCheckResponse {
  before?: QualityReportSection;
  after?: QualityReportSection;
  adjustments?: string[];
  quality_report_path?: string;
  readiness?: ReadinessInfo;
  resolution_writeup?: ResolutionWriteup;
}

// ---------------------------------------------------------------------------
// Constants — density/skip maps and ordered label arrays
// ---------------------------------------------------------------------------

/** Expected message density multiplier per volume tier. */
const VOLUME_DENSITY: Readonly<Record<MessageVolume, number>> = {
  heavy: 0.65,
  regular: 0.35,
  light: 0.18,
  minimal: 0.1,
} as const;

/** Fraction of days skipped per volume tier (contact goes silent). */
const VOLUME_SKIP: Readonly<Record<MessageVolume, number>> = {
  heavy: 0.12,
  regular: 0.45,
  light: 0.82,
  minimal: 0.96,
} as const;

/** Display order for message-volume tiers in the summary grid. */
const VOLUME_ORDER: readonly MessageVolume[] = ['heavy', 'regular', 'light', 'minimal'];

/** Display order for spam-density tiers in the summary grid. */
const SPAM_ORDER: readonly SpamDensity[] = ['high', 'medium', 'low', 'none'];

/** Milliseconds per calendar day. */
const MS_PER_DAY = 86_400_000;

/** Maximum number of resolution items logged to the progress panel. */
const MAX_RESOLUTION_ITEMS = 15;

/** Maximum number of top findings logged to the progress panel. */
const MAX_TOP_FINDINGS = 8;

// ---------------------------------------------------------------------------
// GenerateManager
// ---------------------------------------------------------------------------

/**
 * Manages the generation panel: summary statistics, settings form,
 * SSE-streamed generation, and the AI quality-fix workflow.
 */
export class GenerateManager {
  // -------------------------------------------------------------------
  // Statistic helpers
  // -------------------------------------------------------------------

  /**
   * Count unique cross-device participant pairs linked by timeline events.
   *
   * Two contacts on different devices appearing in the same event count as
   * one cross-device link. Pair order is normalised so each pair is counted
   * once regardless of which device appears first.
   *
   * @returns The number of unique cross-device contact pairs.
   */
  private countCrossDeviceEventLinks(): number {
    const pairSet = new Set<string>();
    for (const ev of scenario.timeline_events) {
      const parts = ev.participants ?? [];
      for (let i = 0; i < parts.length; i++) {
        for (let j = i + 1; j < parts.length; j++) {
          const a = parts[i];
          const b = parts[j];
          if (a && b && a.device_id !== b.device_id) {
            const key = [`${a.device_id}::${a.contact_id}`, `${b.device_id}::${b.contact_id}`].sort().join('|');
            pairSet.add(key);
          }
        }
      }
    }
    return pairSet.size;
  }

  /**
   * Scan all timeline event descriptions and device impacts for location names.
   *
   * Delegates to {@link extractLocations} for each text fragment and
   * deduplicates the combined results.
   *
   * @returns A deduplicated array of location strings.
   */
  private collectAllLocations(): string[] {
    const locs = new Set<string>();
    for (const ev of scenario.timeline_events) {
      for (const l of extractLocations(ev.description ?? '')) locs.add(l);
      for (const txt of Object.values(ev.device_impacts ?? {})) {
        for (const l of extractLocations(txt)) locs.add(l);
      }
    }
    return [...locs];
  }

  /**
   * Estimate the total number of messages the generation run will produce.
   *
   * Uses per-volume density/skip heuristics combined with the configured
   * date range and per-day message bounds.
   *
   * @returns Estimated total message count across all devices and contacts.
   */
  private estimateTotalMessages(): number {
    const s = scenario.generation_settings;
    const start = new Date(s.date_start);
    const end = new Date(s.date_end);
    const days = Math.max(1, Math.round((end.getTime() - start.getTime()) / MS_PER_DAY) + 1);
    const avgBase = (s.messages_per_day_min + s.messages_per_day_max) / 2;
    let total = 0;
    for (const dev of scenario.devices) {
      for (const c of dev.contacts) {
        const vol: MessageVolume = c.message_volume || 'regular';
        const density = VOLUME_DENSITY[vol] || 0.5;
        const skip = VOLUME_SKIP[vol] || 0.25;
        const avgPerDay = avgBase * density;
        const activeDays = days * (1 - skip);
        total += Math.round(avgPerDay * activeDays);
      }
    }
    return total;
  }

  // -------------------------------------------------------------------
  // Log helper factory
  // -------------------------------------------------------------------

  /**
   * Create a log-line appender bound to a specific container element.
   *
   * Each invocation appends a `<div class="log-line [cls]">` to the
   * container and auto-scrolls to the bottom.
   *
   * @param container - The DOM element that receives log lines.
   * @returns A {@link LogFn} closure.
   */
  private createLogFn(container: HTMLElement): LogFn {
    return (text: string, cls?: LogClass): void => {
      const line = document.createElement('div');
      line.className = cls ? `log-line ${cls}` : 'log-line';
      line.textContent = text;
      container.appendChild(line);
      container.scrollTop = container.scrollHeight;
    };
  }

  /**
   * Update the progress bar width based on the current stream state.
   *
   * @param pb - The progress-bar DOM element.
   * @param state - Mutable stream counters.
   */
  private updateProgressBar(pb: HTMLElement, state: StreamState): void {
    const pct = state.totalWork > 0 ? Math.round((state.doneWork / state.totalWork) * 100) : 0;
    pb.style.width = `${String(pct)}%`;
  }

  // -------------------------------------------------------------------
  // Render summary + settings
  // -------------------------------------------------------------------

  /**
   * Render the generation-tab summary grid and populate settings form fields.
   *
   * Computes scenario statistics (device count, contacts, shared contacts,
   * profiles, cross-device links, events, locations, estimated messages,
   * volume mix, spam density, story arcs) and writes the summary HTML into
   * `#summary-grid`. Also populates the settings form inputs and kicks off
   * a progress check.
   */
  public renderGenerate(): void {
    const totalDevices = scenario.devices.length;
    const totalContacts = scenario.devices.reduce((sum, d) => sum + d.contacts.length, 0);
    const totalEvents = scenario.timeline_events.length;
    const sharedPairs = scenario.devices.reduce(
      (sum, d) => sum + d.contacts.filter((c) => (c.shared_with?.length ?? 0) > 0).length,
      0
    );
    const sharedCount = (sharedPairs / 2) | 0;
    const eventLinks = this.countCrossDeviceEventLinks();
    const totalLinks = sharedCount + eventLinks;
    const profilesDone = scenario.devices.reduce((sum, d) => {
      let count = d.owner_personality ? 1 : 0;
      count += d.contacts.filter((c) => c.personality).length;
      return sum + count;
    }, 0);
    const profilesTotal = totalDevices + totalContacts;
    const locations = this.collectAllLocations();
    const estMessages = this.estimateTotalMessages();
    const themeLabel = THEME_LABELS[scenario.theme] || scenario.theme || 'Slice of Life';

    const volBreakdown = this.buildVolumeBreakdown();
    const spamSummary = this.buildSpamSummary();

    const hasStoryArc = scenario.story_arc ? 'Yes' : 'No';
    const charArcsSet = scenario.devices.reduce((sum, d) => {
      let cnt = d.owner_story_arc ? 1 : 0;
      cnt += d.contacts.filter((c) => c.story_arc).length;
      return sum + cnt;
    }, 0);
    const charArcsTotal = totalDevices + totalContacts;

    const langLabel =
      LANGUAGE_LABELS[scenario.generation_settings?.language] || scenario.generation_settings?.language || 'English';

    this.renderSummaryGrid({
      totalDevices,
      totalContacts,
      sharedCount,
      profilesDone,
      profilesTotal,
      totalLinks,
      totalEvents,
      eventLinks,
      locationCount: locations.length,
      estMessages,
      themeLabel,
      hasStoryArc,
      charArcsSet,
      charArcsTotal,
      volBreakdown,
      spamSummary,
      languageLabel: langLabel,
    });

    this.renderCostEstimate(estMessages);
    this.populateSettingsForm();
    void this.checkGenerationProgress();
  }

  /**
   * Build a human-readable volume-mix breakdown string.
   *
   * @returns Comma-separated volume tiers with counts, e.g. "3 heavy, 2 regular".
   */
  private buildVolumeBreakdown(): string {
    const counts: Record<MessageVolume, number> = {
      heavy: 0,
      regular: 0,
      light: 0,
      minimal: 0,
    };
    for (const dev of scenario.devices) {
      for (const c of dev.contacts) counts[c.message_volume || 'regular']++;
    }
    return (
      VOLUME_ORDER.filter((v) => counts[v] > 0)
        .map((v) => `${String(counts[v])} ${v}`)
        .join(', ') || 'N/A'
    );
  }

  /**
   * Build a human-readable spam-density summary string.
   *
   * @returns Comma-separated density tiers with counts, e.g. "1 high, 2 medium".
   */
  private buildSpamSummary(): string {
    const counts: Record<SpamDensity, number> = {
      none: 0,
      low: 0,
      medium: 0,
      high: 0,
    };
    for (const dev of scenario.devices) counts[dev.spam_density || 'medium']++;
    const parts = SPAM_ORDER.filter((v) => counts[v] > 0).map((v) => `${String(counts[v])} ${v}`);
    return parts.length > 0 ? parts.join(', ') : 'N/A';
  }

  /**
   * Inject the summary-grid HTML from pre-computed statistics.
   *
   * @param s - The pre-computed summary statistics object.
   */
  private renderSummaryGrid(s: {
    totalDevices: number;
    totalContacts: number;
    sharedCount: number;
    profilesDone: number;
    profilesTotal: number;
    totalLinks: number;
    totalEvents: number;
    eventLinks: number;
    locationCount: number;
    estMessages: number;
    themeLabel: string;
    hasStoryArc: string;
    charArcsSet: number;
    charArcsTotal: number;
    volBreakdown: string;
    spamSummary: string;
    languageLabel: string;
  }): void {
    const grid = document.getElementById('summary-grid');
    if (!grid) return;

    const stat = (value: string, label: string): string =>
      `<div class="summary-stat"><div class="value">${value}</div><div class="label">${label}</div></div>`;

    grid.innerHTML = [
      stat(String(s.totalDevices), 'Devices'),
      stat(String(s.totalContacts), 'Contacts'),
      stat(String(s.sharedCount), 'Shared Contacts'),
      stat(`${String(s.profilesDone)}/${String(s.profilesTotal)}`, 'Profiles Set'),
      stat(String(s.totalLinks), 'Cross-Device Links'),
      stat(String(s.totalEvents), 'Events'),
      stat(String(s.eventLinks), 'Event Links'),
      stat(String(s.locationCount), 'Locations'),
      stat(`~${String(s.estMessages)}`, 'Est. Messages'),
      stat(s.themeLabel, 'Theme'),
      stat(s.hasStoryArc, 'Story Arc'),
      stat(`${String(s.charArcsSet)}/${String(s.charArcsTotal)}`, 'Char Arcs'),
      stat(s.volBreakdown, 'Volume Mix'),
      stat(s.spamSummary, 'Spam Density'),
      stat(s.languageLabel, 'Language'),
    ].join('\n');
  }

  /**
   * Display or hide the cost-estimate banner based on estimated message count.
   *
   * @param estMessages - Estimated total message count.
   */
  private renderCostEstimate(estMessages: number): void {
    const costDiv = document.getElementById('gen-cost-estimate');
    if (!costDiv || estMessages <= 0) return;

    const batchSize = scenario.generation_settings.batch_size || 25;
    const estCalls = Math.ceil(estMessages / batchSize) * scenario.devices.length;

    const costText = document.getElementById('cost-estimate-text');
    if (costText) {
      costText.textContent = `Estimated: ~${String(estCalls)} API calls, ~${String(estMessages)} total messages across all devices`;
    }
    costDiv.classList.remove('hidden');
  }

  /**
   * Populate the settings form inputs with the current generation settings.
   */
  private populateSettingsForm(): void {
    const gs = scenario.generation_settings;
    const set = (id: string, value: string): void => {
      const el = document.getElementById(id) as HTMLInputElement | null;
      if (el) el.value = value;
    };
    set('gen-date-start', gs.date_start);
    set('gen-date-end', gs.date_end);
    set('gen-msg-min', String(gs.messages_per_day_min));
    set('gen-msg-max', String(gs.messages_per_day_max));
    set('gen-batch-size', String(gs.batch_size));
    set('gen-provider', gs.llm_provider);
    set('gen-temperature', String(gs.temperature));
  }

  // -------------------------------------------------------------------
  // Settings update
  // -------------------------------------------------------------------

  /**
   * Read the current settings form values and write them into the scenario.
   *
   * Triggers a debounced scenario sync to persist changes.
   */
  public updateSettings(): void {
    const val = (id: string): string => (document.getElementById(id) as HTMLInputElement | null)?.value ?? '';

    scenario.generation_settings = {
      date_start: val('gen-date-start'),
      date_end: val('gen-date-end'),
      messages_per_day_min: parseInt(val('gen-msg-min'), 10) || 2,
      messages_per_day_max: parseInt(val('gen-msg-max'), 10) || 8,
      batch_size: parseInt(val('gen-batch-size'), 10) || 25,
      llm_provider: val('gen-provider'),
      llm_model: '',
      temperature: parseFloat(val('gen-temperature')) || 0.9,
      language: (val('scenario-language') || 'en') as Language,
    };
    void syncScenario();
  }

  // -------------------------------------------------------------------
  // Progress check
  // -------------------------------------------------------------------

  /**
   * Query the server for existing generation output and update the resume UI.
   *
   * Shows or hides the resume button and progress info panel depending on
   * whether partial or complete output already exists.
   *
   * @returns The progress response, or `null` on error.
   */
  public async checkGenerationProgress(): Promise<ProgressResponse | null> {
    try {
      const res = await api<ProgressResponse>('GET', '/api/generate/progress');
      const resumeBtn = document.getElementById('gen-resume-btn');
      const progressInfo = document.getElementById('gen-progress-info');
      if (!resumeBtn || !progressInfo) return res;

      if (res.has_partial || res.all_complete) {
        const parts = res.devices.map((d) => {
          const status = d.complete
            ? 'done'
            : d.has_output
              ? `${String(d.contacts_done)}/${String(d.contacts_total)}`
              : 'pending';
          return `${d.label}: ${status} (${String(d.total_messages)} msgs)`;
        });
        progressInfo.innerHTML =
          `<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">` +
          `<strong>Existing output found:</strong><br>${parts.join('<br>')}</div>`;
        progressInfo.classList.remove('hidden');

        if (res.has_partial) {
          resumeBtn.classList.remove('hidden');
        } else {
          resumeBtn.classList.add('hidden');
        }
      } else {
        progressInfo.classList.add('hidden');
        resumeBtn.classList.add('hidden');
      }
      return res;
    } catch {
      return null;
    }
  }

  // -------------------------------------------------------------------
  // Quality check
  // -------------------------------------------------------------------

  /**
   * Run the server-side AI quality-fix workflow and log results.
   *
   * Disables action buttons while the check is in progress, streams the
   * API response into the progress log panel, and re-enables buttons on
   * completion or error.
   */
  public async runQualityCheck(): Promise<void> {
    await syncScenario();

    const fixBtn = document.getElementById('gen-quality-fix-btn') as HTMLButtonElement | null;
    const pc = document.getElementById('progress-container');
    const pl = document.getElementById('progress-log');
    const runBtn = document.getElementById('gen-run-btn') as HTMLButtonElement | null;
    const resumeBtn = document.getElementById('gen-resume-btn') as HTMLButtonElement | null;

    if (!pc || !pl) return;
    pc.classList.remove('hidden');
    pl.innerHTML = '';

    const addLog = this.createLogFn(pl);

    if (fixBtn) fixBtn.disabled = true;
    if (runBtn) runBtn.disabled = true;
    if (resumeBtn) resumeBtn.disabled = true;
    if (fixBtn) fixBtn.innerHTML = '<span class="spinner"></span> Running AI quality fix...';

    try {
      const response = await fetch('/api/generate/quality-check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_adjust: true }),
      });
      if (!response.ok) throw new Error('Quality check failed');
      const data = (await response.json()) as QualityCheckResponse;
      this.logQualityCheckResults(data, addLog);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      addLog(`Quality check error: ${msg}`, 'error');
    } finally {
      if (fixBtn) {
        fixBtn.disabled = false;
        fixBtn.innerHTML = 'AI Quality Fix';
      }
      if (runBtn) runBtn.disabled = false;
      if (resumeBtn) resumeBtn.disabled = false;
    }
  }

  /**
   * Log the results of a quality-check response into the progress panel.
   *
   * @param data - The parsed quality-check response.
   * @param addLog - Log-line appender.
   */
  private logQualityCheckResults(data: QualityCheckResponse, addLog: LogFn): void {
    const before = data.before?.summary;
    const after = data.after?.summary;
    const beforePct = Math.round((before?.overall_score ?? 0) * 100);
    const afterPct = Math.round((after?.overall_score ?? 0) * 100);
    const delta = afterPct - beforePct;

    addLog('AI quality fix complete.', 'success');
    addLog(
      `Quality now: ${String(afterPct)}% (${after?.overall_severity ?? 'ok'}), findings ${String(after?.findings_total ?? 0)}`,
      'success'
    );
    addLog(`Change vs previous check: ${delta >= 0 ? '+' : ''}${String(delta)}%`);

    const adjustments = data.adjustments ?? [];
    if (adjustments.length > 0) {
      addLog('Fixes applied:', 'success');
      for (const a of adjustments) addLog(`  - ${a}`, 'success');
    } else {
      addLog('No automatic fixes were needed.', 'success');
    }

    if (data.quality_report_path) {
      addLog(`Quality report saved: ${data.quality_report_path}`, 'success');
    }

    if (data.readiness) {
      const r = data.readiness;
      addLog(
        `Scenario readiness: personalities ${r.personality_complete}, arcs ${r.arc_complete}, events ${r.events_with_participants}`
      );
    }

    this.logResolutionWriteup(data.resolution_writeup, addLog);
    this.logTopFindings(data.after?.top_findings, addLog);
  }

  /**
   * Log the resolution write-up section of a quality-check response.
   *
   * @param writeup - Optional resolution write-up from the response.
   * @param addLog - Log-line appender.
   */
  private logResolutionWriteup(writeup: ResolutionWriteup | undefined, addLog: LogFn): void {
    if (!writeup) return;
    addLog('Issue resolution write-up:', 'success');
    addLog(
      `  Problems before: ${String(writeup.before_problem_count ?? 0)}, after: ${String(writeup.after_problem_count ?? 0)}, resolved: ${String(writeup.resolved_estimate ?? 0)}`,
      'success'
    );
    for (const item of (writeup.items ?? []).slice(0, MAX_RESOLUTION_ITEMS)) {
      addLog(`  Issue: ${item.issue ?? ''}`, 'success');
      addLog(`    Action: ${item.action ?? ''}`, 'success');
      addLog(`    Result: ${item.result ?? ''}`, 'success');
      if (item.repair_details?.length) {
        for (const rd of item.repair_details) {
          const tag = rd.outcome === 'repaired' ? 'success' : 'error';
          const msgCount = rd.messages_produced ? ` (${String(rd.messages_produced)} msgs)` : '';
          const reasonStr = rd.reason ? ` \u2014 ${rd.reason}` : '';
          addLog(`      [${rd.device ?? ''}] ${rd.thread ?? ''} \u2192 ${rd.outcome}${msgCount}${reasonStr}`, tag);
          for (const issue of rd.issues ?? []) {
            addLog(`        \u2023 ${issue}`);
          }
        }
      }
    }
  }

  /**
   * Log the top quality findings from a report section.
   *
   * @param findings - Optional array of quality findings.
   * @param addLog - Log-line appender.
   */
  private logTopFindings(findings: QualityFinding[] | undefined, addLog: LogFn): void {
    if (!findings || findings.length === 0) {
      addLog('No findings detected after quality check.', 'success');
      return;
    }
    addLog('Top findings:');
    for (const f of findings.slice(0, MAX_TOP_FINDINGS)) {
      const sev = (f.severity ?? 'warning').toUpperCase();
      const check = f.check_id ? ` (${f.check_id})` : '';
      addLog(`${sev}${check}: ${f.message ?? ''}`, f.severity === 'critical' ? 'error' : undefined);
    }
  }

  // -------------------------------------------------------------------
  // Generation (SSE stream)
  // -------------------------------------------------------------------

  /**
   * Start or resume dataset generation, streaming SSE progress events.
   *
   * Opens a POST request to the generation endpoint, reads the response as
   * an SSE stream, and dispatches each event to the appropriate log handler.
   * Disables action buttons while running and re-enables them on completion.
   *
   * @param resume - Whether to resume an existing partial generation.
   * @param overrideChecks - Whether to skip pre-flight quality gates.
   */
  public async runGeneration(resume = false, overrideChecks = false): Promise<void> {
    await syncScenario();

    const btn = document.getElementById('gen-run-btn') as HTMLButtonElement | null;
    const resumeBtn = document.getElementById('gen-resume-btn') as HTMLButtonElement | null;
    const pc = document.getElementById('progress-container');
    const pb = document.getElementById('progress-bar');
    const pl = document.getElementById('progress-log');

    if (!btn || !pc || !pb || !pl) return;

    btn.disabled = true;
    if (resumeBtn) resumeBtn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Generating...';
    pc.classList.remove('hidden');
    pl.innerHTML = '';
    pb.style.width = '0%';

    const addLog = this.createLogFn(pl);
    const url = this.buildGenerateUrl(resume, overrideChecks);

    try {
      const response = await fetch(url, { method: 'POST' });
      if (!response.body) {
        addLog('No response body received', 'error');
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const state: StreamState = { doneWork: 0, totalWork: 0 };
      for (const d of scenario.devices) state.totalWork += d.contacts.length;

      let streaming = true;
      while (streaming) {
        const chunk = await reader.read();
        if (chunk.done) break;
        const lines = decoder.decode(chunk.value, { stream: true }).split('\n');
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.substring(6)) as SseEvent;
            const abort = await this.processSseEvent(event, state, pb, addLog);
            if (abort) {
              streaming = false;
              break;
            }
          } catch {
            /* skip malformed SSE frames */
          }
        }
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      addLog(`Connection error: ${msg}`, 'error');
    }

    btn.disabled = false;
    btn.innerHTML = 'Generate Dataset';
    if (resumeBtn) resumeBtn.disabled = false;
    void this.checkGenerationProgress();
  }

  /**
   * Build the generation endpoint URL with optional query parameters.
   *
   * @param resume - Include `resume=true` query param.
   * @param overrideChecks - Include `override_checks=true` query param.
   * @returns The fully-qualified URL string.
   */
  private buildGenerateUrl(resume: boolean, overrideChecks: boolean): string {
    if (resume && overrideChecks) {
      return '/api/generate/run?resume=true&override_checks=true';
    }
    if (resume) return '/api/generate/run?resume=true';
    return '/api/generate/run';
  }

  // -------------------------------------------------------------------
  // SSE event dispatch
  // -------------------------------------------------------------------

  /**
   * Dispatch a single SSE event to the appropriate handler or inline log.
   *
   * @param event - Parsed SSE event payload.
   * @param state - Mutable stream progress counters.
   * @param pb - The progress-bar DOM element.
   * @param addLog - Log-line appender.
   * @returns `true` if the caller should abort the SSE loop (resume override).
   */
  private async processSseEvent(event: SseEvent, state: StreamState, pb: HTMLElement, addLog: LogFn): Promise<boolean> {
    switch (event.type) {
      case 'device_start':
        addLog(`Starting ${event.label ?? ''}${event.resuming ? ' (resuming)' : ''}`);
        break;
      case 'device_skipped': {
        addLog(`Skipped ${event.label ?? ''} \u2014 ${event.reason ?? ''}`, 'success');
        const contacts = scenario.devices.find((d) => d.device_label === event.label)?.contacts.length ?? 0;
        state.doneWork += contacts;
        this.updateProgressBar(pb, state);
        break;
      }
      case 'contact_start':
        addLog(`  Generating: ${event.name ?? ''}`);
        break;
      case 'contact_skipped':
        state.doneWork++;
        this.updateProgressBar(pb, state);
        addLog(`  Skipped: ${event.name ?? ''} (already done)`, 'success');
        break;
      case 'contact_done':
        state.doneWork++;
        this.updateProgressBar(pb, state);
        addLog(`  Done: ${String(event.messages ?? 0)} messages`, 'success');
        break;
      case 'contact_error':
        addLog(`  Error: ${event.name ?? ''} \u2014 ${event.error ?? ''}`, 'error');
        break;
      case 'quota_exhausted':
        addLog('API quota exhausted! Progress saved. Use Resume to continue.', 'error');
        break;
      case 'group_start':
        addLog(`  Group chat: ${event.group_name ?? ''}`);
        break;
      case 'group_done':
        addLog(`  Group done: ${String(event.messages ?? 0)} messages`, 'success');
        break;
      case 'pairwise_done':
        addLog(
          `  Pairwise direct threads created: ${String(event.threads ?? 0)} (${event.group_name ?? ''})`,
          'success'
        );
        break;
      case 'spam_done':
        addLog(`  Spam injected: ${String(event.threads ?? 0)} threads`, 'success');
        break;
      case 'quality_warning':
        this.logQualityWarning(event, addLog);
        break;
      case 'device_done': {
        const tag = event.partial ? ' (partial)' : '';
        addLog(`Saved: ${event.path ?? ''}${tag}`, 'success');
        await this.checkGenerationProgress();
        break;
      }
      case 'device_event_validation':
        this.logEventValidation(event, addLog);
        break;
      case 'resume_blocked':
        return this.handleResumeBlocked(event, addLog);
      case 'stopped':
        this.logStopped(event, addLog);
        break;
      case 'complete':
        await this.logComplete(event, pb, addLog);
        break;
      case 'error':
        addLog(`Error: ${event.message ?? ''}`, 'error');
        break;
    }
    return false;
  }

  // -------------------------------------------------------------------
  // Complex SSE handlers (extracted to stay under 75-line limit)
  // -------------------------------------------------------------------

  /**
   * Log a quality-warning SSE event.
   *
   * @param event - The parsed SSE event.
   * @param addLog - Log-line appender.
   */
  private logQualityWarning(event: SseEvent, addLog: LogFn): void {
    const entity = event.entity_id ? ` [${event.entity_id}]` : '';
    const check = event.check_id ? ` (${event.check_id})` : '';
    addLog(`Quality ${(event.severity ?? 'warning').toUpperCase()}${check}${entity}: ${event.message ?? ''}`, 'error');
    if (event.suggestion) addLog(`  Suggestion: ${event.suggestion}`);
  }

  /**
   * Log a device-event-validation SSE event including repair details.
   *
   * @param event - The parsed SSE event.
   * @param addLog - Log-line appender.
   */
  private logEventValidation(event: SseEvent, addLog: LogFn): void {
    const status = event.passed ? 'PASSED' : 'FAILED';
    addLog(
      `  Event validation ${status} (${event.label ?? ''}): ` +
        `${String(event.issues_before ?? 0)} critical before \u2192 ${String(event.issues_after ?? 0)} ` +
        `after, ${String(event.warnings_after ?? 0)} warnings, ` +
        `${String(event.repaired_threads ?? 0)} thread(s) repaired`,
      event.passed ? 'success' : 'error'
    );
    if (event.repair_details?.length) {
      for (const rd of event.repair_details) {
        const outcomeTag = rd.outcome === 'regenerated' ? 'success' : 'error';
        addLog(`    Repair: ${rd.thread ?? ''} \u2192 ${rd.outcome}${rd.reason ? ` (${rd.reason})` : ''}`, outcomeTag);
        for (const issue of rd.issues ?? []) {
          addLog(`      Issue: ${issue}`);
        }
      }
    }
    if (!event.passed && event.remaining_findings?.length) {
      addLog('    Unresolved issues:', 'error');
      for (const f of event.remaining_findings) {
        addLog(
          `      ${(f.severity ?? '').toUpperCase()}: ${f.message}`,
          f.severity === 'critical' ? 'error' : undefined
        );
        if (f.suggestion) addLog(`        Fix: ${f.suggestion}`);
      }
    }
  }

  /**
   * Handle a `resume_blocked` SSE event, optionally prompting for override.
   *
   * If the user confirms the override, a new generation run is started with
   * `overrideChecks=true` and the method signals the caller to abort the
   * current SSE loop.
   *
   * @param event - The parsed SSE event.
   * @param addLog - Log-line appender.
   * @returns `true` if the current SSE loop should be aborted.
   */
  private async handleResumeBlocked(event: SseEvent, addLog: LogFn): Promise<boolean> {
    addLog('Resume pre-check blocked continuation.', 'error');
    addLog(event.message ?? '', 'error');
    if (event.quality_report_path) {
      addLog(`Proof report: ${event.quality_report_path}`, 'success');
    }
    if (event.top_findings?.length) {
      for (const f of event.top_findings) {
        const sev = (f.severity ?? 'warning').toUpperCase();
        addLog(`${sev}: ${f.message ?? ''}`, sev === 'CRITICAL' ? 'error' : undefined);
      }
    }
    const proceed = confirm('Resume quality gate found mismatches. Continue anyway with override?');
    if (proceed) {
      addLog('Override approved. Resuming with override checks...', 'success');
      await this.runGeneration(true, true);
      return true;
    }
    return false;
  }

  /**
   * Log a `stopped` SSE event including blocking findings and quality summary.
   *
   * @param event - The parsed SSE event.
   * @param addLog - Log-line appender.
   */
  private logStopped(event: SseEvent, addLog: LogFn): void {
    addLog(event.message ?? '', 'error');
    if (event.blocking_findings?.length) {
      addLog('Blocking findings that prevented completion:', 'error');
      for (const f of event.blocking_findings) {
        const entity = f.entity_id ? ` [${f.entity_id}]` : '';
        addLog(`  CRITICAL${entity}: ${f.message}`, 'error');
        if (f.suggestion) addLog(`    Suggested fix: ${f.suggestion}`);
      }
    }
    if (event.quality_summary) {
      const qs = event.quality_summary;
      const scorePct = Math.round((qs.overall_score ?? 0) * 100);
      addLog(
        `Quality: ${String(scorePct)}% \u2014 ${String(qs.critical_count ?? 0)} critical, ${String(qs.warning_count ?? 0)} warnings`
      );
    }
    addLog('Tip: Run "AI Quality Fix" to auto-repair timeline issues, then Resume.', 'success');
    if (event.run_log_path) {
      addLog(`Run log: ${event.run_log_path}`, 'success');
    }
    if (event.quality_report_path) {
      addLog(`Quality report: ${event.quality_report_path}`, 'success');
    }
  }

  /**
   * Log a `complete` SSE event and finalise the progress bar.
   *
   * @param event - The parsed SSE event.
   * @param pb - The progress-bar DOM element.
   * @param addLog - Log-line appender.
   */
  private async logComplete(event: SseEvent, pb: HTMLElement, addLog: LogFn): Promise<void> {
    addLog('Generation complete!', 'success');
    if (event.quality_summary) {
      const qs = event.quality_summary;
      const scorePct = Math.round((qs.overall_score ?? 0) * 100);
      addLog(`Quality summary: ${String(scorePct)}% (${qs.overall_severity ?? 'ok'})`, 'success');
      addLog(
        `Findings: ${String(qs.findings_total ?? 0)} total, ${String(qs.critical_count ?? 0)} critical, ${String(qs.warning_count ?? 0)} warning`
      );
    }
    if (event.quality_report_path) {
      addLog(`Quality report saved: ${event.quality_report_path}`, 'success');
    }
    if (event.run_log_path) {
      addLog(`Run log saved: ${event.run_log_path}`, 'success');
    }
    pb.style.width = '100%';
    await this.checkGenerationProgress();
  }
}

// ---------------------------------------------------------------------------
// Singleton + convenience exports
// ---------------------------------------------------------------------------

/** Default singleton used by the convenience exports. */
const defaultManager = new GenerateManager();

/**
 * Render the generation-tab summary grid and populate settings form fields.
 * Delegates to the default {@link GenerateManager} singleton.
 */
export function renderGenerate(): void {
  defaultManager.renderGenerate();
}

/**
 * Read settings form values and write them into the scenario.
 * Delegates to the default {@link GenerateManager} singleton.
 */
export function updateSettings(): void {
  defaultManager.updateSettings();
}

/**
 * Start or resume dataset generation via SSE streaming.
 * Delegates to the default {@link GenerateManager} singleton.
 *
 * @param resume - Whether to resume an existing partial generation.
 * @param overrideChecks - Whether to skip pre-flight quality gates.
 * @returns Promise that resolves when generation completes or is aborted.
 */
export function runGeneration(resume = false, overrideChecks = false): Promise<void> {
  return defaultManager.runGeneration(resume, overrideChecks);
}

/**
 * Run the AI quality-fix workflow and log results.
 * Delegates to the default {@link GenerateManager} singleton.
 *
 * @returns Promise that resolves when the quality check completes.
 */
export function runQualityCheck(): Promise<void> {
  return defaultManager.runQualityCheck();
}

/**
 * Query the server for existing generation progress.
 * Delegates to the default {@link GenerateManager} singleton.
 *
 * @returns The progress response, or `null` on error.
 */
export function checkGenerationProgress(): Promise<ProgressResponse | null> {
  return defaultManager.checkGenerationProgress();
}
