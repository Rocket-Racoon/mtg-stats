import { useId } from 'react';
import {
  FAN_CONTENT_DISCLAIMER,
  SCRYFALL_CREDIT,
  type AttributionSegment,
} from '@mtg-stats/card-utils';

/**
 * Renders shared notice segments (wording lives in packages/card-utils, S-1.6.6 AC3).
 *
 * External links open in a new tab (`rel="noopener noreferrer"`): this is an app with live game
 * state, and a same-tab visit to wizards.com or scryfall.com would tear the session down. The
 * new-tab behaviour is announced through `aria-describedby`, so the accessible name stays the
 * link label ("Fan Content Policy"), not "click here".
 */
export function AttributionText({ segments }: { segments: readonly AttributionSegment[] }) {
  const hintId = useId();
  return (
    <>
      {segments.map((s) =>
        s.kind === 'text' ? (
          s.text
        ) : (
          <a
            key={s.href}
            href={s.href}
            target="_blank"
            rel="noopener noreferrer"
            aria-describedby={hintId}
          >
            {s.label}
          </a>
        ),
      )}
      <span id={hintId} hidden>
        Opens in a new tab
      </span>
    </>
  );
}

/** The two notices, in the same markup for the footer and /about. */
export function AttributionNotices() {
  return (
    <>
      <p>
        <AttributionText segments={FAN_CONTENT_DISCLAIMER} />
      </p>
      <p>
        <AttributionText segments={SCRYFALL_CREDIT} />
      </p>
    </>
  );
}
