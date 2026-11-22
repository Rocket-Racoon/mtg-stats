import { spawnSync } from 'node:child_process';
import {
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EVENT_TYPES } from './types';
import { loadVectorFiles, runVectorFile, type VectorFile } from './vector-loader.test.helpers';
import { checkCoverage } from './vector-validator';

/**
 * Behavior of the TypeScript vector runner on temporary directories (S-6.1.2, CONTRACT.md 8.3 to
 * 8.5). Acceptance criteria 1, 2 and 4: every `*.json` file is executed, a malformed file fails and
 * names the file and the field, a vector whose expectation is wrong for this implementation fails
 * and names the vector file, and the per-file test id is the file name. The Python twin is
 * `apps/api/app/games/tests/test_vector_runner_behavior.py`.
 */

const vectorsDir = fileURLToPath(new URL('../vectors/', import.meta.url));

interface DocCase {
  name: string;
  output?: string;
  error?: string;
  invalid?: unknown;
}
type Doc = Record<string, unknown> & {
  expect: { state?: Record<string, unknown>; error?: Record<string, unknown> };
  cases: DocCase[];
};

function real(name: string): Doc {
  return JSON.parse(readFileSync(join(vectorsDir, `${name}.json`), 'utf8')) as Doc;
}

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 's612-vectors-'));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

/** Writes `<name>.json` with its `name` field set to match and returns it as a loaded file. */
function put(name: string, doc: Doc): VectorFile {
  const text = JSON.stringify({ ...doc, name });
  writeFileSync(join(dir, `${name}.json`), text);
  return { name: `${name}.json`, text };
}

