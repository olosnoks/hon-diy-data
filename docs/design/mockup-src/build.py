#!/usr/bin/env python3
"""Builds the hondalog design canvas artboards (.dc.html) from shared parts."""
import json
import pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent / "canvas" / "project"
THREE_URL = "/_blob/c45166be96a166e1b6e8ef04f44ebef5"
SHARED_JS = (HERE / "shared.js").read_text()

CSS = r"""
body{margin:0}
.hl{--bg:#FFFFFF;--panel:#FFFFFF;--sunk:#E9ECEF;--fg:#000000;--fg2:#1F2428;--rule:#000000;--rule2:#5A6570;--acc:#0A3FA8;--accfg:#FFFFFF;--warn:#FFD000;--warnfg:#000000;--alarm:#B00020;--alarmfg:#FFFFFF;--ok:#00572D;--unv:#7A3300;--s1:#0A3FA8;--s2:#963200;width:1366px;height:768px;box-sizing:border-box;background:var(--bg);color:var(--fg);font-family:Inter,'DejaVu Sans',system-ui,sans-serif;font-feature-settings:'tnum' 1;font-size:14px;line-height:1.2;display:flex;flex-direction:column;overflow:hidden;position:relative}
.hl.night{--bg:#000000;--panel:#0B0E11;--sunk:#1A1F25;--fg:#FFFFFF;--fg2:#D6DCE2;--rule:#8B96A1;--rule2:#8B96A1;--acc:#6CB6FF;--accfg:#000000;--warn:#FFC400;--warnfg:#000000;--alarm:#FF7A66;--alarmfg:#000000;--ok:#4ADE80;--unv:#FFB454;--s1:#6CB6FF;--s2:#FF9D4D}
.hl *{box-sizing:border-box}
.hl a{color:inherit;text-decoration:none}
.hl a:hover{color:inherit}
.hl button{font:inherit;color:inherit;cursor:pointer;margin:0}
.hl :focus-visible{outline:3px solid var(--acc);outline-offset:1px}
.mono{font-family:'JetBrains Mono','DejaVu Sans Mono',monospace}
.chip{height:32px;display:flex;align-items:center;gap:8px;padding:0 10px;border:2px solid var(--rule);background:var(--panel);font-size:13px;font-weight:700;white-space:nowrap}
.chip.bad{background:var(--alarm);color:var(--alarmfg);border-color:var(--alarm)}
.chip.caution{background:var(--warn);color:var(--warnfg);border-color:var(--warn)}
.i{width:14px;height:14px;flex:none;display:inline-block}
.i.ok{background:var(--ok);border-radius:50%}
.i.off{border:3px solid currentColor}
.i.rec{background:var(--alarm);border-radius:50%;animation:hlblink 1.2s steps(2) infinite}
.i.arm{border:4px solid currentColor;border-radius:50%}
.i.half{border:3px solid currentColor;border-radius:50%;background:linear-gradient(90deg,currentColor 50%,transparent 50%)}
@keyframes hlblink{50%{opacity:.2}}
.tab{display:flex;align-items:center;gap:8px;padding:0 15px;font-size:15px;font-weight:700;border-right:2px solid var(--rule)}
.tab.on{background:var(--fg);color:var(--bg)}
.hl a.tab.on,.hl a.tab.on:hover{color:var(--bg)}
.key{font-family:'JetBrains Mono','DejaVu Sans Mono',monospace;font-size:12px;font-weight:700;border:2px solid currentColor;padding:0 4px;line-height:16px}
.btn{height:44px;min-width:44px;padding:0 14px;border:2px solid var(--rule);background:var(--panel);font-size:14px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;gap:8px;white-space:nowrap}
.btn.pri{background:var(--acc);color:var(--accfg);border-color:var(--acc)}
.btn.on{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.btn:disabled{background:var(--sunk);color:var(--fg2);border-style:dashed;cursor:default}
.seg{display:flex;gap:0}
.seg button{flex:1;height:44px;padding:0 6px;border:2px solid var(--rule);background:var(--panel);font-size:14px;font-weight:700;margin-right:-2px;white-space:nowrap}
.seg button.on{background:var(--fg);color:var(--bg);border-color:var(--fg);position:relative}
.sec{height:18px;flex:none;display:flex;align-items:center;gap:12px;font-size:13px;font-weight:600;color:var(--fg2)}
.cap{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--fg2)}
.card{border:2px solid var(--rule);background:var(--panel);padding:8px 10px;display:flex;flex-direction:column;gap:6px}
.tile{background:var(--panel);border:2px solid var(--rule);display:flex;flex-direction:column;overflow:hidden;min-width:0}
.tile.warn{box-shadow:inset 0 0 0 3px var(--warn)}
.tile.warn .thead{background:var(--warn);color:var(--warnfg)}
.tile.alarm{background:var(--alarm);color:var(--alarmfg);border-color:var(--alarm)}
.tile.alarm .thead svg{animation:hlblink 1s steps(2) infinite}
.tile.stale{background:repeating-linear-gradient(135deg,var(--sunk) 0 8px,var(--panel) 8px 16px)}
.thead{display:flex;align-items:center;gap:5px;flex:none}
.thead svg{flex:none;fill:currentColor}
.unv{width:18px;height:18px;flex:none;border:2px dashed var(--unv);color:var(--unv);font-size:13px;font-weight:800;line-height:1;display:grid;place-items:center}
.tile.warn .unv,.tile.alarm .unv{color:inherit;border-color:currentColor}
.ver{width:18px;height:18px;flex:none;background:var(--ok);display:grid;place-items:center}
.ver path{fill:none;stroke:var(--panel);stroke-width:2.5}
.tile.alarm .ver,.tile.warn .ver{background:currentColor}
.tile.alarm .ver path{stroke:var(--alarm)}
.tile.warn .ver path{stroke:var(--warn)}
.track{position:relative;flex:none;background:var(--sunk);border:2px solid var(--rule2)}
.tile.alarm .track{background:transparent;border-color:currentColor}
.track i{position:absolute;top:0;bottom:0;left:0;background:currentColor}
.track b{position:absolute;top:-5px;bottom:-5px;width:3px;margin-left:-1px;background:var(--acc)}
.tile.alarm .track b{background:currentColor}
.spark{display:block;width:100%;flex:none}
.spark polyline{fill:none;stroke:currentColor;stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.fsq{width:30px;height:30px;flex:none;border:5px solid currentColor}
.fon .fsq{background:currentColor}
.albar,.wbar,.okbar{height:100%;display:flex;align-items:center;gap:8px;padding:0 12px;font-weight:800;white-space:nowrap}
.albar{background:var(--alarm);color:var(--alarmfg)}
.wbar{background:var(--warn);color:var(--warnfg)}
.albar svg,.wbar svg{fill:currentColor;flex:none}
.hx{height:26px;border:0;padding:0;background:var(--panel);font-family:'JetBrains Mono','DejaVu Sans Mono',monospace;font-size:14px;font-weight:500;color:var(--fg2);box-shadow:inset 0 0 0 1px var(--sunk)}
.hx.old{background:var(--sunk);color:var(--fg);font-weight:700;box-shadow:inset 0 -4px 0 var(--acc)}
.hx.now{background:var(--warn);color:var(--warnfg);font-weight:800}
.hx.mapd{outline:2px dashed var(--unv);outline-offset:-3px}
.hx.sel{outline:3px solid var(--fg);outline-offset:-3px;z-index:1;position:relative}
.tc{height:24px;border:0;padding:0;font-family:'JetBrains Mono','DejaVu Sans Mono',monospace;font-size:13px;font-weight:600;position:relative}
.tc.chg::after{content:'';position:absolute;top:0;right:0;border:5px solid transparent;border-top-color:currentColor;border-right-color:currentColor}
.tc.live{outline:3px solid var(--fg);outline-offset:-3px;font-weight:800;z-index:1}
.tc.sel{outline:3px dashed var(--acc);outline-offset:-3px;z-index:2}
.lane{display:flex;align-items:stretch;flex:none;border-bottom:2px solid var(--sunk)}
.lane.ecu{color:var(--s1)}
.lane.bb{color:var(--s2)}
.lane.dig{color:var(--fg)}
.lane polyline{fill:none;stroke:currentColor;stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round}
.arow{display:grid;grid-template-columns:28px 100px 150px 130px 1fr 96px;align-items:center;gap:8px;height:34px;padding:0 8px;border-bottom:2px solid var(--sunk);font-size:14px;font-weight:600}
.arow.alarm{background:var(--alarm);color:var(--alarmfg)}
.arow.warn{background:var(--warn);color:var(--warnfg)}
.arow svg{fill:currentColor}
.step{display:flex;align-items:center;gap:6px;font-size:13px;font-weight:700;flex:1;min-width:0}
.step u{width:26px;height:26px;flex:none;border:3px solid var(--rule);display:grid;place-items:center;text-decoration:none;font-family:'JetBrains Mono',monospace}
.step.done u{background:var(--ok);border-color:var(--ok);color:var(--panel)}
.step.cur u{background:var(--fg);border-color:var(--fg);color:var(--bg)}
.step.todo{color:var(--fg2)}
input[type=range].scrub{width:100%;height:24px;margin:0;accent-color:var(--acc)}
"""

TABS = [("Main.dc.html", "F1", "Sensors"), ("Drive.dc.html", "F2", "Drive"), ("Map3D.dc.html", "F3", "Map 3D"),
        ("Replay.dc.html", "F4", "Replay"), ("RawTools.dc.html", "F5", "Raw tools"), ("Tune.dc.html", "F6", "Tune"),
        ("Alarms.dc.html", "F7", "Alarms")]

TRI = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 1 15.5 15H.5Z"></path></svg>'
OCT = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M5 .5h6L15.5 5v6L11 15.5H5L.5 11V5Z"></path></svg>'
CHK = '<span class="ver" title="Address verified"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 8.5 6.5 12 13 4.5"></path></svg></span>'
UNV = '<span class="unv" title="Address not verified on this ECU">?</span>'


