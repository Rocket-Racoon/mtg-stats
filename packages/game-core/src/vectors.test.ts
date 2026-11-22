import { fileURLToPath } from 'node:url';
import { describe, it } from 'vitest';
import { EVENT_TYPES } from './types';
import { loadVectorFiles, runVectorFile } from './vector-loader.test.helpers';
import { checkCoverage, uncoveredMessage } from './vector-validator';

/**
 * The shared-vector runner (CONTRACT.md section 8, S-6.1.2): one test per vector file in
 * `vectors/`, named by the file, plus the event-type coverage check. The Python runner in
 * `apps/api` runs the same files.
 */

const files = loadVectorFiles(fileURLToPath(new URL('../vectors/', import.meta.url)));

describe('shared vectors', () => {
  it.each(files.map((f) => f.name))('%s', (name) => {
    runVectorFile(files.find((f) => f.name === name)!);
  });
});

describe('event-type coverage', () => {
  const uncovered = new Set(checkCoverage(files));
  it.each(EVENT_TYPES)('%s is exercised by a vector', (type) => {
    if (uncovered.has(uncoveredMessage(type))) throw new Error(uncoveredMessage(type));
  });
});
