export const PACKAGE_NAME = '@mtg-stats/game-core';

export * from './types';
export * from './canonical-json';
export { initialState } from './state';
export { apply, reduce, replay } from './reducer';
export type { ReplayResult } from './reducer';
