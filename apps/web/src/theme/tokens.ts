/**
 * Single source of truth for the colour tokens (S-1.6.5).
 * `theme.css` mirrors these values as CSS custom properties (`fooBar` -> `--foo-bar`);
 * tests/theme-css.test.ts fails if the two drift and checks the ratio of every pair in
 * CONTRAST_PAIRS in both themes (helper: tests/contrast.ts).
 */
export const THEMES = ['light', 'dark'] as const;
export type Theme = (typeof THEMES)[number];

export const TOKEN_NAMES = [
  'fg',
  'bg',
  'mutedBg',
  'mutedFg',
  'hoverBg',
  'link',
  'accent',
  'onAccent',
  'focus',
  'focusStrong',
  'focusHalo',
  'border',
] as const;
export type TokenName = (typeof TOKEN_NAMES)[number];

export const TOKENS: Record<Theme, Record<TokenName, string>> = {
  light: {
    fg: '#1a1a1a',
    bg: '#ffffff',
    mutedBg: '#f1f2f4',
    mutedFg: '#505763',
    hoverBg: '#e2e4e8',
    link: '#4b3bd1',
    accent: '#5646e0',
    onAccent: '#ffffff',
    focus: '#0b57d0',
    focusStrong: '#000000',
    focusHalo: '#ffffff',
    border: '#d0d3d9',
  },
  dark: {
    fg: '#ececef',
    bg: '#121316',
    mutedBg: '#1c1e23',
    mutedFg: '#a3a9b5',
    hoverBg: '#2a2d34',
    link: '#b1a9ff',
    accent: '#5646e0',
    onAccent: '#ffffff',
    focus: '#7fb0ff',
    focusStrong: '#ffffff',
    focusHalo: '#000000',
    border: '#3a3e47',
  },
};

export interface ContrastPair {
  fg: TokenName;
  bg: TokenName;
  /** Minimum ratio: 4.5 for text, 3 for large text and non-text UI (focus ring). */
  min: number;
  what: string;
}

export const CONTRAST_PAIRS: readonly ContrastPair[] = [
  { fg: 'fg', bg: 'bg', min: 4.5, what: 'body text' },
  { fg: 'fg', bg: 'mutedBg', min: 4.5, what: 'nav and footer text' },
  { fg: 'fg', bg: 'hoverBg', min: 4.5, what: 'nav link text on hover' },
  { fg: 'mutedFg', bg: 'bg', min: 4.5, what: 'muted text' },
  { fg: 'mutedFg', bg: 'mutedBg', min: 4.5, what: 'muted text on nav/footer' },
  { fg: 'link', bg: 'bg', min: 4.5, what: 'link text' },
  { fg: 'link', bg: 'mutedBg', min: 4.5, what: 'link text on nav/footer' },
  { fg: 'onAccent', bg: 'accent', min: 4.5, what: 'active nav link / primary button text' },
  { fg: 'focus', bg: 'bg', min: 3, what: 'focus ring on page' },
  { fg: 'focus', bg: 'mutedBg', min: 3, what: 'focus ring on nav/footer' },
  { fg: 'focusStrong', bg: 'accent', min: 3, what: 'focus ring on active nav link' },
  { fg: 'focusStrong', bg: 'mutedBg', min: 3, what: 'focus ring (active link) on nav surround' },
];

export const kebab = (name: string): string => name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
