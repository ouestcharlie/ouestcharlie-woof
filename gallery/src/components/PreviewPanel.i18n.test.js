import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import PreviewPanel from './PreviewPanel.svelte';
import { baseLocale, setLocale } from '../paraglide/runtime.js';

const MATCH = {
  contentHash: 'abc123',
  partition: '2024/2024-07',
  filename: 'IMG_001.jpg',
  width: 4000,
  height: 3000,
};

const previewUrl = (m) =>
  m?.contentHash ? `http://127.0.0.1:8080/previews/${m.contentHash}.jpg` : null;

function makeProps() {
  return { matches: [MATCH], selectedIndex: 0, onNavigate: vi.fn(), previewUrl };
}

// Restore the base locale so other suites are unaffected.
afterEach(() => setLocale(baseLocale, { reload: false }));

describe('PreviewPanel — localized details panel', () => {
  it('renders the details subpane headings in French', async () => {
    setLocale('fr', { reload: false });
    const { getByText, getByTitle } = render(PreviewPanel, makeProps());
    // Open the details panel via its (localized) info toggle.
    await fireEvent.click(getByTitle('Détails'));
    expect(getByText("Vue d'ensemble")).toBeInTheDocument();
    expect(getByText('Appareil photo')).toBeInTheDocument();
    expect(getByText('Localisation')).toBeInTheDocument();
    // No GPS on MATCH → localized empty state.
    expect(getByText('Aucune donnée de localisation')).toBeInTheDocument();
  });

  it('renders the same headings in English by default', async () => {
    const { getByText, getByTitle } = render(PreviewPanel, makeProps());
    await fireEvent.click(getByTitle('Details'));
    expect(getByText('Overview')).toBeInTheDocument();
    expect(getByText('Camera')).toBeInTheDocument();
    expect(getByText('Location')).toBeInTheDocument();
  });
});
