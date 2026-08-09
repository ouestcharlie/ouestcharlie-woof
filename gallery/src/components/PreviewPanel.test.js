import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import PreviewPanel from './PreviewPanel.svelte';

const MATCH = {
  contentHash: 'abc123',
  partition: '2024/2024-07',
  filename: 'IMG_001.jpg',
  width: 4000,
  height: 3000,
};

const MATCH2 = {
  contentHash: 'xyz789',
  partition: '2024/2024-07',
  filename: 'IMG_002.jpg',
  width: 3000,
  height: 2000,
};

const VIDEO_MATCH = {
  contentHash: 'vid456',
  partition: '2024/2024-07',
  filename: 'MOV_001.mov',
  width: 1920,
  height: 1080,
  mediaType: 'video',
  durationSeconds: 95,
  videoCodec: 'h264',
  hasAudio: true,
};

const previewUrl = (m) =>
  m?.contentHash
    ? `http://127.0.0.1:8080/previews/test/${m.partition}/${m.contentHash}.jpg`
    : null;

const videoUrl = (m) =>
  m?.contentHash
    ? `http://127.0.0.1:8080/video/test/${m.partition}/${m.contentHash}.mp4`
    : null;

function makeProps(matches, selectedIndex = 0) {
  return {
    matches,
    selectedIndex,
    onNavigate: vi.fn(),
    previewUrl,
    videoUrl,
  };
}

describe('PreviewPanel — loading placeholder / swap', () => {
  it('shows incoming img (not yet loaded) before onload fires', () => {
    const { getByAltText } = render(PreviewPanel, makeProps([MATCH]));

    // Img is in the DOM so the browser can fetch it, but not yet marked loaded.
    const img = getByAltText('IMG_001.jpg');
    expect(img.classList.contains('incoming')).toBe(true);
    expect(img.classList.contains('loaded')).toBe(false);
  });

  it('removes the placeholder once onload fires', async () => {
    const { getByAltText, queryByText } = render(PreviewPanel, makeProps([MATCH]));

    await fireEvent.load(getByAltText('IMG_001.jpg'));

    expect(queryByText('Loading…')).toBeNull();
  });

  it('does NOT reset to loading when matches is replaced with new objects carrying the same URL', async () => {
    // Regression: applySession() replaces matches[] with fresh object references.
    // previewLoaded must not reset if jpegUrl is unchanged.
    const { getByAltText, queryByText, rerender } = render(PreviewPanel, makeProps([MATCH]));

    await fireEvent.load(getByAltText('IMG_001.jpg'));
    expect(queryByText('Loading…')).toBeNull();

    // Same data, new object reference — exactly what applySession() does.
    await rerender(makeProps([{ ...MATCH }]));

    expect(queryByText('Loading…')).toBeNull();
  });

  it('resets to loading when navigating to a different photo', async () => {
    const { getByAltText, rerender } = render(
      PreviewPanel,
      makeProps([MATCH, MATCH2]),
    );

    await fireEvent.load(getByAltText('IMG_001.jpg'));

    await rerender(makeProps([MATCH, MATCH2], 1));

    // New image is in the DOM but not yet marked loaded.
    const img = getByAltText('IMG_002.jpg');
    expect(img.classList.contains('incoming')).toBe(true);
    expect(img.classList.contains('loaded')).toBe(false);
  });
});

