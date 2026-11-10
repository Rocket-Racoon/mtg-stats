import { describe, expect, it } from 'vitest';
import {
  FAN_CONTENT_DISCLAIMER,
  FAN_CONTENT_DISCLAIMER_TEXT,
  FAN_CONTENT_POLICY_URL,
  SCRYFALL_CREDIT,
  SCRYFALL_CREDIT_TEXT,
  SCRYFALL_IMAGES_NOTE,
  SCRYFALL_URL,
  attributionToText,
} from './index';

// Anti-drift contract (S-1.6.6 AC3): web and mobile render this wording. Changing it here is a
// deliberate, reviewed act.
describe('attribution wording', () => {
  it('pins the exact disclaimer text', () => {
    expect(FAN_CONTENT_DISCLAIMER_TEXT).toBe(
      'MTG Stats is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.',
    );
  });

  it('pins the exact Scryfall credit', () => {
    expect(SCRYFALL_CREDIT_TEXT).toBe('Card data and images courtesy of Scryfall');
  });

  it('pins the URLs', () => {
    expect(FAN_CONTENT_POLICY_URL).toBe('https://company.wizards.com/en/legal/fancontentpolicy');
    expect(SCRYFALL_URL).toBe('https://scryfall.com');
  });

  it('links the policy and Scryfall with https hrefs and descriptive labels', () => {
    const links = [...FAN_CONTENT_DISCLAIMER, ...SCRYFALL_CREDIT].filter((s) => s.kind === 'link');
    expect(links.map((l) => [l.label, l.href])).toEqual([
      ['Fan Content Policy', FAN_CONTENT_POLICY_URL],
      ['Scryfall', SCRYFALL_URL],
    ]);
    for (const l of links) expect(l.href).toMatch(/^https:\/\//);
  });

  it('says images come from Scryfall', () => {
    expect(SCRYFALL_IMAGES_NOTE).toMatch(/images are loaded directly from Scryfall/);
  });

  it('attributionToText joins text and link labels', () => {
    expect(
      attributionToText([
        { kind: 'text', text: 'a ' },
        { kind: 'link', label: 'b', href: 'https://x.test' },
      ]),
    ).toBe('a b');
  });
});