def chrome_top(active):
    tabs = "".join(
        '<a href="%s" class="tab%s"><span class="key">%s</span>%s</a>' % (f, " on" if f == active else "", k, n)
        for f, k, n in TABS)
    return """
<header style="height: 44px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 8px; background: var(--panel); border-bottom: 2px solid var(--rule)">
<span style="font-size: 19px; font-weight: 800; letter-spacing: -0.02em; margin-right: 4px">hondalog</span>
<span class="chip"><span class="i ok"></span>ECU OK<span class="mono">10.8 Hz</span><span class="mono">0 err</span></span>
<span class="chip"><span class="i ok"></span>BLACK BOX OK<span class="mono">1.0 kHz</span></span>
<span class="chip"><span class="i rec"></span>REC<span class="mono">{{recTime}}</span></span>
<span class="chip"><span class="i arm"></span>STALL CATCHER ARMED</span>
<span style="flex: 1"></span>
<button class="btn" style="height: 36px" onClick="{{toggleTheme}}"><span class="i half"></span>{{themeName}}<span class="key">F9</span></button>
<button class="btn pri" style="height: 36px; min-width: 150px" onClick="{{doMark}}">MARK<span class="key">Space</span></button>
</header>
<nav aria-label="Screens" style="height: 44px; flex: none; display: flex; align-items: stretch; padding: 0 8px; background: var(--sunk); border-bottom: 2px solid var(--rule)">
<span style="display: flex; align-items: stretch; border-left: 2px solid var(--rule)">%s</span>
<span style="flex: 1"></span>
<span class="mono" style="align-self: center; font-size: 13px; font-weight: 500">/dev/hondalog &#183; map p2p-guess-1 &#183; 32 ch &#183; 21 unverified</span>
</nav>
""" % tabs


CHROME_BOTTOM = """
<footer style="height: 32px; flex: none; display: flex; align-items: center; gap: 0; border-top: 2px solid var(--rule); background: var(--panel); font-size: 14px">
<sc-if value="{{hasAlarm}}" hint-placeholder-val="{{false}}"><span class="albar">%s{{alarmMsg}}</span></sc-if>
<sc-if value="{{hasWarn}}" hint-placeholder-val="{{false}}"><span class="wbar">%s{{warnMsg}}</span></sc-if>
<sc-if value="{{noAlarm}}" hint-placeholder-val="{{true}}"><span class="okbar"><span class="i ok"></span>No active alarms</span></sc-if>
<span style="flex: 1"></span>
<span class="mono" style="font-size: 13px; font-weight: 500; padding: 0 10px">Space mark &#183; F1&#8211;F7 screens &#183; F9 theme &#183; Esc acknowledge</span>
</footer>
""" % (OCT, TRI)


def tile(h=142):
    return """<div class="{{c.cls}}" style="height: %dpx">
<div class="thead" style="height: 24px; padding: 0 6px 0 8px">
<sc-if value="{{c.isWarn}}" hint-placeholder-val="{{false}}">%s</sc-if>
<sc-if value="{{c.isAlarm}}" hint-placeholder-val="{{false}}">%s</sc-if>
<span style="flex: 1; min-width: 0; font-size: 13px; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase; white-space: nowrap; overflow: hidden">{{c.label}}</span>
<sc-if value="{{c.unv}}" hint-placeholder-val="{{true}}">%s</sc-if>
<sc-if value="{{c.ver}}" hint-placeholder-val="{{false}}">%s</sc-if>
</div>
<sc-if value="{{c.isVal}}" hint-placeholder-val="{{true}}">
<div style="display: flex; align-items: baseline; gap: 6px; padding: 0 8px; height: 44px; flex: none">
<span style="font-size: 40px; font-weight: 800; line-height: 44px; letter-spacing: -0.02em">{{c.val}}</span>
<span style="font-size: 14px; font-weight: 700">{{c.units}}</span>
</div>
<div class="track" style="height: 10px; margin: 3px 8px 4px"><i style="width: {{c.pct}}%%"></i><b style="left: {{c.minP}}%%"></b><b style="left: {{c.maxP}}%%"></b></div>
</sc-if>
<sc-if value="{{c.isFlag}}" hint-placeholder-val="{{false}}">
<div style="display: flex; align-items: center; gap: 10px; padding: 0 8px; height: 61px; flex: none">
<span class="fsq"></span>
<span style="font-size: 36px; font-weight: 800; letter-spacing: -0.01em">{{c.val}}</span>
</div>
</sc-if>
<svg class="spark" viewBox="0 0 120 30" preserveAspectRatio="none" height="30" aria-hidden="true"><polyline points="{{c.pts}}"></polyline></svg>
<sc-if value="{{c.isVal}}" hint-placeholder-val="{{true}}">
<div class="mono" style="display: flex; justify-content: space-between; padding: 0 8px; font-size: 12px; font-weight: 700; line-height: 18px"><span>&#9660; {{c.min}}</span><span>&#9650; {{c.max}}</span></div>
</sc-if>
<sc-if value="{{c.isFlag}}" hint-placeholder-val="{{false}}">
<div class="mono" style="padding: 0 8px; font-size: 12px; font-weight: 700; line-height: 18px">{{c.chg}} changes / 60 s</div>
</sc-if>
</div>""" % (h, TRI, OCT, UNV, CHK)


LEGEND = """<span style="display: flex; align-items: center; gap: 6px">%s address unverified</span>
<span style="display: flex; align-items: center; gap: 6px">%s verified</span>
<span style="display: flex; align-items: center; gap: 6px"><span class="thead">%s</span>warn</span>
<span style="display: flex; align-items: center; gap: 6px"><span class="thead">%s</span>alarm</span>
<span class="mono">&#9660;&#9650; session min / max &#183; trace = last 60 s</span>""" % (UNV, CHK, TRI, OCT)

# ------------------------------------------------------------------ Sensors
SENSORS_HTML = """
<main style="flex: 1; min-height: 0; padding: 6px 8px; display: flex; flex-direction: column; gap: 4px">
<div class="sec"><span class="cap" style="color: var(--fg)">ECU &#183; K-line</span><span class="mono">24 channels &#183; 10.8 Hz</span><span style="flex: 1"></span>%s</div>
<div style="display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 6px">
<sc-for list="{{ecuTiles}}" as="c" hint-placeholder-count="24">%s</sc-for>
</div>
<div class="sec"><span class="cap" style="color: var(--fg)">Black box &#183; USB</span><span class="mono">8 channels &#183; sampled 1 kHz, shown 10 Hz &#183; own hardware, calibrated</span></div>
<div style="display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 6px">
<sc-for list="{{bbTiles}}" as="c" hint-placeholder-count="8">%s</sc-for>
</div>
</main>
""" % (LEGEND, tile(), tile())

SENSORS_JS = """
  componentDidMount() { this.hlStart(); }
  componentWillUnmount() { this.hlStop(); }
  renderVals() {
    const v = this.hlChrome();
    const t = this.CH.map((c) => this.hlTile(c));
    v.ecuTiles = t.filter((x) => x.src === 'ECU');
    v.bbTiles = t.filter((x) => x.src === 'BB');
    return v;
  }
"""

# ------------------------------------------------------------------ Drive
DRIVE_HTML = """
<main style="flex: 1; min-height: 0; padding: 8px; display: flex; gap: 8px">
<div style="flex: 1; min-width: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px">
<sc-for list="{{big}}" as="c" hint-placeholder-count="6">
<div class="{{c.cls}}" style="height: 312px">
<div class="thead" style="height: 40px; padding: 0 10px 0 12px">
<sc-if value="{{c.isWarn}}" hint-placeholder-val="{{false}}">%s</sc-if>
<sc-if value="{{c.isAlarm}}" hint-placeholder-val="{{false}}">%s</sc-if>
<span style="flex: 1; font-size: 22px; font-weight: 800; letter-spacing: 0.04em; text-transform: uppercase; white-space: nowrap">{{c.label}}</span>
<span style="font-size: 22px; font-weight: 700">{{c.units}}</span>
<sc-if value="{{c.unv}}" hint-placeholder-val="{{true}}">%s</sc-if>
<sc-if value="{{c.ver}}" hint-placeholder-val="{{false}}">%s</sc-if>
</div>
<div style="display: flex; align-items: center; justify-content: flex-end; padding: 0 14px; height: 150px; flex: none">
<span style="font-size: 104px; font-weight: 800; line-height: 150px; letter-spacing: -0.03em">{{c.val}}</span>
</div>
<div class="track" style="height: 22px; margin: 6px 14px 10px"><i style="width: {{c.pct}}%%"></i><b style="left: {{c.minP}}%%"></b><b style="left: {{c.maxP}}%%"></b></div>
<svg class="spark" viewBox="0 0 120 30" preserveAspectRatio="none" height="44" aria-hidden="true"><polyline points="{{c.pts}}"></polyline></svg>
<div class="mono" style="display: flex; justify-content: space-between; padding: 4px 14px 0; font-size: 18px; font-weight: 700"><span>&#9660; {{c.min}}</span><span>{{c.tag}}</span><span>&#9650; {{c.max}}</span></div>
</div>
</sc-for>
</div>
<aside style="width: 340px; flex: none; display: flex; flex-direction: column; gap: 8px">
<section class="card" style="gap: 10px; padding: 12px 14px">
<span class="cap">Stall catcher</span>
<div style="display: flex; align-items: center; gap: 14px">
<span style="width: 44px; height: 44px; flex: none; border: 10px solid currentColor; border-radius: 50%%"></span>
<span style="font-size: 52px; font-weight: 800; letter-spacing: -0.02em; line-height: 1">ARMED</span>
</div>
<div style="display: flex; justify-content: space-between; font-size: 15px; font-weight: 700"><span>Pre-trigger buffer</span><span class="mono">30.0 / 30 s</span></div>
<div class="track" style="height: 18px"><i style="width: 100%%"></i></div>
<div style="font-size: 15px; font-weight: 600; line-height: 1.35">Fires on RPM = 0 after running, ECU silent 1 s, or +B / IG1 below 6 V on the black box. Saves 30 s before and 10 s after.</div>
<div style="display: flex; justify-content: space-between; font-size: 15px; font-weight: 700; border-top: 2px solid var(--rule2); padding-top: 8px"><span>Stalls this drive</span><span class="mono">0</span></div>
</section>
<button onClick="{{doMark}}" style="flex: 1; min-height: 0; border: 0; background: var(--acc); color: var(--accfg); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px">
<span style="font-size: 104px; font-weight: 800; letter-spacing: -0.02em; line-height: 1">MARK</span>
<span style="display: flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 700"><span class="key" style="font-size: 18px; line-height: 26px; padding: 0 10px">Space</span>it just stumbled</span>
<span class="mono" style="font-size: 18px; font-weight: 700">{{marks}} marks &#183; last {{markAgo}}</span>
</button>
</aside>
</main>
""" % (TRI.replace('width="16" height="16"', 'width="24" height="24"'), OCT.replace('width="16" height="16"', 'width="24" height="24"'), UNV, CHK)