function failureOf(file: VectorFile): string {
  try {
    runVectorFile(file);
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
  throw new Error(`${file.name}: expected the runner to fail, but it passed`);
}

describe('loading (8.5)', () => {
  it('fails with the contract message when the directory is missing', () => {
    const missing = join(dir, 'nope');
    expect(() => loadVectorFiles(missing)).toThrow(`vectors directory not found: ${missing}`);
  });

  it('counts a file in place of the directory as not found', () => {
    const notDir = join(dir, 'vectors');
    writeFileSync(notDir, '{}');
    expect(() => loadVectorFiles(notDir)).toThrow(`vectors directory not found: ${notDir}`);
  });

  it('fails with the contract message when the directory is empty', () => {
    expect(() => loadVectorFiles(dir)).toThrow(`vectors directory has no .json files: ${dir}`);
  });

  it('fails as empty when the directory holds only other extensions', () => {
    for (const n of ['a.txt', 'b.json.bak', 'c.jsonl', 'json', 'd.json5']) {
      writeFileSync(join(dir, n), '{}');
    }
    expect(() => loadVectorFiles(dir)).toThrow(`vectors directory has no .json files: ${dir}`);
  });

  it('lists every json file sorted by name and ignores other entries', () => {
    for (const n of ['b.json', 'a.json', 'a-b.json', 'a10.json', 'a2.json', 'has space.json']) {
      writeFileSync(join(dir, n), '{}');
    }
    for (const n of ['notes.txt', 'x.json.bak', 'y.jsonl']) writeFileSync(join(dir, n), '{}');
    mkdirSync(join(dir, 'nested'));
    writeFileSync(join(dir, 'nested', 'deep.json'), '{}');
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual([
      'a-b.json',
      'a.json',
      'a10.json',
      'a2.json',
      'b.json',
      'has space.json',
    ]);
  });

  it('ignores a directory whose name ends in .json (only files are vector files)', () => {
    writeFileSync(join(dir, 'a.json'), '{}');
    mkdirSync(join(dir, 'trap.json'));
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual(['a.json']);
  });

  it('loads every json file of the real vectors directory', () => {
    const onDisk = readdirSync(vectorsDir)
      .filter((n) => n.endsWith('.json'))
      .sort();
    expect(loadVectorFiles(vectorsDir).map((f) => f.name)).toEqual(onDisk);
    expect(onDisk.length).toBeGreaterThanOrEqual(130);
  });
});

describe('per-file tests and coverage tests of the real suite (criterion 1)', () => {
  it('names one test per json file `shared vectors > <file>.json` and one per registered type', () => {
    const vitestBin = join(
      createRequire(import.meta.url).resolve('vitest/package.json'),
      '..',
      'vitest.mjs',
    );
    const run = spawnSync(
      process.execPath,
      [
        vitestBin,
        'run',
        'src/vectors.test.ts',
        '--reporter=json',
        `--outputFile=${join(dir, 'report.json')}`,
        '--pool=forks',
        '--maxWorkers=1',
      ],
      {
        cwd: fileURLToPath(new URL('..', import.meta.url)),
        encoding: 'utf8',
        env: { ...process.env, VITEST: '', VITEST_WORKER_ID: '', VITEST_POOL_ID: '' },
        timeout: 120000,
      },
    );
    const report = JSON.parse(readFileSync(join(dir, 'report.json'), 'utf8')) as {
      success: boolean;
      testResults: {
        assertionResults: {
          ancestorTitles: string[];
          title: string;
          fullName: string;
          status: string;
        }[];
      }[];
    };
    const tests = report.testResults.flatMap((r) => r.assertionResults);
    const perFile = tests.filter((t) => t.ancestorTitles.join(' > ') === 'shared vectors');
    const onDisk = readdirSync(vectorsDir)
      .filter((n) => n.endsWith('.json'))
      .sort();
    expect(perFile.map((t) => t.title)).toEqual(onDisk);
    expect(perFile.map((t) => t.fullName)).toEqual(onDisk.map((n) => `shared vectors ${n}`));
    const coverage = tests.filter((t) => t.ancestorTitles.join(' > ') === 'event-type coverage');
    expect(coverage.map((t) => t.title)).toEqual(
      EVENT_TYPES.map((t) => `${t} is exercised by a vector`),
    );
    expect(run.error).toBeUndefined();
    expect(tests.every((t) => t.status === 'passed')).toBe(true);
    expect(report.success).toBe(true);
  }, 150000);
});

describe('S-6.1.1 coverage that the old seed runner held', () => {
  it.each([
    'canonical-json',
    'initial-state',
    'reject-unknown-event-type',
    'reject-unsupported-version',
    'reject-unsupported-version-and-type',
  ])('the seed vector %s is still in the vectors directory', (name) => {
    expect(loadVectorFiles(vectorsDir).map((f) => f.name)).toContain(`${name}.json`);
  });

  it('every vector is format 1 and named after its file', () => {
    for (const f of loadVectorFiles(vectorsDir)) {
      const doc = JSON.parse(f.text) as Doc;
      expect(doc.format, f.name).toBe(1);
      expect(doc.name, f.name).toBe(f.name.replace(/\.json$/, ''));
      expect(['reducer', 'canonical_json'], f.name).toContain(doc.kind);
    }
  });

  it('every reducer vector expects exactly one of state or error', () => {
    for (const f of loadVectorFiles(vectorsDir)) {
      const doc = JSON.parse(f.text) as Doc;
      if (doc.kind === 'reducer') {
        expect(Object.keys(doc.expect), f.name).toHaveLength(1);
        expect(['state', 'error'], f.name).toContain(Object.keys(doc.expect)[0]);
      }
    }
  });

  it('the shipped vectors exercise every registered event type', () => {
    expect(checkCoverage(loadVectorFiles(vectorsDir))).toEqual([]);
  });

  it('every shipped vector passes the TypeScript runner', () => {
    for (const f of loadVectorFiles(vectorsDir)) runVectorFile(f);
  });
});

describe('a malformed file fails by name (criterion 4)', () => {
  it('names the file and the field', () => {
    const doc = real('reject-unknown-event-type');
    doc.expect = { error: { index: 5, code: 'unknown_event_type' } };
    expect(failureOf(put('reject-broken', doc))).toBe(
      'reject-broken.json: expect.error.index must be less than the number of events (1)',
    );
  });

  it('reports every problem, one per line', () => {
    const doc = real('reject-unknown-event-type');
    delete doc.description;
    doc.format = 2;
    doc.extra = 1;
    expect(failureOf(put('reject-broken', doc)).split('\n')).toEqual([
      'reject-broken.json: format must be the integer 1',
      'reject-broken.json: description is required',
      'reject-broken.json: extra is not a known key',
    ]);
  });

  it('fails an unknown kind naming the file', () => {
    const doc = real('reject-unknown-event-type');
    doc.kind = 'reducers';
    const message = failureOf(put('reject-broken', doc));
    expect(message.startsWith('reject-broken.json: ')).toBe(true);
    expect(message).toContain('kind must be "reducer" or "canonical_json"');
  });

  it('fails invalid JSON naming the file', () => {
    const message = failureOf({ name: 'reject-broken.json', text: '{"kind": "reducer",' });
    expect(message.startsWith('reject-broken.json: invalid JSON: ')).toBe(true);
  });

  it('fails a file that starts with a byte order mark, loaded from disk', () => {
    const text = '﻿' + JSON.stringify({ ...real('reject-unknown-event-type'), name: 'reject-bom' });
    writeFileSync(join(dir, 'reject-bom.json'), text);
    const [file] = loadVectorFiles(dir);
    expect(failureOf(file!).startsWith('reject-bom.json: invalid JSON: ')).toBe(true);
  });

  it('fails a file holding invalid UTF-8 bytes, loaded from disk', () => {
    const doc = { ...real('reject-unknown-event-type'), name: 'reject-bytes', description: 'XX' };
    const bytes = Buffer.from(JSON.stringify(doc), 'utf8');
    const at = bytes.indexOf('XX');
    bytes[at] = 0xff;
    bytes[at + 1] = 0xfe;
    writeFileSync(join(dir, 'reject-bytes.json'), bytes);
    const [file] = loadVectorFiles(dir);
    expect(failureOf(file!).startsWith('reject-bytes.json: invalid JSON: ')).toBe(true);
  });

  it('keeps the valid files of a directory runnable beside a malformed one', () => {
    const good = put('accept-good', real('initial-state-empty-settings'));
    const bad = real('reject-unknown-event-type');
    bad.events = 'nope';
    const badFile = put('reject-bad', bad);
    expect(() => runVectorFile(good)).not.toThrow();
    expect(failureOf(badFile)).toMatch(/^reject-bad\.json: events must be an array/);
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual([
      'accept-good.json',
      'reject-bad.json',
    ]);
  });
});

describe('a wrong expectation fails by file name (criterion 2)', () => {
  it('passes correct copies of real vectors', () => {
    for (const name of [
      'reject-unknown-event-type',
      'accept-optimistic-then-confirmed-seq',
      'initial-state-empty-settings',
      'canonical-json-integers',
    ]) {
      expect(() => runVectorFile(put(`copy-${name}`, real(name)))).not.toThrow();
    }
  });

  it('fails a wrong error code and names the vector', () => {
    const doc = real('reject-unknown-event-type');
    doc.expect = { error: { index: 0, code: 'invalid_event' } };
    const message = failureOf(put('reject-wrong-code', doc));
    expect(message.startsWith('reject-wrong-code.json: ')).toBe(true);
    expect(message).toContain('invalid_event');
    expect(message).toContain('unknown_event_type');
  });

  it('fails a wrong error index and names the vector', () => {
    const doc = real('reject-out-of-order-repeat');
    expect(doc.expect.error!.index).toBe(1);
    doc.expect = { error: { index: 0, code: 'out_of_order' } };
    expect(failureOf(put('reject-wrong-index', doc)).startsWith('reject-wrong-index.json: ')).toBe(
      true,
    );
  });

  it('fails an accepted event where a rejection is expected', () => {
    const doc = real('accept-optimistic-then-confirmed-seq');
    doc.expect = { error: { index: 0, code: 'invalid_event' } };
    expect(
      failureOf(put('accept-but-expects-reject', doc)).startsWith(
        'accept-but-expects-reject.json: ',
      ),
    ).toBe(true);
  });

  it('fails a rejection where a state is expected', () => {
    const doc = real('reject-unknown-event-type');
    doc.expect = { state: real('initial-state-empty-settings').expect.state };
    const message = failureOf(put('reject-but-expects-state', doc));
    expect(message.startsWith('reject-but-expects-state.json: ')).toBe(true);
    expect(message).toContain('unknown_event_type');
  });

  it('fails a wrong final state and names the vector', () => {
    const doc = real('accept-optimistic-then-confirmed-seq');
    doc.expect.state!.last_seq = (doc.expect.state!.last_seq as number) + 1;
    const message = failureOf(put('accept-wrong-state', doc));
    expect(message.startsWith('accept-wrong-state.json: ')).toBe(true);
    expect(message).toContain('final state differs');
  });

  it('compares the final state as canonical JSON, not by key order', () => {
    const doc = real('initial-state-empty-settings');
    const state = doc.expect.state!;
    doc.expect.state = Object.fromEntries(
      Object.keys(state)
        .sort()
        .reverse()
        .map((k) => [k, state[k]]),
    );
    expect(() => runVectorFile(put('accept-reordered', doc))).not.toThrow();
  });

  it('fails a wrong canonical output naming the file and the case', () => {
    const doc = real('canonical-json-integers');
    const first = doc.cases[0]!;
    first.output = `${first.output}0`;
    expect(
      failureOf(put('canonical-wrong-output', doc)).startsWith(
        `canonical-wrong-output.json: case "${first.name}": `,
      ),
    ).toBe(true);
  });

  it('fails a wrong canonical error code naming the file and the case', () => {
    const doc = real('canonical-json-rejections');
    const c = doc.cases.find((x) => 'invalid' in x)!;
    c.error = c.error === 'unsupported_value' ? 'non_integer' : 'unsupported_value';
    expect(
      failureOf(put('canonical-wrong-error', doc)).startsWith(
        `canonical-wrong-error.json: case "${c.name}": `,
      ),
    ).toBe(true);
  });
});

describe('coverage on a directory (8.4)', () => {
  it('counts only the valid reducer vectors', () => {
    const covers = real('accept-all-registered-event-types');
    const good = put('covers-everything', covers);
    const broken = put('broken', { ...covers, events: 'nope' });
    expect(checkCoverage([good, broken])).toEqual([]);
    expect(checkCoverage([broken])).toEqual(EVENT_TYPES.map((t) => `uncovered event type: ${t}`));
  });

  it('lists every type in registry order when only canonical vectors exist', () => {
    const only = put('only-canonical', real('canonical-json-integers'));
    expect(checkCoverage([only])).toEqual(EVENT_TYPES.map((t) => `uncovered event type: ${t}`));
  });
});

describe('discovery of the S-6.1.2 review rules (8.5)', () => {
  it('ignores other spellings of the extension and fails as empty when nothing else is there', () => {
    for (const n of ['X.JSON', 'y.Json', 'z.jSON']) writeFileSync(join(dir, n), '{}');
    expect(() => loadVectorFiles(dir)).toThrow(`vectors directory has no .json files: ${dir}`);
    writeFileSync(join(dir, 'a.json'), '{}');
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual(['a.json']);
  });

  it('ignores directories and nested directories, even when named like a vector', () => {
    mkdirSync(join(dir, 'trap.json'));
    mkdirSync(join(dir, 'nested'));
    writeFileSync(join(dir, 'nested', 'deep.json'), '{}');
    expect(() => loadVectorFiles(dir)).toThrow(`vectors directory has no .json files: ${dir}`);
  });

  it('ignores symbolic links to files, to directories and dangling ones', (ctx) => {
    writeFileSync(join(dir, 'real.json'), '{}');
    mkdirSync(join(dir, 'target-dir'));
    try {
      symlinkSync(join(dir, 'real.json'), join(dir, 'file-link.json'));
      symlinkSync(join(dir, 'target-dir'), join(dir, 'dir-link.json'), 'dir');
      symlinkSync(join(dir, 'missing'), join(dir, 'dangling.json'));
    } catch {
      ctx.skip(); // symbolic links need a privilege on some systems
    }
    expect(loadVectorFiles(dir).map((f) => f.name)).toEqual(['real.json']);
  });

  it('counts only regular files towards the no-json-file failure', (ctx) => {
    writeFileSync(join(dir, 'real.txt'), '{}');
    try {
      symlinkSync(join(dir, 'real.txt'), join(dir, 'only-a-link.json'));
    } catch {
      ctx.skip();
    }
    expect(() => loadVectorFiles(dir)).toThrow(`vectors directory has no .json files: ${dir}`);
  });
});

describe('a canonical file reports every failing case (8.5)', () => {
  it('lists each failing case on its own line, in case order, and not the passing ones', () => {
    const doc = real('canonical-json-integers');
    expect(doc.cases.length).toBeGreaterThanOrEqual(4);
    doc.cases[0]!.output = `${doc.cases[0]!.output}x`;
    doc.cases[2]!.output = `${doc.cases[2]!.output}y`;
    const lines = failureOf(put('canonical-two-wrong', doc)).split('\n');
    expect(lines).toHaveLength(2);
    expect(lines[0]!.startsWith(`canonical-two-wrong.json: case "${doc.cases[0]!.name}": `)).toBe(
      true,
    );
    expect(lines[1]!.startsWith(`canonical-two-wrong.json: case "${doc.cases[2]!.name}": `)).toBe(
      true,
    );
  });

  it('reports a wrong error code and a wrong output of one file together', () => {
    const rejections = real('canonical-json-rejections');
    const bad = rejections.cases.find((c) => 'invalid' in c)!;
    bad.error = bad.error === 'unsupported_value' ? 'non_integer' : 'unsupported_value';
    const input = real('canonical-json-integers').cases[0]!;
    const doc = {
      ...rejections,
      cases: [bad, { ...input, name: 'second', output: `${input.output}z` }],
    } as Doc;
    const lines = failureOf(put('canonical-mixed-wrong', doc)).split('\n');
    expect(lines).toHaveLength(2);
    expect(lines[0]!.startsWith(`canonical-mixed-wrong.json: case "${bad.name}": `)).toBe(true);
    expect(lines[1]!.startsWith('canonical-mixed-wrong.json: case "second": ')).toBe(true);
  });
});

describe('a deep or broken file fails alone (8.3, 8.4)', () => {
  const nested = (levels: number): string => '['.repeat(levels) + ']'.repeat(levels);

  it('names a file nested more than 512 levels with the single depth message', () => {
    const deep = { name: 'deep.json', text: `{"kind":"reducer","x":${nested(520)}}` };
    expect(failureOf(deep)).toBe('deep.json: $ is nested more than 512 levels deep');
  });

  it('keeps the other files and the coverage check running beside deep and broken ones', () => {
    const covers = put('covers-everything', real('accept-all-registered-event-types'));
    const deep = put('deep-one', {
      ...real('accept-all-registered-event-types'),
      x: JSON.parse(nested(600)),
    });
    const broken = { name: 'broken.json', text: '{"kind":' };
    const badBytes = { name: 'bytes.json', text: '﻿{}' };
    const files = [covers, deep, broken, badBytes];
    const outcomes = files.map((f) => {
      try {
        runVectorFile(f);
        return 'passed';
      } catch (e) {
        return e instanceof Error ? e.message.split('\n')[0]! : 'non-error';
      }
    });
    expect(outcomes[0]).toBe('passed');
    expect(outcomes[1]).toBe('deep-one.json: $ is nested more than 512 levels deep');
    expect(outcomes[2]!.startsWith('broken.json: invalid JSON: ')).toBe(true);
    expect(outcomes[3]!.startsWith('bytes.json: invalid JSON: ')).toBe(true);
    expect(checkCoverage(files)).toEqual([]);
    expect(checkCoverage([deep, broken, badBytes])).toEqual(
      EVENT_TYPES.map((t) => `uncovered event type: ${t}`),
    );
  });

  it('accepts a vector nested to exactly 512 levels', () => {
    const doc = { ...real('initial-state-empty-settings'), x: JSON.parse(nested(511)) };
    // The top-level object is level 1, so a 511-level array inside it makes exactly 512.
    expect(() => runVectorFile(put('at-the-limit', doc as Doc))).toThrow(
      /^at-the-limit\.json: x is not a known key/,
    );
  });
});
