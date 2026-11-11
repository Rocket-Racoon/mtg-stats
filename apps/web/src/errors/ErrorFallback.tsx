import { errorMessage, errorRequestId } from './message';

/** Shared by the React ErrorBoundary and the router's root errorElement. */
export function ErrorFallback({ error }: { error: unknown }) {
  const requestId = errorRequestId(error);
  return (
    <div className="error-fallback" role="alert">
      <h1>Something went wrong</h1>
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
