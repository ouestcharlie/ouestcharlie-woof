import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import PhotoGrid from './PhotoGrid.svelte';

// test-setup.js mocks clientWidth = 652, so:
//   columns = max(1, floor((652 + 4) / (160 + 4))) = 4
//   displayPageSize = 4 cols × 3 rows = 12
const JSDOM_PAGE_SIZE = 12;

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

describe('PhotoGrid — page size (4 cols × 3 rows)', () => {
  it('shows at most displayPageSize tiles on the first page', () => {
    // 20 matches: page 0 shows 12 (JSDOM_PAGE_SIZE), page 1 has the rest
    const { container } = render(PhotoGrid, makeProps({ matches: makeMatches(20) }));
    expect(container.querySelectorAll('.tile')).toHaveLength(JSDOM_PAGE_SIZE);
  });

  it('shows fewer tiles on the last partial page', () => {
    // 13 matches: page 0 has 12, page 1 has 1
    const { container } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(13), selectedIndex: 12 }),
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
    const { container } = render(PhotoGrid, makeProps({ matches: makeMatches(12) }));
    expect(container.querySelectorAll('.nav')).toHaveLength(2);
    container.querySelectorAll('.nav').forEach((el) => {
      expect(el.classList.contains('nav-hidden')).toBe(true);
    });
  });

  it('shows nav when photos exceed one page', () => {
    const { getAllByText } = render(PhotoGrid, makeProps({ matches: makeMatches(13) }));
    expect(getAllByText(/Next/).length).toBeGreaterThan(0);
    expect(getAllByText(/Previous/).length).toBeGreaterThan(0);
  });

  it('disables Previous on page 0', () => {
    const { getAllByText } = render(PhotoGrid, makeProps({ matches: makeMatches(25) }));
    expect(getAllByText(/Previous/)[0].closest('button')).toBeDisabled();
  });

  it('disables Next on last page', () => {
    // 13 matches: selectedIndex=12 → page 1, which is the last page
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(13), selectedIndex: 12 }),
    );
    expect(getAllByText(/Next/)[0].closest('button')).toBeDisabled();
  });

  it('calls onPageSelect with first index of next page when Next clicked', async () => {
    const onPageSelect = vi.fn();
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(25), onPageSelect }),
    );
    await fireEvent.click(getAllByText(/Next/)[0].closest('button'));
    expect(onPageSelect).toHaveBeenCalledWith(JSDOM_PAGE_SIZE);
  });

  it('calls onPageSelect with first index of previous page when Previous clicked', async () => {
    const onPageSelect = vi.fn();
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({ matches: makeMatches(25), selectedIndex: JSDOM_PAGE_SIZE, onPageSelect }),
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
      makeProps({ matches: makeMatches(25), selectedIndex: JSDOM_PAGE_SIZE, onSelect }),
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

// displayPageSize=12, serverPageSize=500
describe('PhotoGrid — server-page-aware total count', () => {
  it('uses totalCount for pageCount when totalCount <= serverPageSize', () => {
    // totalCount=200, displayPageSize=12 → ceil(200/12) = 17 pages
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(12),
        totalCount: 200,
        serverPage: 0,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/17/)[0]).toBeTruthy();
  });

  it('uses totalCount and serverPageSize for pageCount when totalCount > serverPageSize', () => {
    // totalCount=600, displayPageSize=12:
    //   full server pages: floor(600/500)=1 → 1 × ceil(500/12)=42 display pages
    //   last server page: 100 photos → ceil(100/12)=9 display pages
    //   total: 51
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(12),
        totalCount: 600,
        serverPage: 0,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/51/)[0]).toBeTruthy();
  });

  it('Next at last local page triggers onFetchServerPage when more exist', async () => {
    const onFetchServerPage = vi.fn().mockResolvedValue(undefined);
    const onPageSelect = vi.fn();
    // 12 matches = 1 display page; totalCount=600 → more server pages remain
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
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
    // 12 matches = 1 display page on server page 1
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
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
    expect(onPageSelect).toHaveBeenCalledWith(JSDOM_PAGE_SIZE - 1); // matches.length - 1
  });

  it('absolute page reflects server page offset (serverPage=1)', () => {
    // serverPage=1, serverPageSize=500, displayPageSize=12
    // absolutePage = 1 × ceil(500/12) + 0 = 42 → +1 = 43
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        selectedIndex: 0,
        totalCount: 1000,
        serverPage: 1,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/43/)[0]).toBeTruthy(); // absolutePage+1
  });

  it('absolute page reflects server page offset (serverPage=2)', () => {
    // serverPage=2, serverPageSize=500, displayPageSize=12
    // absolutePage = 2 × ceil(500/12) + 0 = 84 → +1 = 85
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        selectedIndex: 0,
        totalCount: 1480,
        serverPage: 2,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/85/)[0]).toBeTruthy(); // absolutePage+1
  });

  it('Next is disabled on last server page last display page', () => {
    // totalCount=12, serverPageSize=500, serverPage=0 → 1 display page total, Next disabled
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        totalCount: JSDOM_PAGE_SIZE,
        serverPage: 0,
        serverPageSize: 500,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/Next/)[0].closest('button')).toBeDisabled();
  });
});

