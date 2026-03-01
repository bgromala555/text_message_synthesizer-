/**
 * Text analysis utilities for extracting structured data from free-text descriptions.
 *
 * Provides regex-based extraction functions used by the generation panel
 * to compute scenario statistics such as location counts.
 * @module
 */

/** Minimum character length for a location match to be considered valid. */
const LOCATION_MIN_LENGTH = 4;

/** Maximum character length for a location match to be considered valid. */
const LOCATION_MAX_LENGTH = 60;

/**
 * Extract location names from a free-text description.
 *
 * Uses two regex strategies:
 * 1. Contextual patterns that match capitalized phrases following location
 *    prepositions (e.g. "at Central Park", "near Times Square").
 * 2. A known-location dictionary of NYC neighborhoods and landmarks.
 *
 * Results are deduplicated and filtered to a 4–60 character range.
 *
 * @param text - The free-text string to scan for location references.
 * @returns A deduplicated array of extracted location names.
 */
export function extractLocations(text: string): string[] {
  if (!text) return [];

  const locations: string[] = [];

  // Regex instances are created per-call to avoid stale `lastIndex` from the `g` flag
  const patterns: RegExp[] = [
    /(?:at|near|in|on|from|visit(?:ing)?|went to|headed to|going to|arrives? at|meet(?:ing)? at)\s+([A-Z][A-Za-z\u2019']+(?:\s+[A-Z][A-Za-z\u2019']+){0,4})/g,
    /(?:Central|Prospect|Bryant|Washington Square|Madison Square|Union Square|Times Square|Brooklyn Bridge|Coney Island|Rockaway|Williamsburg|Bushwick|Astoria|Harlem|Chelsea|SoHo|TriBeCa|Midtown|Greenpoint|Dumbo|Flatbush|Park Slope|Bed-Stuy|Fort Greene|Crown Heights|Sunset Park|Bay Ridge|Red Hook|Cobble Hill|Boerum Hill|Carroll Gardens|Jackson Heights|Flushing|Long Island City|Upper East Side|Upper West Side|Lower East Side|East Village|West Village|Greenwich Village|Nolita|NoHo|Financial District|Battery Park|Hell's Kitchen|Murray Hill|Gramercy|Kips Bay|Flatiron|Koreatown)\b/gi,
  ];

  for (const re of patterns) {
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const loc = (m[1] ?? m[0]).trim().replace(/[.,;:!?]+$/, '');
      if (loc.length >= LOCATION_MIN_LENGTH && loc.length <= LOCATION_MAX_LENGTH) {
        locations.push(loc);
      }
    }
  }

  return [...new Set(locations)];
}
