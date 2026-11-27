/**
 * F1 Live: every sensor grouped by system (design canvas artboard 2, ported
 * from screens/sensors.py). Six groups in a 3 x 2 grid, the switches strip,
 * and the raw bytes drawer.
 */
import { useShallow } from 'zustand/react/shallow';
import { CompactContext, useCompact } from './compact';
import { useSession } from '../../core/session';
import { FONTS } from '../theme';
import { COMPACT_BELOW, useStageSize } from '../shell/Stage';
import { useTheme } from '../shell/useTheme';
import { IdleBar } from './IdleBar';
import { extraSwitches, GROUPS, SWITCHES, tilesFor, type GroupDef } from './layout';
import { RAW_PANEL, RawDrawer, unmappedAddrs } from './RawDrawer';
import { Tile } from './Tile';

function Group({ group }: { group: GroupDef }) {
  const t = useTheme();
  const compact = useCompact();
  const sub = useSession((s) => group.sub(s));
  const tiles = useSession(useShallow((s) => tilesFor(group, s.channels).map((d) => d.name)));
  const defs = tiles.map((n) => group.tiles.find((d) => d.name === n) ?? { name: n });
  return (
    <section aria-label={group.title} style={{ border: `2px solid ${t.rule}`, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, padding: compact ? '3px 8px' : '6px 10px', borderBottom: `1px solid ${t.rule}`, flexShrink: 0 }}>
        <h2 style={{ margin: 0, fontSize: 13, fontWeight: 800, letterSpacing: '0.06em' }}>{group.title}</h2>
        <span style={{ fontSize: 12, color: t.fg2, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{sub}</span>
      </div>
      {group.idleBar && <IdleBar compact={compact} />}
      {/* never cut a row off: the tiles scroll inside the group if they still do not fit */}
      <div data-testid={`tiles-${group.id}`} style={{ flexGrow: 1, minHeight: 0, overflowY: 'auto', display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
        gap: compact ? 4 : 6, padding: compact ? '4px 6px' : '8px 10px', alignContent: 'start' }}>
        {defs.map((d) => <Tile key={d.name} def={d} />)}
      </div>
    </section>
  );
}

function Switch({ name, label }: { name: string; label: string }) {
  const t = useTheme();
  const st = useSession((s) => s.channels[name]);
  const known = !!st && st.fresh && st.value !== null;
  const on = known && st!.value! >= 0.5;
  return (
    <span
      data-testid={`switch-${name}`}
      data-on={on || undefined}
      title={known ? `${label}: ${on ? 'on' : 'off'}` : `${label}: no data`}
      style={{
        fontSize: 12, fontWeight: 700, padding: '5px 9px',
        border: `2px ${known ? 'solid' : 'dashed'} ${on ? t.rule : t.line}`,
        background: on ? t.accent : 'transparent', color: on ? t.onAccent : known ? t.fg2 : t.comment,
      }}
    >
      {label}
    </span>
  );
}

export default function LiveScreen() {
  const t = useTheme();
  const panel = useSession((s) => s.panel);
  const togglePanel = useSession((s) => s.togglePanel);
  const rawCount = useSession((s) => unmappedAddrs(s).length);
  const extras = useSession(useShallow((s) => extraSwitches(s.channels).map((x) => `${x.name}\u0000${x.label}`)));
  const switches = [...SWITCHES, ...extras.map((x) => { const [name, label] = x.split('\u0000'); return { name: name!, label: label! }; })];
  const open = panel === RAW_PANEL;
  const compact = useStageSize().height < COMPACT_BELOW;
  return (
    <CompactContext.Provider value={compact}>
    <div style={{ position: 'relative', flexGrow: 1, minHeight: 0, display: 'flex', flexDirection: 'column', background: t.bg, color: t.fg, fontFamily: FONTS.sans }}>
      <div data-compact={compact || undefined} style={{ flexGrow: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gridTemplateRows: 'repeat(2, minmax(0, 1fr))', gap: compact ? 6 : 8, padding: compact ? 6 : 8 }}>
        {GROUPS.map((g) => <Group key={g.id} group={g} />)}
      </div>
      <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 6, padding: compact ? '0 6px 6px 6px' : '0 8px 8px 8px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, fontWeight: 800, letterSpacing: '0.06em', marginRight: 4 }}>SWITCHES</span>
        {switches.map((s) => <Switch key={s.name} name={s.name} label={s.label} />)}
        <div style={{ flexGrow: 1 }} />
        <button
          type="button"
          aria-expanded={open}
          onClick={() => togglePanel(RAW_PANEL)}
          style={{
            fontFamily: FONTS.sans, fontSize: 12.5, fontWeight: 700, background: open ? t.accent : t.bg,
            color: open ? t.onAccent : t.fg, border: `2px solid ${t.rule}`, padding: '6px 12px', minHeight: 32, cursor: 'pointer',
          }}
        >
          Raw bytes ({rawCount}) {open ? '◂' : '▸'}
        </button>
      </div>
      {open && <RawDrawer />}
    </div>
    </CompactContext.Provider>
  );
}