// displayPageSize=12, serverPageSize=500
describe('PhotoGrid — pageMap-based absolutePage and totalDisplayPages', () => {
  // Two chained sessions with different pagesizes (all full pages — no partial last page):
  //   session 0: pageSize=12, pageCount=2, totalCount=24 → dpp=1  → 2 display pages
  //   session 1: pageSize=24, pageCount=1, totalCount=24 → dpp=2  → 2 display pages
  // Total display pages = 2×1 + 1×2 = 4
  const pageMap = [
    { pageSize: 12, pageCount: 2, totalCount: 24 },
    { pageSize: 24, pageCount: 1, totalCount: 24 },
  ];

  it('totalDisplayPages sums display pages across all pageMap entries', () => {
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        totalCount: 48,
        serverPage: 0,
        serverPageSize: 12,
        pageMap,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/\/ 4/)[0]).toBeTruthy(); // "1 / 4"
  });

  it('absolutePage is 1 for serverPage 0 in session 0', () => {
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        totalCount: 48,
        serverPage: 0,
        serverPageSize: 12,
        pageMap,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/^1 \//)[0]).toBeTruthy();
  });

  it('absolutePage is 2 for serverPage 1 in session 0', () => {
    // serverPage=1: remaining=1 < pageCount=2 → offset=0+1×1+0=1 → +1=2
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        totalCount: 48,
        serverPage: 1,
        serverPageSize: 12,
        pageMap,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/^2 \//)[0]).toBeTruthy();
  });

  it('absolutePage is 3 for first local page of serverPage 2 (session 1, dpp=2)', () => {
    // serverPage=2: session 0 exhausted (offset=2), session 1: 0×2+0=2 → +1=3
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        selectedIndex: 0,
        totalCount: 48,
        serverPage: 2,
        serverPageSize: 24,
        pageMap,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/^3 \//)[0]).toBeTruthy();
  });

  it('absolutePage is 4 for second local page of serverPage 2 (session 1, dpp=2)', () => {
    // serverPage=2, localPage=1 (selectedIndex=12 → floor(12/12)=1): 2+0×2+1=3 → +1=4
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE * 2),
        selectedIndex: JSDOM_PAGE_SIZE,
        totalCount: 48,
        serverPage: 2,
        serverPageSize: 24,
        pageMap,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/^4 \//)[0]).toBeTruthy();
  });

  it('totalDisplayPages accounts for partial last server page in each session', () => {
    // session 0: pageSize=24, pageCount=2, totalCount=36
    //   page 0: 24 photos → ceil(24/12)=2 dpp
    //   page 1: 12 photos → ceil(12/12)=1 dpp  ← partial last page
    //   session contribution: 2+1 = 3 display pages
    // session 1: pageSize=12, pageCount=1, totalCount=12 → 1 display page
    // total: 4   (uniform formula would give 2×2 + 1×1 = 5 — wrong)
    const partialPageMap = [
      { pageSize: 24, pageCount: 2, totalCount: 36 },
      { pageSize: 12, pageCount: 1, totalCount: 12 },
    ];
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        totalCount: 48,
        serverPage: 0,
        serverPageSize: 24,
        pageMap: partialPageMap,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/\/ 4/)[0]).toBeTruthy(); // "1 / 4", not "1 / 5"
  });

  it('absolutePage offset after a session with a partial last page is correct', () => {
    // After session 0 (pageSize=24, totalCount=36) the display offset must be 3,
    // not the uniform 4 that ceil(totalCount/pageSize)×dpp = 2×2 would give.
    // serverPage=2 → first page of session 1 → absolutePage = 3 → "4 / 4"
    const partialPageMap = [
      { pageSize: 24, pageCount: 2, totalCount: 36 },
      { pageSize: 12, pageCount: 1, totalCount: 12 },
    ];
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        selectedIndex: 0,
        totalCount: 48,
        serverPage: 2,
        serverPageSize: 12,
        pageMap: partialPageMap,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/^4 \//)[0]).toBeTruthy(); // absolutePage+1 = 4, not 5
  });

  it('falls back to uniform formula when pageMap is null', () => {
    // No pageMap: serverPage=1, serverPageSize=500, displayPageSize=12
    // absolutePage = 1×ceil(500/12)+0 = 42 → "43 / ..."
    const { getAllByText } = render(
      PhotoGrid,
      makeProps({
        matches: makeMatches(JSDOM_PAGE_SIZE),
        totalCount: 1000,
        serverPage: 1,
        serverPageSize: 500,
        pageMap: null,
        onFetchServerPage: vi.fn(),
      }),
    );
    expect(getAllByText(/^43 \//)[0]).toBeTruthy();
  });
});