DRIVE_JS = """
  componentDidMount() { this.hlStart(); }
  componentWillUnmount() { this.hlStop(); }
  renderVals() {
    const v = this.hlChrome();
    v.big = ['rpm', 'vss', 'ect', 'batt', 'fpv', 'stft'].map((id) => {
      const c = this.CH.find((x) => x.id === id);
      const t = this.hlTile(Object.assign({}, c, { d: Math.min(c.d, 1) }));
      t.tag = c.src === 'BB' ? 'BLACK BOX' : 'ECU';
      return t;
    });
    return v;
  }
"""

# ------------------------------------------------------------------ Map 3D
SEG = """<div class="seg"><sc-for list="{{%s}}" as="m" hint-placeholder-count="3"><button class="{{m.cls}}" onClick="{{m.pick}}">{{m.name}}</button></sc-for></div>"""

MAP_HTML = """
<main style="flex: 1; min-height: 0; padding: 8px; display: flex; gap: 8px">
<div style="position: relative; width: 968px; height: 632px; flex: none; border: 2px solid var(--rule); background: var(--bg)">
<canvas data-hl3d="1" width="964" height="628" ref="{{setCv}}" onPointerDown="{{pd}}" onPointerMove="{{pm}}" onPointerUp="{{pu}}" onPointerCancel="{{pu}}" role="img" aria-label="3D surface of the selected channel over RPM and MAP, with the live operating point" style="display: block; width: 964px; height: 628px; touch-action: none; cursor: grab"></canvas>
<div style="position: absolute; left: 0; top: 0; padding: 8px 12px; background: var(--panel); border-right: 2px solid var(--rule); border-bottom: 2px solid var(--rule); display: flex; flex-direction: column; gap: 2px; max-width: 560px">
<span style="font-size: 17px; font-weight: 800">{{title}}</span>
<span style="font-size: 13px; font-weight: 600; color: var(--fg2)">{{caption}}</span>
</div>
<div style="position: absolute; right: 0; bottom: 0; padding: 6px 10px; background: var(--panel); border-left: 2px solid var(--rule); border-top: 2px solid var(--rule); display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 700">
<span style="width: 16px; height: 16px; border-radius: 50%%; background: #E6007A; border: 3px solid var(--fg)"></span>live point + 7 s trail
<span class="mono">drag = orbit</span>
</div>
</div>
<aside style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px">
<div style="display: flex; flex-direction: column; gap: 3px"><span class="cap">View</span>%s</div>
<div style="display: flex; flex-direction: column; gap: 3px"><span class="cap">Height (Z)</span>%s</div>
<div style="display: flex; flex-direction: column; gap: 3px"><span class="cap">Colour</span>%s</div>
<section class="card">
<span class="cap">Operating point</span>
<div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 12px">
<div><div style="font-size: 12px; font-weight: 700">RPM</div><div style="font-size: 38px; font-weight: 800; line-height: 1">{{opRpm}}</div></div>
<div><div style="font-size: 12px; font-weight: 700">MAP kPa</div><div style="font-size: 38px; font-weight: 800; line-height: 1">{{opMap}}</div></div>
<div><div style="font-size: 12px; font-weight: 700">{{zLabel}} at point</div><div style="font-size: 38px; font-weight: 800; line-height: 1">{{opZ}}</div></div>
<div><div style="font-size: 12px; font-weight: 700">Samples in cell {{opCell}}</div><div style="font-size: 38px; font-weight: 800; line-height: 1">{{opHits}}</div></div>
</div>
</section>
<section class="card">
<span class="cap">{{legTitle}}</span>
<div style="height: 22px; border: 2px solid var(--rule); background: {{legGrad}}"></div>
<div class="mono" style="display: flex; justify-content: space-between; font-size: 13px; font-weight: 700"><span>{{legLo}}</span><span>{{legMid}}</span><span>{{legHi}}</span></div>
<div style="display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600"><span style="width: 18px; height: 18px; background: var(--sunk); border: 2px solid var(--rule2)"></span>grey = no samples logged in that cell yet</div>
</section>
<div style="display: flex; flex-direction: column; gap: 3px"><span class="cap">Camera</span>%s</div>
<section class="card" style="flex: 1; min-height: 0">
<span class="cap">Coverage</span>
<div style="display: flex; justify-content: space-between; font-size: 15px; font-weight: 700"><span>Cells with data</span><span class="mono">{{seen}} / 200</span></div>
<div class="track" style="height: 14px"><i style="width: {{seenPct}}%%"></i></div>
</section>
</aside>
</main>
""" % (SEG % "modes", SEG % "zs", SEG % "cols", SEG % "views")

MAP_JS = """
  componentDidMount() { this.hlStart(); this.hl3dStart({ logged: true, colorBy: 'trim' }); }
  componentWillUnmount() { this.hlStop(); }
  renderVals() {
    const v = this.hlChrome(), M = this.hl3dData(), st = this.state || {};
    const mode = st.mode || 'surface', zKey = st.zKey || 'fuel', colorBy = st.colorBy || 'trim';
    if (this.g) { this.g.mode = mode; this.g.zKey = zKey; this.g.colorBy = colorBy; }
    const seg = (list, cur, key) => list.map((a) => ({ name: a[1], cls: cur === a[0] ? 'on' : '', pick: () => this.setState({ [key]: a[0] }) }));
    v.modes = seg([['surface', 'Surface'], ['hist', 'Time in cell'], ['stall', 'Stall paths']], mode, 'mode');
    v.zs = seg([['fuel', 'Injector ms'], ['ign', 'Ignition \\u00b0'], ['trim', 'Fuel trim %']], zKey, 'zKey');
    v.cols = seg([['z', 'Same as Z'], ['trim', 'Fuel trim'], ['hits', 'Samples']], colorBy, 'colorBy');
    v.views = [['Iso', -0.62, 0.5], ['Top', 0, 1.5], ['RPM side', 0, 0.1], ['MAP side', -1.5708, 0.1]].map((a) => ({
      name: a[0], cls: '', pick: () => { if (this.g) { this.g.user = true; this.g.az = a[1]; this.g.el = a[2]; } }
    }));
    v.opRpm = Math.round(this.cur.rpm);
    v.opMap = Math.round(this.cur.map);
    v.zLabel = mode === 'hist' ? 'Samples' : M.zName[zKey];
    if (this.g) {
      const p = this.hl3dAt(this.cur.rpm, this.cur.map);
      v.opZ = mode === 'hist' ? String(M.hits[p.idx]) : p.v.toFixed(zKey === 'fuel' ? 2 : 1);
      v.opCell = 'R' + (p.i + 1) + ' M' + (p.j + 1);
      v.opHits = M.hits[p.idx];
    } else { v.opZ = '---'; v.opCell = ''; v.opHits = '---'; }
    const seq = 'linear-gradient(90deg,#00224E,#35456C,#666970,#948E77,#C8B866,#FEE838)';
    const div = 'linear-gradient(90deg,#053061,#2166AC,#F7F7F7,#E08214,#7F3B08)';
    const r = M.rng[zKey];
    if (mode === 'stall') {
      v.title = 'Stall approach paths';
      v.caption = 'Last 30 s before each saved stall, drawn on the observed map. Diamond = where the engine died. Bigger bead = closer to the stall.';
      v.legTitle = 'Paths: one colour per stall file'; v.legGrad = 'var(--sunk)'; v.legLo = 'stall 1'; v.legMid = 'stall 2'; v.legHi = 'stall 3';
    } else if (mode === 'hist') {
      v.title = 'Time in cell';
      v.caption = 'Bar height and colour = samples logged in each RPM x MAP cell (log scale). Shows where the engine actually lives.';
      v.legTitle = 'Samples per cell (log)'; v.legGrad = seq; v.legLo = '1'; v.legMid = '30'; v.legHi = '900';
    } else {
      v.title = 'Observed map: ' + M.zName[zKey] + ' over RPM x MAP';
      v.caption = 'Height = mean logged value per cell. Sample data. Built from the log, so it works on the stock P2P before any chip.';
      if (colorBy === 'trim') { v.legTitle = 'Colour: fuel trim (short + long)'; v.legGrad = div; v.legLo = '\\u221212 % rich'; v.legMid = '0'; v.legHi = '+12 % lean'; }
      else if (colorBy === 'hits') { v.legTitle = 'Colour: samples per cell (log)'; v.legGrad = seq; v.legLo = '1'; v.legMid = '30'; v.legHi = '900'; }
      else { v.legTitle = 'Colour: ' + M.zName[zKey]; v.legGrad = seq; v.legLo = String(r[0]); v.legMid = String((r[0] + r[1]) / 2); v.legHi = String(r[1]); }
    }
    v.seen = M.seen;
    v.seenPct = (M.seen / 2).toFixed(0);
    v.setCv = this.setCv; v.pd = this.pd; v.pm = this.pm; v.pu = this.pu;
    return v;
  }
"""

# ------------------------------------------------------------------ Replay
PLOT_W = 814
LBL_W = 150
AX = "".join(
    '<span class="mono" style="position: absolute; top: 2px; left: %dpx; width: 60px; margin-left: -30px; text-align: center; font-size: 13px; font-weight: 700">%s</span>'
    % (LBL_W + round((t + 30) / 40 * PLOT_W), ("+%d" % t if t > 0 else ("−%d" % -t if t < 0 else "0")))
    for t in range(-30, 11, 5))

LANE = """<sc-for list="{{%s}}" as="l" hint-placeholder-count="%d">
<div class="{{l.cls}}" style="height: %dpx">
<div style="width: 150px; flex: none; display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 0 8px 0 6px; color: var(--fg); border-right: 2px solid var(--rule)">
<span style="font-size: 13px; font-weight: 800; letter-spacing: 0.03em; white-space: nowrap">{{l.label}}</span>
<span class="mono" style="font-size: 15px; font-weight: 700; white-space: nowrap">{{l.val}}</span>
</div>
<svg viewBox="0 0 400 %d" preserveAspectRatio="none" width="814" height="%d" aria-hidden="true" style="display: block; flex: none"><polyline points="{{l.pts}}"></polyline></svg>
</div>
</sc-for>"""

