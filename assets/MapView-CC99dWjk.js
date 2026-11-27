import{q as $,D as Ke,o as u,F as se}from"./index-DeDZ3giG.js";const Ze=$.createContext(Ke),D=()=>$.useContext(Ze),M={fontFamily:se.mono};function L(e){const t=D(),{kind:n="normal",disabled:a,active:r}=e,s=n==="primary"?t.accent:n==="danger"?t.alarm:r?t.fg:t.panel,i=n==="primary"?t.onAccent:n==="danger"?t.onAlarm:r?t.bg:t.fg;return u.jsxs("button",{type:e.type??"button",onClick:e.onClick,disabled:a,title:e.title,"aria-label":e["aria-label"],"aria-pressed":r,"data-testid":e.testId,style:{background:s,color:i,border:`2px solid ${n==="normal"?t.rule:s}`,borderRadius:0,padding:"0 12px",minHeight:36,fontFamily:se.sans,fontWeight:700,fontSize:14,cursor:a?"default":"pointer",opacity:a?.45:1,display:"inline-flex",alignItems:"center",gap:8,whiteSpace:"nowrap",...e.style},children:[e.hotkey&&u.jsx("span",{style:{...M,fontSize:12,border:`1px solid ${i}`,padding:"0 4px"},children:e.hotkey}),e.children]})}function ie(e){const t=D();return u.jsxs("section",{style:{background:t.panel,border:`2px solid ${t.rule}`,display:"flex",flexDirection:"column",minHeight:0,minWidth:0,...e.style},children:[(e.title||e.right)&&u.jsxs("header",{style:{display:"flex",alignItems:"center",gap:8,padding:"6px 10px",borderBottom:`2px solid ${t.line}`},children:[u.jsx("h3",{style:{margin:0,fontSize:14,fontWeight:800,flex:1,minWidth:0},children:e.title}),e.right]}),u.jsx("div",{style:{padding:10,minHeight:0,flex:1,overflow:"auto",...e.bodyStyle},children:e.children})]})}function en(e){const t=D();return u.jsx("div",{role:"tablist","aria-label":e.label,style:{display:"flex",flexWrap:"wrap",borderBottom:`2px solid ${t.rule}`},children:e.items.map(([n,a])=>{const r=n===e.value;return u.jsx("button",{role:"tab","aria-selected":r,onClick:()=>e.onChange(n),style:{background:r?t.accent:"transparent",color:r?t.onAccent:t.fg,border:"none",borderRight:`1px solid ${t.line}`,padding:"8px 14px",fontFamily:se.sans,fontWeight:700,fontSize:14,cursor:"pointer",borderRadius:0},children:a},n)})})}function pe(e,t={}){return{background:e.bg,color:e.fg,border:`2px solid ${e.rule}`,borderRadius:0,padding:"6px 8px",fontSize:14,fontFamily:se.sans,minHeight:36,boxSizing:"border-box",...t}}function R(e){const t=D();return u.jsx("input",{type:e.type??"text",value:e.value,disabled:e.disabled,"aria-label":e["aria-label"],placeholder:e.placeholder,onChange:n=>e.onChange(n.target.value),onKeyDown:n=>{n.key==="Enter"&&e.onEnter?.(),n.stopPropagation()},style:pe(t,{...e.monospace?M:{},...e.style})})}function Xe(e){const t=D();return u.jsx("select",{value:e.value,disabled:e.disabled,"aria-label":e["aria-label"],onChange:n=>e.onChange(n.target.value),style:pe(t,e.style),children:e.options.map(([n,a])=>u.jsx("option",{value:n,style:{background:t.bg,color:t.fg},children:a},n))})}function Ge(e){const t=D();return u.jsx("textarea",{value:e.value,readOnly:e.readOnly,rows:e.rows??8,"aria-label":e["aria-label"],placeholder:e.placeholder,spellCheck:!1,onChange:n=>e.onChange?.(n.target.value),onKeyDown:n=>n.stopPropagation(),style:pe(t,{...M,fontSize:13,width:"100%",resize:"vertical",lineHeight:1.4,...e.style})})}function tn(e){const t=D();return u.jsxs("label",{style:{display:"inline-flex",alignItems:"center",gap:8,cursor:"pointer",fontWeight:600},children:[u.jsx("input",{type:"checkbox",checked:e.checked,disabled:e.disabled,onChange:n=>e.onChange(n.target.checked),style:{width:18,height:18,accentColor:t.accent}}),e.children]})}function nn(e){const t=D(),n=e.kind==="ok"?t.ok:e.kind==="err"?t.alarm:t.accent;return u.jsxs("div",{role:"status",style:{display:"flex",flexDirection:"column",gap:4,marginTop:8},children:[u.jsxs("div",{style:{display:"flex",alignItems:"baseline",gap:12},children:[u.jsx("span",{style:{fontSize:26,fontWeight:800},children:e.pct===null?"":`${Math.round(e.pct)}%`}),u.jsx("span",{style:{fontWeight:700},children:e.stage})]}),u.jsx("div",{role:"progressbar","aria-valuemin":0,"aria-valuemax":100,"aria-valuenow":e.pct??void 0,style:{height:18,border:`2px solid ${t.rule}`,background:t.bg,position:"relative"},children:u.jsx("div",{style:{position:"absolute",inset:0,width:e.pct===null?"30%":`${Math.max(0,Math.min(100,e.pct))}%`,background:n}})}),e.detail&&u.jsx("span",{style:{color:t.fg2,fontSize:13},children:e.detail})]})}function De(e){const t=D(),n=e.status,a={...M,fontSize:12,fontWeight:700,padding:"1px 6px",display:"inline-block"};return n==="verified"?u.jsx("span",{style:{...a,background:t.ok,color:t.bg},children:"✓ verified"}):n==="suspect"?u.jsx("span",{style:{...a,background:t.alarm,color:t.onAlarm},children:"✗ suspect"}):u.jsx("span",{style:{...a,border:`2px dashed ${t.unverified}`,color:t.unverified},children:"? unverified"})}function W(e){const t=D();return u.jsx("p",{style:{color:t.fg2,fontSize:13,margin:"6px 0",lineHeight:1.45,...e.style},children:e.children})}function Ye(e,t,n="application/octet-stream"){const a=new Blob([typeof t=="string"?t:new Uint8Array(t)],{type:n}),r=URL.createObjectURL(a),s=document.createElement("a");s.href=r,s.download=e,s.click(),setTimeout(()=>URL.revokeObjectURL(r),1e3)}function an(e){return typeof e.arrayBuffer=="function"?e.arrayBuffer().then(t=>new Uint8Array(t)):new Promise((t,n)=>{const a=new FileReader;a.onload=()=>t(new Uint8Array(a.result)),a.onerror=()=>n(a.error),a.readAsArrayBuffer(e)})}const Qe=`# hondalog address map
#
# This is a BEST GUESS for a stock P2P. The addresses and formulas come from
# kerpz/ArduinoHondaOBD (hobd_uni2), which was tested on a P2T (OBD2) and a
# chipped P30 (OBD1) - not on a P2P. Every entry is marked "unverified" until
# you have checked it against the real car. Use Raw tools > Memory scanner to
# find what really lives where, then edit this file and reconnect.
#
# The app reads ~/.config/hondalog/addressmap.toml (copied from the shipped
# default on first run). Bump "version" when you change it: the version is
# written into every log header.
#
# Channel fields:
#   name     column name in the CSV (letters, digits, underscore)
#   label    text on the dashboard
#   addr     RAM address, 0x00-0xFF
#   length   bytes to read (1-4)
#   formula  Python-style expression. x = the bytes as one unsigned
#            big-endian number, b[0], b[1].. = the single bytes.
#            Helpers: bit(x, n)  signed(x, bits)  poly(x, c0, c1, ...)
#                     abs min max round
#   units    free text
#   min/max  gauge and plot range
#   decimals digits after the point
#   kind     "value" (default) or "flag" (0/1 lamp)
#   gauge    show a big number on the dashboard
#   plot     plotted by default
#   status   "unverified", "verified" or "suspect" (checked and it failed)
#   rate     poll rate class: "fast", "medium" or "slow". How often the
#            channel is read (see rate_*_hz under [protocol]). Left out, the
#            name decides (rpm map tps inj ign are fast, o2 stft ltft vss
#            medium, anything else slow).
#   endian   "big" (default) or "little": byte order of x for 2-4 byte channels
#   source   where the address and formula came from
#   notes    free text
#
# [channel.evidence] is written by the verification tool when you confirm a
# channel: date, method, result, summary, log, map_version, map_hash, reference.
#
# [channel.variants.NAME] overrides addr, length, formula, endian, units,
# min, max, decimals, rate, label, source, notes for another ECU, or drops the
# channel there with absent = true. Verified marks never carry over to
# another ECU: each variant has its own status and evidence.
#
# Further down: [constants], [[math]] channels and [[alarm]] rules.

schema = 2
version = "p2p-guess-2 (kerpz P2T map)"
ecu = "P2P (confirmed on a 2000 Civic EX, 2026-11-25)"
ecu_variant = "P2P"                     # which of the [variants] below to load

# The plain channel entries describe the first variant listed here.
[variants.P2P]
description = "2000 Civic EX, D16Y8, OBD2B. Confirmed 2026-11-25: Honda reads answer at 10400 baud, only after the wake frame (baud = 10400 under [protocol])."

[variants.P2T]
description = "kerpz's OBD2 test ECU. The addresses in this file are his."
protocol = { baud = 9600 }              # kerpz reads his ECU at 9600

[variants.P28]
description = "Chipped OBD1 P28. Needs the OBD1 datalog link, not this K-line protocol."

# Where the ECU ID bytes are thought to live (kerpz: 20 05 76 0A 5B).
[ecu_id]
addr = 0x76
length = 10

# Framing overrides. Defaults are in hondalog/protocol.py (class Framing).
# Uncomment to change. init_sequence = "" sends no wake-up bytes.
[protocol]
# The stock P2P answers Honda reads at 10400 baud, and only after the wake
# frame below (init_sequence, sent by default). It does not answer at 9600.
baud = 10400
# cmd_read = 0x20
# reply_header = 0x00        # -1 accepts any first byte
# reply_timeout_s = 0.2
# init_sequence = "68 6a f5 af bf b3 b2 c1 db b3 e9"
# init_delay_s = 0.3
# max_read_len = 16          # largest block the ECU answers (hondalog-firstcontact probes it)
# Read rates the scheduler aims for, in reads per second per rate class:
# rate_fast_hz = 20
# rate_medium_hz = 5
# rate_slow_hz = 1
# Link timing. reply_timeout_s and inter_request_delay_s above are the ceiling
# and the floor; the link adapts between them from what it measures.
# adaptive_timing = true
# min_reply_timeout_s = 0.02
# inter_byte_timeout_s = 0.05
# inter_request_delay_s = 0.002
# max_inter_request_delay_s = 0.05
# retries = 1                # extra attempts per read before it counts as failed

[[channel]]
name = "rpm"
label = "RPM"
addr = 0x00
length = 2
formula = "x / 4"                       # OBD1 ECUs: 1875000 / (x + 1)
units = "rpm"
min = 0
max = 7200
decimals = 0
gauge = true
plot = true
status = "verified"
rate = "fast"
source = "kerpz hobd_uni2 (P2T)"

[channel.variants.P28]            # OBD1 stores the period, not the speed (kerpz)
formula = "1875000 / (x + 1)"
source = "kerpz hobd_uni2 (OBD1 branch)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "Honda read vs OBD2 PID at warm idle on the 2000 Civic EX P2P (tools/crosscheck_car.py)"
reference = "PID 0C"
[[channel]]
name = "vss"
label = "Speed"
addr = 0x02
length = 1
formula = "x"
units = "km/h"
min = 0
max = 200
decimals = 0
gauge = true
status = "verified"
rate = "medium"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "Honda read vs OBD2 PID at warm idle on the 2000 Civic EX P2P (tools/crosscheck_car.py)"
reference = "PID 0D"
[[channel]]
name = "ect"
label = "Coolant"
addr = 0x10
length = 1
formula = "poly(x, 155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437)"
units = "°C"
min = -20
max = 120
decimals = 0
gauge = true
status = "verified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "Honda read vs OBD2 PID at warm idle on the 2000 Civic EX P2P (tools/crosscheck_car.py)"
reference = "PID 05"
[[channel]]
name = "iat"
label = "Intake air"
addr = 0x11
length = 1
formula = "poly(x, 155.04149, -3.0414878, 0.03952185, -0.00029383913, 0.0000010792568, -0.0000000015618437)"
units = "°C"
min = -20
max = 80
decimals = 0
gauge = true
status = "verified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "Honda read vs OBD2 PID at warm idle on the 2000 Civic EX P2P (tools/crosscheck_car.py)"
reference = "PID 0F"
[[channel]]
name = "map"
label = "MAP"
addr = 0x12
length = 1
formula = "x * 0.716 - 5"               # about 101 at key-on/WOT, 25-40 at idle
units = "kPa"
min = 0
max = 110
decimals = 0
gauge = true
plot = true
status = "verified"
rate = "fast"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "Honda read vs OBD2 PID at warm idle on the 2000 Civic EX P2P (tools/crosscheck_car.py)"
reference = "PID 0B"
[[channel]]
name = "baro"
label = "Baro"
addr = 0x13
length = 1
formula = "x * 0.716 - 5"
units = "kPa"
min = 60
max = 110
decimals = 0
status = "unverified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "tps"
label = "Throttle"
addr = 0x14
length = 1
formula = "x / 2.55"
units = "%"
min = 0
max = 100
decimals = 0
gauge = true
plot = true
status = "verified"
rate = "fast"
source = "the raw byte is the OBD2 PID 11 byte on the P2P"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "raw byte equals the PID 11 byte: 7.8 % at idle both ways"
reference = "PID 11"
[[channel]]
name = "o2"
label = "O2"
addr = 0x15
length = 1
formula = "x / 51.3"
units = "V"
min = 0
max = 1.2
decimals = 2
gauge = true
plot = true
status = "unverified"
rate = "medium"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "batt"
label = "Battery"
addr = 0x17
length = 1
formula = "x / 10.45"
units = "V"
min = 8
max = 16
decimals = 1
gauge = true
plot = true
status = "unverified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "alt_fr"
label = "Alternator load"
addr = 0x18
length = 1
formula = "x / 2.55"
units = "%"
min = 0
max = 100
decimals = 0
status = "unverified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "eld"
label = "Electrical load"
addr = 0x19
length = 1
formula = "77.06 - x / 2.5371"
units = "A"
min = 0
max = 80
decimals = 1
status = "unverified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "stft"
label = "Short trim"
addr = 0x20
length = 1
formula = "(x / 128 - 1) * 100"
units = "%"
min = -30
max = 30
decimals = 1
gauge = true
status = "verified"
rate = "medium"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "Honda read vs OBD2 PID at warm idle on the 2000 Civic EX P2P (tools/crosscheck_car.py)"
reference = "PID 06"
[[channel]]
name = "ltft"
label = "Long trim"
addr = 0x22                             # kerpz uses 0x21 in one place and 0x22 in another
length = 1
formula = "(x / 128 - 1) * 100"
units = "%"
min = -30
max = 30
decimals = 1
gauge = true
status = "verified"
rate = "medium"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "crosscheck:iso"
result = "pass"
summary = "Honda read vs OBD2 PID at warm idle on the 2000 Civic EX P2P (tools/crosscheck_car.py)"
reference = "PID 07"
[[channel]]
name = "inj"
label = "Injector"
addr = 0x24
length = 2
formula = "x / 250"
units = "ms"
min = 0
max = 16
decimals = 2
gauge = true
status = "unverified"
rate = "fast"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "ign"
label = "Ignition"
addr = 0x26
length = 1
formula = "(x - 24) / 4"
units = "° BTDC"
min = -10
max = 50
decimals = 1
gauge = true
status = "unverified"
rate = "fast"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "ign_limit"
label = "Ignition limit"
addr = 0x27
length = 1
formula = "(x - 24) / 4"
units = "°"
min = -10
max = 50
decimals = 1
status = "unverified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "iacv"
label = "Idle valve"
addr = 0x28
length = 1
formula = "x / 2.55"
units = "%"
min = 0
max = 100
decimals = 0
status = "unverified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[[channel]]
name = "knock"
label = "Knock"
addr = 0x3C
length = 1
formula = "x / 51"
units = "V"
min = 0
max = 5
decimals = 2
status = "unverified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

# ---- status flags -----------------------------------------------------------

[[channel]]
name = "ac_sw"
label = "A/C"
addr = 0x08
length = 1
formula = "bit(x, 1)"
kind = "flag"
status = "verified"
rate = "slow"
source = "P30 (fedorenkoalex) + S2000 (mr-sneezy) bit numbering; confirmed on the 2000 Civic EX P2P; kerpz used bit 2 (the PSP switch)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "manual"
result = "pass"
summary = "bit 1 of 0x08 followed the A/C button twice"
reference = "A/C button"
[[channel]]
name = "vtec"
label = "VTEC"
addr = 0x0A
length = 1
formula = "bit(x, 2)"
kind = "flag"
status = "unverified"
rate = "slow"
source = "VTEC solenoid output: P30 (fedorenkoalex) + S2000 (mr-sneezy); kerpz used bit 3. Not yet seen switching on the P2P"

[[channel]]
name = "mil"
label = "MIL"
addr = 0x0B
length = 1
formula = "bit(x, 5)"
kind = "flag"
status = "verified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "manual"
result = "pass"
summary = "bit 5 of 0x0B on at key on (bulb check), off running"
reference = "key-on bulb check"
[[channel]]
name = "closed_loop"
label = "Closed loop"
addr = 0x0F
length = 1
formula = "bit(x, 0)"
kind = "flag"
status = "verified"
rate = "slow"
source = "kerpz hobd_uni2 (P2T)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "manual"
result = "pass"
summary = "bit 0 of 0x0F off when revved to 1600-2400 rpm, on again at idle"
reference = "rev test"

[[channel]]
name = "fuel_pump"
label = "Fuel pump"
addr = 0x0B
length = 1
formula = "bit(x, 0)"
kind = "flag"
status = "verified"
rate = "slow"
source = "PGM-FI main relay output: P30 (fedorenkoalex) + S2000 (mr-sneezy)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "manual"
result = "pass"
summary = "bit 0 of 0x0B on at key on (pump prime) and while running"
reference = "key-on prime"

[[channel]]
name = "fan"
label = "Fan cmd (ECM)"
addr = 0x0C
length = 1
formula = "bit(x, 1)"
kind = "flag"
status = "unverified"
rate = "slow"
source = "P30 (fedorenkoalex) only; the ECU's own fan command, not the 83 C thermoswitch path"

[[channel]]
name = "ac_clutch"
label = "A/C clutch"
addr = 0x0B
length = 1
formula = "bit(x, 1)"
kind = "flag"
status = "verified"
rate = "slow"
source = "A/C compressor clutch relay output: P30 (fedorenkoalex) + S2000 (mr-sneezy)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "manual"
result = "pass"
summary = "bit 1 of 0x0B came on after the A/C request with idle-up to ~1050 rpm, off with it"
reference = "A/C button"

[[channel]]
name = "brake"
label = "Brake"
addr = 0x08
length = 1
formula = "bit(x, 3)"
kind = "flag"
status = "unverified"
rate = "slow"
source = "brake pedal switch: P30 (fedorenkoalex) + S2000 (mr-sneezy)"

[[channel]]
name = "starter"
label = "Starter"
addr = 0x08
length = 1
formula = "bit(x, 0)"
kind = "flag"
status = "verified"
rate = "slow"
source = "starter signal: P30 (fedorenkoalex) + S2000 (mr-sneezy)"

[channel.evidence]
date = "2026-11-25T19:45"
method = "manual"
result = "pass"
summary = "bit 0 of 0x08 set only while cranking"
reference = "key cycle"

# ---- OBD2-only channels -----------------------------------------------------
# Standard Mode 01 PIDs that have no Honda RAM address here. They are read only
# in OBD2-only live mode (the ECU ignores Honda reads but answers ISO 9141-2)
# and stay empty in Honda mode. Fields: name, pid, label, units, min, max,
# decimals, kind, gauge (own tile), plot, rate, source, notes. Decoding is
# SAE J1979's (hondalog/obd2.py), not a formula.

[[obd2]]
name = "o2_post"
pid = 0x15
label = "O2 post"
units = "V"
min = 0
max = 1.2
decimals = 2
gauge = true
plot = true
rate = "medium"
notes = "Downstream (post-catalyst) O2 sensor, bank 1 sensor 2: A / 200 volts"

[[obd2]]
name = "calc_load"
pid = 0x04
label = "Calc load"
units = "%"
min = 0
max = 100
decimals = 0
rate = "medium"
notes = "PID 04 calculated load value. Feeds the load math channel; no tile of its own"

# ---- constants --------------------------------------------------------------
# Named numbers for the expressions below. Check them against your engine.

[constants]
cylinders = 4
displacement_l = 1.590                  # D16Y8
inj_flow_cc_min = 240                   # stock D16Y8 injector at 3 bar (not measured here)
fuel_density_g_cc = 0.745               # petrol
ve_assumed = 0.85                       # volumetric efficiency guess for afr_est

# ---- math channels ----------------------------------------------------------
# Worked out from other channels at poll rate, and again on replay.
#
#   name     column name, like a channel
#   expr     [rpm] or rpm = a channel, [rpm-1] = its previous sample, constants
#            by name. Operators: + - * / // % **  < <= > >= == !=  && || !
#            (also and/or/not)  a ? b : c  (also b if a else c)  & | ^ << >> ~
#            Over time: avg(x, ms) slope(x, ms) minw(x, ms) maxw(x, ms)
#                       stdev(x, ms) smooth(x, n) median(x, n) ema(x, ms)
#                       prev(x, n) delta(x) rate(x) integ(x) held(cond, ms)
#            Plain:     abs min max round int floor ceil sqrt exp ln log10 pow
#                       sign clamp(x, lo, hi) between(x, lo, hi) bit signed
#                       poly table(x, x0, y0, x1, y1, ...) thermistor
#            Other:     valid(x) coalesce(a, b) time()
#   label units min max decimals gauge plot kind description enabled
#
# A math channel may use another math channel. Loops are refused.
# It is only as good as its inputs: all of these are built on unverified addresses.

[[math]]
name = "inj_duty"
label = "Injector duty"
expr = "[inj] * [rpm] / 1200"           # one squirt per cylinder every two turns
units = "%"
min = 0
max = 100
decimals = 1
description = "Share of the time the injector is open. Above about 85% it is out of fuel."

[[math]]
name = "fuel_flow"
label = "Fuel flow"
expr = "[inj_duty] / 100 * inj_flow_cc_min * cylinders * 60 / 1000"
units = "L/h"
min = 0
max = 60
decimals = 2
description = "Estimate from injector size and duty. Ignores injector dead time."

[[math]]
name = "total_trim"
label = "Total trim"
expr = "[stft] + [ltft]"
units = "%"
min = -40
max = 40
decimals = 1
description = "Short plus long fuel trim: how far the ECU is correcting the base fuel."

[[math]]
name = "rpm_rate"
label = "RPM rate"
expr = "slope([rpm], 500)"
units = "rpm/s"
min = -5000
max = 5000
decimals = 0
description = "How fast engine speed is changing, fitted over the last half second."

[[math]]
name = "load"
label = "Load"
expr = "coalesce([calc_load], [map] / [baro] * 100)"
units = "%"
min = 0
max = 100
decimals = 0
description = "OBD2 PID 04 calculated load when the ECU sends it, otherwise manifold pressure as a share of barometric pressure."

[[math]]
name = "afr_est"
label = "AFR estimate"
expr = "clamp(([map] * displacement_l / cylinders * ve_assumed / (0.287 * ([iat] + 273.15))) / ([inj] / 60000 * inj_flow_cc_min * fuel_density_g_cc), 5, 30)"
units = "AFR"
min = 8
max = 22
decimals = 1
description = "Air mass (speed-density with an assumed VE) over fuel mass. A rough guide only: it is not a wideband."

[[math]]
name = "rpm_smooth"
label = "RPM (smoothed)"
expr = "smooth([rpm], 5)"
units = "rpm"
min = 0
max = 7200
decimals = 0
description = "Mean of the last 5 RPM readings."

[[math]]
name = "map_avg"
label = "MAP (0.5 s mean)"
expr = "avg([map], 500)"
units = "kPa"
min = 0
max = 110
decimals = 0

[[math]]
name = "batt_min_5s"
label = "Battery low (5 s)"
expr = "minw([batt], 5000)"
units = "V"
min = 8
max = 16
decimals = 1
description = "Lowest battery reading in the last 5 seconds. Catches short sags."

# ---- alarms -----------------------------------------------------------------
# Checked on every sample, on measured and math channels alike.
#
#   name        letters, digits, underscore
#   channel     the channel that low/high/rate limits look at (colours its tile)
#   low / high  trip below / above this value
#   rate_low / rate_high   trip when the value changes faster than this per
#               second (fitted over rate_window_ms, default 500)
#   when        an expression that trips the alarm, for combinations
#   clear_when  an expression that re-arms a \`when\` alarm (default: \`when\` false)
#   gate        the rule only runs while this expression is true
#   hold_s      must stay tripped this long before the alarm is raised
#   clear_hold_s  must stay clear this long before it clears
#   hysteresis  low/high: must come back inside the limit by this much to clear
#   severity    "warn" or "critical"
#   priority    higher shows first (default 50 warn, 100 critical)
#   latching    true = stays up until acknowledged
#   actions     any of "tile", "banner", "beep", "marker", "capture"
#   message     text; {value} {value:.1f} {label} {units} {high} {low} {rate}
#               and any channel name in braces are filled in
#   label enabled
#
# The limits below are sensible starting points, not Honda figures.

[[alarm]]
name = "ect_high"
label = "Coolant hot"
channel = "ect"
high = 105
hold_s = 2
hysteresis = 3
severity = "critical"
latching = true
actions = ["tile", "banner", "beep", "marker"]
message = "Coolant {value:.0f} °C"

[[alarm]]
name = "batt_low"
label = "Battery low"
channel = "batt"
low = 12.0
hold_s = 3
hysteresis = 0.3
gate = "[rpm] > 400"                    # engine running: the alternator should hold it up
severity = "warn"
actions = ["tile", "banner", "marker"]
message = "Battery {value:.1f} V with the engine running"

[[alarm]]
name = "batt_high"
label = "Overcharging"
channel = "batt"
high = 15.2
hold_s = 2
hysteresis = 0.2
severity = "warn"
actions = ["tile", "banner", "marker"]
message = "Battery {value:.1f} V"

[[alarm]]
name = "over_rev"
label = "Over-rev"
channel = "rpm"
high = 6800
hysteresis = 300
severity = "warn"
actions = ["tile", "banner", "beep"]
message = "{value:.0f} rpm"

[[alarm]]
name = "idle_collapse"
label = "Idle collapsing"
channel = "rpm"
rate_low = -400                         # rpm per second
rate_window_ms = 400
gate = "[rpm] < 600"                    # already below a normal idle and still falling
severity = "warn"
actions = ["tile", "marker"]
message = "RPM {value:.0f} falling {rate:.0f} rpm/s"

[[alarm]]
name = "lean_under_load"
label = "Lean under load"
when = "[o2] < 0.2 && [tps] > 60 && [rpm] > 3000"
hold_s = 0.5
clear_hold_s = 1
severity = "critical"
actions = ["banner", "beep", "marker"]
message = "O2 {o2:.2f} V at {tps:.0f}% throttle, {rpm:.0f} rpm"
`,et=Array.from({length:256},(e,t)=>t.toString(16).toUpperCase().padStart(2,"0"));function ge(e){return e>=0&&e<=255?et[e]:"--"}function sn(e){const t=e.replace(/,/g," ").replace(/0x/g," ").replace(/0X/g," "),n=[];for(let a of t.split(/\s+/).filter(Boolean)){if(a.length%2&&(a="0"+a),!/^[0-9a-fA-F]+$/.test(a))throw new Error(`not hex: ${a}`);for(let r=0;r<a.length;r+=2)n.push(parseInt(a.slice(r,r+2),16))}return Uint8Array.from(n)}function ve(e){return Array.from(e,t=>ge(t)).join(" ")}function Ie(e){let t=0;for(let n=0;n<e.length;n++)t+=e[n];return-t&255}function tt(e){return Uint8Array.from([...Array.from(e),Ie(e)])}function nt(e){let t=0;for(let n=0;n<e.length;n++)t+=e[n];return e.length>0&&(t&255)===0}function at(e,t=0,n=3){for(let a=0;a<e.length-2;a++){if(t>=0&&e[a]!==t)continue;const r=e[a+1]-n+3;if(r>=3&&a+r<=e.length){const s=Array.from(e).slice(a,a+r);if(nt(s))return Uint8Array.from(s)}}return null}function ce(e,t=32){if(e.length!==5&&e.length!==4||e[0]!==t||e[1]!==5)return!1;const n=e[3];return n>=1&&n<=16}function st(e,t){return t>=e.t0&&t<=(e.t1??1/0)}class be{mem=[];times=[];marks=[];meta={};get length(){return this.mem.length}append(t,n){this.mem.push(Int16Array.from(t)),this.times.push(typeof n=="number"?new Float64Array(256).fill(n):Float64Array.from(n))}get t(){return this.times.map(t=>t.reduce((n,a)=>Math.max(n,a),-1/0))}readAddresses(){const t=[];for(let n=0;n<256;n++)this.mem.some(a=>a[n]>=0)&&t.push(n);return t}memAt(t){const n=this.mem[t],a=new Map;for(let r=0;r<256;r++)n[r]>=0&&a.set(r,n[r]);return a}window(t,n){const a=new be;return this.t.forEach((s,i)=>{s>=t&&s<=n&&(a.mem.push(this.mem[i]),a.times.push(this.times[i]))}),a.marks=[...this.marks],a.meta={...this.meta},a}markMask(t){return this.times.map(n=>Array.from(n,a=>this.marks.some(r=>r.label===t&&st(r,a))))}}const ke="OBD2 only: this ECU answers standard OBD2 (ISO 9141-2), not Honda reads, so the sweep and the console are off. Live values, codes and readiness still work.",Ce=Array.from({length:16},(e,t)=>[t*16,16]);class Be{constructor(t,n=()=>performance.now()/1e3){this.link=t,this.clock=n}static HIST=120;cur=new Int16Array(256).fill(-1);fresh=new Uint8Array(256);changes=new Int32Array(256);minv=new Int16Array(256).fill(255);maxv=new Int16Array(256);sweeps=0;sweepS=0;sweeping=!1;revision=0;expert=!1;expertLog=[];hist=[];histT=[];consoleText="";blocks=Ce;loop=null;listeners=new Set;sweepListeners=new Set;available(){return!!this.link&&this.link.available()&&!this.obd2Only()}obd2Only(){return!!this.link?.obd2Only?.()}subscribe(t){return this.listeners.add(t),()=>this.listeners.delete(t)}onSweep(t){return this.sweepListeners.add(t),()=>this.sweepListeners.delete(t)}bump(){this.revision++;for(const t of this.listeners)t()}setBlocks(t){this.blocks=t??Ce}setSweeping(t){this.sweeping=t&&this.available(),t&&this.obd2Only()&&(this.consoleText=ke),this.bump(),this.sweeping&&!this.loop&&(this.loop=this.run().finally(()=>{this.loop=null}))}async stopped(){await this.loop}async run(){for(;this.sweeping&&this.available();)await this.sweepOnce(),await new Promise(t=>setTimeout(t,0));this.sweeping&&(this.sweeping=!1,this.bump())}async sweepOnce(){const t=this.link;if(!t)return;const n=this.clock(),a=new Float64Array(256).fill(NaN),r=new Int16Array(256).fill(-1);this.fresh.fill(0);let s=0,i="";for(const[l,c]of this.blocks){if(!this.available())break;let g;try{g=await t.readMem(l,c)}catch(y){s++,i=y.message;continue}const f=this.clock();for(let y=0;y<g.length&&l+y<256;y++){const p=l+y,k=g[y],S=this.cur[p];S>=0&&S!==k&&(this.fresh[p]=1,this.changes[p]++),this.minv[p]=Math.min(this.minv[p],k),this.maxv[p]=Math.max(this.maxv[p],k),this.cur[p]=k,r[p]=k,a[p]=f}}if(s===this.blocks.length){this.sweeping=!1,this.consoleText=`sweep stopped: ${i||"no reply"}`,this.bump();return}const o=this.clock();this.sweeps++,this.sweepS=o-n,this.hist.push(Int16Array.from(this.cur)),this.histT.push(o),this.hist.length>Be.HIST&&(this.hist.shift(),this.histT.shift());for(const l of this.sweepListeners)l(r,a,o);this.bump()}clearChanges(){this.changes.fill(0),this.fresh.fill(0);for(let t=0;t<256;t++){const n=this.cur[t];this.minv[t]=n>=0?n:255,this.maxv[t]=n>=0?n:0}this.hist=[],this.histT=[],this.sweeps=0,this.bump()}history(t){return this.hist.map(n=>n[t]).filter(n=>n>=0)}recording(){const t=new be;return this.hist.forEach((n,a)=>{n.some(r=>r>=0)&&t.append(n,this.histT[a])}),t}console(){return this.consoleText}setConsole(t){this.consoleText=t,this.bump()}setExpert(t){this.expert=t,this.bump()}async send(t,n){const a=this.link;if(!a||!this.available()||!t.length||!a.raw)return this.setConsole(this.obd2Only()?ke:"not connected"),this.consoleText;const r=n?tt(t):t;if(!this.expert&&!ce(r))return this.setConsole("!! refused: only read requests (20 05 addr len) go out; switch Expert on to send anything else"),this.consoleText;this.expert&&!ce(r)&&this.expertLog.push(`${new Date().toISOString()} ${ve(r)}`);const s=this.clock();try{const{echo:i,rest:o}=await a.raw(r),l=(this.clock()-s)*1e3;let c;o.length?at(o)?c="checksum OK":c="no valid frame":c="no reply"+(i.length?"":", no echo: check cable and 12 V"),this.setConsole(`${ve(o)||"(nothing)"}  ·  ${c} · ${l.toFixed(0)} ms`)}catch(i){this.setConsole(`!! ${i.message}`)}return this.consoleText}exportText(t=new Date){const n=s=>String(s).padStart(2,"0"),r=[`# hondalog memory sweep, ${`${t.getFullYear()}-${n(t.getMonth()+1)}-${n(t.getDate())} ${n(t.getHours())}:${n(t.getMinutes())}:${n(t.getSeconds())}`}, ${this.sweeps} sweeps`,"# addr value min max changes"];for(let s=0;s<256;s++){const i=this.cur[s];i>=0&&r.push(`0x${s.toString(16).toUpperCase().padStart(2,"0")} ${i.toString(16).toUpperCase().padStart(2,"0")} ${this.minv[s]} ${this.maxv[s]} ${this.changes[s]}`)}return r.join(`
`)+`
`}}function rt(e){const t=[0,e.length+3&255,...Array.from(e)];return Uint8Array.from([...t,Ie(t)])}class it{constructor(t=()=>performance.now()/1e3,n=0){this.clock=t,this.delayMs=n,this.t0=t()}state={rpm:780,ac:!1,brake:!1,load:!1,key:!0,running:!0,rev:0,ect:82};t0;available(){return!0}act(t){const n=this.state;switch(t){case"ac_on":n.ac=!0;break;case"ac_off":n.ac=!1;break;case"brake_on":n.brake=!0;break;case"brake_off":n.brake=!1;break;case"load_on":n.load=!0;break;case"load_off":n.load=!1;break;case"rev":n.rev=this.clock();break;case"rev_hold":n.rpm=3e3;break;case"rev_release":n.rpm=780;break;case"key_off":n.key=!1,n.running=!1;break;case"key_on":n.key=!0;break;case"stall":n.running=!1;break;case"start":n.key=!0,n.running=!0,n.rpm=780;break}}ram(){const t=this.state,n=this.clock()-this.t0,a=new Int16Array(256).fill(0);let r=t.running?t.rpm+15*Math.sin(n*1.3)+(t.ac?40:0):0;t.running&&t.rev&&this.clock()-t.rev<2&&(r=2e3);const s=Math.round(r*4);return a[0]=s>>8&255,a[1]=s&255,a[8]=132|(t.ac?2:0)|(t.brake?8:0),a[10]=16|(t.running?128:0),a[11]=(t.running?65:0)|(t.key&&!t.running?32:0)|(t.ac&&t.running?2:0),a[12]=t.load?0:1,a[15]=t.running?1:0,a[16]=Math.round(200-t.ect*1.5),a[18]=t.running?Math.round((35+Math.sin(n))/.716):147,a[19]=147,a[20]=r>1500?40:0,a[21]=t.running?Math.round(51.3*(.45+.4*Math.sin(n*3))):0,a[23]=Math.round((t.running?14.1-(t.load?.3:0):12.4)*10.45),a[25]=Math.round((t.load?40:20)*2.5371),a[28]=a[25],a[32]=128+Math.round(10*Math.sin(n*2)),a[39]=r<845?0:Math.min(4,Math.floor((r-845)/50)+1),a[40]=t.running?10+(t.ac?20:0)+(t.load?8:0):0,a[41]=t.running?40+(t.ac?25:0)+(t.load?10:0):0,a[47]=Math.floor(n/33)&255,a[51]=55,a[53]=45,a[54]=7,a[56]=25,a[57]=25,a[58]=25,a[50]=45-(Math.floor(n*7)%17===0?1:0),a[55]=45,a[118]=12,a[119]=168,a[154]=t.running?2:1,a[156]=t.running?60:0,a[160]=t.running?48:0,a[164]=t.running?1:0,a}async readMem(t,n){if(this.delayMs&&await new Promise(r=>setTimeout(r,this.delayMs)),!this.state.key)throw new Error("no reply");const a=this.ram();return Uint8Array.from({length:Math.min(n,256-t)},(r,s)=>a[t+s])}async raw(t){if(ce(t)){const n=await this.readMem(t[2],t[3]);return{echo:t,rest:rt(n)}}return{echo:t,rest:new Uint8Array}}}const rn=["high","medium","low","none"],X="README.md",j="drive-bytes.md",Ae="2026-11-27/index-bytes.md",J="2026-11-27/output-bytes.md",N="2026-11-27/analog-bytes.md",ot="2026-11-27/idle-810.md";function x(e,t,n,a,r,s,i){return{key:`0x${e.toString(16).toUpperCase().padStart(2,"0")}${t===null?"":"."+t}`,addr:e,bit:t,name:n,what:a,evidence:r,confidence:s,notes:"",source:i}}const lt=[x(3,null,"dead_03_07","0x03-0x07: 0 in every read, driving included","3,198 slow reads incl. WOT, interstate, decel cut","high",j),x(8,0,"starter","starter input","set in the first cranking read only","high",j),x(8,2,"psp","P/S pressure switch, 0 = steering loaded (formula 1 - bit(x, 2))","18 zeros, all junction turns or parking; never with yaw < 10°/s","high",j),x(8,3,"brake","brake switch","set in 97 % of reads decelerating harder than 4 km/h/s","high",j),x(8,7,"not clutch/gear","always 1: all gears, neutral, every clutch take-up; probably the A/T P/N input fixed on the M/T","constant; manual: no clutch or gear input on this ECM","high",j),x(9,null,"scs?","all bits 0; literature b3 = SCS (service check)","never jumped while driving","low",j),x(10,2,"vtec","VTEC solenoid on","1 in one read at 5,376 rpm WOT (23:03:11.70), 0 at 4,786","medium",j),x(10,7,"ecu_ready?","always 1 with the key on","flag-bits.md","low",j),x(11,6,"o2_heater","O2-sensor heater control output: off with the engine stopped, on from the first running read, off above ~3,500 rpm at MAP ≥ ~84 kPa (even in closed loop at 100 km/h); not ALTC (no voltage step); primary or secondary heater unknown","43 zero reads: key-on, crank and 8 high-load episodes; ALT FR +1.3 % (~1-3 A) with it on, battery unchanged","medium",J),x(12,0,"altc?","ALTC, alternator control (1 = normal charging): drops to 0 for a moment at a sudden electrical load step at idle","in those reads ALT FR rises half as much and the battery dips 2.5× deeper; three published tables name the bit Alt-C; ECM pin C2 ALTC exists","medium",J),x(12,3,"vtec_psw","VTEC pressure switch (oil pressure feedback)","set in the same single read as 0x0A b2","low",j),x(13,null,"dead_0d","dead on this car: 0 in all 22,658 reads (key-on, crank, A/C, brake, PSP, VTEC, WOT, decel cut); flags for hardware it does not have. The P2T 'bit 3 = engine-mount solenoid' cannot apply: the 1999-2000 P2P pinout has no such output","output-bytes.md; manual pinout p.361-364 (2026-11-27/ecu-io-checklist.md)","high",J),x(14,null,"dead_0e","dead on this car: 0 in all 22,658 reads; drop from the map after a cold-start log","output-bytes.md","high",J),x(15,0,"closed_loop","0 for ~14 s after start, in decel fuel cut and in WOT enrichment","10.5 % of reads 0, matching 0x9A = 4","high",j),x(22,null,"dead_16","dead: 0 in 8,682 reads (crank, WOT, 5,400 rpm, 107 km/h, decel cut, hours of idle); most likely an unused analog slot on this variant (a heater state would be a bit)","confirm on a cold-start log, then drop","high",J),x(26,null,"dead_1a_1f","0x1A, 0x1B, 0x1D-0x1F: 0 in every read","idle and driving","high",j),x(28,null,"eld copy","copy of ELD (0x19)","equal in 3,198 of 3,198 reads, both passes","high",X),x(33,null,"dead_21_23","0x21, 0x23 (neighbours of LTFT): 0","idle and driving","high",j),x(39,null,"ign_corr_27","ignition correction / limit in 1/4° counts (~0.23° advance per count at fixed rpm and MAP). Below 41 kPa an exact rpm quantiser 0-4 (steps of 58.7 rpm from 781, saturating at 1,016); 41-50 kPa a MAP ramp; above ~50 kPa a remembered state that drops on fast rpm rises and WOT and climbs back 0.2-0.7 counts/s: knock-control timing recovery. Not a table index","24,500 reads over 34 drives","medium",Ae),x(40,null,"iacv","IAC valve, relative: opens with the throttle off idle to ~200, dashpot decay 20 → 6 over ~15 s after a stop","Spearman 0.92 with TPS off idle","medium",j),x(41,null,"iac_cmd","idle air control command before the output curve; 0x28 is a fixed, lagged function of it","r 0.95 with 0x28, same curve in both drives, moves first; hondamotor.ru lists IAC at 0x29","high",X),x(42,null,"iac_learn","learned idle air (IAC learn) in backup RAM: at fully warm idle a lagged copy of the IAC command, otherwise frozen or moving very slowly; survives key-off","output-bytes.md (medium-high)","medium",J),x(43,null,"dead_2b_31","0x2B-0x2E, 0x30, 0x31, 0x34: 0","idle and driving","high",j),x(47,null,"purge_duty?","EVAP purge enable timer / ramp (not the duty itself): runs only with ECT above ~66-69 °C, ramps with airflow, cut in decel and on throttle moves, STFT falls as it rises; the ~810 rpm purge idle follows 5-12 s after it tops out; reset by a start, rpm > 1,000 or ECT < ~68 °C","idle-810: a 450-480 s rule gets 96.5-97 % of 6,893 idle seconds right; manual p.491: 810 ± 50 rpm = purging","medium",ot),x(50,null,"analog_32","live 8-bit sample of a fixed, ratiometric ~0.88 V input: an unused ECM input pin with a bias (or an internal reference); 45, single-read dips to 44 (4.2 %), independent of 0x37; not the EVAP tank pressure","meter check: key on, ECM C6 and A29 against C18 (~0.88 V at C6, ~2.5 V at A29)","medium",N),x(51,null,"purge_ect_thr","constant 55 = 67.5 °C on the ECT scale: most likely the EVAP purge enable threshold (manual: 68 °C)","a cold-start log with 0x2F: purge must not start while ECT raw > 55","low",N),x(53,null,"ect_thr_35","constant 45 = 75.6 °C on the ECT scale (warm-up or thermostat-open level?); never flickers","a cold-start log: a bit that changes as ECT raw falls through 45","low",N),x(54,null,"const_36","constant 7: a calibration count or index (or a 0.14 V ECT short limit)","needs the ROM","none",N),x(55,null,"analog_37","the same kind as 0x32 (a second biased input, or the same pin sampled crank-synchronously): 45, dips to 44 in 6.6 % of reads, fewer with rpm and load","analog-bytes.md","medium",N),x(56,null,"const_38","0x38 = 0x39 = 25 = 99.5 °C on the ECT scale: a stored constant (ECM fan or A/C-cut level?)","a long hot idle until the fan runs: does FANC switch at ECT raw ≈ 25?","low",N),x(58,0,"post_start?","0x3A = 25, bit 0 cleared for the first ~8 s after a start and back as the front O2 leaves its bias rail: a post-start / O2-active flag","a cold start from key-on: does the 24 period last as long as the O2 sits at 200?","low",N),x(59,null,"dead_3b","0 in every read","idle and driving","high",j),x(60,null,"knock_level","knock-sensor background-noise level: noisy (each read independent), rises about with rpm^2.9, ignores load, the same in fuel cut; not a column index. The x/51 V formula is unverified","23,300 reads; any step model leaves 19 % misclassified","medium",Ae),x(118,null,"idle_target","idle target x/4 rpm (0x76-0x77); ECT-driven, −60 rpm while rolling at low ECT","rpm follows 2-3 s later, r 0.95","medium",X),x(120,null,"ecu_id","ECU id / constants start here (not 0x76)","sweep-40-ff.md","medium",X),x(128,null,"monitor_80_8c","0x80-0x8C: per-monitor status / condition bit fields, high nibble unused; lead: bit 2 = incomplete (three bytes have it, three monitors are incomplete)","one read of 0x80-0x9F plus PID 01 at the next key-on","low",N),x(134,null,"o2_monitor?","monitor status bits; lead: the O2 monitor (one of 0x86/0x88/0x8B should lose bit 2 when PID 01 clears a monitor). Earlier read as a small signed table following STFT","sweep-40-ff.md; analog-bytes.md section 3","low",N),x(143,null,"ltft_cell_2?","142 = +10.9 %: a second long-term fuel-trim cell (LTFT 0x22 was 138-145 all evening)","read 0x80-0x9F after 20 min of steady cruising: a trim cell moves slowly and differently from 0x22","low",N),x(148,null,"monitor_94_99","0x94/0x95/0x96/0x99 = 2/15/4/5: 0x95 = supported non-continuous monitors (0x0F), 0x94 = complete mask (heater), 0x96 and 0x99 monitor bookkeeping or counters","PID 01 at the next key-on after highway driving","low",N),x(154,null,"fuel_status","OBD2 PID 03 encoding: 1 = open loop not ready, 2 = closed loop, 4 = open loop driving","1 after start, 2 closed, 4 fuel cut and WOT","high",j),x(155,null,"dead_9b_9f","0x9B, 0x9D-0x9F: 0","idle and driving","high",j),x(156,null,"load_ecu","calculated load (PID 04), 12.5-98 %","98 % at WOT","medium",X),x(160,null,"o2_2","rear O2 (x / 51.3 V): 0.74 V median at cruise, independent of the front, 0.02 V in cut; 201 = start-up default","r 0.11 with the front at any lag on the highway","medium",j),x(164,0,"xa4_b0","an idle-control status bit: on ~2 s after the radiator-fan load dropped off (rpm overshot), off ~46 s later with no visible trigger","one event, in the only full sweep","low",J)],_e="hondalog.bytes.unknown",dt={load(){try{return JSON.parse(globalThis.localStorage?.getItem(_e)??"{}")}catch{return{}}},save(e){try{globalThis.localStorage?.setItem(_e,JSON.stringify(e))}catch{}}};function on(e,t=lt){const n=e.load();return t.map(a=>({...a,...n[a.key]??{}}))}function ln(e,t,n){const a=e.load();a[t]={...a[t]??{},...n},e.save(a)}const ct=Qe;function dn(){return{link:new it,demo:!0,live:null,mapText:ct,saveMap:null,notes:dt,clock:()=>performance.now()/1e3}}let ue={};function cn(e){ue={...ue,...e}}function un(){return ue}const hn=$.createContext(null);/*!
 * Copyright (c) Squirrel Chat et al., All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holder nor the names of its contributors
 *    may be used to endorse or promote products derived from this software without
 *    specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */function ut(e,t){let n=e.slice(0,t).split(/\r?\n/);return[n.length,n.pop().length+1]}function ht(e,t,n){let a=e.split(/\r?\n/),r="",s=(Math.log10(t+1)|0)+1;for(let i=t-1;i<=t+1;i++){let o=a[i-1];o&&(r+=i.toString().padEnd(s," "),r+=":  ",r+=o,r+=`
`,i===t&&(r+=" ".repeat(s+n+2),r+=`^
`))}return r}class v extends Error{line;column;codeblock;constructor(t,n){const[a,r]=ut(n.toml,n.ptr),s=ht(n.toml,a,r);super(`Invalid TOML document: ${t}

${s}`,n),this.line=a,this.column=r,this.codeblock=s}static x(t,n,a){throw new v(t,{toml:n.s,ptr:a??n.p})}}/*!
 * Copyright (c) Squirrel Chat et al., All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holder nor the names of its contributors
 *    may be used to endorse or promote products derived from this software without
 *    specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */function Ne(e){let t=e.p,n=e.s.charCodeAt(e.p++),a=n,r=n===39,s=n===e.s.charCodeAt(e.p)&&n===e.s.charCodeAt(e.p+1);s&&((n=e.s.charCodeAt(e.p+=2))===10?e.p++:n===13&&e.s.charCodeAt(e.p+1)===10&&(e.p+=2));let i="",o=e.p,l=0;for(;e.p<e.s.length;e.p++)if(n=e.s.charCodeAt(e.p),s&&(n===10||n===13&&e.s.charCodeAt(e.p+1)===10))l=l&&3;else if(n<32&&n!==9||n===127)v.x("control characters are not allowed in strings",e);else if((!l||l===3)&&n===a&&(!s||e.s.charCodeAt(e.p+1)===a&&e.s.charCodeAt(e.p+2)===a)){if(s&&(e.s.charCodeAt(e.p+3)===a&&e.p++,e.s.charCodeAt(e.p+3)===a&&e.p++),!l){let c=e.s.slice(o,e.p);i=i?i+c:c}return e.p+=s?3:1,i}else if(!l)!r&&n===92&&(i+=e.s.slice(o,o=e.p),l=1);else if(l===1)if(n===120||n===117||n===85){let c=e.p++-1,g=0,f=n===120?2:n===117?4:8;for(let y=0;y<f;y++,e.p++){let p=e.s.charCodeAt(e.p),k=p>=48&&p<=57?p-48:p>=65&&p<=70?p-65+10:p>=97&&p<=102?p-97+10:-1;k<0&&v.x("invalid non-hex character in unicode escape",e),g=g<<4|k}(g<0||g>1114111||g>=55296&&g<=57343)&&v.x("invalid unicode escape",e,c),i+=String.fromCodePoint(g),o=e.p--,l=0}else s&&(n===32||n===9)?l=2:(n===98?i+="\b":n===116?i+="	":n===110?i+=`
`:n===102?i+="\f":n===114?i+="\r":n===101?i+="\x1B":n===34?i+='"':n===92?i+="\\":v.x("unrecognised escape sequence",e),o=e.p+1,l=0);else n!==32&&n!==9&&(l===2&&v.x("invalid escape: only line-ending whitespace may be escaped",e,o),l=!r&&n===92?1:0,o=e.p);v.x("unfinished string",e,t)}/*!
 * Copyright (c) Squirrel Chat et al., All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holder nor the names of its contributors
 *    may be used to endorse or promote products derived from this software without
 *    specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */let ft=/^(\d{4}-\d{2}-\d{2})?[Tt ]?(?:(\d{2}):\d{2}(?::\d{2}(?:\.\d+)?)?)?(Z|z|[-+]\d{2}:\d{2})?$/i;class K extends Date{#t=!1;#n=!1;#e=null;constructor(t,n,a){let r=!0,s=!0,i="Z",o;if(typeof t=="string")if(n)e:{if(n<3){if(+t.slice(11,13)>23){t="";break e}n===2?(i=null,t+="Z"):(o=t.charCodeAt(t.length-1))!==90&&o!==122&&(i=t.slice(t.length-6)),a&&(t=t.slice(0,10)+"T"+t.slice(11))}else n===4&&(t=+t.slice(0,2)>23?"":`0000-01-01T${t}Z`);r=n!==4,s=n!==3}else{let l=t.match(ft);l?(l[1]||(r=!1,t=`0000-01-01T${t}`),s=!!l[2],s&&t[10]===" "&&(t=t.replace(" ","T")),l[2]&&+l[2]>23?t="":(i=l[3]||null,!i&&s&&(t+="Z"))):t=""}super(t),isNaN(this.getTime())||(this.#t=r,this.#n=s,this.#e=i)}isDateTime(){return this.#t&&this.#n}isLocal(){return!this.#t||!this.#n||!this.#e}isDate(){return this.#t&&!this.#n}isTime(){return this.#n&&!this.#t}isValid(){return this.#t||this.#n}toISOString(){let t=super.toISOString();if(this.isDate())return t.slice(0,10);if(this.isTime())return t.slice(11,23);if(this.#e===null)return t.slice(0,-1);if(this.#e==="Z"||this.#e==="z")return t;let n=+this.#e.slice(1,3)*60+ +this.#e.slice(4,6);return n=this.#e[0]==="-"?n:-n,new Date(this.getTime()-n*6e4).toISOString().slice(0,-1)+this.#e}static wrapAsOffsetDateTime(t,n="Z"){let a=new K(t);return a.#e=n,a}static wrapAsLocalDateTime(t){let n=new K(t);return n.#e=null,n}static wrapAsLocalDate(t){let n=new K(t);return n.#n=!1,n.#e=null,n}static wrapAsLocalTime(t){let n=new K(t);return n.#t=!1,n.#e=null,n}}/*!
 * Copyright (c) Squirrel Chat et al., All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holder nor the names of its contributors
 *    may be used to endorse or promote products derived from this software without
 *    specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */function P(e,t=10){return t===16?e>47&&e<58||e>64&&e<71||e>96&&e<103:e>47&&e<48+t}function he(e,t){return e===32||e===9||e===10||e===13||t&&(e===t||e===44)||e===35}function ye(e,t){let n=e.p,a=e.s.charCodeAt(e.p);if(a===91||a===123){e.d--||v.x("document contains excessively nested structures. aborting.",e);let r=a===91?yt(e):bt(e);return e.d++,r}return a===34||a===39?Ne(e):a===116?((e.s.charCodeAt(++e.p)!==114||e.s.charCodeAt(++e.p)!==117||e.s.charCodeAt(++e.p)!==101)&&v.x("invalid value",e,n),e.p++,!0):a===102?((e.s.charCodeAt(++e.p)!==97||e.s.charCodeAt(++e.p)!==108||e.s.charCodeAt(++e.p)!==115||e.s.charCodeAt(++e.p)!==101)&&v.x("invalid value",e,n),e.p++,!1):a===43||a===45?ne(e,e.p,e.s.charCodeAt(++e.p),44-a,t):e.s.charCodeAt(e.p+4)===45&&e.s.charCodeAt(e.p+7)===45?mt(e,a,t):e.s.charCodeAt(e.p+2)===58?pt(e,a,t):ne(e,e.p,a,0,t)}function ne(e,t,n,a,r){let s=n,i=0,o=!1;if(s===105)return(e.s.charCodeAt(++e.p)!==110||e.s.charCodeAt(++e.p)!==102)&&v.x("invalid value",e,t),e.p++,(a||1)/0;if(s===110)return(e.s.charCodeAt(++e.p)!==97||e.s.charCodeAt(++e.p)!==110)&&v.x("invalid value",e,t),e.p++,NaN;if(s===48){if(++e.p>=e.s.length||he(s=e.s.charCodeAt(e.p),r))return e.bi===!0?0n:0;if(!a){if(s===120)return oe(e,t,16,r);if(s===98)return oe(e,t,2,r);if(s===111)return oe(e,t,8,r)}s===46?i=2:s===101||s===69?i=4:v.x("illegal leading zero",e,t)}else P(s)||v.x("invalid value",e,t);for(;++e.p<e.s.length&&(s=e.s.charCodeAt(e.p),!he(s,r));)i||(i=1),s===95?(i&1||v.x("illegal underscore",e),i+=11,o=!0):i===1&&s===46?i=2:(i===1||i===3)&&(s===101||s===69)?i=4:i===4&&(s===43||s===45)?i=6:P(s)?i>9?i-=11:i&1||i++:v.x("illegal character in numeric literal",e);if(!i){let c=(n-48)*(a||1);return e.bi===!0?BigInt(c):c}i&1||v.x("unfinished numeric value",e,t);let l=e.s.slice(t,e.p);return o&&(l=l.replaceAll("_","")),i>1?parseFloat(l):ze(e,l,10,t)}function oe(e,t,n,a){let r,s=1;for(;++e.p<e.s.length&&(r=e.s.charCodeAt(e.p),!he(r,a));)r===95?(s&1&&v.x("illegal underscore",e),s=3):P(r,n)?s&1&&s--:v.x("illegal character in numeric literal",e);s&1&&v.x("unfinished numeric value",e);let i=e.s.slice(t+2,e.p);return s&&(i=i.replaceAll("_","")),ze(e,i,n,t)}function ze(e,t,n,a){if(e.bi!==!0)e:{let r=parseInt(t,n);if(!Number.isSafeInteger(r)){if(e.bi)break e;v.x("integer value cannot be represented losslessly",e,a)}return r}return BigInt(n===10?t:(n===2?"0b":n===8?"0o":"0x")+t)}function mt(e,t,n){let a=e.p++,r;if(!P(t)||!P(e.s.charCodeAt(e.p++))||!P(e.s.charCodeAt(e.p++))||!P(e.s.charCodeAt(e.p++)))return ne(e,e.p=a,t,0,n);if(e.p+=5,P(e.s.charCodeAt(e.p++))||v.x("invalid date-time: date part is malformed",e,a),e.p>=e.s.length||((t=e.s.charCodeAt(e.p))!==32||(r=!0,!P(e.s.charCodeAt(e.p+1))))&&t!==84&&t!==116){let i=e.s.slice(a,e.p);return G(e,i,3,!1,a)}if(e.s.charCodeAt(e.p+=3)!==58&&v.x("invalid date-time: time part is malformed",e,a),e.s.charCodeAt(e.p+=3)===58&&(e.p+=3),e.s.charCodeAt(e.p)===46)for(;P(e.s.charCodeAt(++e.p)););if(t=e.s.charCodeAt(e.p)){if(t===90||t===122){let i=e.s.slice(a,++e.p);return G(e,i,1,r,a,"[+00:00]")}if(t===43||t===45){let i=e.s.slice(a,e.p+=6);return G(e,i,1,r,a,!e.ld&&"["+e.s.slice(e.p-6,e.p)+"]")}}let s=e.s.slice(a,e.p);return G(e,s,2,r,a)}function pt(e,t,n){let a=e.p;if(!P(t)||!P(e.s.charCodeAt(++e.p)))return ne(e,--e.p,t,0,n);if(e.s.charCodeAt(e.p+=4)===58&&(e.p+=3),e.s.charCodeAt(e.p)===46)for(;P(e.s.charCodeAt(++e.p)););let r=e.s.slice(a,e.p);return G(e,r,4,!1,a)}function G(e,t,n,a,r,s){if(e.ld){let i=new K(t,n,a);return i.isValid()||v.x("invalid date",e,r),i}try{switch(s&&(t+=s),n){case 1:return Temporal.ZonedDateTime.from(t);case 2:return Temporal.PlainDateTime.from(t);case 3:return Temporal.PlainDate.from(t);case 4:return Temporal.PlainTime.from(t)}}catch(i){v.x(i instanceof Error?i.message:""+i,e,r)}}/*!
 * Copyright (c) Squirrel Chat et al., All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holder nor the names of its contributors
 *    may be used to endorse or promote products derived from this software without
 *    specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */function gt(e){for(;e.p<e.s.length;e.p++){let t=e.s.charCodeAt(e.p);if(t===10)break;if(t===13&&e.s.charCodeAt(e.p+1)===10){e.p++;break}(t<32&&t!==9||t===127)&&v.x("control characters are not allowed in comments",e)}}function U(e,t,n){let a;for(;e.p<e.s.length;){for(;e.p<e.s.length&&((a=e.s.charCodeAt(e.p))===32||a===9||!t&&(a===10||a===13&&e.s.charCodeAt(e.p+1)===10));)e.p++;if(n||a!==35)break;gt(e)}}/*!
 * Copyright (c) Squirrel Chat et al., All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holder nor the names of its contributors
 *    may be used to endorse or promote products derived from this software without
 *    specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */function fe(e,t=61){let n,a=0,r=[],s,i=e.s.charCodeAt(n=e.p);do{if(i===t)return a||v.x("unexpected end of key",e),a===1&&r.push(e.s.slice(s,e.p)),e.p++,r;i===46?(a||v.x("illegal empty bare key",e),a===1&&r.push(e.s.slice(s,e.p)),a=0):!a&&(i===34||i===39)?(i===e.s.charCodeAt(e.p+1)&&i===e.s.charCodeAt(e.p+2)&&v.x("illegal quoted key: multiline strings are not allowed",e),r.push(Ne(e)),a=2,e.p--):i===32||i===9?a===1&&(r.push(e.s.slice(s,e.p)),a=2):a===2||i<48&&i!==45||i>57&&i<65||i>90&&i<97&&i!==95||i>122?v.x("illegal character in key",e):a||(a=1,s=e.p)}while(i=e.s.charCodeAt(++e.p));v.x("incomplete key-value: cannot find end of key",e,n)}function bt(e){let t=e.p++,n=Object.create(null),a=new Set,r;for(;e.p<e.s.length;){if(U(e),(r=e.s.charCodeAt(e.p))===125)return e.p++,n;let s,i=n,o=!1,l=e.p,c=fe(e);for(let f=0;f<c.length;f++){f&&(i=o?i[s]:i[s]=Object.create(null)),s=c[f],(o=Object.hasOwn(i,s))&&(typeof i[s]!="object"||a.has(i[s]))&&v.x("trying to redefine an already defined value",e,l);let y=s==="__proto__";if(e.uk&&(y||s==="constructor")){i=e.uk!==1&&v.x("document contains an unsafe property",e,l);break}!o&&y&&Object.defineProperty(i,s,{enumerable:!0,configurable:!0,writable:!0})}o&&v.x("trying to redefine an already defined value",e,l),U(e,!0,!0);let g=ye(e,125);if(i&&typeof(i[s]=g)=="object"&&a.add(g),U(e),(r=e.s.charCodeAt(e.p++))===125)return n;r!==44&&v.x("expected comma or end of structure",e,e.p-1)}v.x("unfinished table",e,t)}function yt(e){let t=e.p++,n=[],a;for(;e.p<e.s.length;){if(U(e),(a=e.s.charCodeAt(e.p))===93)return e.p++,n;if(n.push(ye(e,93)),U(e),(a=e.s.charCodeAt(e.p++))===93)return n;a!==44&&v.x("expected comma or end of structure",e,e.p-1)}v.x("unfinished array",e,t)}/*!
 * Copyright (c) Squirrel Chat et al., All rights reserved.
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the copyright holder nor the names of its contributors
 *    may be used to endorse or promote products derived from this software without
 *    specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */function Se(e,t,n,a,r){let s=n,i=a,o,l=!1,c;for(let g=0;g<t.length;g++){if(g){if(s=l?s[o]:s[o]=Object.create(null),i=(c=i[o]).c,r===0&&(c.t===1||c.t===2))return null;if(c.t===2){let f=s.length-1;s=s[f],i=i[f].c}}if(o=t[g],(l=Object.hasOwn(s,o))&&i[o]?.t===0&&i[o]?.d)return null;if(!l){let f=o==="__proto__";if(e.uk&&(f||o==="constructor"))return!1;f&&(Object.defineProperty(s,o,{enumerable:!0,configurable:!0,writable:!0}),Object.defineProperty(i,o,{enumerable:!0,configurable:!0,writable:!0})),i[o]={t:g<t.length-1&&r===2?3:r,d:!1,i:0,c:Object.create(null)}}}if(c=i[o],c.t!==r&&!(r===1&&c.t===3)||(r===2&&(c.d||(c.d=!0,s[o]=[]),s[o].push(s=Object.create(null)),c.c[c.i++]=c={t:1,d:!1,i:0,c:Object.create(null)}),c.d))return null;if(c.d=!0,r===1)s=l?s[o]:s[o]=Object.create(null);else if(r===0&&l)return null;return[o,s,c.c]}function Te(e,t,n){(t===null||e.uk===2)&&v.x(t===null?"trying to redefine an already defined table or value":"document contains an unsafe property",e,n)}function Fe(e,t={}){let n={s:e,p:0,d:t.maxDepth??1e3,bi:t.integersAsBigInt??!1,ld:t.useLegacyDate??!0,uk:t.unsafeKeyBehaviour==="throw"?2:t.unsafeKeyBehaviour==="drop"?1:0},a=Object.create(null),r=Object.create(null),s,i=!1,o=a,l=r;for(e.charCodeAt(0)===65279&&n.p++,U(n);n.p<e.length;){if(e.charCodeAt(n.p)===91){let c=e.charCodeAt(++n.p)===91;s=n.p+=+c,i=!1;let g=fe(n,93);c&&(e.charCodeAt(n.p)!==93&&v.x("expected end of table array declaration",n),n.p++);let f=Se(n,g,a,r,c?2:1);f?(l=f[2],o=f[1]):(Te(n,f,s),i=!0)}else{s=n.p;let c=fe(n),g=Se(n,c,o,l,0);!g&&!i&&Te(n,g,s),U(n,!0,!0);let f=ye(n,void 0);g&&!i&&(g[1][g[0]]=f)}U(n,!0),n.p<e.length&&(s=e.charCodeAt(n.p))!==10&&(s!==13||e.charCodeAt(n.p+1)!==10)&&v.x("each key-value declaration must be followed by an end-of-line",n),U(n)}return a}/*! noble-hashes - MIT License (c) 2022 Paul Miller (paulmillr.com) */function wt(e){return e instanceof Uint8Array||ArrayBuffer.isView(e)&&e.constructor.name==="Uint8Array"}function re(e,...t){if(!wt(e))throw new Error("Uint8Array expected");if(t.length>0&&!t.includes(e.length))throw new Error("Uint8Array expected of length "+t+", got length="+e.length)}function je(e,t=!0){if(e.destroyed)throw new Error("Hash instance has been destroyed");if(t&&e.finished)throw new Error("Hash#digest() has already been called")}function xt(e,t){re(e);const n=t.outputLen;if(e.length<n)throw new Error("digestInto() expects output buffer of length at least "+n)}function me(...e){for(let t=0;t<e.length;t++)e[t].fill(0)}function le(e){return new DataView(e.buffer,e.byteOffset,e.byteLength)}function z(e,t){return e<<32-t|e>>>t}function fn(e,t){return e<<t|e>>>32-t>>>0}const vt=typeof Uint8Array.from([]).toHex=="function"&&typeof Uint8Array.fromHex=="function",kt=Array.from({length:256},(e,t)=>t.toString(16).padStart(2,"0"));function Ct(e){if(re(e),vt)return e.toHex();let t="";for(let n=0;n<e.length;n++)t+=kt[e[n]];return t}function At(e){if(typeof e!="string")throw new Error("string expected");return new Uint8Array(new TextEncoder().encode(e))}function Le(e){return typeof e=="string"&&(e=At(e)),re(e),e}class _t{}function St(e){const t=a=>e().update(Le(a)).digest(),n=e();return t.outputLen=n.outputLen,t.blockLen=n.blockLen,t.create=()=>e(),t}function Tt(e,t,n,a){if(typeof e.setBigUint64=="function")return e.setBigUint64(t,n,a);const r=BigInt(32),s=BigInt(4294967295),i=Number(n>>r&s),o=Number(n&s),l=a?4:0,c=a?0:4;e.setUint32(t+l,i,a),e.setUint32(t+c,o,a)}function jt(e,t,n){return e&t^~e&n}function $t(e,t,n){return e&t^e&n^t&n}class Ot extends _t{constructor(t,n,a,r){super(),this.finished=!1,this.length=0,this.pos=0,this.destroyed=!1,this.blockLen=t,this.outputLen=n,this.padOffset=a,this.isLE=r,this.buffer=new Uint8Array(t),this.view=le(this.buffer)}update(t){je(this),t=Le(t),re(t);const{view:n,buffer:a,blockLen:r}=this,s=t.length;for(let i=0;i<s;){const o=Math.min(r-this.pos,s-i);if(o===r){const l=le(t);for(;r<=s-i;i+=r)this.process(l,i);continue}a.set(t.subarray(i,i+o),this.pos),this.pos+=o,i+=o,this.pos===r&&(this.process(n,0),this.pos=0)}return this.length+=t.length,this.roundClean(),this}digestInto(t){je(this),xt(t,this),this.finished=!0;const{buffer:n,view:a,blockLen:r,isLE:s}=this;let{pos:i}=this;n[i++]=128,me(this.buffer.subarray(i)),this.padOffset>r-i&&(this.process(a,0),i=0);for(let f=i;f<r;f++)n[f]=0;Tt(a,r-8,BigInt(this.length*8),s),this.process(a,0);const o=le(t),l=this.outputLen;if(l%4)throw new Error("_sha2: outputLen should be aligned to 32bit");const c=l/4,g=this.get();if(c>g.length)throw new Error("_sha2: outputLen bigger than state");for(let f=0;f<c;f++)o.setUint32(4*f,g[f],s)}digest(){const{buffer:t,outputLen:n}=this;this.digestInto(t);const a=t.slice(0,n);return this.destroy(),a}_cloneInto(t){t||(t=new this.constructor),t.set(...this.get());const{blockLen:n,buffer:a,length:r,finished:s,destroyed:i,pos:o}=this;return t.destroyed=i,t.finished=s,t.length=r,t.pos=o,r%n&&t.buffer.set(a),t}clone(){return this._cloneInto()}}const H=Uint32Array.from([1779033703,3144134277,1013904242,2773480762,1359893119,2600822924,528734635,1541459225]),Et=Uint32Array.from([1116352408,1899447441,3049323471,3921009573,961987163,1508970993,2453635748,2870763221,3624381080,310598401,607225278,1426881987,1925078388,2162078206,2614888103,3248222580,3835390401,4022224774,264347078,604807628,770255983,1249150122,1555081692,1996064986,2554220882,2821834349,2952996808,3210313671,3336571891,3584528711,113926993,338241895,666307205,773529912,1294757372,1396182291,1695183700,1986661051,2177026350,2456956037,2730485921,2820302411,3259730800,3345764771,3516065817,3600352804,4094571909,275423344,430227734,506948616,659060556,883997877,958139571,1322822218,1537002063,1747873779,1955562222,2024104815,2227730452,2361852424,2428436474,2756734187,3204031479,3329325298]),V=new Uint32Array(64);class Pt extends Ot{constructor(t=32){super(64,t,8,!1),this.A=H[0]|0,this.B=H[1]|0,this.C=H[2]|0,this.D=H[3]|0,this.E=H[4]|0,this.F=H[5]|0,this.G=H[6]|0,this.H=H[7]|0}get(){const{A:t,B:n,C:a,D:r,E:s,F:i,G:o,H:l}=this;return[t,n,a,r,s,i,o,l]}set(t,n,a,r,s,i,o,l){this.A=t|0,this.B=n|0,this.C=a|0,this.D=r|0,this.E=s|0,this.F=i|0,this.G=o|0,this.H=l|0}process(t,n){for(let f=0;f<16;f++,n+=4)V[f]=t.getUint32(n,!1);for(let f=16;f<64;f++){const y=V[f-15],p=V[f-2],k=z(y,7)^z(y,18)^y>>>3,S=z(p,17)^z(p,19)^p>>>10;V[f]=S+V[f-7]+k+V[f-16]|0}let{A:a,B:r,C:s,D:i,E:o,F:l,G:c,H:g}=this;for(let f=0;f<64;f++){const y=z(o,6)^z(o,11)^z(o,25),p=g+y+jt(o,l,c)+Et[f]+V[f]|0,S=(z(a,2)^z(a,13)^z(a,22))+$t(a,r,s)|0;g=c,c=l,l=o,o=i+p|0,i=s,s=r,r=a,a=p+S|0}a=a+this.A|0,r=r+this.B|0,s=s+this.C|0,i=i+this.D|0,o=o+this.E|0,l=l+this.F|0,c=c+this.G|0,g=g+this.H|0,this.set(a,r,s,i,o,l,c,g)}roundClean(){me(V)}destroy(){this.set(0,0,0,0,0,0,0,0),me(this.buffer)}}const Mt=St(()=>new Pt),Dt=Mt,It=["**","//","<<",">>","<=",">=","==","!=","+","-","*","/","%","(",")","[","]",",","&","|","^","<",">","~"];function Bt(e){const t=[];let n=0;for(;n<e.length;){const a=e[n];if(/\s/.test(a)){n++;continue}const r=/^0[xX][0-9a-fA-F]+/.exec(e.slice(n));if(r){t.push({k:"num",v:parseInt(r[0],16)}),n+=r[0].length;continue}const s=/^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(e.slice(n));if(s){t.push({k:"num",v:parseFloat(s[0])}),n+=s[0].length;continue}const i=/^[A-Za-z_][A-Za-z0-9_]*/.exec(e.slice(n));if(i){t.push({k:"id",v:i[0]}),n+=i[0].length;continue}const o=It.find(l=>e.startsWith(l,n));if(!o)throw new Error(`bad formula ${JSON.stringify(e)}: unexpected ${JSON.stringify(a)}`);t.push({k:"op",v:o}),n+=o.length}return t}const Nt={bit:(e,t)=>Math.trunc(e)>>Math.trunc(t)&1,signed:(e,t=8)=>{const n=2**t,a=(Math.trunc(e)%n+n)%n;return a>=n/2?a-n:a},poly:(e,...t)=>t.reduce((n,a,r)=>n+a*e**r,0),abs:Math.abs,min:Math.min,max:Math.max,round:(e,t=0)=>{const n=10**t,a=e*n,r=Math.round(a);return(Math.abs(a%1)===.5?r%2===0?r:r-1:r)/n},int:Math.trunc,float:e=>e};function we(e,t={}){const n=Bt(e);let a=0;const r=()=>n[a],s=h=>{const b=r();return b?.k==="op"&&b.v===h},i=h=>{const b=r();return b?.k==="id"&&b.v===h},o=h=>{if(!s(h))throw new Error(`bad formula ${JSON.stringify(e)}: expected ${h}`);a++};function l(){const h=r();if(!h)throw new Error(`bad formula ${JSON.stringify(e)}: ends too soon`);if(h.k==="num"){a++;const b=h.v;return()=>b}if(h.k==="op"&&h.v==="("){a++;const b=E();return o(")"),b}if(h.k==="id"){a++;const b=h.v;if(s("(")){a++;const d=[];if(!s(")"))for(d.push(E());s(",");)a++,d.push(E());o(")");const m=Nt[b];if(!m)throw new Error(`bad formula ${JSON.stringify(e)}: unknown function ${b}`);return w=>m(...d.map(_=>_(w)))}if(b==="x")return d=>d.x;if(b==="b"){o("[");const d=E();return o("]"),m=>{const w=Math.trunc(d(m)),_=m.b[w];if(_===void 0)throw new Error(`b[${w}] out of range`);return _}}if(b==="True")return()=>1;if(b==="False")return()=>0;if(b in t){const d=t[b];return()=>d}throw new Error(`bad formula ${JSON.stringify(e)}: unknown name ${b}`)}throw new Error(`bad formula ${JSON.stringify(e)}: unexpected ${h.v}`)}function c(){if(s("-")){a++;const h=c();return b=>-h(b)}if(s("+"))return a++,c();if(s("~")){a++;const h=c();return b=>~Math.trunc(h(b))}return g()}function g(){const h=l();if(s("**")){a++;const b=c();return d=>h(d)**b(d)}return h}function f(){let h=c();for(;;){const b=r();if(b?.k!=="op"||!["*","/","//","%"].includes(b.v))return h;a++;const d=c(),m=h;b.v==="*"?h=w=>m(w)*d(w):b.v==="/"?h=w=>{const _=d(w);if(_===0)throw new Error("division by zero");return m(w)/_}:b.v==="//"?h=w=>{const _=d(w);if(_===0)throw new Error("division by zero");return Math.floor(m(w)/_)}:h=w=>{const _=d(w);if(_===0)throw new Error("division by zero");const B=m(w);return B-_*Math.floor(B/_)}}}function y(){let h=f();for(;s("+")||s("-");){const b=r().v;a++;const d=f(),m=h;h=b==="+"?w=>m(w)+d(w):w=>m(w)-d(w)}return h}function p(){let h=y();for(;s("<<")||s(">>");){const b=r().v;a++;const d=y(),m=h;h=b==="<<"?w=>Math.trunc(m(w))<<Math.trunc(d(w)):w=>Math.trunc(m(w))>>Math.trunc(d(w))}return h}function k(){let h=p();for(;s("&");){a++;const b=p(),d=h;h=m=>Math.trunc(d(m))&Math.trunc(b(m))}return h}function S(){let h=k();for(;s("^");){a++;const b=k(),d=h;h=m=>Math.trunc(d(m))^Math.trunc(b(m))}return h}function O(){let h=S();for(;s("|");){a++;const b=S(),d=h;h=m=>Math.trunc(d(m))|Math.trunc(b(m))}return h}function I(){let h=O();for(;;){const b=r();if(b?.k!=="op"||!["<",">","<=",">=","==","!="].includes(b.v))return h;a++;const d=O(),m=h,w=b.v;h=_=>{const B=m(_),q=d(_);return(w==="<"?B<q:w===">"?B>q:w==="<="?B<=q:w===">="?B>=q:w==="=="?B===q:B!==q)?1:0}}}function A(){if(i("not")){a++;const h=A();return b=>h(b)?0:1}return I()}function T(){let h=A();for(;i("and");){a++;const b=A(),d=h;h=m=>{const w=d(m);return w&&b(m)}}return h}function E(){let h=T();for(;i("or");){a++;const b=T(),d=h;h=m=>{const w=d(m);return w||b(m)}}return h}const Z=E();if(a!==n.length)throw new Error(`bad formula ${JSON.stringify(e)}: unexpected ${n[a].v}`);return(h,b)=>Z({x:h,b})}const $e=["unverified","verified","suspect"],de=["fast","medium","slow"],te=/^[A-Za-z_][A-Za-z0-9_]*$/,zt=new Set(["schema","version","ecu","ecu_variant","base_variant","variants","ecu_id","protocol","constants","channel","math","alarm","obd2"]),Ft=new Set(["addr","length","formula","endian","label","units","min","max","decimals","rate","status","evidence","source","notes","absent"]),Lt={rpm:"fast",map:"fast",tps:"fast",inj:"fast",ign:"fast",o2:"medium",stft:"medium",ltft:"medium",vss:"medium"};class C extends Error{}const F=e=>typeof e=="object"&&e!==null&&!Array.isArray(e)&&!(e instanceof Date),ae=e=>typeof e=="number"&&Number.isInteger(e);function Y(e,t){return typeof e=="number"?e:typeof e=="bigint"?Number(e):t}function Oe(e){const t=e.name;if(typeof t!="string"||!te.test(t))throw new C(`bad channel name ${JSON.stringify(t)}`);const n=new Set(["name","addr","length","formula","label","units","min","max","decimals","kind","gauge","plot","status","rate","endian","source","notes","evidence","variants","group"]),a=Object.keys(e).filter(p=>!n.has(p)).sort();if(a.length)throw new C(`${t}: unexpected field ${JSON.stringify(a[0])}`);const r=e.addr;if(!ae(r)||r<0||r>255)throw new C(`${t}: addr out of range`);const s=e.length??1;if(!ae(s)||s<1||s>4||r+s>256)throw new C(`${t}: length must be 1-4 and stay below 0x100`);const i=e.kind??"value";if(i!=="value"&&i!=="flag")throw new C(`${t}: kind must be 'value' or 'flag'`);let o=typeof e.rate=="string"?e.rate.trim().toLowerCase():e.rate===void 0?"":null;if(o===null)throw new C(`${t}: rate must be one of ${de.join(", ")}`);if(o=o||Lt[t]||"slow",!de.includes(o))throw new C(`${t}: rate must be one of ${de.join(", ")}`);const l=e.endian??"big";if(l!=="big"&&l!=="little")throw new C(`${t}: endian must be 'big' or 'little'`);const c=typeof e.status=="string"?e.status.toLowerCase():e.status===void 0?"unverified":"";if(!$e.includes(c))throw new C(`${t}: status must be one of ${$e.join(", ")}`);const g=e.formula??"x";if(typeof g!="string")throw new C(`${t}: formula must be text`);try{we(g)}catch(p){const k=p.message;if(!/unknown name/.test(k))throw new C(`${t}: ${k}`)}let f=null;if(e.evidence!==void 0){if(!F(e.evidence))throw new C(`${t}: evidence must be a table`);f=Object.fromEntries(Object.entries(e.evidence).map(([p,k])=>[p,String(k)]))}if(e.variants!==void 0){if(!F(e.variants))throw new C(`${t}: variants must be a table`);for(const[p,k]of Object.entries(e.variants)){if(!F(k))throw new C(`${t}: variants.${p} must be a table`);const S=Object.keys(k).filter(O=>!Ft.has(O)).sort();if(S.length)throw new C(`${t}: variants.${p} cannot set ${JSON.stringify(S[0])}`)}}const y=i==="flag";return{name:t,label:e.label||t,addr:r,length:s,formula:g,units:typeof e.units=="string"?e.units:"",min:y?0:Y(e.min,0),max:y?1:Y(e.max,255),decimals:y?0:Y(e.decimals,0),kind:i,status:c,rate:o,endian:l,source:e.source??"",notes:e.notes??"",evidence:f}}function Ue(e,t){let n;try{n=Fe(e)}catch(d){throw new C(`not valid TOML: ${d.message.split(`
`)[0]}`)}const a=n.schema??1;if(!ae(a)||a<1)throw new C("schema must be a whole number");if(a>2)throw new C(`written for map schema ${a}, this hondalog reads up to 2`);if(a>=2){const d=Object.keys(n).filter(m=>!zt.has(m)).sort();if(d.length)throw new C(`unknown section or key ${JSON.stringify(d[0])}`)}const r=n.channel??[];if(!Array.isArray(r))throw new C("[[channel]] entries expected");const s=r.map(d=>{if(!F(d))throw new C("[[channel]] entries expected");return d}),i=s.map(Oe);if(!i.length)throw new C("no [[channel]] entries");const o=i.map(d=>d.name),l=[...new Set(o.filter((d,m)=>o.indexOf(d)!==m))].sort();if(l.length)throw new C(`duplicate channel names ${l.join(", ")}`);const c=n.variants??{};if(!F(c)||!Object.values(c).every(F))throw new C("[variants] must hold one table per ECU");const g=Object.keys(c),f=String(n.base_variant||g[0]||""),y=String(n.ecu_variant||f),p=new Set;if(s.forEach(d=>{F(d.variants)&&Object.keys(d.variants).forEach(m=>p.add(m))}),g.length){const d=[...new Set([...p,y,f])].filter(m=>m&&!g.includes(m)).sort();if(d.length)throw new C(`ECU variant ${JSON.stringify(d[0])} is not listed under [variants]`)}else if(y&&!p.has(y)&&y!==f)throw new C(`no ECU variant ${JSON.stringify(y)} in this map`);const k=[];if(i.forEach((d,m)=>{if(!y||y===f){k.push(d);return}const w={...s[m].variants?.[y]??{}};if(w.absent)return;const _={...s[m],status:"unverified",...w};delete _.variants,delete _.absent,"evidence"in w||delete _.evidence;const B=Oe(_);k.push(B)}),!k.length)throw new C(`variant ${JSON.stringify(y)} has no channels`);const S=y!==f&&F(c[y])?c[y]:{},O={};for(const[d,m]of[["[constants]",n.constants],[`[variants.${y}.constants]`,S.constants]])if(m!==void 0){if(!F(m))throw new C(`${d} must be a table`);for(const[w,_]of Object.entries(m)){if(!te.test(w))throw new C(`${d}: bad name ${JSON.stringify(w)}`);if(typeof _!="number"&&typeof _!="bigint")throw new C(`${d}: ${w} must be a number`);O[w]=Number(_)}}const I=Object.keys(O).filter(d=>o.includes(d)).sort();if(I.length)throw new C(`constant ${JSON.stringify(I[0])} has the same name as a channel`);for(const d of k)try{we(d.formula,O)}catch(m){throw new C(`${d.name}: ${m.message}`)}const A=(n.obd2??[]).map(d=>({name:String(d.name),pid:Y(d.pid,0),units:d.units??"",kind:d.kind??"value"})),T=A.map(d=>d.name).filter(d=>o.includes(d));if(T.length)throw new C(`obd2 channel ${JSON.stringify(T[0])} has the same name as a channel`);const E=(n.math??[]).map(d=>{if(typeof d.name!="string"||!te.test(d.name))throw new C(`bad math channel name ${JSON.stringify(d.name)}`);if(typeof d.expr!="string")throw new C(`math ${d.name}: expr must be text`);return{name:d.name,expr:d.expr,units:d.units??"",label:d.label||d.name,enabled:d.enabled!==!1}}),Z=(n.alarm??[]).map(d=>{if(typeof d.name!="string"||!te.test(d.name))throw new C(`bad alarm name ${JSON.stringify(d.name)}`);const m=d.severity??"warn";if(m!=="warn"&&m!=="critical")throw new C(`alarm ${JSON.stringify(d.name)}: severity must be 'warn' or 'critical'`);return{...d,name:d.name}}),h=F(n.ecu_id)?n.ecu_id:{},b={version:String(n.version??"unversioned"),ecu:String(S.description||n.ecu||""),variant:y,baseVariant:f,variants:g,channels:k,math:E,alarms:Z,obd2:A,constants:O,ecuId:{addr:ae(h.addr)?h.addr:null,length:Y(h.length,10)},text:e};return{...b,hash:Vt(b)}}function Ut(e,t){try{const n=Ue(e,t),a=[],r=new Map;for(const s of n.channels)for(let i=s.addr;i<s.addr+s.length;i++){const o=r.get(i);o&&s.kind!=="flag"&&a.push(`note: 0x${i.toString(16).toUpperCase().padStart(2,"0")} is read by ${o} and ${s.name}`),o||r.set(i,s.name)}return a.filter(s=>!s.startsWith("note:")).concat(a.filter(s=>s.startsWith("note:")))}catch(n){return[n.message]}}function He(e){if(!Number.isFinite(e))return e>0?"Infinity":e<0?"-Infinity":"NaN";if(Number.isInteger(e)&&Math.abs(e)<1e16)return`${e}.0`;let t=String(e);const n=/^(-?[\d.]+)e([+-])(\d+)$/.exec(t);if(n)t=`${n[1]}e${n[2]}${n[3].padStart(2,"0")}`;else if(Math.abs(e)<1e-4&&e!==0){const a=e.toExponential(),r=/^(-?[\d.]+)e([+-])(\d+)$/.exec(a);t=`${r[1]}e${r[2]}${r[3].padStart(2,"0")}`}return t}function Q(e,t){if(e==null)return"null";if(typeof e=="string")return JSON.stringify(e);if(typeof e=="number")return t?He(e):String(e);if(typeof e=="boolean")return e?"true":"false";if(Array.isArray(e))return`[${e.map(a=>Q(a,t)).join(",")}]`;const n=e;return`{${Object.keys(n).sort().map(a=>`${JSON.stringify(a)}:${Q(n[a],t)}`).join(",")}}`}function Ht(e){const t=[],n=e.channels.map(s=>Q([s.name,s.addr,s.length,s.endian,s.formula,s.units,s.kind],!1)),a=Object.keys(e.constants).sort().map(s=>`[${JSON.stringify(s)},${He(e.constants[s])}]`),r=e.math.filter(s=>s.enabled).map(s=>Q([s.name,s.expr,s.units],!1));return t.push(`"channels":[${n.join(",")}]`),t.push(`"constants":[${a.join(",")}]`),t.push(`"math":[${r.join(",")}]`),e.obd2.length&&t.push(`"obd2":[${e.obd2.map(s=>Q([s.name,s.pid,s.units,s.kind],!1)).join(",")}]`),t.push(`"variant":${JSON.stringify(e.variant)}`),`{${t.join(",")}}`}function Vt(e){return Ct(Dt(new TextEncoder().encode(Ht(e)))).slice(0,12)}function Rt(e){const t=e.indexOf("("),n=t<0?e:e.slice(0,t),a=t<0?"":e.slice(t),r=[...n.matchAll(/\d+/g)],s=r[r.length-1];if(s&&s.index!==void 0){const i=n.slice(s.index+s[0].length).replace(/^[ \-_.]+|[ \-_.]+$/g,"");if(!(i.length>0&&/^[A-Za-z]+$/.test(i)))return`${n.slice(0,s.index)}${Number(s[0])+1}${n.slice(s.index+s[0].length)}${a}`}return e?`${e} r2`:"r2"}function Ve(e){if(typeof e=="string")return JSON.stringify(e);if(typeof e=="boolean")return e?"true":"false";if(typeof e=="number")return Number.isInteger(e),String(e);throw new Error(`cannot write ${typeof e}`)}function Ee(e,t){return e==="addr"&&typeof t=="number"?`${e} = 0x${t.toString(16).toUpperCase().padStart(2,"0")}`:`${e} = ${Ve(t)}`}const ee=/^\s*\[\[?\s*([A-Za-z0-9_.]+)\s*\]\]?/;function Re(e,t,n){for(let a=0;a<e.length;a++){if(!new RegExp(`^\\s*\\[\\[\\s*${t}\\s*\\]\\]`).test(e[a]))continue;let r=a+1;for(;r<e.length;){const s=ee.exec(e[r]);if(s&&!s[1].startsWith(`${t}.`)||s&&/^\s*\[\[/.test(e[r])&&s[1]===t)break;r++}for(let s=a+1;s<r&&!ee.test(e[s]);s++){const i=/^\s*name\s*=\s*"([^"]*)"/.exec(e[s]);if(i){if(i[1]===n)return{start:a,end:r};break}}a=r-1}return null}function Jt(e){let t=!1;for(let n=0;n<e.length;n++){const a=e[n];if(a==='"'&&e[n-1]!=="\\"&&(t=!t),a==="#"&&!t)return[e.slice(0,n).trimEnd(),e.slice(n)]}return[e.trimEnd(),""]}function Je(e,t,n,a){let r=t;const s=new Map(Object.entries(a));for(let o=t;o<n&&!ee.test(e[o]);o++){const l=/^(\s*)([A-Za-z0-9_]+)(\s*=\s*)(.*)$/.exec(e[o]);if(!l)continue;r=o+1;const c=l[2];if(!s.has(c))continue;const g=s.get(c);s.delete(c);const[,f]=Jt(l[4]);if(g==null){e.splice(o,1),o--,n--,r--;continue}const y=f?"   ":"";e[o]=`${l[1]}${Ee(c,g)}${y}${f}`}const i=[...s.entries()].filter(([,o])=>o!=null).map(([o,l])=>Ee(o,l));return e.splice(r,0,...i),n+i.length}function xe(e,t,n){const a=e.split(/\r?\n/),r=Re(a,"channel",t);if(!r)throw new C(`no channel named ${JSON.stringify(t)}`);return Je(a,r.start+1,r.end,n),a.join(`
`)}function We(e,t,n){const a=e.split(/\r?\n/),r=Re(a,"channel",t);if(!r)throw new C(`no channel named ${JSON.stringify(t)}`);for(let s=r.start+1;s<r.end;s++)if(/^\s*\[\s*channel\.evidence\s*\]/.test(a[s])){let i=s+1;for(;i<r.end&&!ee.test(a[i]);)i++;for(;i>s+1&&a[i-1].trim()==="";)i--;a.splice(s,i-s),r.end-=i-s;break}if(n){const s=Object.entries(n).filter(([,o])=>o).map(([o,l])=>`${o} = ${Ve(String(l))}`);let i=r.end;for(;i>r.start+1&&a[i-1].trim()==="";)i--;a.splice(i,0,"[channel.evidence]",...s)}return a.join(`
`)}function Wt(e,t,n,a){let r=xe(e,t,{status:n});return a&&(r=We(r,t,a)),r}function mn(e,t,n,a=1,r="big"){let s=xe(e,t,{addr:n,length:a,endian:r==="big"?null:r,status:"unverified"});return s=We(s,t,null),qe(s)}function qt(e,t,n){const a=e.split(/\r?\n/);let r=a.findIndex(s=>ee.test(s));return r<0&&(r=a.length),Je(a,0,r,{[t]:n}),a.join(`
`)}function qe(e){let t="unversioned";try{const n=Fe(e);t=String(n.version??"unversioned")}catch{}return qt(e,"version",Rt(t))}function Kt(e,t){const n=[];for(const r of["version","ecu","variant"])e[r]!==t[r]&&n.push({section:"map",name:"",field:r,old:e[r],new:t[r]});for(const r of[...new Set([...Object.keys(e.constants),...Object.keys(t.constants)])].sort())e.constants[r]!==t.constants[r]&&n.push({section:"constant",name:r,field:"value",old:e.constants[r],new:t.constants[r]});const a=(r,s,i)=>{const o=new Map(s.map(c=>[c.name,c])),l=new Map(i.map(c=>[c.name,c]));for(const c of[...new Set([...o.keys(),...l.keys()])].sort()){const g=o.get(c),f=l.get(c);if(!g){n.push({section:r,name:c,field:"",old:void 0,new:"added"});continue}if(!f){n.push({section:r,name:c,field:"",old:"removed",new:void 0});continue}for(const y of[...new Set([...Object.keys(g),...Object.keys(f)])].sort()){const p=g[y],k=f[y];JSON.stringify(p)!==JSON.stringify(k)&&n.push({section:r,name:c,field:y,old:p,new:k})}}};return a("channel",e.channels,t.channels),a("math",e.math,t.math),a("alarm",e.alarms,t.alarms),{changes:n,decodeChanged:e.hash!==t.hash}}function Zt(e){const t=n=>n===void 0?"—":typeof n=="number"?String(n):JSON.stringify(n);return e.field?`${e.section}${e.name?" "+e.name:""}.${e.field}: ${t(e.old)} → ${t(e.new)}`:`${e.section} ${e.name}: ${e.new==="added"?"added":"removed"}`}function pn(e,t){const n=r=>t instanceof Map?t.get(r):t[r],a={};for(const r of e.channels){const s=[];for(let l=r.addr;l<r.addr+r.length;l++){const c=n(l);if(c===void 0||c<0)break;s.push(c)}if(s.length!==r.length){a[r.name]=null;continue}const o=(r.endian==="big"?s:[...s].reverse()).reduce((l,c)=>l*256+c,0);try{const l=Xt(r.formula,e.constants)(o,s);a[r.name]=Number.isFinite(l)?l:null}catch{a[r.name]=null}}return a}const Pe=new Map;function Xt(e,t){const n=e+"\0"+JSON.stringify(t);let a=Pe.get(n);return a||(a=we(e,t),Pe.set(n,a)),a}function Me(e){try{return{map:Ue(e),error:""}}catch(t){return{map:null,error:t.message}}}function Gt(e){const{ch:t}=e,n=D(),[a,r]=$.useState(`0x${ge(t.addr)}`),[s,i]=$.useState(String(t.length)),[o,l]=$.useState(t.endian),[c,g]=$.useState(t.formula),[f,y]=$.useState(t.units),[p,k]=$.useState(t.label),[S,O]=$.useState(t.notes),[I,A]=$.useState(""),[T,E]=$.useState(""),Z=()=>{const b=Number(a.trim().toLowerCase().startsWith("0x")?parseInt(a.trim().slice(2),16):Number(a)),d=Number(s);if(!Number.isInteger(b)||b<0||b>255){E("addr must be 0x00-0xFF");return}if(!Number.isInteger(d)||d<1||d>4){E("length must be 1-4");return}E("");const m={};b!==t.addr&&(m.addr=b),d!==t.length&&(m.length=d),o!==t.endian&&(m.endian=o),c!==t.formula&&(m.formula=c),f!==t.units&&(m.units=f),p!==t.label&&(m.label=p),S!==t.notes&&(m.notes=S||null),("addr"in m||"length"in m||"endian"in m||"formula"in m)&&(m.status="unverified"),e.onApply(m)},h={display:"grid",gridTemplateColumns:"90px 1fr",gap:8,alignItems:"center",marginBottom:6};return u.jsxs("div",{children:[u.jsxs("div",{style:{display:"flex",gap:8,alignItems:"center",marginBottom:8},children:[u.jsx("strong",{style:{...M,fontSize:16},children:t.name}),u.jsx(De,{status:t.status})]}),u.jsxs("label",{style:h,children:["addr",u.jsx(R,{"aria-label":"addr",value:a,onChange:r,monospace:!0})]}),u.jsxs("label",{style:h,children:["length",u.jsx(R,{"aria-label":"length",value:s,onChange:i,monospace:!0})]}),u.jsxs("label",{style:h,children:["endian",u.jsx(Xe,{"aria-label":"endian",value:o,onChange:l,options:[["big","big"],["little","little"]]})]}),u.jsxs("label",{style:h,children:["formula",u.jsx(R,{"aria-label":"formula",value:c,onChange:g,monospace:!0})]}),u.jsxs("label",{style:h,children:["units",u.jsx(R,{"aria-label":"units",value:f,onChange:y})]}),u.jsxs("label",{style:h,children:["label",u.jsx(R,{"aria-label":"label",value:p,onChange:k})]}),u.jsxs("label",{style:h,children:["notes",u.jsx(R,{"aria-label":"notes",value:S,onChange:O})]}),T&&u.jsx(W,{style:{color:n.alarm},children:T}),u.jsx(L,{onClick:Z,children:"Apply to the text"}),u.jsx(W,{children:"A new address, length, byte order or formula resets the channel to unverified."}),t.evidence&&u.jsxs(W,{children:[u.jsx("strong",{children:"Evidence:"})," ",t.evidence.method," ",t.evidence.result," ",t.evidence.date," · ",t.evidence.summary]}),u.jsxs("div",{style:{marginTop:8},children:[u.jsx(R,{"aria-label":"why",value:I,onChange:A,placeholder:"why (goes into the evidence)",style:{width:"100%",marginBottom:6}}),u.jsxs("div",{style:{display:"flex",gap:6,flexWrap:"wrap"},children:[u.jsx(L,{onClick:()=>e.onMark("verified",I),children:"Mark verified"}),u.jsx(L,{onClick:()=>e.onMark("suspect",I),children:"Mark suspect"}),u.jsx(L,{onClick:()=>e.onMark("unverified",I),children:"Mark unverified"})]})]})]})}function gn(e){const t=D(),{text:n,setText:a}=e,r=$.useMemo(()=>Me(n),[n]),s=$.useMemo(()=>Me(e.savedText),[e.savedText]),i=$.useMemo(()=>Ut(n),[n]),[o,l]=$.useState(null),[c,g]=$.useState(""),[f,y]=$.useState("form"),p=r.map,k=p?.channels.find(A=>A.name===o)??null,S=p&&s.map?Kt(s.map,p):null,O=A=>{try{a(A(n)),g("")}catch(T){g(T.message)}},I=async()=>{if(!p){g(`not saved: ${r.error}`);return}if(e.saveMap)try{await e.saveMap(n),e.onSaved?.(n),g(`saved: ${p.version} #${p.hash}`)}catch(A){g(`not saved: ${A.message}`)}else Ye("addressmap.toml",n,"text/plain"),e.onSaved?.(n),g("downloaded addressmap.toml (no map store in this build)")};return u.jsxs("div",{style:{display:"grid",gridTemplateColumns:"minmax(0, 1fr) minmax(0, 1fr)",gap:8,height:"100%",minHeight:0},children:[u.jsx(ie,{title:p?`${p.version} · #${p.hash}`:"Address map (does not load)",right:u.jsxs("div",{style:{display:"flex",gap:6},children:[u.jsx(L,{active:f==="form",onClick:()=>y("form"),children:"Channels"}),u.jsx(L,{active:f==="text",onClick:()=>y("text"),children:"TOML"})]}),children:f==="text"?u.jsx(Ge,{"aria-label":"address map TOML",value:n,onChange:a,rows:30,style:{height:"100%",minHeight:420}}):p?u.jsxs("table",{style:{width:"100%",borderCollapse:"collapse",fontSize:13},children:[u.jsx("thead",{children:u.jsxs("tr",{style:{...M,fontSize:12,textAlign:"left"},children:[u.jsx("th",{children:"NAME"}),u.jsx("th",{children:"ADDR"}),u.jsx("th",{children:"LEN"}),u.jsx("th",{children:"FORMULA"}),u.jsx("th",{children:"UNITS"}),u.jsx("th",{children:"STATUS"})]})}),u.jsx("tbody",{children:p.channels.map(A=>{const T=A.name===o;return u.jsxs("tr",{onClick:()=>l(A.name),"aria-selected":T,style:{cursor:"pointer",background:T?t.sunk:"transparent",borderBottom:`1px solid ${t.line}`,outline:T?`2px solid ${t.fg}`:"none"},children:[u.jsx("td",{style:{...M,fontWeight:700,padding:"5px 4px"},children:A.name}),u.jsxs("td",{style:M,children:["0x",ge(A.addr)]}),u.jsx("td",{style:M,children:A.length}),u.jsx("td",{style:{...M,fontSize:12},children:A.formula}),u.jsx("td",{children:A.units}),u.jsx("td",{children:u.jsx(De,{status:A.status})})]},A.name)})})]}):u.jsx(W,{style:{color:t.alarm},children:r.error})}),u.jsxs("div",{style:{display:"flex",flexDirection:"column",gap:8,minHeight:0},children:[u.jsx(ie,{title:"Edit",style:{flex:"1 1 auto"},children:k?u.jsx(Gt,{ch:k,onApply:A=>O(T=>Object.keys(A).length?xe(T,k.name,A):T),onMark:(A,T)=>O(E=>Wt(E,k.name,A,{date:new Date().toISOString().slice(0,19),method:"manual",result:A==="verified"?"pass":A==="suspect"?"fail":"",summary:T,map_version:p.version,map_hash:p.hash}))},`${k.name}:${n.length}`):u.jsx(W,{children:"Pick a channel, or edit the TOML directly. Marks keep the file's comments; the hash only moves when decoding changes."})}),u.jsxs(ie,{title:"Check, version and save",style:{flex:"0 0 auto",maxHeight:300},children:[u.jsx("div",{style:{...M,fontSize:13,marginBottom:6},"data-testid":"map-validation",children:i.length===0?"OK  the map loads":i.map((A,T)=>u.jsxs("div",{style:{color:A.startsWith("note:")?t.fg2:t.alarm},children:[A.startsWith("note:")?"":"NO  ",A]},T))}),u.jsxs("div",{style:{display:"flex",gap:6,flexWrap:"wrap"},children:[u.jsx(L,{onClick:()=>O(qe),children:"Bump version"}),u.jsx(L,{onClick:()=>a(e.savedText),disabled:n===e.savedText,children:"Revert"}),u.jsx(L,{kind:"primary",onClick:()=>void I(),disabled:!p||n===e.savedText,children:"Save map"})]}),c&&u.jsx(W,{children:c}),S&&S.changes.length>0&&u.jsxs("div",{style:{marginTop:6},children:[u.jsxs("strong",{children:["Changes against the saved map",S.decodeChanged?` (decoding changed: #${s.map.hash} → #${p.hash})`:" (decoding unchanged)",":"]}),u.jsx("ul",{style:{...M,fontSize:12,margin:"4px 0",paddingLeft:18},children:S.changes.slice(0,40).map((A,T)=>u.jsx("li",{children:Zt(A)},T))}),S.decodeChanged&&p.version===s.map.version&&u.jsx(W,{style:{color:t.warn},children:"Decoding changed but the version did not: bump it, the version goes into every log."})]})]})]})]})}export{Ze as A,L as B,ie as C,Qe as D,hn as E,Ce as F,en as G,gn as H,an as I,ct as J,Ut as K,St as L,C as M,W as N,Ot as O,nn as P,fn as Q,me as R,Xe as S,R as T,jt as U,Ge as a,Ct as b,it as c,Ye as d,we as e,pn as f,tn as g,ge as h,sn as i,mn as j,on as k,rn as l,M as m,ln as n,be as o,Fe as p,Wt as q,cn as r,Dt as s,xe as t,D as u,De as v,un as w,dn as x,Be as y,Me as z};
//# sourceMappingURL=MapView-CC99dWjk.js.map
