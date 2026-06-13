import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor, fireEvent } from '@testing-library/svelte';
import IndexingProgress from './IndexingProgress.svelte';

function mockFetch(sessions) {
  let call = 0;
  global.fetch = vi.fn(() => {
    const data = Array.isArray(sessions) ? sessions[Math.min(call++, sessions.length - 1)] : sessions;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(data) });
  });
}

function runningSession(overrides = {}) {
  return { status: 'running', progress: 0, total: 1, message: '', summary: null, error: null, ...overrides };
}

function completedSession(summary = {}) {
  return { status: 'completed', progress: 1, total: 1, message: '', summary, error: null };
}

function failedSession(error = 'disk full') {
  return { status: 'failed', progress: 0, total: 1, message: '', summary: null, error };
}

const baseProps = { serverUrl: 'http://localhost', sessionId: 'abc', library: 'MyLib', partition: '' };

afterEach(() => vi.restoreAllMocks());

// ---------------------------------------------------------------------------

describe('IndexingProgress — running state', () => {
  it('shows progress bar while running', async () => {
    mockFetch(runningSession({ progress: 3, total: 10 }));
    const { container } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => {
      const bar = container.querySelector('progress');
      expect(bar).toBeTruthy();
      expect(bar.value).toBe(3);
      expect(bar.max).toBe(10);
    });
  });

  it('shows progress message when provided', async () => {
    mockFetch(runningSession({ message: 'Processing 2024/07' }));
    const { getByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByText('Processing 2024/07')).toBeTruthy());
  });

  it('shows RUNNING status chip', async () => {
    mockFetch(runningSession());
    const { getByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByText('running')).toBeTruthy());
  });
});

// ---------------------------------------------------------------------------

describe('IndexingProgress — completed state', () => {
  it('shows summary card with photo and sidecar counts', async () => {
    mockFetch(completedSession({
      totalPhotosProcessed: 1250,
      totalSidecarsCreated: 999,
      totalThumbnailsRebuilt: 5,
      totalErrors: 0,
      totalDurationMs: 45000,
    }));
    const { getByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => {
      expect(getByText(/1250/)).toBeTruthy();   // photos processed
      expect(getByText(/999/)).toBeTruthy();    // sidecars created
      expect(getByText(/45\.0s/)).toBeTruthy(); // duration in title
    });
  });

  it('does not show errors row when totalErrors is 0', async () => {
    mockFetch(completedSession({ totalErrors: 0, totalPhotosProcessed: 10, totalDurationMs: 1000 }));
    const { queryByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(queryByText(/Errors/)).toBeNull());
  });

  it('shows collapsible errors row when totalErrors > 0', async () => {
    mockFetch(completedSession({
      totalErrors: 2,
      topErrorDetails: ['file1.jpg: read error', 'file2.jpg: corrupt'],
      totalPhotosProcessed: 10,
      totalDurationMs: 1000,
    }));
    const { getByText, container } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => {
      // <details><summary> splits "Errors: " and <strong>2</strong> into separate nodes;
      // query the container element directly instead of by text.
      expect(container.querySelector('details')).toBeTruthy();
    });
    // error details are in the DOM even when collapsed
    await waitFor(() => expect(getByText('file1.jpg: read error')).toBeTruthy());
  });

  it('shows COMPLETED status chip', async () => {
    mockFetch(completedSession());
    const { getByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByText('completed')).toBeTruthy());
  });

  it('hides progress bar after completion', async () => {
    mockFetch(completedSession({ totalPhotosProcessed: 5, totalDurationMs: 500 }));
    const { container } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(container.querySelector('progress')).toBeNull());
  });
});

// ---------------------------------------------------------------------------

describe('IndexingProgress — failed state', () => {
  it('shows error card with the error message', async () => {
    mockFetch(failedSession('disk full'));
    const { getByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByText('disk full')).toBeTruthy());
  });

  it('shows FAILED status chip', async () => {
    mockFetch(failedSession());
    const { getByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByText('failed')).toBeTruthy());
  });
});

// ---------------------------------------------------------------------------