REPLAY_HTML = """
<main style="flex: 1; min-height: 0; padding: 8px; display: flex; gap: 8px">
<div style="width: 968px; flex: none; display: flex; flex-direction: column; gap: 6px">
<div style="display: flex; align-items: center; gap: 8px; height: 44px; flex: none">
<button class="btn" aria-label="Previous event" onClick="{{prevEv}}"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M2 2h3v12H2zM14 2v12L6 8z" style="fill: currentColor"></path></svg></button>
<button class="btn" aria-label="Play" onClick="{{play}}"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3 1.5v13L14 8z" style="fill: currentColor"></path></svg></button>
<button class="btn" aria-label="Next event" onClick="{{nextEv}}"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M11 2h3v12h-3zM2 2v12l8-6z" style="fill: currentColor"></path></svg></button>
<span class="mono" style="font-size: 14px; font-weight: 700">stall_20261115_140211.csv</span>
<span style="font-size: 13px; font-weight: 600; color: var(--fg2)">simulated capture</span>
<span style="flex: 1"></span>
<span class="cap">Cursor</span>
<span class="mono" style="font-size: 26px; font-weight: 800; min-width: 118px; text-align: right">{{curT}} s</span>
</div>
<div style="position: relative; display: flex; flex-direction: column; border: 2px solid var(--rule); background: var(--panel)">
<div class="sec" style="height: 16px; padding: 0 6px; background: var(--sunk); font-size: 12px"><span style="width: 12px; height: 12px; background: var(--s1)"></span><span class="cap" style="color: var(--fg)">ECU &#183; K-line &#183; 10 Hz</span></div>
%s
<div class="sec" style="height: 16px; padding: 0 6px; background: var(--sunk); font-size: 12px"><span style="width: 12px; height: 12px; background: var(--s2)"></span><span class="cap" style="color: var(--fg)">Black box &#183; 1 kHz &#183; aligned to ECU on tach edges</span></div>
%s
<div class="sec" style="height: 16px; padding: 0 6px; background: var(--sunk); font-size: 12px"><span class="cap">States</span></div>
%s
<div style="position: relative; height: 22px; flex: none">%s<span class="mono" style="position: absolute; left: 6px; top: 2px; font-size: 13px; font-weight: 700">s from stall</span></div>
<sc-for list="{{marks}}" as="m" hint-placeholder-count="3">
<div style="position: absolute; top: 0; bottom: 22px; left: {{m.x}}px; width: 0; border-left: {{m.line}}; pointer-events: none">
<span style="position: absolute; top: 0; left: 0; padding: 0 6px; font-size: 12px; font-weight: 800; line-height: 16px; white-space: nowrap; background: {{m.bg}}; color: {{m.fg}}">{{m.name}}</span>
</div>
</sc-for>
<div style="position: absolute; top: 0; bottom: 0; left: {{curX}}px; width: 4px; margin-left: -2px; background: var(--acc); pointer-events: none">
<span style="position: absolute; bottom: 0; left: -7px; border: 9px solid transparent; border-bottom-color: var(--acc)"></span>
</div>
<div onPointerDown="{{scrub}}" onPointerMove="{{scrub}}" style="position: absolute; left: 150px; right: 0; top: 0; bottom: 0; cursor: col-resize; touch-action: none"></div>
</div>
<label style="display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 700; padding-left: 6px">
<span style="width: 136px; flex: none">Scrub &#183; &#8592; &#8594; step</span>
<input class="scrub" type="range" min="0" max="400" step="1" value="{{curK}}" onChange="{{slide}}">
</label>
</div>
<aside style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px">
<section class="card" style="padding: 8px 0 0">
<span class="cap" style="padding: 0 10px">Order of loss &#183; first to fall</span>
<div style="display: flex; flex-direction: column">
<sc-for list="{{loss}}" as="e" hint-placeholder-count="5">
<button onClick="{{e.jump}}" style="display: grid; grid-template-columns: 26px 74px 1fr; align-items: center; gap: 8px; min-height: 48px; padding: 4px 10px; border: 0; border-top: 2px solid var(--sunk); background: {{e.bg}}; color: {{e.fg}}; text-align: left">
<span class="mono" style="font-size: 18px; font-weight: 800">{{e.n}}</span>
<span class="mono" style="font-size: 15px; font-weight: 700">{{e.t}}</span>
<span style="display: flex; flex-direction: column"><span style="font-size: 14px; font-weight: 800">{{e.sig}}</span><span style="font-size: 13px; font-weight: 600">{{e.what}}</span></span>
</button>
</sc-for>
</div>
</section>
<section class="card">
<span class="cap">Held up through the stall</span>
<span style="font-size: 14px; font-weight: 600; line-height: 1.35">ECU +B and IG1 stayed above 12 V. Fuel pump command stayed ON. The ECU never lost power and kept asking for fuel.</span>
</section>
<section class="card" style="border-width: 4px">
<span class="cap">Reading</span>
<span style="font-size: 16px; font-weight: 700; line-height: 1.3">Pump feed dropped 2.1 s before the engine died while the ECU still commanded it. Look between the main relay output and the pump.</span>
</section>
<div style="display: flex; flex-direction: column; gap: 3px">
<span class="cap">Jump to</span>
<div class="seg"><sc-for list="{{jumps}}" as="m" hint-placeholder-count="3"><button class="{{m.cls}}" onClick="{{m.pick}}">{{m.name}}</button></sc-for></div>
</div>
</aside>
</main>
""" % (LANE % ("ecuL", 5, 40, 40, 40), LANE % ("bbL", 5, 40, 40, 40), LANE % ("digL", 3, 20, 20, 20), AX)

REPLAY_JS = """
  componentDidMount() { this.hlStart(); }
  componentWillUnmount() { this.hlStop(); clearInterval(this._play); }
  hlStall() {
    if (this.S) return this.S;
    const N = 401, S = this.S = { N, d: {}, pts: {} };
    const ids = ['rpm', 'map', 'tps', 'o2', 'inj', 'bbatt', 'ecub', 'fpv', 'ig1', 'tach', 'fpc', 'cl', 'mk'];
    ids.forEach((i) => { S.d[i] = []; });
    for (let k = 0; k < N; k++) {
      const t = -30 + k * 0.1;
      const n = (a, f) => Math.sin(t * f + a * 7.3) * 0.6 + Math.sin(t * f * 2.3 + a) * 0.4;
      const blip = Math.exp(-Math.pow((t + 17) / 1.6, 2));
      const sag = t < -0.9 ? 1 : (t < 0 ? -t / 0.9 : 0);
      const run = t < 0;
      const rpm = (790 + 1900 * blip + n(1, 3) * 14) * Math.pow(sag, 0.7);
      const off = 0.3 * Math.exp(-Math.max(0, t) / 2);
      S.d.rpm.push(rpm);
      S.d.map.push(run ? 33 + 40 * blip + n(2, 2) * 0.8 + (1 - sag) * 60 : 101);
      S.d.tps.push(1 + 34 * blip);
      S.d.o2.push(t < -1.3 ? 0.46 + 0.36 * Math.tanh(3 * Math.sin(t * 5.2)) : 0.05);
      S.d.inj.push(run ? (2.4 + 3 * blip) * (t > -1.3 ? 1.22 : 1) : 0);
      S.d.bbatt.push(run ? 14.05 + n(3, 2) * 0.04 : 12.4 + off);
      S.d.ecub.push((run ? 13.92 : 12.3 + off) + n(4, 2) * 0.03);
      S.d.fpv.push(t < -2.1 ? 13.62 + n(5, 2.6) * 0.05 : Math.max(0.2, 13.6 * Math.exp(-(t + 2.1) / 0.06)));
      S.d.ig1.push((run ? 13.84 : 12.25 + off) + n(6, 2) * 0.03);
      S.d.tach.push(rpm / 30);
      S.d.fpc.push(t < 2 ? 1 : 0);
      S.d.cl.push(t < -1.0 ? 1 : 0);
      S.d.mk.push((t >= -22 && t < -21.5) || (t >= -9 && t < -8.5) ? 1 : 0);
    }
    S.lanes = [
      ['rpm', 'RPM', 0, 3000, 0, 'ecu'], ['map', 'MAP kPa', 0, 110, 0, 'ecu'], ['tps', 'TPS %', 0, 100, 0, 'ecu'], ['o2', 'O2 V', 0, 1.2, 2, 'ecu'], ['inj', 'INJ ms', 0, 8, 2, 'ecu'],
      ['bbatt', 'BATTERY V', 8, 16, 2, 'bb'], ['ecub', 'ECU +B V', 8, 16, 2, 'bb'], ['fpv', 'FP FEED V', 0, 16, 2, 'bb'], ['ig1', 'IG1 V', 8, 16, 2, 'bb'], ['tach', 'TACH Hz', 0, 100, 0, 'bb'],
      ['fpc', 'FUEL PUMP CMD', 0, 1, 0, 'dig'], ['cl', 'CLOSED LOOP', 0, 1, 0, 'dig'], ['mk', 'MARKER', 0, 1, 0, 'dig']
    ];
    for (const L of S.lanes) {
      const h = L[5] === 'dig' ? 20 : 40;
      let p = '';
      for (let k = 0; k < N; k++) {
        const f = Math.max(0, Math.min(1, (S.d[L[0]][k] - L[2]) / (L[3] - L[2])));
        p += k + ',' + (h - 4 - f * (h - 8)).toFixed(1) + ' ';
      }
      S.pts[L[0]] = p;
    }
    return S;
  }
  renderVals() {
    const v = this.hlChrome(), S = this.hlStall(), st = this.state || {};
    const k = st.k == null ? 279 : st.k;
    const go = (kk) => this.setState({ k: Math.max(0, Math.min(400, Math.round(kk))) });
    const tk = (t) => (t + 30) * 10;
    const lanes = S.lanes.map((L) => ({
      label: L[1], cls: 'lane ' + L[5], pts: S.pts[L[0]], grp: L[5],
      val: L[5] === 'dig' ? (S.d[L[0]][k] ? 'ON' : 'OFF') : S.d[L[0]][k].toFixed(L[4])
    }));
    v.ecuL = lanes.filter((l) => l.grp === 'ecu');
    v.bbL = lanes.filter((l) => l.grp === 'bb');
    v.digL = lanes.filter((l) => l.grp === 'dig');
    const t = -30 + k * 0.1;
    v.curT = (t < 0 ? '\\u2212' : '+') + Math.abs(t).toFixed(1);
    v.curX = 150 + k / 400 * 814;
    v.curK = k;
    const ev = [-22, -9, -2.1, -1.3, -0.3, 0];
    v.marks = [
      { name: 'MARK 1', x: 150 + tk(-22) / 400 * 814, line: '3px dashed var(--fg)', bg: 'var(--fg)', fg: 'var(--bg)' },
      { name: 'MARK 2', x: 150 + tk(-9) / 400 * 814, line: '3px dashed var(--fg)', bg: 'var(--fg)', fg: 'var(--bg)' },
      { name: 'STALL', x: 150 + tk(0) / 400 * 814, line: '4px solid var(--alarm)', bg: 'var(--alarm)', fg: 'var(--alarmfg)' }
    ];
    v.scrub = (e) => {
      if (e.type === 'pointermove' && !(e.buttons & 1)) return;
      const r = e.currentTarget.getBoundingClientRect();
      go((e.clientX - r.left) / r.width * 400);
    };
    v.slide = (e) => go(Number(e.target.value));
    v.prevEv = () => { const p = ev.filter((x) => tk(x) < k - 0.5); go(p.length ? tk(p[p.length - 1]) : 0); };
    v.nextEv = () => { const p = ev.filter((x) => tk(x) > k + 0.5); go(p.length ? tk(p[0]) : 400); };
    v.play = () => {
      if (this._play) { clearInterval(this._play); this._play = null; return; }
      this._play = setInterval(() => {
        const c = (this.state || {}).k == null ? 279 : this.state.k;
        if (c >= 400) { clearInterval(this._play); this._play = null; return; }
        go(c + 1);
      }, 100);
    };
    const L = (n, tt, sig, what, hot) => ({ n, t: (tt < 0 ? '\\u2212' : '') + Math.abs(tt).toFixed(2) + ' s', sig, what, jump: () => go(tk(tt)), bg: hot ? 'var(--alarm)' : 'var(--panel)', fg: hot ? 'var(--alarmfg)' : 'var(--fg)' });
    v.loss = [
      L(1, -2.1, 'FP FEED \\u00b7 black box', 'fell below 11 V, 13.6 V to 0.2 V in 0.2 s', true),
      L(2, -1.3, 'O2 \\u00b7 ECU', 'pinned lean at 0.05 V, injector time +22 %', false),
      L(3, -1.0, 'CLOSED LOOP \\u00b7 ECU', 'dropped to open loop', false),
      L(4, -0.3, 'RPM \\u00b7 ECU', 'below 400 rpm', false),
      L(5, 0, 'RPM = 0', 'stall trigger', false)
    ];
    v.jumps = [['MARK 1', -22], ['MARK 2', -9], ['STALL', 0]].map((a) => ({ name: a[0], cls: '', pick: () => go(tk(a[1])) }));
    return v;
  }
"""

