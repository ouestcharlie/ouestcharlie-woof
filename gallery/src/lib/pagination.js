// Pure pagination math for MediaGrid.
//
// The server returns results in variable-size "server pages" described by a
// pageMap: one entry per contiguous run of same-sized pages, each entry
// { pageSize, pageCount, totalCount }. The grid displays fixed-size "display
// pages" of `displayPageSize` tiles (columns × rows). These helpers translate
// between the two so the grid can show a single running "page N / M" counter
// spanning every server page.
//
// Within a pageMap entry, the first `pageCount - 1` pages hold `pageSize` items
// each; the last holds the remainder (`totalCount - (pageCount-1)*pageSize`),
// which may be partial.

/**
 * Number of display pages a server page of `size` items occupies.
 * @param {number} size
 * @param {number} displayPageSize
 * @returns {number}
 */
function displayPagesFor(size, displayPageSize) {
  return Math.ceil(size / displayPageSize);
}

/**
 * Absolute (across all server pages) display-page index for the local display
 * page `localPage` within server page `serverPage`.
 *
 * @param {Array<{pageSize: number, pageCount: number, totalCount: number}>} pageMap
 * @param {number} serverPage - zero-based server page index
 * @param {number} localPage - zero-based display page within that server page
 * @param {number} displayPageSize - tiles per display page (> 0)
 * @returns {number}
 */
export function absolutePageFromMap(pageMap, serverPage, localPage, displayPageSize) {
  let offset = 0;
  let remaining = serverPage;
  for (const { pageSize, pageCount, totalCount } of pageMap) {
    const dpp = displayPagesFor(pageSize, displayPageSize);
    if (remaining < pageCount) return offset + remaining * dpp + localPage;
    const fullPages = pageCount - 1;
    const lastSize = totalCount - fullPages * pageSize;
    offset += fullPages * dpp + displayPagesFor(lastSize, displayPageSize);
    remaining -= pageCount;
  }
  return offset + localPage;
}

/**
 * Total number of display pages across every server page in the map.
 * Always at least 1.
 *
 * @param {Array<{pageSize: number, pageCount: number, totalCount: number}>} pageMap
 * @param {number} displayPageSize - tiles per display page (> 0)
 * @returns {number}
 */
export function totalDisplayPages(pageMap, displayPageSize) {
  const total = pageMap.reduce((sum, { pageSize, pageCount, totalCount }) => {
    const fullPages = pageCount - 1;
    const lastSize = totalCount - fullPages * pageSize;
    return sum + fullPages * displayPagesFor(pageSize, displayPageSize)
      + displayPagesFor(lastSize, displayPageSize);
  }, 0);
  return Math.max(1, total);
}
