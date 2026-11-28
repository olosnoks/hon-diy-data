/**
 * Setup > Downloads: direct links to the newest GitHub release for every
 * platform (Windows, Linux, Android, the CYD firmware, the offline web app).
 * The release list comes from the GitHub API, asked only when this tab opens.
 * The last answer is kept so the tab still shows something offline. The
 * build for the device you're on is marked, and an update is flagged when
 * the release is newer than what's running.
 */
import { useCallback, useEffect, useState } from 'react';
import { Button, Card, mono, Note, useTheme } from '../bytes/controls';

export const REPO = 'olosnoks/hon-diy-data';
export const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`;
const API_LATEST = `https://api.github.com/repos/${REPO}/releases/latest`;
const CACHE_KEY = 'hondalog.latestRelease';

export interface ReleaseAsset { name: string; browser_download_url: string; size: number }
export interface Release { tag_name: string; name?: string | null; published_at?: string | null; html_url: string; assets: ReleaseAsset[] }

export type Target = 'windows' | 'linux' | 'appimage' | 'android' | 'handheld' | 'web';

/** What each release file is, in the order shown. Names follow scripts/release/build-all.ps1. */
export const TARGETS: readonly { key: Target; label: string; what: string; match: RegExp }[] = [
  { key: 'android', label: 'Android', what: 'the car radio or a phone: install over the old version, drives are kept', match: /-android\.apk$/ },
  { key: 'windows', label: 'Windows', what: 'desktop app: unzip and run hondalog.exe', match: /-windows-x64\.zip$/ },
  { key: 'linux', label: 'Linux', what: 'desktop app (x64): unpack and run ./hondalog', match: /-linux-x64\.tar\.gz$/ },
  { key: 'appimage', label: 'Linux AppImage', what: 'desktop app as one file: chmod +x and run', match: /\.AppImage$/ },
  { key: 'handheld', label: 'CYD firmware', what: 'merged image for the handheld (Setup > CYD black box, or esptool)', match: /^hondalog-handheld-.*\.bin$/ },
  { key: 'web', label: 'Web app', what: 'this app to run from a folder with no internet', match: /-web\.zip$/ },
];

export interface Download { key: Target; label: string; what: string; asset: ReleaseAsset }

/** The release's files, one per platform it has. */
export function pickDownloads(r: Release): Download[] {
  const out: Download[] = [];
  for (const t of TARGETS) {
    const asset = r.assets.find((a) => t.match.test(a.name));
    if (asset) out.push({ key: t.key, label: t.label, what: t.what, asset });
  }
  return out;
}

export function versionOf(tag: string): string {
  return tag.replace(/^v/i, '').trim();
}

/** a > b for dotted versions ("0.2.10" > "0.2.9"); anything after "-" is ignored. */
export function isNewer(a: string, b: string): boolean {
  const pa = versionOf(a).split('-')[0]!.split('.').map((x) => parseInt(x, 10) || 0);
  const pb = versionOf(b).split('-')[0]!.split('.').map((x) => parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d) return d > 0;
  }
  return false;
}

/** Which file is for the device this runs on. */
export function thisDevice(ua: string = typeof navigator === 'undefined' ? '' : navigator.userAgent): Target | null {
  if (typeof window !== 'undefined' && 'HondalogAndroid' in window) return 'android';
  if (/Android/i.test(ua)) return 'android';
  if (/Windows/i.test(ua)) return 'windows';
  if (/Linux|X11/i.test(ua)) return 'linux';
  return null;
}