# ------------------------------------------------------------------ Raw tools
HEXCOLS = "".join('<span class="mono" style="text-align: center; font-size: 13px; font-weight: 700; line-height: 22px">x%X</span>' % i for i in range(16))
HEXROWS = "".join('<span class="mono" style="height: 26px; line-height: 26px; font-size: 13px; font-weight: 700; text-align: right; padding-right: 8px">0x%X0</span>' % i for i in range(16))

RAW_HTML = """
<main style="flex: 1; min-height: 0; padding: 8px; display: flex; gap: 8px">
<div style="width: 772px; flex: none; display: flex; flex-direction: column; gap: 6px">
<div style="display: flex; align-items: center; gap: 8px; height: 44px; flex: none">
<button class="btn on"><span class="i rec" style="background: currentColor"></span>Sweeping</button>
<button class="btn" onClick="{{clear}}">Clear changes</button>
<span class="mono" style="font-size: 14px; font-weight: 700">0x00&#8211;0xFF &#183; sweep {{sweep}} &#183; 0.9 s / sweep</span>
<span style="flex: 1"></span>
<button class="btn">Export</button>
</div>
<div style="border: 2px solid var(--rule); background: var(--panel); display: grid; grid-template-columns: 58px minmax(0, 1fr)">
<span></span>
<div style="display: grid; grid-template-columns: repeat(16, minmax(0, 1fr)); border-bottom: 2px solid var(--rule)">%s</div>
<div style="display: flex; flex-direction: column; border-right: 2px solid var(--rule)">%s</div>
<div style="display: grid; grid-template-columns: repeat(16, minmax(0, 1fr))">
<sc-for list="{{bytes}}" as="b" hint-placeholder-count="256"><button class="{{b.cls}}" aria-label="{{b.aria}}" onClick="{{b.pick}}">{{b.hex}}</button></sc-for>
</div>
</div>
<div style="display: flex; align-items: center; gap: 16px; height: 26px; flex: none; font-size: 13px; font-weight: 700">
<span style="display: flex; align-items: center; gap: 6px"><span class="hx now mono" style="width: 34px; display: grid; place-items: center">3F</span>changing now</span>
<span style="display: flex; align-items: center; gap: 6px"><span class="hx old mono" style="width: 34px; display: grid; place-items: center">3F</span>changed earlier</span>
<span style="display: flex; align-items: center; gap: 6px"><span class="hx mapd mono" style="width: 34px; display: grid; place-items: center">3F</span>in address map, unverified</span>
<span style="display: flex; align-items: center; gap: 6px"><span class="hx sel mono" style="width: 34px; display: grid; place-items: center">3F</span>selected</span>
</div>
<section class="card" style="flex: 1; min-height: 0; gap: 4px">
<span class="cap">Hex console</span>
<div style="display: flex; gap: 8px; align-items: center">
<label class="mono" style="display: flex; align-items: center; gap: 8px; flex: 1; font-size: 15px; font-weight: 700">TX<input class="mono" type="text" defaultValue="20 05 14 01" aria-label="Bytes to send" style="flex: 1; height: 40px; padding: 0 10px; border: 2px solid var(--rule); background: var(--bg); color: var(--fg); font-size: 16px; font-weight: 700"></label>
<button class="btn">+ checksum</button>
<button class="btn pri">Send</button>
</div>
<div class="mono" style="font-size: 15px; font-weight: 700">RX&#160; 00 03 {{selHex}} {{rxSum}} &#160;&#183;&#160; checksum OK &#183; 31 ms</div>
</section>
</div>
<aside style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px">
<section class="card">
<div style="display: flex; align-items: baseline; gap: 14px">
<span class="cap">Byte</span>
<span class="mono" style="font-size: 34px; font-weight: 800; line-height: 1">{{selAddr}}</span>
<span class="mono" style="font-size: 34px; font-weight: 800; line-height: 1; margin-left: auto">{{selHex}}</span>
<span class="mono" style="font-size: 20px; font-weight: 700">{{selDec}}</span>
</div>
<div style="display: flex; align-items: center; gap: 4px">
<sc-for list="{{bits}}" as="q" hint-placeholder-count="8"><span class="mono" style="width: 30px; height: 30px; display: grid; place-items: center; font-size: 15px; font-weight: 800; border: 3px solid var(--fg); background: {{q.bg}}; color: {{q.fg}}">{{q.v}}</span></sc-for>
<span class="mono" style="font-size: 13px; font-weight: 700; margin-left: 8px">bit 7 &#8594; 0</span>
</div>
<svg class="spark" viewBox="0 0 120 30" preserveAspectRatio="none" height="40" aria-hidden="true" style="border: 2px solid var(--rule2); background: var(--sunk)"><polyline points="{{selPts}}"></polyline></svg>
<div class="mono" style="display: flex; justify-content: space-between; font-size: 14px; font-weight: 700"><span>min {{selMin}}</span><span>max {{selMax}}</span><span>{{selCnt}} changes</span></div>
<div style="font-size: 14px; font-weight: 700">{{selMap}}</div>
</section>
<section class="card" style="flex: 1; min-height: 0; gap: 8px">
<div style="display: flex; align-items: baseline; justify-content: space-between"><span class="cap">Map a channel &#183; guided</span><span style="font-size: 14px; font-weight: 800">Target: Throttle (TPS)</span></div>
<div style="display: flex; gap: 6px">
<sc-for list="{{steps}}" as="s" hint-placeholder-count="5"><span class="{{s.cls}}"><u>{{s.n}}</u>{{s.name}}</span></sc-for>
</div>
<div style="border: 4px solid var(--fg); padding: 10px 12px; display: flex; flex-direction: column; gap: 4px">
<span style="font-size: 22px; font-weight: 800; line-height: 1.2">{{stepTitle}}</span>
<span style="font-size: 15px; font-weight: 600; line-height: 1.35">{{stepBody}}</span>
</div>
<div style="display: flex; flex-direction: column">
<div class="mono" style="display: grid; grid-template-columns: 70px 1fr 110px 60px; gap: 10px; font-size: 12px; font-weight: 800; padding: 0 8px 2px"><span>ADDR</span><span>FOLLOWS THROTTLE</span><span>RANGE SEEN</span><span style="text-align: right">NOW</span></div>
<sc-for list="{{cands}}" as="k" hint-placeholder-count="4">
<button onClick="{{k.pick}}" class="mono" style="display: grid; grid-template-columns: 70px 1fr 110px 60px; gap: 10px; align-items: center; height: 44px; padding: 0 8px; border: 0; border-top: 2px solid var(--sunk); background: {{k.bg}}; color: {{k.fg}}; font-size: 15px; font-weight: 700; text-align: left">
<span>{{k.addr}}</span>
<span style="display: flex; align-items: center; gap: 8px"><span class="track" style="flex: 1; height: 12px; border-color: currentColor"><i style="width: {{k.corr}}%%"></i></span><span style="width: 44px">{{k.corrT}}</span></span>
<span>{{k.range}}</span>
<span style="text-align: right">{{k.now}}</span>
</button>
</sc-for>
</div>
<span style="flex: 1"></span>
<div style="display: flex; gap: 8px">
<button class="btn" onClick="{{back}}">Back</button>
<span style="flex: 1"></span>
<button class="btn" onClick="{{reset}}">Not it, try again</button>
<button class="btn pri" style="min-width: 190px" onClick="{{next}}">{{nextLabel}}</button>
</div>
</section>
</aside>
</main>
""" % (HEXCOLS, HEXROWS)

