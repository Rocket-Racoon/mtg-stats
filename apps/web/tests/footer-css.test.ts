// @vitest-environment node
// S-1.6.6 AC4, static over the CSS. jsdom cannot lay out, so this only asserts that nothing in the
// footer's CSS prevents wrapping. The real 360px "no horizontal scroll" measurement is the
// real-browser check of S-1.8.5 (Playwright) and MANUAL until then.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const css = readFileSync(fileURLToPath(new URL('../src/App.css', import.meta.url)), 'utf8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
);

const body = (selector: string): string => {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = new RegExp(`(?:^|\\})\\s*${esc}\\s*\\{([^}]*)\\}`, 'm').exec(css);
  if (!m) throw new Error(`no rule for ${selector}`);
  return m[1] ?? '';
};

describe('footer text wraps at narrow widths (S-1.6.6 AC4)', () => {
  it('never forbids wrapping anywhere in the stylesheet', () => {
    expect(css).not.toMatch(/white-space:\s*(nowrap|pre)\b/);
  });

  it('the footer wraps its children and the notice breaks long words and URLs', () => {
    expect(body('.shell-footer')).toMatch(/flex-wrap:\s*wrap/);
    expect(body('.attribution')).toMatch(/overflow-wrap:\s*(anywhere|break-word)/);
    expect(body('.attribution')).toMatch(/min-width:\s*0/);
  });

  it('the notice has no fixed width that could overflow a 360px viewport', () => {
    expect(body('.attribution')).not.toMatch(/(^|[;\s])(width|min-width)\s*:\s*[1-9]/);
    expect(body('.shell-footer')).not.toMatch(/(^|[;\s])(width|min-width)\s*:\s*[1-9]/);
  });

  it('the footer text colour is --muted-fg on --muted-bg (a checked pair)', () => {
    expect(body('.shell-footer')).toMatch(/color:\s*var\(--muted-fg\)/);
    expect(body('.shell-footer')).toMatch(/background:\s*var\(--muted-bg\)/);
  });
});
