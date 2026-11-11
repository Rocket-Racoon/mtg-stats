import { useEffect, useRef } from 'react';
import { errorMessage, errorRequestId } from './message';

/** Shared by the React ErrorBoundary and the router's root errorElement. */
export function ErrorFallback({ error }: { error: unknown }) {
  const requestId = errorRequestId(error);
  const heading = useRef<HTMLHeadingElement>(null);
  // Land keyboard and screen-reader users on the fallback.
  useEffect(() => {
    heading.current?.focus();
  }, []);
  return (
    <div className="error-fallback" role="alert">
      <h1 ref={heading} tabIndex={-1}>
        Something went wrong
      </h1>
      <p>{errorMessage(error)}</p>
      {requestId !== null && <p>Request ID: {requestId}</p>}
      <button
        type="button"
        onClick={() => {
          window.location.reload();
        }}
      >
        Reload
      </button>
    </div>
  );
}
