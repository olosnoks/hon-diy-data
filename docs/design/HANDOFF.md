# hondalog UI: handoff for the Qt implementer

Design canvas (interactive mockups, 10 artboards at 1366x768):
https://claude.ai/artifact/UZP8DiarTkdK1RWKmk7vSS

Mockup sources (generated): `/home/claude/design/canvas/project/*.dc.html`.
Generator: `/home/claude/design/build/build.py` + `shared.js`. The simulator,
tile logic and 3D scene code in `shared.js` are the reference behaviour.

Status of the mockups: the page logic was run in Node against stubs (every
template value resolves, the three.js r149 scene builds in all 9 mode
combinations). The canvas itself was not opened or screenshotted, so no
exported images exist in this folder. Treat pixel positions below as the
spec and the canvas as illustration.

Target: ThinkPad T430, 1366x768 TN panel, Intel HD 4000 (Mesa), Debian 13,
Qt 6.8 / PySide6. All data in the mockups is simulated.

---

## 1. Decisions

### Theme: ship both, Daylight is the default
- **Daylight** (black on white) is the default and the one to use in the car
  by day. A TN panel in sun loses its blacks to glare first; a white ground
  keeps the full luminance range for the ink, and the panel's off-axis
  contrast inversion hurts dark greys far more than black-on-white.
- **Night** (white on true black) is for dusk, garage and night driving, where
  a white screen is a glare source.
- **F9** toggles at any time, on any screen. Persist the choice in QSettings.
- Both themes use the same layout, shapes and weights. Only tokens change.
- Normal state has no colour. Colour appears only for warn, alarm, verified,
  unverified and the accent. That keeps a colour meaning something.

### 3D views that earn their place
| View | Verdict | Why |
|---|---|---|
| Observed map surface (RPM x MAP, Z = mean logged injector ms / ignition / trim, colour = trim or samples), live point, 7 s trail | **Build** | Works today on the stock P2P: the surface is rebuilt from the log, so it shows the ECU's real fuel and spark behaviour before any chip. Trim as colour shows lean/rich regions at a glance. |
| Time-in-cell 3D bars | **Build** (mode of the same scene) | Shows where the engine lives, and which cells have enough samples to trust. |
| Stall approach paths on the map | **Build** (mode of the same scene) | Replaces the "3D stall timeline". Answers "do all stalls happen in the same operating region?", which a time plot cannot. |
| 3D stall timeline (time as a third axis) | **Rejected** | Time-synced signals read better as stacked 2D lanes with one cursor. 3D adds occlusion and hides the order of events. |
| Tuning surface next to the 2D table | **Build** (same scene, Z = table) | Standard in Hondata / Crome / TunerPro: spikes and holes show in 3D, exact values are edited in 2D. |

One scene class, three modes, two hosts (Map 3D screen, Tune screen).

### Qt approach: QtWidgets + pyqtgraph (2D) + pyqtgraph.opengl (3D)
Recommended over a QML / Qt Quick 3D rewrite. Reasons:
1. The app is already about 1,500 lines of PySide6 widgets and pyqtgraph.
   Poller, logger, replay and raw tools keep working.
2. The replay lanes need pyqtgraph's `setDownsampling(auto=True)` and
   `setClipToView(True)` for 1 kHz black-box data. Qt Quick has no equal
   without Qt Graphs.
3. Licence: Qt Quick 3D and Qt Graphs are GPLv3/commercial only. Widgets
   (LGPL) and pyqtgraph (MIT) leave the project's licence open.
4. The 3D scenes are tiny (under 6k triangles). `GLViewWidget` with
   `GLSurfacePlotItem`, `GLMeshItem`, `GLScatterPlotItem`, `GLLinePlotItem`
   and `GLTextItem` covers everything here. `GLLinePlotItem(width=2)` gives
   real 2 px lines, which Qt Quick 3D cannot do without custom geometry.
5. Tiles are custom-painted `QWidget`s (one `paintEvent`, QPainter). 32 tiles
   at 10 Hz is trivial, and painting by hand is the only way to get exact
   token colours, 2 px rules and tabular numerals from widgets.

