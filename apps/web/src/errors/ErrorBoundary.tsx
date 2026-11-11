import { Component, type ReactNode } from 'react';
import { ErrorFallback } from './ErrorFallback';

interface Props {
  children: ReactNode;
}
interface State {
  error: unknown;
  failed: boolean;
}

/** Catches render errors outside the router (route errors go to the root errorElement). */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, failed: false };

  static getDerivedStateFromError(error: unknown): State {
    return { error, failed: true };
  }

  render() {
    return this.state.failed ? <ErrorFallback error={this.state.error} /> : this.props.children;
  }
}
