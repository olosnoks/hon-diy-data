/**
 * Pin shapes (port of draw_pin in hondalog/ui/mapview.py): stall = octagon
 * in the alarm colour, near-stall = triangle in warn, link dropout = square
 * in orange, other capture = diamond in accent, alarm = triangle in alarm,
 * marker = a numbered badge, start of the track = a ring. All outlined in fg
 * so they read on any tile.
 */
import type { Theme } from '../theme';

export type PinKind = 'stall' | 'nearstall' | 'dropout' | 'capture' | 'alarm' | 'marker' | 'start';

export const PIN_LABEL: Record<PinKind, string> = {
  stall: 'stall', nearstall: 'near-stall', dropout: 'dropout', capture: 'capture', alarm: 'alarm', marker: 'mark', start: 'start',
};

export function pinSvg(kind: PinKind, theme: Theme, size = 22, label = '', selected = false): string {
  const s = size, c = s / 2, r = s / 2 - 2;
  const stroke = `stroke="${selected ? theme.livePoint : theme.fg}" stroke-width="${selected ? 3 : 2}"`;
  let shape = '';
  switch (kind) {
    case 'stall': {
      const pts = Array.from({ length: 8 }, (_, i) => {
        const a = Math.PI / 8 + (i * Math.PI) / 4;
        return `${(c + r * Math.cos(a)).toFixed(1)},${(c + r * Math.sin(a)).toFixed(1)}`;
      }).join(' ');
      shape = `<polygon points="${pts}" fill="${theme.alarm}" ${stroke}/>`;
      break;
    }
    case 'nearstall':
      shape = `<polygon points="${c},2 ${s - 2},${s - 3} 2,${s - 3}" fill="${theme.warn}" ${stroke}/>`;
      break;
    case 'alarm':
      shape = `<polygon points="${c},2 ${s - 2},${s - 3} 2,${s - 3}" fill="${theme.alarm}" ${stroke}/>`
        + `<text x="${c}" y="${s - 5}" text-anchor="middle" font-size="${s * 0.5}" font-weight="800" fill="${theme.onAlarm}">!</text>`;
      break;
    case 'dropout':
      shape = `<rect x="3" y="3" width="${s - 6}" height="${s - 6}" fill="${theme.unverified}" ${stroke}/>`;
      break;
    case 'capture':
      shape = `<polygon points="${c},2 ${s - 2},${c} ${c},${s - 2} 2,${c}" fill="${theme.accent}" ${stroke}/>`;
      break;
    case 'marker':
      shape = `<rect x="2" y="2" width="${s - 4}" height="${s - 4}" rx="3" fill="${theme.sunk}" ${stroke}/>`
        + `<text x="${c}" y="${c + s * 0.17}" text-anchor="middle" font-family="JetBrains Mono, monospace" font-size="${s * 0.46}" font-weight="700" fill="${theme.fg}">${label || 'M'}</text>`;
      break;
    case 'start':
      shape = `<circle cx="${c}" cy="${c}" r="${r - 1}" fill="none" stroke="${theme.fg}" stroke-width="3"/>`
        + `<circle cx="${c}" cy="${c}" r="${r - 4}" fill="none" stroke="${theme.bg}" stroke-width="2"/>`;
      break;
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${s}" height="${s}" viewBox="0 0 ${s} ${s}">${shape}</svg>`;
}
