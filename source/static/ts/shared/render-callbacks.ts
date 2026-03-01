/**
 * Callback registry to break circular dependencies between feature managers.
 *
 * Modules register their render functions here at initialization time;
 * other modules consume them via {@link getCallback} without creating
 * direct imports. This eliminates the EventManager ↔ LinkChartManager
 * circular dependency.
 * @module
 */

/** Callback with no arguments and no return value. */
type VoidCallback = () => void;

/** Callback for toggling event participant selection. */
type ParticipantCallback = (eventIndex: number, deviceId: string, contactId: string) => void;

/** Known callback names and their corresponding function signatures. */
interface CallbackRegistry {
  renderLinkChart?: VoidCallback;
  renderEvents?: VoidCallback;
  renderGroupChats?: VoidCallback;
  toggleEventParticipant?: ParticipantCallback;
}

/** Module-level registry storing callback implementations. */
const callbacks: CallbackRegistry = {};

/**
 * Register a callback function under a named key.
 *
 * Modules call this at initialization time to expose their render
 * or action functions to other modules without creating direct imports.
 *
 * @param name - The registry key identifying the callback.
 * @param fn - The function implementation to register.
 */
export function registerCallback(name: keyof CallbackRegistry, fn: VoidCallback | ParticipantCallback): void {
  (callbacks as Record<string, unknown>)[name] = fn;
}

/**
 * Retrieve a previously registered callback by name.
 *
 * Returns `undefined` if no callback has been registered under the
 * given name. Callers should use optional chaining for safe invocation.
 *
 * @typeParam K - The registry key to look up.
 * @param name - The registry key identifying the callback.
 * @returns The registered callback, or `undefined`.
 */
export function getCallback<K extends keyof CallbackRegistry>(name: K): NonNullable<CallbackRegistry[K]> | undefined {
  return callbacks[name] as NonNullable<CallbackRegistry[K]> | undefined;
}
