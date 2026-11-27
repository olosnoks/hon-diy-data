# Parity decisions (round 2): desktop (Python) and web (TypeScript) must match

Decided by the coordinator for the owner, 2026-11-27. Reference: the parity report
screenshots in C:\Users\skons\Documents\hon-diy-data-desktop\docs\screenshots\parity\.

| # | Difference | Decision | Who changes |
|---|---|---|---|
| 1 | Launch-margin bar on F2 | **rpm − idle target**, scale −300…+700 rpm, red below −150, yellow −150…0, readout "+124 rpm"; also show the last launch's low under it ("last launch low 462 rpm") | web |
| 2 | F2 bar titles | sentence case: "RPM vs idle target", "Launch margin" | web |
| 3 | Default units | **US** (°F, mph, mi; MAP stays kPa) in both | web |
| 4 | Status lamp shapes | **square** = link ok, as in the design; update the desktop shape key and the web's Setup shape key to say square | desktop + web keys |
| 5 | ECU error count | show "N err" when errors > 0 in both; ECU silent/lost = alarm-filled chip in both | web |
| 6 | Theme toggle in the top bar | keep it in both (◐ F9) — a tap target matters on the Android head unit | web adds |
| 7 | Bottom strip | add to the web: worst alarm/warning, passing messages, link summary, key hints (hide key hints on touch / compact sizes) | web |
| 8 | Global stall banner | add to the web: on a caught stall, banner under the top bar with "Open in Replay (Enter)" / "Dismiss (Esc)", same wording as desktop | web |
| 9 | Button key labels | write keys as "(F4)" like the design, in both | desktop |
| 10 | Sub-tab rows | **joined strip** like the web in both | desktop |
| 11 | Bytes pages | both: **Scanner · Unknown bytes · Address map · Verify · Rank · Poke test** (Rank and Verify as own pages on desktop; the desktop "Map a channel" wizard lives in Verify and is ported to the web) | desktop + web |
| 12 | Setup pages | both: **Alarms · CYD black box · Units · Cable · Files · Theme · About**, plus **ROM / Tune** on the desktop only (the P28 ROM tools are Python-only; note it in About) | desktop |
| 13 | Setup › Alarms | both: live alarm list + Acknowledge (Esc) + the address-map rule table; drop the old design legend on the desktop | desktop |
| 14 | Events list text for harsh events | desktop wording "0.38 g · 55→21 km/h" in both; migrate old web localStorage notes of kind `harsh` to harsh_accel/harsh_brake by sign | web |
| 15 | Events day navigation | both: prev/next day buttons + a day dropdown | both |
| 16 | Replay | both: Live mode (L), speed keys 1/2/4, prev/next event buttons, × to close a lane | both |
| 17 | GPS map | both: GeoJSON export, tile settings on the GPS screen, zoom buttons | desktop adds |
| 18 | SIMULATOR / DEMO badge | both show a yellow "SIMULATOR" (or "DEMO DATA" for the desktop demo) badge in the top bar | desktop adds |
| 19 | Simulated content | the web simulator gets a built-in **recorded demo drive** with GPS (made-up track, no real coordinates) and a 3rd-gear WOT pull 2,000→6,500 rpm, like the desktop's demo_drive_view, so Power/Replay/GPS show content offline | web |
| 20 | Live tile content | both use the **same channel set per group** from the shared group table; desktop shows the one-line notes like the web; both show min/max small | both |
| 21 | Screenshot tool | fix tools/screenshots.py IndexError in extras() (replay.forensics.loss[0]) | desktop |

Rules: keep both test suites green (desktop: only the known ~35 Windows-only failures; web: tsc 0, vitest all pass). Don't commit, push or switch branches; don't touch ..\hon-diy-data, ~/hondalog, ~/.config/hondalog or serial ports.