describe('PreviewPanel — navigation buttons', () => {
  it('disables prev on first photo', () => {
    const { container } = render(PreviewPanel, makeProps([MATCH, MATCH2], 0));
    expect(container.querySelector('.nav.prev')).toBeDisabled();
  });

  it('disables next on last photo', () => {
    const { container } = render(PreviewPanel, makeProps([MATCH, MATCH2], 1));
    expect(container.querySelector('.nav.next')).toBeDisabled();
  });

  it('calls onNavigate(-1) when prev is clicked', async () => {
    const onNavigate = vi.fn();
    const { container } = render(PreviewPanel, {
      ...makeProps([MATCH, MATCH2], 1),
      onNavigate,
    });
    await fireEvent.click(container.querySelector('.nav.prev'));
    expect(onNavigate).toHaveBeenCalledWith(0);
  });

  it('calls onNavigate(+1) when next is clicked', async () => {
    const onNavigate = vi.fn();
    const { container } = render(PreviewPanel, {
      ...makeProps([MATCH, MATCH2], 0),
      onNavigate,
    });
    await fireEvent.click(container.querySelector('.nav.next'));
    expect(onNavigate).toHaveBeenCalledWith(1);
  });
});

describe('PreviewPanel — keyboard navigation', () => {
  it('advances selection on ArrowRight when active', async () => {
    const onNavigate = vi.fn();
    render(PreviewPanel, { ...makeProps([MATCH, MATCH2], 0), onNavigate, active: true });
    await fireEvent.keyDown(window, { key: 'ArrowRight' });
    expect(onNavigate).toHaveBeenCalledWith(1);
  });

  it('moves back on ArrowLeft when active', async () => {
    const onNavigate = vi.fn();
    render(PreviewPanel, { ...makeProps([MATCH, MATCH2], 1), onNavigate, active: true });
    await fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(onNavigate).toHaveBeenCalledWith(0);
  });

  it('ignores arrow keys while hidden (active: false) so grid browsing does not jump the selection', async () => {
    const onNavigate = vi.fn();
    render(PreviewPanel, { ...makeProps([MATCH, MATCH2], 0), onNavigate, active: false });
    await fireEvent.keyDown(window, { key: 'ArrowRight' });
    await fireEvent.keyDown(window, { key: 'ArrowLeft' });
    expect(onNavigate).not.toHaveBeenCalled();
  });
});

describe('PreviewPanel — caption bar', () => {
  it('renders filename in the caption', () => {
    const { getByText } = render(PreviewPanel, makeProps([MATCH, MATCH2], 0));
    expect(getByText('IMG_001.jpg')).toBeTruthy();
  });

  it('renders the first 5 tags as pills, truncating the rest', () => {
    const match = { ...MATCH, tags: ['a', 'b', 'c', 'd', 'e', 'f', 'g'] };
    const { container } = render(PreviewPanel, makeProps([match]));
    // Panel is closed, so pills only come from the caption bar.
    const pills = container.querySelectorAll('.caption .pill');
    expect(pills.length).toBe(5);
    expect(pills[0].textContent).toBe('a');
    expect(pills[4].textContent).toBe('e');
  });

  it('truncates the caption description to 100 characters', () => {
    const long = 'x'.repeat(150);
    const match = { ...MATCH, description: long };
    const { container } = render(PreviewPanel, makeProps([match]));
    const desc = container.querySelector('.caption-desc');
    expect(desc.textContent).toBe('x'.repeat(100) + '…');
  });
});