Cost of this choice: no free animation, theme switch is "set tokens, call
`update()` on everything", and pyqtgraph.opengl needs an OpenGL
compatibility profile. Check on the T430 first:
`glxinfo | grep -i "opengl version"` (must show a non-core context, 3.0+).
If it does not, fall back to QML + Qt Quick 3D: tiles become a `Repeater`,
tokens a QML singleton, the surface a `QQuick3DGeometry` subclass, bars and
trail `QQuick3DInstancing`, labels 2D `Text` placed with
`View3D.mapFrom3DScene()`.

Do not style with Qt stylesheets beyond buttons. Use one `Theme` object
(dict of QColor) and paint from it.

---

## 2. Design tokens

### Colours (contrast measured with the WCAG 2.x formula)

Daylight
| Token | Hex | Used on | Ratio |
|---|---|---|---|
| bg / panel | #FFFFFF | | |
| sunk (tracks, group headers) | #E9ECEF | | |
| fg | #000000 | bg / sunk | 21.00 / 17.71 |
| fg2 (labels, captions) | #1F2428 | bg / sunk | 15.66 / 13.20 |
| rule (2 px borders) | #000000 | bg | 21.00 |
| rule2 (track outline, dividers; non-text) | #5A6570 | bg / sunk | 5.95 / 5.02 |
| accent | #0A3FA8 | bg / sunk | 9.17 / 7.74 |
| on-accent | #FFFFFF | accent | 9.17 |
| warn fill | #FFD000 | text on it #000000 | 14.27 |
| alarm fill | #B00020 | text on it #FFFFFF | 7.33 |
| ok (verified) | #00572D | bg | 8.75 |
| unverified | #7A3300 | bg / sunk | 9.13 / 7.70 |
| series 1 (ECU traces) | #0A3FA8 | bg | 9.17 |
| series 2 (black-box traces) | #963200 | bg | 7.63 |

Night
| Token | Hex | Used on | Ratio |
|---|---|---|---|
| bg | #000000 | | |
| panel | #0B0E11 | | |
| sunk | #1A1F25 | | |
| fg | #FFFFFF | bg / panel / sunk | 21.00 / 19.35 / 16.58 |
| fg2 | #D6DCE2 | panel / sunk | 14.00 / 12.00 |
| rule and rule2 (non-text) | #8B96A1 | bg / panel | 6.97 / 6.43 |
| accent | #6CB6FF | panel / sunk | 9.01 / 7.72 |
| on-accent | #000000 | accent | 9.77 |
| warn fill | #FFC400 | text on it #000000 | 13.15 |
| alarm fill | #FF7A66 | text on it #000000; as text on panel | 8.23; 7.58 |
| ok | #4ADE80 | panel | 11.11 |
| unverified | #FFB454 | panel / sunk | 10.97 / 9.40 |
| series 1 | #6CB6FF | panel | 9.01 |
| series 2 | #FF9D4D | panel / sunk | 9.38 / 8.04 |

Every text pairing is 7:1 or better. The two below 7:1 are non-text rules
(3:1 is the bar there). Series 2 in Daylight is 6.43 on `sunk`, so never put
series-2 text on `sunk`; use a colour swatch plus `fg` text, as the replay
group headers do.

Theme-independent
| Use | Value |
|---|---|
| Live operating point | #E6007A sphere with a `fg` outline shell (4.5:1 on either ground, outline carries the contrast) |
| Sequential ramp (values, samples) | #00224E, #35456C, #666970, #948E77, #C8B866, #FEE838 (cividis-like, colour-blind safe, monotonic lightness) |
| Diverging ramp (fuel trim) | #053061, #2166AC, #F7F7F7, #E08214, #7F3B08 (blue = rich / ECU pulling fuel, orange = lean / ECU adding) |
| No-data cell in 3D | Daylight #CCD1D6, Night #3D454D |
| Table heat, Daylight | #FFFFFF to #FFD000 to #FF9600, text #000000 (9.61 at the hot end) |
| Table heat, Night | #0B0E11 to #0A3FA8, text #FFFFFF (9.17 at the hot end) |

