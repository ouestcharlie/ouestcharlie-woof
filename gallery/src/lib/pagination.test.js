import { describe, it, expect } from 'vitest';
import { absolutePageFromMap, totalDisplayPages } from './pagination.js';

describe('totalDisplayPages', () => {
  it('returns 1 for an empty map', () => {
    expect(totalDisplayPages([], 12)).toBe(1);
  });

  it('returns 1 when a single partial page fits in one display page', () => {
    // 5 items, display page holds 12 → 1 display page.
    expect(totalDisplayPages([{ pageSize: 100, pageCount: 1, totalCount: 5 }], 12)).toBe(1);
  });

  it('splits a single server page across multiple display pages', () => {
    // 100 items / 12 per display page = ceil(100/12) = 9.
    expect(totalDisplayPages([{ pageSize: 100, pageCount: 1, totalCount: 100 }], 12)).toBe(9);
  });

  it('sums full pages plus a partial last page within one entry', () => {
    // 3 pages of 100: two full (ceil(100/12)=9 each) + last of 100-200 → 250 total.
    // last page holds 50 → ceil(50/12)=5. Total = 9 + 9 + 5 = 23.
    expect(totalDisplayPages([{ pageSize: 100, pageCount: 3, totalCount: 250 }], 12)).toBe(23);
  });

  it('handles a display page larger than a server page', () => {
    // Each server page holds 10 items but the display page holds 20 → each
    // server page is 1 display page. 3 pages → 3 display pages.
    expect(totalDisplayPages([{ pageSize: 10, pageCount: 3, totalCount: 25 }], 20)).toBe(3);
  });

  it('sums across multiple map entries', () => {
    const map = [
      { pageSize: 100, pageCount: 2, totalCount: 150 }, // full(9) + last 50 → 5 = 14
      { pageSize: 60, pageCount: 1, totalCount: 24 },   // ceil(24/12) = 2
    ];
    expect(totalDisplayPages(map, 12)).toBe(16);
  });
});

describe('absolutePageFromMap', () => {
  const map = [{ pageSize: 100, pageCount: 3, totalCount: 250 }];

  it('is 0 for the first display page of the first server page', () => {
    expect(absolutePageFromMap(map, 0, 0, 12)).toBe(0);
  });

  it('adds the local display page within the current server page', () => {
    expect(absolutePageFromMap(map, 0, 3, 12)).toBe(3);
  });

  it('offsets by full server pages already passed', () => {
    // server page 1 starts after ceil(100/12) = 9 display pages.
    expect(absolutePageFromMap(map, 1, 0, 12)).toBe(9);
    expect(absolutePageFromMap(map, 2, 0, 12)).toBe(18);
  });

  it('crosses map entries, accounting for a partial last page in a prior entry', () => {
    const multi = [
      { pageSize: 100, pageCount: 2, totalCount: 150 }, // 14 display pages (9 + 5)
      { pageSize: 60, pageCount: 1, totalCount: 24 },
    ];
    // first display page of the second entry's server page (absolute server page 2).
    expect(absolutePageFromMap(multi, 2, 0, 12)).toBe(14);
  });

  it('handles a single one-item map', () => {
    expect(absolutePageFromMap([{ pageSize: 1, pageCount: 1, totalCount: 1 }], 0, 0, 12)).toBe(0);
  });
});
