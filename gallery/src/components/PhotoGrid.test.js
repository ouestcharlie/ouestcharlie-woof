import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import PhotoGrid from './PhotoGrid.svelte';

// jsdom reports clientWidth = 0, so columns = max(1, floor(4/164)) = 1 and serverPageSize = 3.
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
    // totalCount, serverPage, serverPageSize, onFetchServerPage — not set here so the
    // component's own defaults apply (totalCount falls back to matches.length).
    onSelect: vi.fn(),
    onPageSelect: vi.fn(),
    ...overrides,
  };
}

describe('PhotoGrid — page size (3 rows × columns)', () => {
  it('shows at most serverPageSize tiles on the first page', () => {
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
    const { container } = render(PhotoGrid, makeProps({ matches: makeMatches(3) }));
    expect(container.querySelectorAll('.nav')).toHaveLength(2);
    container.querySelectorAll('.nav').forEach((el) => {
      expect(el.classList.contains('nav-hidden')).toBe(true);
    });
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

// jsdom: columns=1, displayPageSize=3
describe('PhotoGrid — server-page-aware total count', () => {
  it('uses totalCount for pageCount if tocalCount < serverPageSize', () => {
    // totalCount=600, displayPageSizre=3 → ceil(200/3)=67 pages
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(13),
        totalCount: 200,
        serverPage: 0,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/67/)[0]).toBeTruthy();
  });
  
  it('uses totalCount for pageCount and server Page size if tocalCount > serverPageSize', () => {
    // totalCount=600, displayPageSizre=3 → ceil(500/3) + ceil((600-500)/3)=201 pages
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(13),
        totalCount: 600,
        serverPage: 0,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/201/)[0]).toBeTruthy();
  });

  it('Next at last local page triggers onFetchServerPage when more exist', async () => {
    const onFetchServerPage = vi.fn().mockResolvedValue(undefined);
    const onPageSelect = vi.fn();
    // 3 matches = 1 display page; totalCount=600 → more server pages remain
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(3),
        totalCount: 600,
        serverPage: 0,
        serverPageSize: 500,
        onFetchServerPage,
        onPageSelect,
      }),
    );
    await fireEvent.click(getAllByText(/Next/)[0].closest('button'));
    expect(onFetchServerPage).toHaveBeenCalledWith(1);
    // After the server fetch resolves, selectedIndex must be reset to 0
    expect(onPageSelect).toHaveBeenCalledWith(0);
  });

  it('Previous at first local page triggers onFetchServerPage when serverPage > 0', async () => {
    const onFetchServerPage = vi.fn().mockResolvedValue(undefined);
    const onPageSelect = vi.fn();
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(3),
        totalCount: 600,
        serverPage: 1,
        serverPageSize: 500,
        onFetchServerPage,
        onPageSelect,
      }),
    );
    await fireEvent.click(getAllByText(/Previous/)[0].closest('button'));
    expect(onFetchServerPage).toHaveBeenCalledWith(0);
    // After the server fetch resolves, select the last photo of the newly loaded page
    expect(onPageSelect).toHaveBeenCalledWith(2); // matches.length - 1 = 3 - 1
  });

  it('absolute page reflects server page offset', () => {
    // serverPage=1, serverPageSize=500, displayPageSize=3 → absolutePage = ceil(500/3) + 0 = 167
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(3),
        totalCount: 1000,
        serverPage: 1,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/168/)[0]).toBeTruthy(); // absolutePage+1 = 168
  });

  it('Next is disabled on last server page last display page', () => {
    // totalCount=3, serverPageSize=500, serverPage=0, displayPageSize=3 → 1 page total, Next disabled
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(3),
        totalCount: 3,
        serverPage: 0,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/Next/)[0].closest('button')).toBeDisabled();
  });
});
