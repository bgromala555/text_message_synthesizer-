/**
 * HTML utility functions shared across the application.
 */

/** Escape HTML special characters to prevent XSS. */
export function esc(str: string): string {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}