describe('PreviewPanel — details side panel', () => {
  function openPanel(container) {
    const toggle = container.querySelector('.info-toggle');
    return fireEvent.click(toggle);
  }

  it('is collapsed by default', () => {
    const { container } = render(PreviewPanel, makeProps([MATCH]));
    expect(container.querySelector('.details')).toBeNull();
  });

  it('shows the three subpanes when toggled open', async () => {
    const { container } = render(PreviewPanel, makeProps([MATCH]));
    await openPanel(container);
    const headings = [...container.querySelectorAll('.subpane h3')].map((h) => h.textContent);
    expect(headings).toEqual(['Overview', 'Camera', 'Location']);
  });

  it('renders camera make/model when present', async () => {
    const match = { ...MATCH, make: 'Canon', model: 'EOS R5' };
    const { container, getByText } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    expect(getByText('Canon EOS R5')).toBeTruthy();
  });

  // Wiring smoke: formatted EXIF values reach the Camera subpane. Exhaustive
  // formatting arithmetic (trailing zeros, sub-second exposure, 35mm-eq
  // omission, GPS hemispheres) lives in lib/format.test.js.
  it('renders formatted aperture and focal length in the Camera subpane', async () => {
    const match = {
      ...MATCH,
      aperture: 1.7999999523162842,
      focalLength: 5.539999961853027,
      focalLength35mm: 23,
    };
    const { container, getByText } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    expect(getByText('f/1.8')).toBeTruthy();
    expect(getByText('5.5 mm (23 mm eq.)')).toBeTruthy();
  });

  it('renders a positive rating as stars', async () => {
    const match = { ...MATCH, rating: 4 };
    const { container } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    const stars = container.querySelector('.stars');
    expect(stars).not.toBeNull();
    expect(stars.textContent).toBe('★★★★☆');
    expect(stars.getAttribute('aria-label')).toBe('Rating: 4 of 5');
  });

  it('omits stars when rating is absent, zero, or rejected (-1)', async () => {
    for (const rating of [undefined, 0, -1]) {
      const { container } = render(PreviewPanel, makeProps([{ ...MATCH, rating }]));
      await openPanel(container);
      expect(container.querySelector('.stars')).toBeNull();
    }
  });

  it('shows an empty state for the Location subpane when GPS is absent', async () => {
    const { container, getByText } = render(PreviewPanel, makeProps([MATCH]));
    await openPanel(container);
    expect(getByText('No location data')).toBeTruthy();
  });

  it('renders GPS coordinates when present', async () => {
    const match = { ...MATCH, gps: [48.8566, 2.3522] };
    const { container, getByText } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    expect(getByText(/48\.85660° N, 2\.35220° E/)).toBeTruthy();
  });
});

describe('PreviewPanel — video rendering', () => {
  function openPanel(container) {
    return fireEvent.click(container.querySelector('.info-toggle'));
  }

  it('renders a <video> with poster and src for a video match', () => {
    const { container } = render(PreviewPanel, makeProps([VIDEO_MATCH]));
    const video = container.querySelector('video.preview-video');
    expect(video).not.toBeNull();
    expect(video.getAttribute('src')).toContain('/video/test/');
    expect(video.getAttribute('poster')).toContain('/previews/test/');
    expect(container.querySelector('img.preview-img')).toBeNull();
  });

  it('renders a photo with <img>, not <video>', () => {
    const { container } = render(PreviewPanel, makeProps([MATCH]));
    expect(container.querySelector('video')).toBeNull();
    expect(container.querySelector('img.preview-img')).not.toBeNull();
  });

  it('shows the Video subpane (not Camera) for a video match', async () => {
    const { container, getByText } = render(PreviewPanel, makeProps([VIDEO_MATCH]));
    await openPanel(container);
    const headings = [...container.querySelectorAll('.subpane h3')].map((h) => h.textContent);
    expect(headings).toEqual(['Overview', 'Video', 'Location']);
    expect(getByText('H.264')).toBeTruthy();
  });

  it('renders the duration in the Overview subpane (mm:ss)', async () => {
    const { container } = render(PreviewPanel, makeProps([VIDEO_MATCH]));
    await openPanel(container);
    // Duration appears in both the Overview subpane and the caption bar.
    const overviewDuration = [...container.querySelectorAll('.subpane .field-val')]
      .some((el) => el.textContent === '1:35');
    expect(overviewDuration).toBe(true);
  });

  it('renders "No" for hasAudio: false (presence check, not truthiness)', async () => {
    const match = { ...VIDEO_MATCH, hasAudio: false };
    const { container, getByText } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    expect(getByText('No')).toBeTruthy();
  });

  it('hides the audio row when hasAudio is absent', async () => {
    const match = { ...VIDEO_MATCH };
    delete match.hasAudio;
    const { container, queryByText } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    expect(queryByText('Audio')).toBeNull();
  });

  it('shows container make/model only when present', async () => {
    const match = { ...VIDEO_MATCH, make: 'Apple', model: 'iPhone 15' };
    const { container, getByText } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    expect(getByText('Apple iPhone 15')).toBeTruthy();
  });

  it('does not render photo-only Camera rows for a video', async () => {
    const { container, queryByText } = render(PreviewPanel, makeProps([VIDEO_MATCH]));
    await openPanel(container);
    expect(queryByText('Lens')).toBeNull();
    expect(queryByText('ISO')).toBeNull();
    expect(queryByText('Aperture')).toBeNull();
  });

  it('populates the Location subpane for a video with GPS', async () => {
    const match = { ...VIDEO_MATCH, gps: [48.8566, 2.3522] };
    const { container, getByText } = render(PreviewPanel, makeProps([match]));
    await openPanel(container);
    expect(getByText(/48\.85660° N, 2\.35220° E/)).toBeTruthy();
  });
});

