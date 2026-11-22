// A tool once turned the CSS escape \2713 into the bytes C2 B9 33 ("superscript one" and "3"),
// which every user saw as "Crew 13" on the current option. App.css must stay plain ASCII, and
// the check mark must be the real escape.
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const css = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), '../src/App.css'),
  'utf8',
);

describe('App.css bytes', () => {
  it('contains only ASCII characters', () => {
    const bad = [...css].filter(
      (c) => c.charCodeAt(0) > 126 || (c.charCodeAt(0) < 32 && !'\n'.includes(c)),
    );
    expect(bad.map((c) => `U+${c.charCodeAt(0).toString(16)}`)).toEqual([]);
  });

  it('never contains the mangled check mark', () => {
    expect(css).not.toContain('¹');
  });

  it('marks the selected option with the real CSS escape and no alternative text', () => {
    const backslash = String.fromCharCode(92); // written this way so no tool can eat it
    expect(css).toContain(`content: ' ${backslash}2713' / '';`);
  });
});
