/**
 * Centralized DOM element references.
 * Avoids scattered getElementById calls throughout the codebase.
 */

/**
 * Retrieve a DOM element by its ID, throwing if not found.
 *
 * @param id - The element's ID attribute value.
 * @returns The element cast to the requested type.
 * @throws Error if no element with the given ID exists in the document.
 */
export function getElement<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) {
    throw new Error(`DOM element #${id} not found`);
  }
  return el as T;
}

/**
 * Query the DOM for a single element matching a CSS selector.
 *
 * @param selector - A valid CSS selector string.
 * @returns The first matching element cast to the requested type, or null.
 */
export function queryElement<T extends HTMLElement>(selector: string): T | null {
  return document.querySelector<T>(selector);
}