describe('PreviewPanel — crossfade / shownUrl layer', () => {
  it('adds loaded class to incoming img after onload', async () => {
    const { getByAltText } = render(PreviewPanel, makeProps([MATCH]));
    const img = getByAltText('IMG_001.jpg');
    expect(img.classList.contains('loaded')).toBe(false);
    await fireEvent.load(img);
    expect(img.classList.contains('loaded')).toBe(true);
  });

  it('keeps previous image visible as background while next image loads', async () => {
    const { getByAltText, container, rerender } = render(PreviewPanel, makeProps([MATCH, MATCH2]));
    await fireEvent.load(getByAltText('IMG_001.jpg'));

    await rerender(makeProps([MATCH, MATCH2], 1));

    // shownUrl layer shows the previous image (aria-hidden), incoming shows the new one
    const hidden = container.querySelector('img[aria-hidden="true"]');
    expect(hidden).not.toBeNull();
    expect(hidden.src).toContain('abc123');
    expect(getByAltText('IMG_002.jpg')).toBeTruthy();
  });

  it('updates background layer to the new image once it finishes loading', async () => {
    const { getByAltText, container, rerender } = render(PreviewPanel, makeProps([MATCH, MATCH2]));
    await fireEvent.load(getByAltText('IMG_001.jpg'));
    await rerender(makeProps([MATCH, MATCH2], 1));
    await fireEvent.load(getByAltText('IMG_002.jpg'));

    // shownUrl is now xyz789 — background layer updated to the newly loaded image
    const hidden = container.querySelector('img[aria-hidden="true"]');
    expect(hidden).not.toBeNull();
    expect(hidden.src).toContain('xyz789');
  });
});

describe('PreviewPanel — spinner delay', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('does not show spinner immediately on first load', () => {
    const { container } = render(PreviewPanel, makeProps([MATCH]));
    expect(container.querySelector('.spinner')).toBeNull();
  });

  it('shows spinner after 300ms if image has not loaded', async () => {
    const { container } = render(PreviewPanel, makeProps([MATCH]));
    await vi.advanceTimersByTimeAsync(300);
    expect(container.querySelector('.spinner')).not.toBeNull();
  });

  it('does not show spinner if image loads before 300ms', async () => {
    const { container, getByAltText } = render(PreviewPanel, makeProps([MATCH]));
    await fireEvent.load(getByAltText('IMG_001.jpg'));
    await vi.advanceTimersByTimeAsync(300);
    expect(container.querySelector('.spinner')).toBeNull();
  });

  it('hides spinner once image loads even after 300ms', async () => {
    const { container, getByAltText } = render(PreviewPanel, makeProps([MATCH]));
    await vi.advanceTimersByTimeAsync(300);
    expect(container.querySelector('.spinner')).not.toBeNull();
    await fireEvent.load(getByAltText('IMG_001.jpg'));
    expect(container.querySelector('.spinner')).toBeNull();
  });
});