### Type
Bundle the fonts with the app (`QFontDatabase.addApplicationFont`), both
OFL: **Inter** (500/600/700/800) for everything, **JetBrains Mono**
(500/700/800) for hex, addresses, key caps, min/max. Turn on tabular
figures for Inter: `font.setFeature(QFont.Tag("tnum"), 1)` (Qt 6.7+). Never use a
weight below 500; thin strokes vanish in glare.

| px | Weight | Use |
|---|---|---|
| 104 | 800 | Drive tile value, MARK word |
| 52 | 800 | Stall-catcher state word, Tune selected/live value |
| 40 | 800 | Sensor tile value |
| 38 / 36 / 34 | 800 | Map readouts / flag ON-OFF / byte inspector |
| 26 | 800 | Replay cursor time |
| 22 | 800 / 700 | Drive tile label and units, wizard step title |
| 19 | 800 | Wordmark |
| 18 | 700-800 | Drive min/max, banner title |
| 15-16 | 700 | Tabs, body emphasis, mono values in lists |
| 14 | 600-700 | Body, buttons, tile units |
| 13 | 700-800 | Tile labels (caps, +0.04em), chips, mono secondary |
| 12 | 700-800 | Minimum. Tile min/max, key caps, captions (caps, +0.06em) |

### Spacing and rules
- Base unit 2 px. Screen padding 8. Tile gap 6. Panel gap 8. Card padding 8/10.
- Every border is 2 px (4 px for the one emphasised box per screen).
  No 1 px lines anywhere, including chart grids and the 3D wireframe.
- Controls 44 px tall (36 px inside the status bar). Corners are square.
- No shadows, gradients or transparency in the UI chrome.

---

## 3. Frame shared by every screen (1366x768)

| Region | y | h | Content |
|---|---|---|---|
| Status bar | 0 | 44 | Wordmark; chips: ECU, BLACK BOX, REC, STALL CATCHER; spacer; theme button (F9); MARK button (min 150 wide, accent fill) |
| Tab bar | 44 | 44 | F1 Sensors, F2 Drive, F3 Map 3D, F4 Replay, F5 Raw tools, F6 Tune, F7 Alarms. Active tab = inverted (fg fill, bg text). Right: port, map id, channel and unverified counts (mono 13) |
| Content | 88 | 648 | Per screen |
| Alarm strip | 736 | 32 | Left: alarm segment (alarm fill, octagon), warn segment (warn fill, triangle), or "No active alarms" with a filled ok circle. Right: key hints (mono 13) |

Chips: 32 high, 2 px border, 13/700, icon 14 px then text then mono figures.

Chip icon shapes (shape first, colour second):
- filled circle (ok colour): link up
- hollow square (3 px): off / not connected
- half-filled circle: connecting
- ring (4 px): stall catcher armed
- filled circle, blinking 1.2 s (alarm colour): recording
- X in an alarm-filled chip: ECU silent

Stall banner: when the catcher fires, a 44 px alarm-fill bar covers the tab
bar row (y 44-88) so content does not move. Octagon, "STALL hh:mm:ss",
one-line cause, "Open in replay (Enter)", "Dismiss (Esc)".

---

## 4. Components

### Sensor tile (163 x 142, value channel)
```
+--------------------------------------+  2 px border (rule)
| [icon] LABEL                    [?]  |  header 24: pad L8 R6, label 13/800 caps
| 2150 rpm                             |  value row 44: value 40/800, units 14/700
| [=========|====     |           ]    |  linear gauge 10 high, margin 3/8/4
| ~~~~~~~~~sparkline~~~~~~~~~~~~~~~    |  30 high, full width
| v 742                       ^ 3820   |  mono 12/700, row 18
+--------------------------------------+
```
- **Linear gauge**: track = sunk with 2 px rule2 outline; fill = fg from
  channel min to value; two accent ticks 3 px wide, 5 px taller than the
  track on each side, at session min and session max.
