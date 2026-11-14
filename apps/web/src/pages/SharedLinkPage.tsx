import { useParams } from 'react-router';
import { OpenInAppButton, type LinkKind } from '../ui/OpenInAppButton';
import { PlaceholderPage } from './Placeholder';

/**
 * Public landing page for /invite/*, /reset/* and /join/* (S-1.6.7). A placeholder until S-2.1.6,
 * S-2.1.9 and S-6.2.3; it already offers "Open in app" on Android browsers. The router decodes the
 * splat and the button re-encodes it.
 */
export function SharedLinkPage({ kind, title }: { kind: LinkKind; title: string }) {
  const value = useParams()['*'] ?? '';
  return (
    <PlaceholderPage title={title}>
      <p>This page is not built yet.</p>
      <OpenInAppButton kind={kind} value={value} />
    </PlaceholderPage>
  );
}
