import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  ABANDON_REASONS,
  COUNTERS,
  CONTROL_MODES,
  ELIMINATION_REASONS,
  EVENT_TYPES,
  PHASES,
  REDUCER_ERROR_CODES,
  SERVER_REJECT_CODES,
  SUPPORTED_VERSIONS,
  TRUST_MODES,
  VALUE_SET_FIELDS,
  WIN_CONDITIONS,
} from './types';

const contract = readFileSync(new URL('../CONTRACT.md', import.meta.url), 'utf8');

/** First cell of each table row that sits under the given heading, taken from `code` spans. */
function firstCells(heading: string): string[] {
  const start = contract.indexOf(`\n${heading}\n`);
  if (start < 0) throw new Error(`CONTRACT.md has no heading ${heading}`);
  const rest = contract.slice(start + heading.length + 2);
  const end = rest.search(/\n#{1,3} /);
  const section = end < 0 ? rest : rest.slice(0, end);
  return [...section.matchAll(/^\| `([a-z_]+)` *\|/gm)].map((m) => m[1]!);
}

describe('CONTRACT.md stays in sync with the TS types', () => {
  it('lists the event registry exactly, in order', () => {
    expect(firstCells('## 4. Event registry')).toEqual([...EVENT_TYPES]);
  });

  it('lists the reducer error codes exactly, in order', () => {
    expect(firstCells('### 6.2 Reducer error codes')).toEqual([...REDUCER_ERROR_CODES]);
  });

  it('lists the server-only reject codes exactly', () => {
    expect(firstCells('### 6.3 Server-only reject codes')).toEqual([...SERVER_REJECT_CODES]);
  });

  it.each([
    ['phase', PHASES],
    ['trust_mode', TRUST_MODES],
    ['control_mode', CONTROL_MODES],
    ['win_condition', WIN_CONDITIONS],
    ['elimination reason', ELIMINATION_REASONS],
    ['counter', COUNTERS],
    ['value_set field', VALUE_SET_FIELDS],
    ['abandon reason', ABANDON_REASONS],
  ])('lists every %s value', (_name, values) => {
    for (const v of values) expect(contract).toContain(`\`${v}\``);
  });

  it('names the supported schema versions', () => {
    expect(SUPPORTED_VERSIONS).toEqual([1]);
    expect(contract).toContain('`v` = 1');
  });
});
