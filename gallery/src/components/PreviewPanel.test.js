import { describe, it, expect, vi } from 'vitest';
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

const previewUrl = (m) =>
  m?.contentHash
    ? `http://127.0.0.1:8080/previews/test/${m.partition}/${m.contentHash}.jpg`
    : null;

const thumbnailTile = () => null;

function makeProps(matches, selectedIndex = 0) {
  return {
    matches,
    selectedIndex,
    onNavigate: vi.fn(),
    previewUrl,
    thumbnailTile,
  };
}

describe('PreviewPanel — loading placeholder / swap', () => {
  it('shows loading placeholder and the img before onload fires', () => {
    const { getByText, getByAltText } = render(PreviewPanel, makeProps([MATCH]));

    // Placeholder visible, img already in DOM (to allow the browser to fetch).
    expect(getByText('Loading…')).toBeTruthy();
    expect(getByAltText('IMG_001.jpg')).toBeTruthy();
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
    const { getByAltText, getByText, rerender } = render(
      PreviewPanel,
      makeProps([MATCH, MATCH2]),
    );

    await fireEvent.load(getByAltText('IMG_001.jpg'));

    await rerender(makeProps([MATCH, MATCH2], 1));

    expect(getByText('Loading…')).toBeTruthy();
  });
});

describe('PreviewPanel — navigation buttons', () => {
  it('disables prev on first photo', () => {
    const { getAllByRole } = render(PreviewPanel, makeProps([MATCH, MATCH2], 0));
    const [prev] = getAllByRole('button');
    expect(prev).toBeDisabled();
  });

  it('disables next on last photo', () => {
    const { getAllByRole } = render(PreviewPanel, makeProps([MATCH, MATCH2], 1));
    const buttons = getAllByRole('button');
    const next = buttons[buttons.length - 1];
    expect(next).toBeDisabled();
  });

  it('calls onNavigate(-1) when prev is clicked', async () => {
    const onNavigate = vi.fn();
    const { getAllByRole } = render(PreviewPanel, {
      ...makeProps([MATCH, MATCH2], 1),
      onNavigate,
    });
    const [prev] = getAllByRole('button');
    await fireEvent.click(prev);
    expect(onNavigate).toHaveBeenCalledWith(0);
  });

  it('calls onNavigate(+1) when next is clicked', async () => {
    const onNavigate = vi.fn();
    const { getAllByRole } = render(PreviewPanel, {
      ...makeProps([MATCH, MATCH2], 0),
      onNavigate,
    });
    const buttons = getAllByRole('button');
    const next = buttons[buttons.length - 1];
    await fireEvent.click(next);
    expect(onNavigate).toHaveBeenCalledWith(1);
  });
});

describe('PreviewPanel — metadata', () => {
  it('renders filename', () => {
    const { getByText } = render(PreviewPanel, makeProps([MATCH, MATCH2], 0));
    expect(getByText('IMG_001.jpg')).toBeTruthy();
  });

  it('renders camera make/model when present', () => {
    const match = { ...MATCH, make: 'Canon', model: 'EOS R5' };
    const { getByText } = render(PreviewPanel, makeProps([match]));
    expect(getByText('Canon EOS R5')).toBeTruthy();
  });

  it('renders tags when present', () => {
    const match = { ...MATCH, tags: ['holiday', 'sunset'] };
    const { getByText } = render(PreviewPanel, makeProps([match]));
    expect(getByText('Tags: holiday, sunset')).toBeTruthy();
  });

  it('omits camera line when make/model absent', () => {
    const { queryByText } = render(PreviewPanel, makeProps([MATCH]));
    expect(queryByText(/Canon/)).toBeNull();
  });
});
