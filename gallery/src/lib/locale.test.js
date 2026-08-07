import { describe, it, expect, afterEach } from 'vitest';
import { resolveLocale, applyLocale } from './locale.js';
import { baseLocale, getLocale, setLocale } from '../paraglide/runtime.js';

// Keep the global locale from leaking between tests.
afterEach(() => setLocale(baseLocale, { reload: false }));

describe('resolveLocale', () => {
  it('returns the base locale for empty / unknown tags', () => {
    expect(resolveLocale(null)).toBe(baseLocale);
    expect(resolveLocale(undefined)).toBe(baseLocale);
    expect(resolveLocale('')).toBe(baseLocale);
    expect(resolveLocale('xx-YY')).toBe(baseLocale);
  });

  it('matches an exact tag case-insensitively (en-GB stays distinct)', () => {
    expect(resolveLocale('en-GB')).toBe('en-GB');
    expect(resolveLocale('en-gb')).toBe('en-GB');
  });

  it('falls back to the primary subtag', () => {
    expect(resolveLocale('fr-FR')).toBe('fr');
    expect(resolveLocale('de-CH')).toBe('de');
    expect(resolveLocale('zh-Hans-CN')).toBe('zh');
    expect(resolveLocale('ja-JP')).toBe('ja');
  });

  it('prefers en over en-GB for a bare "en"', () => {
    expect(resolveLocale('en')).toBe('en');
  });
});

describe('applyLocale', () => {
  it('activates the resolved locale in memory', () => {
    applyLocale('fr-FR');
    expect(getLocale()).toBe('fr');
    applyLocale('ja');
    expect(getLocale()).toBe('ja');
  });

  it('reflects the resolved locale on <html lang>', () => {
    applyLocale('de-CH');
    expect(document.documentElement.lang).toBe('de');
  });

  it('falls back to the base locale for an unknown tag', () => {
    applyLocale('xx-YY');
    expect(getLocale()).toBe(baseLocale);
  });
});
