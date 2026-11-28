import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DownloadsTab, isNewer, pickDownloads, thisDevice, versionOf, type Release } from './DownloadsTab';

afterEach(cleanup);
beforeEach(() => { localStorage.clear(); });

const URL0 = 'https://github.com/olosnoks/hon-diy-data/releases/download/v0.2.1/';
/** The asset names scripts/release/build-all.ps1 makes (as on the v0.2.0 release). */
const REL: Release = {
  tag_name: 'v0.2.1', name: 'hondalog 0.2.1', published_at: '2026-11-28T09:00:00Z',
  html_url: 'https://github.com/olosnoks/hon-diy-data/releases/tag/v0.2.1',
  assets: ['FLASH-handheld.txt', 'hondalog-0.2.1-android.apk', 'hondalog-0.2.1-linux-x64.tar.gz', 'hondalog-0.2.1-web.zip',
    'hondalog-0.2.1-windows-x64.zip', 'hondalog-handheld-0.2.1.bin', 'SHA256SUMS']
    .map((name, i) => ({ name, browser_download_url: URL0 + name, size: (i + 1) * 1_500_000 })),
};

const fakeFetch = (status: number, body: unknown) => (async () =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })) as unknown as typeof fetch;

describe('release downloads', () => {
  it('picks one file per platform, in display order', () => {
    expect(pickDownloads(REL).map((d) => `${d.key}:${d.asset.name}`)).toEqual([
      'android:hondalog-0.2.1-android.apk',
      'windows:hondalog-0.2.1-windows-x64.zip',
      'linux:hondalog-0.2.1-linux-x64.tar.gz',
      'handheld:hondalog-handheld-0.2.1.bin',
      'web:hondalog-0.2.1-web.zip',
    ]);
  });

  it('compares versions numerically', () => {
    expect(versionOf('v0.2.1')).toBe('0.2.1');
    expect(isNewer('v0.2.1', '0.2.0')).toBe(true);
    expect(isNewer('0.2.10', '0.2.9')).toBe(true);
    expect(isNewer('0.2.0', '0.2.0')).toBe(false);
    expect(isNewer('0.1.9', '0.2.0')).toBe(false);
    expect(isNewer('1.0', '0.9.9')).toBe(true);
  });

  it('knows the device', () => {
    expect(thisDevice('Mozilla/5.0 (Linux; Android 10; wv)')).toBe('android');
    expect(thisDevice('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')).toBe('windows');
    expect(thisDevice('Mozilla/5.0 (X11; Linux x86_64)')).toBe('linux');
  });

  it('shows direct links and flags an update', async () => {
    render(<DownloadsTab running="0.2.0" fetchFn={fakeFetch(200, REL)} />);
    await waitFor(() => expect(screen.getByTestId('download-android')).toBeTruthy());
    expect(screen.getByTestId('download-android').getAttribute('href')).toBe(URL0 + 'hondalog-0.2.1-android.apk');
    expect(screen.getByTestId('download-windows').getAttribute('href')).toBe(URL0 + 'hondalog-0.2.1-windows-x64.zip');
    expect(screen.getByTestId('download-linux').getAttribute('href')).toBe(URL0 + 'hondalog-0.2.1-linux-x64.tar.gz');
    expect(screen.getByTestId('downloads-update')).toBeTruthy();
    expect(screen.getByText('SHA256SUMS').getAttribute('href')).toBe(URL0 + 'SHA256SUMS');
  });

  it('says up to date, and keeps the last answer for offline', async () => {
    const { unmount } = render(<DownloadsTab running="0.2.1" fetchFn={fakeFetch(200, REL)} />);
    await waitFor(() => expect(screen.getByText('up to date')).toBeTruthy());
    unmount();
    const offline = (async () => { throw new TypeError('Failed to fetch'); }) as unknown as typeof fetch;
    render(<DownloadsTab running="0.2.1" fetchFn={offline} />);
    await waitFor(() => expect(screen.getByTestId('downloads-status').textContent).toMatch(/offline, showing the last check/));
    expect(screen.getByTestId('download-android')).toBeTruthy();
  });

  it('with no release yet, links the releases page', async () => {
    render(<DownloadsTab running="0.2.0" fetchFn={fakeFetch(404, { message: 'Not Found' })} />);
    await waitFor(() => expect(screen.getByTestId('downloads-status').textContent).toMatch(/no release published yet/));
    expect(screen.getByText(/releases\/latest/)).toBeTruthy();
    fireEvent.click(screen.getByTestId('downloads-refresh'));
  });
});