RAW_JS = """
  componentDidMount() { this.hlStart(); }
  componentWillUnmount() { this.hlStop(); }
  hlMem() {
    if (!this.mem) {
      this.mem = new Uint8Array(256);
      for (let i = 0; i < 256; i++) this.mem[i] = (i * 73 + 41) % 251 > 196 ? (i * 37 + 11) % 256 : 0;
      this.chgAt = new Float64Array(256).fill(-1e9);
      this.cnt = new Uint16Array(256);
      this.bmin = new Uint8Array(256).fill(255);
      this.bmax = new Uint8Array(256);
      this.bh = [];
      for (let i = 0; i < 256; i++) this.bh.push([]);
      this.mapped = { 0: 'rpm', 1: 'rpm', 2: 'vss', 8: 'ac_sw', 10: 'vtec', 11: 'mil', 15: 'closed_loop', 16: 'ect', 17: 'iat', 18: 'map', 19: 'baro', 20: 'tps', 21: 'o2', 23: 'batt', 24: 'alt_fr', 25: 'eld', 32: 'stft', 34: 'ltft', 36: 'inj', 37: 'inj', 38: 'ign', 39: 'ign_limit', 40: 'iacv', 60: 'knock' };
      this.verifiedAddr = { 0: 1, 1: 1, 20: 1, 23: 1 };
      this.sweep = 148;
    }
    const c = this.cur, m = this.mem, t = this.simT, set = (a, x) => {
      x = Math.max(0, Math.min(255, Math.round(x)));
      if (m[a] !== x) { m[a] = x; this.chgAt[a] = t; this.cnt[a]++; }
    };
    const rw = Math.round(c.rpm), iw = Math.round(c.inj * 250);
    set(0, rw >> 8); set(1, rw & 255); set(2, c.vss); set(8, c.ac); set(10, c.vtec); set(11, c.mil); set(15, c.cl);
    set(16, 200 - c.ect); set(17, 200 - c.iat * 1.4); set(18, c.map * 2.2); set(19, 222); set(20, c.tps * 2.55); set(21, c.o2 * 51);
    set(23, c.batt * 10); set(24, c.altfr * 2.55); set(25, c.eld * 2); set(32, 128 + c.stft * 2); set(34, 128 + c.ltft * 2);
    set(36, iw >> 8); set(37, iw & 255); set(38, 40 + c.ign * 2); set(40, c.iacv * 2.55); set(60, c.knock * 51);
    set(48, Math.floor(t * 4) & 255); set(68, Math.floor(t) & 255); set(90, 25 + c.tps * 2.3); set(97, c.fan ? 4 : 0); set(118, 14 + c.tps * 0.4);
    for (let i = 0; i < 256; i++) {
      if (m[i] < this.bmin[i]) this.bmin[i] = m[i];
      if (m[i] > this.bmax[i]) this.bmax[i] = m[i];
      const h = this.bh[i]; h.push(m[i]); if (h.length > 120) h.shift();
    }
  }
  renderVals() {
    const v = this.hlChrome(), st = this.state || {};
    this.hlMem();
    const sel = st.sel == null ? 20 : st.sel, step = st.step == null ? 3 : st.step, t = this.simT;
    const hx = (x, w) => { let s = x.toString(16).toUpperCase(); while (s.length < w) s = '0' + s; return s; };
    v.bytes = [];
    for (let i = 0; i < 256; i++) {
      const age = t - this.chgAt[i];
      v.bytes.push({
        hex: hx(this.mem[i], 2), aria: 'Byte 0x' + hx(i, 2),
        cls: 'hx' + (age < 1.2 ? ' now' : (this.cnt[i] > 0 ? ' old' : '')) + (this.mapped[i] && !this.verifiedAddr[i] ? ' mapd' : '') + (i === sel ? ' sel' : ''),
        pick: () => this.setState({ sel: i })
      });
    }
    v.sweep = this.sweep + Math.floor(t / 0.9);
    v.clear = () => { this.cnt.fill(0); this.chgAt.fill(-1e9); this.setState({ cl: t }); };
    const b = this.mem[sel];
    v.selAddr = '0x' + hx(sel, 2); v.selHex = hx(b, 2); v.selDec = String(b);
    v.rxSum = hx((256 - ((0 + 3 + b) & 255)) & 255, 2);
    v.bits = [];
    for (let q = 7; q >= 0; q--) { const on = (b >> q) & 1; v.bits.push({ v: on, bg: on ? 'var(--fg)' : 'transparent', fg: on ? 'var(--bg)' : 'var(--fg)' }); }
    const h = this.bh[sel];
    let p = '';
    const lo = this.bmin[sel], hi = Math.max(this.bmax[sel], lo + 1);
    for (let i = 0; i < h.length; i++) p += (i + 120 - h.length) + ',' + (27 - (h[i] - lo) / (hi - lo) * 24).toFixed(1) + ' ';
    v.selPts = p; v.selMin = this.bmin[sel]; v.selMax = this.bmax[sel]; v.selCnt = this.cnt[sel];
    v.selMap = this.mapped[sel] ? 'Address map: ' + this.mapped[sel] + (this.verifiedAddr[sel] ? ' \\u00b7 verified' : ' \\u00b7 unverified guess (kerpz P2T)') : 'Not in the address map';
    const names = ['Target', 'Baseline', 'Stimulus', 'Candidates', 'Confirm'];
    v.steps = names.map((name, i) => ({ n: i < step ? '\\u2713' : String(i + 1), name, cls: 'step ' + (i < step ? 'done' : (i === step ? 'cur' : 'todo')) }));
    const T = [
      ['Pick what you are looking for', 'One channel at a time. Throttle is a good first one: you control it and nothing else moves with it.'],
      ['Hold still for 5 seconds', 'Engine warm and idling, foot off everything. Bytes that move now are noise and are ruled out.'],
      ['Press the throttle to the floor, slowly, 3 times', 'Key on, engine off is fine. Bytes that rise and fall with your foot are ranked below as you go.'],
      ['Pick the byte that follows your foot', '0x14 tracked all 3 presses and returned to the same resting value. Tap a row to inspect it in the grid.'],
      ['Confirm the scaling', 'Closed = 20, floor = 97 raw. Formula x / 2.55 gives 8 % to 38 % here. Save to mark 0x14 as verified.']
    ];
    v.stepTitle = T[step][0]; v.stepBody = T[step][1];
    v.nextLabel = step === 4 ? 'Save as verified' : (step === 3 ? 'Use 0x14 for TPS' : 'Next');
    v.next = () => this.setState({ step: Math.min(4, step + 1) });
    v.back = () => this.setState({ step: Math.max(0, step - 1) });
    v.reset = () => this.setState({ step: 1 });
    v.cands = [[20, 98], [90, 91], [118, 64], [18, 41]].map((a) => ({
      addr: '0x' + hx(a[0], 2), corr: a[1], corrT: (a[1] / 100).toFixed(2),
      range: this.bmin[a[0]] + '\\u2013' + this.bmax[a[0]], now: hx(this.mem[a[0]], 2),
      bg: a[0] === sel ? 'var(--fg)' : 'var(--panel)', fg: a[0] === sel ? 'var(--bg)' : 'var(--fg)',
      pick: () => this.setState({ sel: a[0] })
    }));
    return v;
  }
"""

# ------------------------------------------------------------------ Tune
MAPV = [round(17 + j * 84 / 9) for j in range(10)]
RPMV = [round((500 + i * 7000 / 19) / 50) * 50 for i in range(20)]
TCOLS = "".join('<span class="mono" style="text-align: center; font-size: 13px; font-weight: 800; line-height: 24px">%d</span>' % m for m in MAPV)
TROWS = "".join('<span class="mono" style="height: 24px; line-height: 24px; font-size: 13px; font-weight: 800; text-align: right; padding-right: 8px">%d</span>' % r for r in RPMV)

