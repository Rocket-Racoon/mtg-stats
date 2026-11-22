import { useId, type ReactNode } from 'react';
import type { PlaygroupRole } from '@mtg-stats/api-client';
import { can, type PlaygroupAction } from './permissions';

interface ActionSlotProps {
  action: PlaygroupAction;
  /** The caller's role in the playgroup shown. */
  role: PlaygroupRole | null | undefined;
  /** Section heading, one per capability. */
  title: string;
  /** What the owning story renders. Until that story lands, a short "coming soon" note. */
  children?: ReactNode;
}

/**
 * A named place on the playgroup home for one capability (S-2.4.5). It renders nothing at all
 * when the role may not do `action` (hidden, not disabled), so the visibility rule lives in
 * `can()` only. The stories that build the invite panel, role changes, ownership transfer, guest
 * dialogs and house rules put their UI in `children` of the matching slot.
 */
export function ActionSlot({ action, role, title, children }: ActionSlotProps) {
  const headingId = useId();
  if (!can(role, action)) return null;
  return (
    <section aria-labelledby={headingId} data-slot={action}>
      <h4 id={headingId}>{title}</h4>
      {children ?? <p>Coming soon.</p>}
    </section>
  );
}