describe('IndexingProgress — MCP callbacks on completion', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('calls updateModelContext and sendMessage when mcpReady and completed', async () => {
    const updateModelContext = vi.fn().mockResolvedValue(undefined);
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    const mcpApp = { updateModelContext, sendMessage };

    mockFetch(completedSession({ totalPhotosProcessed: 42, totalDurationMs: 1000 }));
    render(IndexingProgress, {
      props: { ...baseProps, mcpApp, mcpReady: true },
    });

    await waitFor(() => expect(updateModelContext).toHaveBeenCalledOnce());
    const [{ content }] = updateModelContext.mock.calls[0];
    expect(content[0].text).toMatch(/Photos processed: 42/);
    expect(sendMessage).toHaveBeenCalledWith({
      role: 'user',
      content: [{ type: 'text', text: 'Indexing complete.' }],
    });
  });

  it('does not call MCP callbacks before mcpReady even if status is completed', async () => {
    const updateModelContext = vi.fn().mockResolvedValue(undefined);
    const mcpApp = { updateModelContext, sendMessage: vi.fn().mockResolvedValue(undefined) };

    mockFetch(completedSession({ totalPhotosProcessed: 5, totalDurationMs: 500 }));
    render(IndexingProgress, {
      props: { ...baseProps, mcpApp, mcpReady: false },
    });

    // Give the component time to process — callbacks must NOT fire yet.
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(updateModelContext).not.toHaveBeenCalled();
  });

  it('calls updateModelContext with error text on failure', async () => {
    const updateModelContext = vi.fn().mockResolvedValue(undefined);
    const sendMessage = vi.fn().mockResolvedValue(undefined);
    const mcpApp = { updateModelContext, sendMessage };

    mockFetch(failedSession('out of memory'));
    render(IndexingProgress, {
      props: { ...baseProps, mcpApp, mcpReady: true },
    });

    await waitFor(() => expect(updateModelContext).toHaveBeenCalledOnce());
    const [{ content }] = updateModelContext.mock.calls[0];
    expect(content[0].text).toMatch(/out of memory/);
    expect(sendMessage).toHaveBeenCalledWith({
      role: 'user',
      content: [{ type: 'text', text: 'Indexing failed.' }],
    });
  });
});

// ---------------------------------------------------------------------------

describe('IndexingProgress — stop button', () => {
  it('shows Stop button while running', async () => {
    mockFetch(runningSession());
    const { getByRole } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByRole('button', { name: 'Stop' })).toBeTruthy());
  });

  it('hides Stop button after completion', async () => {
    mockFetch(completedSession({ totalPhotosProcessed: 5, totalDurationMs: 500 }));
    const { queryByRole } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(queryByRole('button', { name: 'Stop' })).toBeNull());
  });

  it('POSTs to cancel endpoint when Stop is clicked', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve(runningSession()) })
      .mockResolvedValue({ ok: true, json: () => Promise.resolve({ status: 'cancelling' }) });
    global.fetch = fetchMock;

    const { getByRole } = render(IndexingProgress, { props: baseProps });
    const btn = await waitFor(() => getByRole('button', { name: 'Stop' }));
    await fireEvent.click(btn);

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        'http://localhost/api/indexing/abc/cancel',
        { method: 'POST' },
      ),
    );
  });

  it('shows "cancelled" chip and neutral card when status is cancelled', async () => {
    mockFetch({ status: 'cancelled', progress: 0, total: 1, message: '', summary: null, error: null });
    const { getByText } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByText('cancelled')).toBeTruthy());
    await waitFor(() => expect(getByText('Indexing stopped')).toBeTruthy());
  });

  it('does not call MCP callbacks when status is cancelled', async () => {
    const updateModelContext = vi.fn().mockResolvedValue(undefined);
    const mcpApp = { updateModelContext, sendMessage: vi.fn().mockResolvedValue(undefined) };

    mockFetch({ status: 'cancelled', progress: 0, total: 1, message: '', summary: null, error: null });
    render(IndexingProgress, { props: { ...baseProps, mcpApp, mcpReady: true } });

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(updateModelContext).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------

describe('IndexingProgress — header', () => {
  it('shows library name in header', async () => {
    mockFetch(runningSession());
    const { getByRole } = render(IndexingProgress, { props: baseProps });
    await waitFor(() => expect(getByRole('heading', { name: /MyLib/ })).toBeTruthy());
  });

  it('shows partition in header when provided', async () => {
    mockFetch(runningSession());
    const { getByRole } = render(IndexingProgress, {
      props: { ...baseProps, partition: '2024/07' },
    });
    await waitFor(() => expect(getByRole('heading', { name: /2024\/07/ })).toBeTruthy());
  });
});
