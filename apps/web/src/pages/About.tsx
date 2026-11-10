import { SCRYFALL_IMAGES_NOTE } from '@mtg-stats/card-utils';
import { AttributionNotices } from '../Attribution';
import { env } from '../env';
import { PlaceholderPage } from './Placeholder';

/** Public page (no sign-in): app version, legal notice and the Scryfall credit (S-1.6.6 AC2). */
export function AboutPage() {
  return (
    <PlaceholderPage title="About">
      <p>
        MTG Stats version <span data-testid="app-version">{env.appVersion}</span>
      </p>
      <div className="attribution">
        <AttributionNotices />
      </div>
      <p>{SCRYFALL_IMAGES_NOTE}</p>
    </PlaceholderPage>
  );
}
