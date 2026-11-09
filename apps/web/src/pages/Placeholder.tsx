import { useEffect, type ReactNode } from 'react';

export function PlaceholderPage({ title, children }: { title: string; children?: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · MTG Stats`;
  }, [title]);
  return (
    <section aria-labelledby="page-title">
      <h2 id="page-title">{title}</h2>
      {children ?? <p>This page is not built yet.</p>}
    </section>
  );
}
