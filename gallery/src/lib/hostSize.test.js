import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { notifyHostHeight, notifyHostMeasured } from './hostSize.js';

function makeApp() {
  return { sendSizeChanged: vi.fn().mockResolvedValue(undefined) };
}

describe('notifyHostHeight', () => {
  it('sends a positive height to the host', () => {
    const app = makeApp();
    notifyHostHeight(app, 320);
    expect(app.sendSizeChanged).toHaveBeenCalledWith({ height: 320 });
  });

  it('is a no-op without an app', () => {
    expect(() => notifyHostHeight(null, 320)).not.toThrow();
  });

  it('does not send a zero or negative height', () => {
    const app = makeApp();
    notifyHostHeight(app, 0);
    notifyHostHeight(app, -5);
    expect(app.sendSizeChanged).not.toHaveBeenCalled();
  });

  it('swallows a rejected sendSizeChanged', async () => {
    const app = { sendSizeChanged: vi.fn().mockRejectedValue(new Error('gone')) };
    expect(() => notifyHostHeight(app, 100)).not.toThrow();
    await Promise.resolve();
  });
});

describe('notifyHostMeasured', () => {
  let raf;
  beforeEach(() => {
    // Run the rAF callback synchronously so the send is observable.
    raf = vi.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb) => {
      cb();
      return 1;
    });
  });
  afterEach(() => raf.mockRestore());

  it('measures scrollHeight on the next frame and reports it', () => {
    const app = makeApp();
    notifyHostMeasured(app, { scrollHeight: 512 });
    expect(app.sendSizeChanged).toHaveBeenCalledWith({ height: 512 });
  });

  it('is a no-op without an app or element', () => {
    const app = makeApp();
    notifyHostMeasured(app, null);
    notifyHostMeasured(null, { scrollHeight: 100 });
    expect(app.sendSizeChanged).not.toHaveBeenCalled();
  });

  it('does not send when the measured height is zero', () => {
    const app = makeApp();
    notifyHostMeasured(app, { scrollHeight: 0 });
    expect(app.sendSizeChanged).not.toHaveBeenCalled();
  });
});
