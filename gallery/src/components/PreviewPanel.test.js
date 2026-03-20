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
    onClose: vi.fn(),
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
    const match2 = { ...MATCH, contentHash: 'xyz789', filename: 'IMG_002.jpg' };
    const { getByAltText, getByText, rerender } = render(
      PreviewPanel,
      makeProps([MATCH, match2]),
    );

    await fireEvent.load(getByAltText('IMG_001.jpg'));
    expect(getByAltText('IMG_001.jpg')).toBeTruthy();

    // Navigate to a different photo — must go back to loading state.
    await rerender(makeProps([MATCH, match2], 1));

    expect(getByText('Loading…')).toBeTruthy();
  });
});
