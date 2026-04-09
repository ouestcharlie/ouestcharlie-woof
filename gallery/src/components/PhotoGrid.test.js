import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import PhotoGrid from './PhotoGrid.svelte';

// jsdom reports clientWidth = 0, so columns = max(1, floor(4/164)) = 1 and pageSize = 3.
const JSDOM_PAGE_SIZE = 3;

function makeMatches(n) {
  return Array.from({ length: n }, (_, i) => ({
    contentHash: `hash${i}`,
    filename: `IMG_${String(i).padStart(3, '0')}.jpg`,
  }));
}

function makeProps(overrides = {}) {
  return {
    matches: [],
    loading: false,
    selectedIndex: 0,
    thumbnailTile: () => null,
    onSelect: vi.fn(),
    onPageSelect: vi.fn(),
    ...overrides,
  };
}

describe('PhotoGrid — page size (3 rows × columns)', () => {
  it('shows at most pageSize tiles on the first page', () => {
    const { container } = render(PhotoGrid, makeProps({ matches: makeMatches(10) }));
    expect(container.querySelectorAll('.tile')).toHaveLength(JSDOM_PAGE_SIZE);
  });

  it('shows fewer tiles on the last partial page', () => {
    // 4 matches: page 0 has 3, page 1 has 1
    const { container } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(4), selectedIndex: 3 }),
    );
    expect(container.querySelectorAll('.tile')).toHaveLength(1);
  });

  it('shows all tiles when total fits within one page', () => {
    const { container } = render(PhotoGrid, makeProps({ matches: makeMatches(2) }));
    expect(container.querySelectorAll('.tile')).toHaveLength(2);
  });
});

describe('PhotoGrid — pagination controls', () => {
  it('shows no nav when all photos fit on one page', () => {
    const { queryByText } = render(PhotoGrid, makeProps({ matches: makeMatches(3) }));
    expect(queryByText(/Previous/)).toBeNull();
    expect(queryByText(/Next/)).toBeNull();
  });

  it('shows nav when photos exceed one page', () => {
    const { getAllByText } = render(PhotoGrid, makeProps({ matches: makeMatches(4) }));
    expect(getAllByText(/Next/).length).toBeGreaterThan(0);
    expect(getAllByText(/Previous/).length).toBeGreaterThan(0);
  });

  it('disables Previous on page 0', () => {
    const { getAllByText } = render(PhotoGrid, makeProps({ matches: makeMatches(7) }));
    expect(getAllByText(/Previous/)[0].closest('button')).toBeDisabled();
  });

  it('disables Next on last page', () => {
    // 4 matches: selectedIndex=3 → page 1, which is the last page
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(4), selectedIndex: 3 }),
    );
    expect(getAllByText(/Next/)[0].closest('button')).toBeDisabled();
  });

  it('calls onPageSelect with first index of next page when Next clicked', async () => {
    const onPageSelect = vi.fn();
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(7), onPageSelect }),
    );
    await fireEvent.click(getAllByText(/Next/)[0].closest('button'));
    expect(onPageSelect).toHaveBeenCalledWith(JSDOM_PAGE_SIZE);
  });

  it('calls onPageSelect with first index of previous page when Previous clicked', async () => {
    const onPageSelect = vi.fn();
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(7), selectedIndex: JSDOM_PAGE_SIZE, onPageSelect }),
    );
    await fireEvent.click(getAllByText(/Previous/)[0].closest('button'));
    expect(onPageSelect).toHaveBeenCalledWith(0);
  });
});

describe('PhotoGrid — tile selection', () => {
  it('calls onSelect with absolute index on tile click (page 0)', async () => {
    const onSelect = vi.fn();
    const { container } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(3), onSelect }),
    );
    const tiles = container.querySelectorAll('.tile');
    await fireEvent.click(tiles[2]);
    expect(onSelect).toHaveBeenCalledWith(2);
  });

  it('calls onSelect with correct absolute index on page 1', async () => {
    const onSelect = vi.fn();
    const { container } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(7), selectedIndex: JSDOM_PAGE_SIZE, onSelect }),
    );
    const tiles = container.querySelectorAll('.tile');
    await fireEvent.click(tiles[0]);
    expect(onSelect).toHaveBeenCalledWith(JSDOM_PAGE_SIZE);
  });
});

describe('PhotoGrid — loading state', () => {
  it('renders skeleton tiles when loading', () => {
    const { container } = render(PhotoGrid, makeProps({ loading: true }));
    expect(container.querySelectorAll('.skeleton')).toHaveLength(JSDOM_PAGE_SIZE);
  });

  it('renders no real tiles when loading', () => {
    const { container } = render(
      PhotoGrid,
      makeProps({ loading: true, matches: makeMatches(5) }),
    );
    const nonSkeleton = Array.from(container.querySelectorAll('.tile')).filter(
      (el) => !el.classList.contains('skeleton'),
    );
    expect(nonSkeleton).toHaveLength(0);
  });
});