- **Sparkline**: 120 samples = last 60 s at 2 Hz, 2 px stroke in fg, round
  joins, no fill, no axes. Auto-ranged to the window with a minimum span of
  4 % of the channel range so noise does not look like signal.
- **Verified / unverified marker**, always top right, 18 x 18:
  unverified = dashed 2 px box with "?" in the unverified colour;
  verified = solid ok-colour square with a 2.5 px check in panel colour.
- **Flag tile**: 30 px square with 5 px border, filled = ON, hollow = OFF,
  word ON/OFF at 36/800; the sparkline is a step trace; footer
  "n changes / 60 s".

### Alarm states (same tile)
| State | Shape and position | Colour |
|---|---|---|
| Normal | plain | none |
| Warn | triangle left of the label, header band filled, 3 px inset ring | warn fill, black text |
| Alarm | octagon left of the label (blinks 1 s until acknowledged), whole tile filled, gauge and marker switch to the on-alarm colour | alarm fill |
| Stale / no data | 45 degree hatch, 8 px stripes, value shows `---` | sunk / panel |

Rules: warn has no sound. Alarm beeps twice (880 Hz, 200 ms), writes an
auto-MARK into the log, and latches until the value has been back in range
for 2 s. Esc acknowledges (stops the blink, keeps the fill). Limits live in
`addressmap.toml` per channel: `warn_lo`, `warn_hi`, `alarm_lo`, `alarm_hi`.

### Drive tile (328 x 312)
Header 40 (label 22/800 caps, units 22/700, marker). Value 104/800,
right-aligned in a 150 px row. Gauge 22 high (margin 6/14/10). Sparkline 44.
Footer mono 18/700: min, source tag (ECU / BLACK BOX), max.

### Buttons and segmented controls
44 high, 2 px border, 14/700. Primary = accent fill. Selected segment =
inverted. Disabled = sunk fill with a dashed border. Key caps are mono
12/700 in a 2 px box next to the label.

---

## 5. Screen layouts (all coordinates in px, origin top-left of the window)

### 5.1 All sensors (F1): 32 channels, no scrolling
- Content padding 6 top/bottom, 8 left/right. Inner width 1350.
- Section header "ECU - K-line" at y 94, h 18, with the legend on the right.
- ECU grid: 8 columns, gap 6, tile 163.5 x 142. Rows at y 116, 264, 412.
  Order, row-major: RPM, Speed, MAP, Throttle, Injector, Ign BTDC, O2,
  Short trim / Long trim, Coolant, Intake air, Baro, Battery, Alt load,
  Elec load, Idle valve / Knock, Ign limit, then flags Closed loop, VTEC,
  A/C, Rad fan, Fuel pump, MIL.
- Section header "Black box - USB" at y 558, h 18.
- Black-box row at y 580: Battery, ECU +B, FP feed, IG1, Tach, Ign trigger,
  Inj pulse, Marker (flag).
- A 33rd to 40th channel goes in a fifth row by cutting tile height to 112
  (drop the min/max footer, sparkline 22).

### 5.2 Driving view (F2)
- Left: 3 x 2 grid at x 8, y 96, tiles 328.67 x 312, gap 8. Default slots:
  RPM, Speed, Coolant, Battery, FP feed (black box), Short trim. The six
  slots are user-assignable; 4-up uses a 2 x 2 grid of 497 x 312.
- Right column x 1018, w 340: stall-catcher card on top (state word 52/800
  with a 44 px ring, buffer bar 18 high, one sentence on triggers, stall
  count), then the MARK button filling the rest (about 340 x 370, accent
  fill, word 104/800, key cap, "n marks - last 14 s ago").
- Stall-catcher states: OFF (hollow square), ARMED (ring), TRIGGERED
  (octagon, card filled alarm, shows the saved file name), SAVING (+10 s
  countdown bar).

### 5.3 Map 3D (F3)
- 3D viewport: x 8, y 96, 968 x 632 including the 2 px border (GL surface
  964 x 628). Title and one-line caption in an opaque panel box, top left.
  Marker key in an opaque box, bottom right.