TUNE_HTML = """
<main style="flex: 1; min-height: 0; padding: 8px; display: flex; gap: 8px">
<div style="width: 560px; flex: none; display: flex; flex-direction: column; gap: 6px">
%s
<div style="border: 2px solid var(--rule); background: var(--panel); display: grid; grid-template-columns: 60px minmax(0, 1fr)">
<span class="mono" style="font-size: 11px; font-weight: 800; line-height: 12px; padding: 0 4px; border-right: 2px solid var(--rule); border-bottom: 2px solid var(--rule)">RPM &#8595;<br>kPa &#8594;</span>
<div style="display: grid; grid-template-columns: repeat(10, minmax(0, 1fr)); border-bottom: 2px solid var(--rule)">%s</div>
<div style="display: flex; flex-direction: column; border-right: 2px solid var(--rule)">%s</div>
<div style="display: grid; grid-template-columns: repeat(10, minmax(0, 1fr))">
<sc-for list="{{cells}}" as="c" hint-placeholder-count="200"><button class="{{c.cls}}" aria-label="{{c.aria}}" onClick="{{c.pick}}" style="background: {{c.bg}}; color: {{c.fg}}">{{c.val}}</button></sc-for>
</div>
</div>
<div style="display: flex; align-items: center; gap: 6px; height: 44px; flex: none">
<button class="btn" aria-label="Decrease selected cell" onClick="{{dec}}">&#8722;<span class="key">-</span></button>
<button class="btn" aria-label="Increase selected cell" onClick="{{inc}}">+<span class="key">=</span></button>
<button class="btn">Interpolate<span class="key">I</span></button>
<button class="btn">Smooth<span class="key">S</span></button>
<button class="btn" onClick="{{undo}}">Undo all<span class="key">^Z</span></button>
</div>
</div>
<aside style="flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px">
<div style="position: relative; width: 782px; height: 404px; flex: none; border: 2px solid var(--rule); background: var(--bg)">
<canvas data-hl3d="1" width="778" height="400" ref="{{setCv}}" onPointerDown="{{pd}}" onPointerMove="{{pm}}" onPointerUp="{{pu}}" onPointerCancel="{{pu}}" role="img" aria-label="3D surface of the table being edited" style="display: block; width: 778px; height: 400px; touch-action: none; cursor: grab"></canvas>
<div style="position: absolute; right: 0; top: 0; padding: 6px 10px; background: var(--panel); border-left: 2px solid var(--rule); border-bottom: 2px solid var(--rule); display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 700">
<span style="display: flex; align-items: center; gap: 8px"><span style="width: 14px; height: 14px; border-radius: 50%%; background: #E6007A; border: 3px solid var(--fg)"></span>live point</span>
<span style="display: flex; align-items: center; gap: 8px"><span style="width: 14px; height: 14px; background: var(--acc); transform: rotate(45deg)"></span>selected cell</span>
</div>
</div>
<div style="flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px">
<section class="card">
<span class="cap">Selected cell</span>
<span class="mono" style="font-size: 15px; font-weight: 700">{{selName}}</span>
<div style="display: flex; align-items: baseline; gap: 8px"><span style="font-size: 52px; font-weight: 800; line-height: 1">{{selVal}}</span><span style="font-size: 18px; font-weight: 700">{{unit}}</span></div>
<div class="mono" style="font-size: 15px; font-weight: 700">base {{selBase}} &#183; {{selDelta}}</div>
</section>
<section class="card">
<span class="cap">Live cell</span>
<span class="mono" style="font-size: 15px; font-weight: 700">{{liveName}}</span>
<div style="display: flex; align-items: baseline; gap: 8px"><span style="font-size: 52px; font-weight: 800; line-height: 1">{{liveVal}}</span><span style="font-size: 18px; font-weight: 700">{{unit}}</span></div>
<div class="mono" style="font-size: 15px; font-weight: 700">interpolated &#183; trim {{liveTrim}} %%</div>
</section>
<section class="card">
<span class="cap">Chip &#183; P28 only</span>
<span style="font-size: 15px; font-weight: 700; line-height: 1.3">{{nChg}} cells changed, not written. The stock P2P cannot be written: this screen unlocks with the chipped P28.</span>
<span style="flex: 1"></span>
<div style="display: flex; gap: 6px"><button class="btn" style="flex: 1">Save .bin</button><button class="btn" style="flex: 1" disabled>Write to chip</button></div>
</section>
</div>
</aside>
</main>
""" % (SEG % "tabs", TCOLS, TROWS)

TUNE_JS = """
  componentDidMount() { this.hlStart(); this.hl3dStart({ logged: false, colorBy: 'z', dist: 4.9, el: 0.46 }); }
  componentWillUnmount() { this.hlStop(); }
  tuneInit() {
    if (this.TB) return;
    const M = this.hl3dData();
    this.TB = { fl: M.fuel.slice(), fh: M.fuel.map((x, k) => x * (1.02 + 0.1 * (k % M.NX) / M.NX)), il: M.ign.slice(), ih: M.ign.map((x) => x - 2.5) };
    this.BASE = { fl: this.TB.fl.slice(), fh: this.TB.fh.slice(), il: this.TB.il.slice(), ih: this.TB.ih.slice() };
    [[7, 4, 0.22], [8, 4, 0.31], [9, 4, 0.2], [8, 5, 0.36], [9, 5, 0.27], [8, 3, 0.12]].forEach((a) => { this.TB.fl[a[1] * M.NX + a[0]] += a[2]; });
    this.zArr = () => this.TB[(this.state || {}).tab || 'fl'];
    this.selIdx = 5 * M.NX + 8;
  }
  renderVals() {
    const v = this.hlChrome(), M = this.hl3dData(), st = this.state || {};
    this.tuneInit();
    const tab = st.tab || 'fl', fuel = tab[0] === 'f', A = this.TB[tab], B = this.BASE[tab], night = this.hlTheme() === 'night';
    if (this.g) { this.g.zKey = fuel ? 'fuel' : 'ign'; this.g.mode = 'surface'; this.g.colorBy = 'z'; }
    v.tabs = [['fl', 'Fuel \\u00b7 low cam'], ['fh', 'Fuel \\u00b7 VTEC'], ['il', 'Ignition \\u00b7 low cam'], ['ih', 'Ignition \\u00b7 VTEC']].map((a) => ({
      name: a[1], cls: tab === a[0] ? 'on' : '', pick: () => { this._rev = (this._rev || 0) + 1; this.setState({ tab: a[0] }); }
    }));
    const r = M.rng[fuel ? 'fuel' : 'ign'], d = fuel ? 2 : 1, sel = this.selIdx;
    const fx = Math.max(0, Math.min(M.NX - 1, (this.cur.rpm - 500) / 7000 * (M.NX - 1))), fy = Math.max(0, Math.min(M.NY - 1, (this.cur.map - 17) / 84 * (M.NY - 1)));
    const live = Math.round(fy) * M.NX + Math.round(fx);
    const mix = (a, b, t) => 'rgb(' + [0, 1, 2].map((q) => Math.round(a[q] + (b[q] - a[q]) * t)).join(',') + ')';
    const heat = (t) => night ? mix([11, 14, 17], [10, 63, 168], t) : (t < 0.5 ? mix([255, 255, 255], [255, 208, 0], t * 2) : mix([255, 208, 0], [255, 150, 0], t * 2 - 1));
    v.cells = [];
    let nChg = 0;
    for (let i = 0; i < M.NX; i++) for (let j = 0; j < M.NY; j++) {
      const k = j * M.NX + i, t = Math.max(0, Math.min(1, (A[k] - r[0]) / (r[1] - r[0]))), chg = Math.abs(A[k] - B[k]) > 1e-6;
      if (chg) nChg++;
      v.cells.push({
        val: A[k].toFixed(d), bg: heat(t), fg: night ? '#FFFFFF' : '#000000', aria: M.rpm[i] + ' rpm, ' + M.map[j] + ' kPa',
        cls: 'tc' + (chg ? ' chg' : '') + (k === live ? ' live' : '') + (k === sel ? ' sel' : ''),
        pick: () => { this.selIdx = k; this.setState({ selK: k }); }
      });
    }
    const bump = (s) => () => { A[this.selIdx] += s * (fuel ? 0.05 : 0.5); this._rev = (this._rev || 0) + 1; this.setState({ rev: this._rev }); };
    v.inc = bump(1); v.dec = bump(-1);
    v.undo = () => { this.TB[tab] = this.BASE[tab].slice(); this._rev = (this._rev || 0) + 1; this.setState({ rev: this._rev }); };
    v.unit = fuel ? 'ms' : '\\u00b0BTDC';
    v.selName = M.rpm[sel % M.NX] + ' rpm \\u00d7 ' + M.map[Math.floor(sel / M.NX)] + ' kPa';
    v.selVal = A[sel].toFixed(d); v.selBase = B[sel].toFixed(d);
    const dl = B[sel] ? (A[sel] - B[sel]) / B[sel] * 100 : 0;
    v.selDelta = (dl > 0 ? '+' : (dl < 0 ? '\\u2212' : '')) + Math.abs(dl).toFixed(1) + ' %';
    v.liveName = Math.round(this.cur.rpm) + ' rpm \\u00d7 ' + Math.round(this.cur.map) + ' kPa';
    v.liveVal = this.g ? this.hl3dAt(this.cur.rpm, this.cur.map).v.toFixed(d) : A[live].toFixed(d);
    const tr = this.cur.stft + this.cur.ltft;
    v.liveTrim = (tr > 0 ? '+' : '\\u2212') + Math.abs(tr).toFixed(1);
    v.nChg = nChg;
    v.setCv = this.setCv; v.pd = this.pd; v.pm = this.pm; v.pu = this.pu;
    return v;
  }
"""

# ------------------------------------------------------------------ Alarms
def bar(label, chips):
    return """<div style="display: flex; align-items: center; gap: 8px; height: 40px"><span class="cap" style="width: 124px; flex: none">%s</span>%s</div>""" % (label, chips)


X = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 2 14 14M14 2 2 14" style="fill: none; stroke: currentColor; stroke-width: 3.5"></path></svg>'
ALARMS_HTML = """
<main style="flex: 1; min-height: 0; padding: 8px; display: flex; flex-direction: column; gap: 8px">
<div style="display: flex; gap: 8px; flex: none">
<section class="card" style="flex: 1; gap: 2px">
<span class="cap">Connection bar states &#183; shape first, colour second</span>
%s
%s
%s
%s
</section>
<section class="card" style="width: 420px; flex: none; gap: 5px; font-size: 14px; font-weight: 700">
<span class="cap">Shape key</span>
<span style="display: flex; align-items: center; gap: 10px"><span class="i ok"></span>filled circle: link up, data flowing</span>
<span style="display: flex; align-items: center; gap: 10px"><span class="i off"></span>hollow square: off or not connected</span>
<span style="display: flex; align-items: center; gap: 10px"><span class="i half"></span>half circle: working on it</span>
<span style="display: flex; align-items: center; gap: 10px"><span class="i arm"></span>ring: stall catcher armed</span>
<span style="display: flex; align-items: center; gap: 10px"><span class="thead">%s</span>triangle + yellow band: warning limit</span>
<span style="display: flex; align-items: center; gap: 10px"><span class="thead">%s</span>octagon + filled tile: alarm limit</span>
<span style="display: flex; align-items: center; gap: 10px">%s dashed box: address is a guess</span>
</section>
</div>
<div style="height: 44px; flex: none; display: flex; align-items: center; gap: 12px; padding: 0 6px 0 12px; background: var(--alarm); color: var(--alarmfg)">
<span class="thead">%s</span>
<span style="font-size: 18px; font-weight: 800">STALL 14:02:11</span>
<span style="font-size: 15px; font-weight: 700; flex: 1">RPM dropped to 0. FP FEED fell first, 2.1 s earlier. Saved stall_20261115_140211.csv</span>
<button class="btn" style="height: 34px; background: var(--alarmfg); color: var(--alarm); border-color: var(--alarmfg)">Open in replay<span class="key">Enter</span></button>
<button class="btn" style="height: 34px; background: transparent; color: var(--alarmfg); border-color: var(--alarmfg)">Dismiss<span class="key">Esc</span></button>
</div>
<div style="display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 6px; flex: none">
<sc-for list="{{demo}}" as="c" hint-placeholder-count="8">
<div style="display: flex; flex-direction: column; gap: 3px; min-width: 0">
%s
<span class="cap" style="white-space: nowrap">{{c.cap}}</span>
</div>
</sc-for>
</div>
<div style="flex: 1; min-height: 0; display: flex; gap: 8px">
<section class="card" style="flex: 1; min-width: 0; padding: 8px 0 0; gap: 4px">
<span class="cap" style="padding: 0 10px">Alarm list &#183; live, newest first</span>
<div style="display: flex; flex-direction: column">
<sc-for list="{{activeAlarms}}" as="a" hint-placeholder-count="1">
<div class="{{a.cls}}">
<span class="thead"><sc-if value="{{a.isAlarm}}" hint-placeholder-val="{{false}}">%s</sc-if><sc-if value="{{a.isWarn}}" hint-placeholder-val="{{true}}">%s</sc-if></span>
<span style="font-weight: 800">{{a.sev}}</span><span>{{a.src}} {{a.name}}</span><span class="mono" style="font-weight: 700">{{a.val}}</span><span>{{a.lim}}</span><span class="mono">active</span>
</div>
</sc-for>
<div class="arow"><span class="i off" style="width: 12px; height: 12px"></span><span style="font-weight: 800">CLEARED</span><span>BLACK BOX FP FEED</span><span class="mono" style="font-weight: 700">9.71 V min</span><span>below 11 V for 2.5 s, auto-marked</span><span class="mono">{{lastDip}}</span></div>
</div>
</section>
<section class="card" style="width: 420px; flex: none; gap: 3px">
<span class="cap">Limits &#183; edited in addressmap.toml</span>
<div class="mono" style="display: grid; grid-template-columns: 1fr 86px 86px; gap: 2px 8px; font-size: 13px; font-weight: 700">
<span>CHANNEL</span><span>WARN</span><span>ALARM</span>
<sc-for list="{{limits}}" as="q" hint-placeholder-count="8"><span>{{q.name}}</span><span>{{q.w}}</span><span>{{q.a}}</span></sc-for>
</div>
</section>
</div>
</main>
""" % (
    bar("Disconnected", '<span class="chip"><span class="i off"></span>ECU no port</span><span class="chip"><span class="i off"></span>BLACK BOX not found</span><span class="chip"><span class="i off"></span>REC stopped</span><span class="chip"><span class="i off"></span>STALL CATCHER off</span>'),
    bar("Connecting", '<span class="chip caution"><span class="i half"></span>ECU waking K-line, try 2</span><span class="chip"><span class="i ok"></span>BLACK BOX OK<span class="mono">1.0 kHz</span></span><span class="chip"><span class="i off"></span>REC waiting</span><span class="chip"><span class="i off"></span>STALL CATCHER off</span>'),
    bar("Live", '<span class="chip"><span class="i ok"></span>ECU OK<span class="mono">10.8 Hz</span><span class="mono">0 err</span></span><span class="chip"><span class="i ok"></span>BLACK BOX OK<span class="mono">1.0 kHz</span></span><span class="chip"><span class="i rec"></span>REC<span class="mono">{{recTime}}</span></span><span class="chip"><span class="i arm"></span>STALL CATCHER ARMED</span>'),
    bar("ECU lost", '<span class="chip bad">' + X + 'ECU SILENT<span class="mono">1.4 s</span></span><span class="chip"><span class="i ok"></span>BLACK BOX OK<span class="mono">1.0 kHz</span></span><span class="chip"><span class="i rec"></span>REC<span class="mono">{{recTime}}</span></span><span class="chip bad"><span class="thead">' + OCT + '</span>STALL CAUGHT, saving +10 s</span>'),
    TRI, OCT, UNV, OCT.replace('width="16" height="16"', 'width="22" height="22"'), tile(142), OCT, TRI)

ALARMS_JS = """
  componentDidMount() { this.hlStart(); }
  componentWillUnmount() { this.hlStop(); }
  renderVals() {
    const v = this.hlChrome();
    const T = (id, cap, over) => {
      const c = this.CH.find((x) => x.id === id);
      return Object.assign(this.hlTile(c), { cap }, over || {});
    };
    const base = (t) => t.replace(' warn', '').replace(' alarm', '');
    const rp = T('rpm', 'Normal, verified'), mp = T('map', 'Normal, unverified'), ia = T('iat', 'Warning'), fp = T('fpv', 'Alarm'), o2 = T('o2', 'No data: hatched, dashes');
    rp.cls = base(rp.cls); rp.isWarn = rp.isAlarm = false;
    ia.cls = base(ia.cls) + ' warn'; ia.isWarn = true; ia.isAlarm = false;
    fp.cls = base(fp.cls) + ' alarm'; fp.isAlarm = true; fp.isWarn = false; fp.val = '9.80'; fp.pct = '61.3';
    o2.cls = 'tile stale'; o2.val = '---'; o2.pct = '0'; o2.pts = '';
    const on = T('fp', 'Flag on: filled square'), off = T('vtec', 'Flag off: hollow square'), ml = T('mil', 'Flag in alarm');
    on.cls = 'tile fon'; on.val = 'ON'; off.cls = 'tile foff'; off.val = 'OFF';
    ml.cls = 'tile alarm fon'; ml.val = 'ON'; ml.isAlarm = true;
    v.demo = [rp, mp, ia, fp, o2, on, off, ml];
    const f = (x, u) => (x == null ? '\\u2014' : x + ' ' + u);
    v.limits = this.CH.filter((c) => c.lo != null || c.hi != null || c.wlo != null || c.whi != null).filter((c) => c.k !== 'f').slice(0, 9).map((c) => ({
      name: (c.src === 'BB' ? 'BB ' : '') + c.label,
      w: [c.wlo != null ? '<' + c.wlo : '', c.whi != null ? '>' + c.whi : ''].filter(Boolean).join(' ') || '\\u2014',
      a: [c.lo != null ? '<' + c.lo : '', c.hi != null ? '>' + c.hi : ''].filter(Boolean).join(' ') || '\\u2014'
    }));
    const ph = ((this.simT % 34) + 34) % 34;
    const ago = ph >= 31.5 ? ph - 31.5 : ph + 2.5;
    v.lastDip = Math.floor(ago) + ' s ago';
    return v;
  }
"""

SCREENS = {
    "Sensors": dict(tab="Main.dc.html", title="All sensors", html=SENSORS_HTML, js=SENSORS_JS, three=False),
    "Drive": dict(tab="Drive.dc.html", title="Driving view", html=DRIVE_HTML, js=DRIVE_JS, three=False),
    "Map3D": dict(tab="Map3D.dc.html", title="3D map and analysis", html=MAP_HTML, js=MAP_JS, three=True),
    "Replay": dict(tab="Replay.dc.html", title="Stall forensics replay", html=REPLAY_HTML, js=REPLAY_JS, three=False),
    "RawTools": dict(tab="RawTools.dc.html", title="Raw tools and memory mapper", html=RAW_HTML, js=RAW_JS, three=False),
    "Tune": dict(tab="Tune.dc.html", title="Tuning table editor", html=TUNE_HTML, js=TUNE_JS, three=True),
    "Alarms": dict(tab="Alarms.dc.html", title="Status bar and alarm states", html=ALARMS_HTML, js=ALARMS_JS, three=False),
}

# file -> (screen, default theme, board title)
BOARDS = [
    ("Main.dc.html", "Sensors", "day", "1 All sensors · Daylight"),
    ("Sensors-Night.dc.html", "Sensors", "night", "1 All sensors · Night"),
    ("Drive.dc.html", "Drive", "day", "2 Driving view · Daylight"),
    ("Drive-Night.dc.html", "Drive", "night", "2 Driving view · Night"),
    ("Map3D.dc.html", "Map3D", "night", "3 Map 3D · Night"),
    ("Map3D-Daylight.dc.html", "Map3D", "day", "3 Map 3D · Daylight"),
    ("Replay.dc.html", "Replay", "day", "4 Stall forensics replay"),
    ("RawTools.dc.html", "RawTools", "day", "5 Raw tools and memory mapper"),
    ("Tune.dc.html", "Tune", "night", "6 Tuning table editor (P28)"),
    ("Alarms.dc.html", "Alarms", "day", "7 Status bar and alarms"),
]

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>@@TITLE@@</title>
<script src="./support.js"></script>
@@THREE@@</head>
<body>
<x-dc>
<helmet>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700;800&amp;family=JetBrains+Mono:wght@500;700;800&amp;display=swap">
<style>@@CSS@@</style>
</helmet>
<div class="{{themeClass}}">
@@BODY@@
</div>
</x-dc>
<script type="text/x-dc" data-dc-script data-props='{"theme":{"editor":"enum","options":["day","night"],"default":"@@THEME@@"},"$preview":{"width":1366,"height":768}}'>
class Component extends DCLogic {
@@JS@@
}
</script>
</body>
</html>
"""


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    boards, order = {}, []
    for n, (fname, screen, theme, btitle) in enumerate(BOARDS):
        s = SCREENS[screen]
        body = chrome_top(s["tab"]) + s["html"] + CHROME_BOTTOM
        js = (SHARED_JS + s["js"]).replace("@@THEME@@", theme)
        page = (PAGE.replace("@@TITLE@@", "hondalog: " + s["title"])
                .replace("@@THREE@@", '<script src="%s"></script>\n' % THREE_URL if s["three"] else "")
                .replace("@@CSS@@", CSS).replace("@@BODY@@", body).replace("@@THEME@@", theme).replace("@@JS@@", js))
        (OUT / fname).write_text(page)
        col, row = n % 2, n // 2
        boards[fname] = {"x": col * (1366 + 80), "y": row * (768 + 120), "w": 1366, "h": 768, "title": btitle, "is_interactive": True}
        order.append(fname)
    canvas = {
        "v": 3, "createdOnFiles": {"v": 1, "at": "2026-11-15T03:00:00Z"}, "title": "hondalog UI design",
        "launch": {"view": "canvas"}, "pages": [], "boards": boards, "order": order,
        "notes": {"title": {"x": 0, "y": -300, "text": "hondalog in-car UI, 1366 x 768", "kind": "title1", "maxW": 2812}},
        "designSystems": [],
    }
    (OUT / "canvas.json").write_text(json.dumps(canvas, indent=1))
    print("built", len(order), "artboards")


if __name__ == "__main__":
    build()
