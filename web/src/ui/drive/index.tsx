/**
 * F2 Drive: big numbers for the road (screens/drive.py). Six huge readouts,
 * rpm vs idle target and the launch margin under them, the stall catcher
 * and the MARK pad on the right (340 px).
 */
import { FONTS } from '../theme';
import { useTheme } from '../shell/useTheme';
import { IdleTargetBar, LaunchMarginBar } from './Bars';
import { BigTile } from './BigTile';
import { MarkPad } from './MarkPad';
import { StallCard } from './StallCard';

/** session.py DRIVE_DEFAULT, with throttle in the slot of the unmapped "fpv". */
export const DRIVE_SLOTS = ['rpm', 'vss', 'ect', 'batt', 'tps', 'stft'];
export const SIDE_W = 340;

export default function DriveScreen() {
  const t = useTheme();
  return (
    <div style={{ flexGrow: 1, minHeight: 0, display: 'flex', gap: 8, padding: 8, background: t.bg, color: t.fg, fontFamily: FONTS.sans }}>
      <div style={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ flexGrow: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gridTemplateRows: 'repeat(2, minmax(0, 1fr))', gap: 8 }}>
          {DRIVE_SLOTS.map((n) => <BigTile key={n} name={n} />)}
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          <IdleTargetBar />
          <LaunchMarginBar />
        </div>
      </div>
      <div style={{ width: SIDE_W, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <StallCard />
        <MarkPad />
      </div>
    </div>
  );
}