- Side panel x 984, w 374, 8 px gaps, top to bottom: View segment (Surface /
  Time in cell / Stall paths), Height segment (Injector ms / Ignition /
  Fuel trim %), Colour segment (Same as Z / Fuel trim / Samples), Operating
  point card (RPM, MAP, Z at point, samples in cell; 38/800), Legend card
  (22 px ramp bar, three mono labels, no-data swatch), Camera segment (Iso /
  Top / RPM side / MAP side), Coverage card (cells with data, bar).

### 5.4 Stall forensics replay (F4)
- Left column x 8, w 968.
  - Toolbar y 96, h 44: previous event, play, next event, file name, spacer,
    cursor time (mono 26/800).
  - Plot block y 146: label column 150 wide (name 13/800, value at cursor
    mono 15/700), plot 814 wide. Window is -30 s to +10 s (20.35 px/s).
    Group header 16, then five ECU lanes of 40; group header 16, five
    black-box lanes of 40; group header 16, three state lanes of 20; time
    axis 22 with labels every 5 s.
  - ECU lanes: RPM, MAP, TPS, O2, INJ in series 1. Black-box lanes: Battery,
    ECU +B, FP feed, IG1, Tach in series 2. States: Fuel pump cmd, Closed
    loop, Marker in fg. 2 px traces, no fill.
  - Markers: MARK n = 3 px dashed fg line with an inverted name tag at the
    top. STALL = 4 px solid alarm line with an alarm tag.
  - Cursor: 4 px accent line through all lanes with a triangle handle at the
    bottom. Click or drag anywhere in the plot to scrub. A full-width slider
    sits under the block for keyboard use.
  - The two sources are aligned on tach edges; show the residual in the
    header if it exceeds 5 ms.
- Right column x 984, w 374: "Order of loss" list (rank, time before stall,
  signal, what happened; the first row is alarm-filled; click jumps the
  cursor), "Held up through the stall" card, "Reading" card (4 px border,
  one sentence), "Jump to" segment.
- Order of loss is computed, not typed: for each channel find the first time
  it leaves its normal band in the 30 s before the trigger, then sort.

### 5.5 Raw tools and memory mapper (F5)
- Left column x 8, w 772.
  - Toolbar h 44: Sweeping toggle, Clear changes, range and sweep counter,
    Export.
  - Hex grid: row-header column 58, sixteen columns of about 44, header row
    22, sixteen rows of 26. JetBrains Mono 14.
  - Byte states: changing now (changed within the last sweep) = warn fill,
    800 weight; changed earlier = sunk fill with a 4 px accent bar along the
    bottom, 700 weight; in the address map but unverified = dashed 2 px
    inset outline; selected = solid 3 px fg outline. Static bytes are fg2 at
    500 weight.
  - Legend row 26, then the hex console card (TX field 40 high, "+ checksum",
    Send, RX line).
- Right column x 788, w 570.
  - Byte inspector: address and value (mono 34/800), decimal, eight bit boxes
    of 30 px (filled = 1), 40 px history sparkline, min / max / change count,
    address-map line.
  - Guided mapping wizard: five numbered steps (Target, Baseline, Stimulus,
    Candidates, Confirm). Done = ok-filled box with a check, current =
    inverted, to do = hollow. One instruction box with a 4 px border (title
    22/800, one or two lines of 15/600). Candidate table: address, a bar for
    how closely the byte followed the stimulus, range seen, value now; rows
    are 44 high and select the byte in the grid. Buttons: Back, "Not it, try
    again", primary action.
  - Ranking: Pearson correlation between each byte and the expected stimulus
    shape (three presses), bytes that moved during Baseline are excluded.

### 5.6 Tuning table editor (F6, chipped P28 only)
- Left column x 8, w 560: table segment h 44 (Fuel low cam, Fuel VTEC,
  Ignition low cam, Ignition VTEC); grid with a 60 px RPM header column, ten
  MAP columns of about 49, header row 24, twenty RPM rows of 24, mono
  13/600; edit bar h 44 (minus, plus, Interpolate, Smooth, Undo).
