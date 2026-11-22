import { fail, field, isRunning, isSeatId, payloadOf, seatRefsError } from './handler-utils';
import type { Handler } from './handler-utils';

/*
 * Handlers of S-6.1.5 (CONTRACT.md 4.7.4 and 4.7.5): the monarch and the initiative. The hand-off
 * when the holder is eliminated is in the elimination routine (`elimination.ts`, 4.5.2). Pure:
 * nothing here reads a clock, randomness or I/O (section 7).
 */

function setHolder(key: 'monarch' | 'initiative'): Handler {
  return (state, event, seq) => {
    const seat = field(payloadOf(event), 'seat');
    if (!isSeatId(seat)) return fail('invalid_payload');
    if (!isRunning(state)) return fail('invalid_phase');
    const refs = seatRefsError(state, [seat]);
    if (refs !== null) return fail(refs);
    return { ok: true, state: { ...state, [key]: seat, last_seq: seq } };
  };
}

export const monarchSet: Handler = setHolder('monarch');
export const initiativeSet: Handler = setHolder('initiative');
