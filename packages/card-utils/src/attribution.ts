/**
 * Fan Content Policy disclaimer and Scryfall credit (S-1.6.6).
 *
 * The single source of the wording for web and mobile, so it cannot drift between apps. Each app
 * renders its own markup from this structured data. The tests pin the exact strings and URLs.
 */

export const FAN_CONTENT_POLICY_URL = 'https://company.wizards.com/en/legal/fancontentpolicy';
export const SCRYFALL_URL = 'https://scryfall.com';

/** A piece of notice text: plain, or a link (`label` is the visible and accessible text). */
export type AttributionSegment =
  | { readonly kind: 'text'; readonly text: string }
  | { readonly kind: 'link'; readonly label: string; readonly href: string };

/** The disclaimer as segments; concatenated they read as `FAN_CONTENT_DISCLAIMER_TEXT`. */
export const FAN_CONTENT_DISCLAIMER: readonly AttributionSegment[] = [
  { kind: 'text', text: 'MTG Stats is unofficial Fan Content permitted under the ' },
  { kind: 'link', label: 'Fan Content Policy', href: FAN_CONTENT_POLICY_URL },
  {
    kind: 'text',
    text: '. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.',
  },
];

/** The Scryfall credit as segments. */
export const SCRYFALL_CREDIT: readonly AttributionSegment[] = [
  { kind: 'text', text: 'Card data and images courtesy of ' },
  { kind: 'link', label: 'Scryfall', href: SCRYFALL_URL },
];

/** Shown on the About screen only. */
export const SCRYFALL_IMAGES_NOTE =
  'Card images are loaded directly from Scryfall; MTG Stats does not store or host them.';

/** Plain-text form of segments (for tests, accessibility labels and logs). */
export function attributionToText(segments: readonly AttributionSegment[]): string {
  return segments.map((s) => (s.kind === 'text' ? s.text : s.label)).join('');
}

export const FAN_CONTENT_DISCLAIMER_TEXT = attributionToText(FAN_CONTENT_DISCLAIMER);
export const SCRYFALL_CREDIT_TEXT = attributionToText(SCRYFALL_CREDIT);