function sizeText(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

function readCache(): Release | null {
  try {
    const s = localStorage.getItem(CACHE_KEY);
    return s ? (JSON.parse(s) as Release) : null;
  } catch {
    return null;
  }
}

export async function fetchLatest(fetchFn: typeof fetch = fetch): Promise<Release> {
  const r = await fetchFn(API_LATEST, { headers: { Accept: 'application/vnd.github+json' } });
  if (r.status === 404) throw new Error('no release published yet');
  if (!r.ok) throw new Error(`GitHub answered ${r.status}`);
  const rel = (await r.json()) as Release;
  if (!rel || !Array.isArray(rel.assets)) throw new Error('unexpected answer from GitHub');
  try { localStorage.setItem(CACHE_KEY, JSON.stringify(rel)); } catch { /* storage full or blocked */ }
  return rel;
}

export function DownloadsTab(props: { running: string; fetchFn?: typeof fetch }) {
  const t = useTheme();
  const [release, setRelease] = useState<Release | null>(readCache);
  const [state, setState] = useState<'loading' | 'ok' | 'error'>('loading');
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setState('loading');
    fetchLatest(props.fetchFn).then((r) => { setRelease(r); setState('ok'); })
      .catch((e: unknown) => { setError((e as Error).message || String(e)); setState('error'); });
  }, [props.fetchFn]);
  useEffect(load, [load]);

  const mine = thisDevice();
  const downloads = release ? pickDownloads(release) : [];
  const latest = release ? versionOf(release.tag_name) : '';
  const update = release ? isNewer(latest, props.running) : false;
  const sums = release?.assets.find((a) => a.name === 'SHA256SUMS');
  const flash = release?.assets.find((a) => a.name === 'FLASH-handheld.txt');
  const link = { color: t.accent, fontWeight: 700 } as const;

  return (
    <Card
      title={release ? `Downloads · ${release.name || `hondalog ${latest}`}` : 'Downloads'}
      right={<Button onClick={load} disabled={state === 'loading'} testId="downloads-refresh">{state === 'loading' ? 'Checking…' : 'Check again'}</Button>}
      style={{ maxWidth: 1100 }}
    >
      <div data-testid="downloads-status" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8, fontSize: 14 }}>
        <span>Running <span style={mono}>{props.running}</span></span>
        {release && <span>· latest <span style={mono}>{latest}</span>{release.published_at ? ` (${release.published_at.slice(0, 10)})` : ''}</span>}
        {release && (update
          ? <span data-testid="downloads-update" style={{ background: t.accent, color: t.onAccent, fontWeight: 800, padding: '2px 8px' }}>UPDATE AVAILABLE</span>
          : <span style={{ color: t.ok, fontWeight: 700 }}>up to date</span>)}
        {state === 'error' && <span style={{ color: t.alarm, fontWeight: 700 }}>{release ? `offline, showing the last check: ${error}` : `could not check: ${error}`}</span>}
      </div>

      {downloads.length > 0 ? (
        <div role="table" aria-label="release downloads" style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto auto', columnGap: 14, rowGap: 6, alignItems: 'center' }}>
          {downloads.map((d) => {
            const here = d.key === mine || (mine === 'linux' && d.key === 'appimage' && !downloads.some((x) => x.key === 'linux'));
            return [
              <span key={`${d.key}-l`} role="cell" style={{ fontWeight: 800, fontSize: 15, whiteSpace: 'nowrap' }}>
                {d.label}{here && <span style={{ marginLeft: 8, fontSize: 11, border: `1.5px solid ${t.accent}`, color: t.accent, padding: '0 4px' }}>THIS DEVICE</span>}
              </span>,
              <span key={`${d.key}-w`} role="cell" style={{ fontSize: 13, color: t.fg2, minWidth: 0 }}>
                <span style={{ ...mono, color: t.fg }}>{d.asset.name}</span> · {d.what}
              </span>,
              <span key={`${d.key}-s`} role="cell" style={{ ...mono, fontSize: 13, whiteSpace: 'nowrap' }}>{sizeText(d.asset.size)}</span>,
              <a key={`${d.key}-a`} role="cell" data-testid={`download-${d.key}`} href={d.asset.browser_download_url} target="_blank" rel="noopener noreferrer"
                style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center', minHeight: 40, padding: '0 16px',
                  background: here ? t.accent : t.panel, color: here ? t.onAccent : t.fg, border: `2px solid ${here ? t.accent : t.rule}`,
                  fontWeight: 800, fontSize: 14, textDecoration: 'none', whiteSpace: 'nowrap',
                }}>Download</a>,
            ];
          })}
        </div>
      ) : state !== 'loading' && (
        <Note>No files to show. All releases: <a href={RELEASES_PAGE} target="_blank" rel="noopener noreferrer" style={link}>{RELEASES_PAGE}</a></Note>
      )}

      {release && (
        <Note style={{ marginTop: 12 }}>
          <a href={release.html_url} target="_blank" rel="noopener noreferrer" style={link}>Release notes</a>
          {sums && <> · <a href={sums.browser_download_url} target="_blank" rel="noopener noreferrer" style={link}>SHA256SUMS</a></>}
          {flash && <> · <a href={flash.browser_download_url} target="_blank" rel="noopener noreferrer" style={link}>CYD flashing steps</a></>}
          {' · '}<a href={`https://github.com/${REPO}/releases`} target="_blank" rel="noopener noreferrer" style={link}>all releases</a>
        </Note>
      )}
      {mine === 'android' && (
        <Note>On the radio, Download opens the browser. When the APK has downloaded, open it to update: it installs over this
          version and keeps the drives. A test-signed APK can't update a release-signed one.</Note>
      )}
      <Note>This tab asks GitHub (api.github.com) for the newest release when it opens. Nothing about you or your drives is sent.</Note>
    </Card>
  );
}
