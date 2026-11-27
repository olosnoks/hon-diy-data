/**
 * Dracula and Darker: the only two themes (the owner can't use white screens).
 * Mirrors the CYD's firmware palette (firmware/handheld/main/ui) so the
 * handheld, the desktop builds, Android and the web look the same.
 * F9 toggles. Secondary text must keep 4.5:1 on the background.
 */
export interface Theme {
  name: 'dracula' | 'darker';
  bg: string; panel: string; sunk: string; line: string; rule: string;
  fg: string; fg2: string; comment: string;
  accent: string; onAccent: string;
  warn: string; onWarn: string; alarm: string; onAlarm: string; ok: string; unverified: string;
  series: [string, string, string, string, string];
  livePoint: string;
}

export const DRACULA: Theme = {
  name: 'dracula',
  bg: '#282A36', panel: '#21222C', sunk: '#343746', line: '#44475A', rule: '#6272A4',
  fg: '#F8F8F2', fg2: '#C5C8D6', comment: '#6272A4',
  accent: '#BD93F9', onAccent: '#282A36',
  warn: '#F1FA8C', onWarn: '#282A36', alarm: '#FF5555', onAlarm: '#282A36', ok: '#50FA7B', unverified: '#FFB86C',
  series: ['#BD93F9', '#FF79C6', '#8BE9FD', '#50FA7B', '#FFB86C'],
  livePoint: '#FF79C6',
};

export const DARKER: Theme = {
  ...DRACULA,
  name: 'darker',
  bg: '#191A21', panel: '#121318', sunk: '#21222C', onAccent: '#191A21', onWarn: '#191A21', onAlarm: '#191A21',
};

export const FONTS = {
  sans: "Inter, system-ui, sans-serif",
  mono: "'JetBrains Mono', ui-monospace, monospace",
};

/** Design size the layout is drawn for (ThinkPad T430); everything scales from it. */
export const DESIGN = { width: 1366, height: 768 };