- Cell states: heat fill by value; live cell = solid 3 px fg outline and 800
  weight; selection = dashed 3 px accent outline; changed since load =
  filled triangle in the top-right corner.
- Right column x 576, w 782: 3D viewport 782 x 404 (GL 778 x 400) with the
  live point and a diamond on the selected cell; below, three cards:
  Selected cell (value 52/800, base, delta %), Live cell (interpolated
  value, current trim), Chip (changed count, Save .bin, Write to chip
  disabled until a P28 with an emulator or burner is present).
- On the stock P2P the tab is visible but read-only with that explanation.

### 5.7 Status bar and alarms (F7)
- Top row: connection-bar states (Disconnected, Connecting, Live, ECU lost)
  and the shape key.
- Stall banner sample (44 high).
- Row of eight tile states with captions.
- Alarm list (rows 34 high: icon, severity, channel, value, limit, age;
  active rows are filled) and the limits table.

---

## 6. 3D scene spec

Coordinate system: X = RPM (500 to 7500 maps to -1..+1), Z = MAP (17 to
101 kPa maps to -0.7..+0.7), Y = value (range min..max maps to 0..0.9).

| Item | Spec |
|---|---|
| Camera | Perspective, vertical FOV 30 degrees, target (0, 0.3, 0), distance 4.6 (4.9 in Tune). Default azimuth -0.62 rad, elevation 0.50 rad. Elevation clamp 0.08 to 1.5 rad. Presets: Iso (-0.62, 0.50), Top (0, 1.5), RPM side (0, 0.1), MAP side (-pi/2, 0.1). |
| Orbit | Left drag: 0.008 rad/px azimuth, 0.006 rad/px elevation. Wheel: distance 3 to 8. The mockup sways +/-0.2 rad until first touched; do not ship that. |
| Surface mesh | 20 x 10 vertices at the table breakpoints (19 x 9 quads, 342 triangles). Unlit, per-vertex colour from the ramp so colours match the legend exactly. Double sided. |
| Wireframe | Grid lines along both axes on top of the surface, fg colour, 2 px (`GLLinePlotItem(width=2, mode='lines')`), polygon offset on the surface to stop z-fighting. |
| Frame | Floor grid at the breakpoints plus four height levels on the two back walls, rule2 colour, 2 px. |
| Time-in-cell bars | One box per cell with samples, footprint 0.082 x 0.12, height = log(1+n)/log(901) x 0.9, colour from the sequential ramp, flat Lambert (ambient 0.72, one directional 0.45 from (-1.5, 3, 2)). Merge into one mesh: 200 boxes, 2,400 triangles, one draw call. |
| Live point | Sphere radius 0.036 in #E6007A inside a back-face shell radius 0.052 in fg. Drop line to the floor plus two floor lines to the RPM and MAP edges, fg, 2 px. Position is bilinear on the surface. |
| Trail | Last 60 positions sampled every 0.12 s (about 7 s). Spheres from radius 0.007 (oldest) to 0.020 (newest), fg colour. One scatter item. |
| Stall paths | Per stall file: 40 points over the last 30 s, sphere radius 0.011 to 0.025 growing toward the stall, one colour per file (series 1, series 2, #6A1B9A / #D2A8FF), octahedron radius 0.06 where the engine died. Surface drawn in the no-data grey. |
| Selected cell (Tune) | Octahedron radius 0.045 in accent, 0.07 above the vertex. |
| Labels | RPM ticks every 1000, MAP ticks at 20/40/60/80/100, five Z ticks, three axis titles. 2D text over the GL view (`GLTextItem`), Inter 700, 16 px ticks and 20 px titles, fg colour. |
| No-data cells | Vertices with zero samples take the no-data grey, whatever the colour mode (logged views only). |

Update policy
- Data arrives at about 10 Hz. Move the live point, trail and readouts at
  that rate. Rebuild the surface vertex buffer at most once per second while
  logging, and immediately on a table edit.
- Render on demand: repaint only when data changed or the camera moved. No
  free-running loop when idle. Cap orbit redraws at 60 fps.

Budget for HD 4000 at 964 x 628
| Limit | Value |
|---|---|
| Draw calls per frame | 40 max (about 12 geometry + about 20 text labels) |
| Triangles | 6,000 max (typical: 342 surface, 2,400 bars, 2,000 spheres) |
| MSAA | 4x. Drop to 2x, then off, if a frame takes more than 12 ms |
| Lights | 1 ambient + 1 directional, bars only |
| Not allowed | Shadows, post-processing, translucent surfaces, textures other than text |
| Frame time target | Under 8 ms while orbiting |

Mockup differences to ignore: WebGL draws 1 px lines (ship 2 px), labels are
sprite textures (ship 2D text), and the mockup falls back to a flat canvas
painter if WebGL is missing.

---

## 7. Interaction and keyboard map

Global
| Key | Action |
|---|---|
| F1 to F7 | Sensors, Drive, Map 3D, Replay, Raw tools, Tune, Alarms |
| F9 | Toggle Daylight / Night |
| Space | MARK, always, except while a text field has focus |
| Esc | Acknowledge alarms; dismiss the stall banner |
| Enter | Open the caught stall in Replay (while the banner shows) |
| Ctrl+R | Start / stop recording |
| F11 | Full screen |

Sensors: click a tile to pin it to the Drive view; right-click for limits
and "mark verified".

Map 3D
| Key | Action |
|---|---|
| Arrow keys | Orbit 5 degrees |
| PgUp / PgDn | Zoom |
| 1 2 3 4 | Camera presets Iso, Top, RPM side, MAP side |
| V / Z / C | Cycle view mode / height channel / colour |

Replay
| Key | Action |
|---|---|
| Left / Right | Step 0.1 s (Shift: 1 s) |
| Home / End | Start / end of file |
| [ / ] | Previous / next event (marks, losses, stall) |
| P | Play / pause |
| + / - | Zoom the time axis around the cursor |
| 0 | The whole drive |
| Ctrl+Left / Ctrl+Right | Pan a quarter of the window |
| Wheel / Shift+wheel / right drag | Zoom around the pointer / pan / pan |
| Click or drag in the plot | Move the cursor |
| D / Shift+click | Second cursor (B) at the cursor / where clicked; readouts show A minus B |
| C | Compare: lay a second drive over this one (again: what to line up on) |
| A | Next alignment (first sample, first marker, stall, RPM through 400) |
| , . / < > | Slide the second drive 0.1 s / 1 s |
| Shift+C | Remove the second drive |
| O / L | Open the drive picker / back to live |

Raw tools
| Key | Action |
|---|---|
| Arrow keys | Move the selected byte |
| S | Sweep on / off |
| C | Clear changes |
| N / B | Wizard next / back |
| Enter | Send the hex console line |

Tune
| Key | Action |
|---|---|
| Arrow keys | Move (Shift: extend selection) |
| = / - | Raise / lower (fuel 0.05 ms, ignition 0.5 degrees; Shift: x5) |
| I / S | Interpolate / smooth the selection |
| T | Next table |
| Ctrl+Z / Ctrl+Y / Ctrl+S | Undo / redo (also Ctrl+Shift+Z) / save .bin |
| Ctrl+O / Ctrl+D / Ctrl+B | Open ROM… / Definition… / Burn image… |

---

## 8. Things to confirm with the owner
- Units. The mockups keep the app defaults (km/h, degrees C, kPa). The
  address map already allows mph and degrees F per channel. Settled: the
  owner's default is US (mph, degrees F), MAP stays in kPa, volts as they
  are; Settings > Units changes it (`hondalog/ui/units.py`, display only).
- Alarm limits shown are placeholders (coolant warn 100 / alarm 108 C,
  battery warn below 12.8 / alarm below 11.5 V, black-box rails alarm below
  11 V, trims warn at 12 to 15 %).
- The black-box channel list and rates (8 channels, 1 kHz) are assumed from
  the brief.
- The P28 table size (20 RPM x 10 MAP per cam) is the usual OBD1 layout;
  confirm against the actual bin.
