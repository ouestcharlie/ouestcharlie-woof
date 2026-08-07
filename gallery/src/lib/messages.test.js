import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { locales, baseLocale } from '../paraglide/runtime.js';

// Guards against catalogue drift: every locale must define exactly the same
// message keys as the base locale (Paraglide otherwise silently falls back).
// Vitest runs with the gallery dir as cwd, where messages/ lives.
function loadKeys(locale) {
  const json = JSON.parse(readFileSync(resolve('messages', `${locale}.json`), 'utf8'));
  return Object.keys(json).filter((k) => k !== '$schema').sort();
}

describe('message catalogues', () => {
  const baseKeys = loadKeys(baseLocale);

  it('base catalogue is non-empty', () => {
    expect(baseKeys.length).toBeGreaterThan(0);
  });

  for (const locale of locales.filter((l) => l !== baseLocale)) {
    it(`${locale} defines the same keys as ${baseLocale}`, () => {
      expect(loadKeys(locale)).toEqual(baseKeys);
    });
  }
});
