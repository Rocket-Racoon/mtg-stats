// Minimal in-test BroadcastChannel: instances with the same name hear each other's posts (never
// their own), delivered asynchronously like the real thing.
type Listener = (event: MessageEvent<unknown>) => void;

const registry = new Map<string, Set<FakeBroadcastChannel>>();

export class FakeBroadcastChannel {
  static posts: { name: string; data: unknown }[] = [];
  readonly listeners = new Set<Listener>();
  closed = false;

  constructor(readonly name: string) {
    const set = registry.get(name) ?? new Set<FakeBroadcastChannel>();
    set.add(this);
    registry.set(name, set);
  }

  postMessage(data: unknown): void {
    FakeBroadcastChannel.posts.push({ name: this.name, data });
    for (const other of registry.get(this.name) ?? []) {
      if (other === this || other.closed) continue;
      setTimeout(() => {
        if (other.closed) return;
        for (const l of [...other.listeners]) l({ data } as MessageEvent<unknown>);
      }, 0);
    }
  }

  addEventListener(type: string, listener: Listener): void {
    if (type === 'message') this.listeners.add(listener);
  }

  removeEventListener(type: string, listener: Listener): void {
    if (type === 'message') this.listeners.delete(listener);
  }

  close(): void {
    this.closed = true;
    registry.get(this.name)?.delete(this);
  }
}
