/**
 * Receive buffer shared by the browser transports: a reader loop pushes
 * chunks in, `Transport.read(max, timeoutMs)` takes them out. Never throws on
 * timeout; resolves with what arrived (possibly empty).
 */
export class ByteQueue {
  private chunks: Uint8Array[] = [];
  private size = 0;
  private waiters: (() => void)[] = [];
  private closedErr: Error | null = null;

  get length(): number {
    return this.size;
  }

  push(chunk: Uint8Array): void {
    if (chunk.length === 0) return;
    this.chunks.push(chunk);
    this.size += chunk.length;
    this.wake();
  }

  /** Take up to `max` bytes that are already buffered. */
  take(max: number): Uint8Array {
    const n = Math.min(max, this.size);
    const out = new Uint8Array(n);
    let off = 0;
    while (off < n) {
      const c = this.chunks[0]!;
      const want = n - off;
      if (c.length <= want) {
        out.set(c, off);
        off += c.length;
        this.chunks.shift();
      } else {
        out.set(c.subarray(0, want), off);
        this.chunks[0] = c.subarray(want);
        off += want;
      }
    }
    this.size -= n;
    return out;
  }

  /** Wait until at least one byte is buffered, the queue is failed, or the timeout runs out. */
  async read(max: number, timeoutMs: number): Promise<Uint8Array> {
    if (max <= 0) return new Uint8Array(0);
    if (this.size === 0 && !this.closedErr && timeoutMs > 0) {
      await new Promise<void>((resolve) => {
        let done = false;
        const finish = () => {
          if (done) return;
          done = true;
          clearTimeout(timer);
          this.waiters = this.waiters.filter((w) => w !== finish);
          resolve();
        };
        const timer = setTimeout(finish, timeoutMs);
        this.waiters.push(finish);
      });
    }
    if (this.size === 0 && this.closedErr) throw this.closedErr;
    return this.take(max);
  }

  clear(): void {
    this.chunks = [];
    this.size = 0;
  }

  /** The reader loop died (device unplugged): pending and future empty reads reject with `err`. */
  fail(err: Error): void {
    this.closedErr = err;
    this.wake();
  }

  reset(): void {
    this.clear();
    this.closedErr = null;
  }

  private wake(): void {
    const w = this.waiters;
    this.waiters = [];
    for (const f of w) f();
  }
}
