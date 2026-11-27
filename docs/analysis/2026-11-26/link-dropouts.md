# K-line dropouts and false "stall" captures (2026-11-26)

**Data.** I made a read-only copy of `hondalog.db` at 21:47 and put it in `work-link/`. The copy itself is git-ignored like the other copies. These are the drives:

- `drive_2026_11_26_2045` (id 33): the main one. 1715 s at idle, 55,727 requests.
- `drive_2026_11_26_2114` (id 34): 1292 s, used as a check.
- The 2026-11-25 drives that have link-loss captures.
- The other agent's sweep (`work-sweep/`, 21:49:30–21:53:02) for the comparison in §4.

**Scripts** are in `work-link/`:

| script | what it does | output |
|---|---|---|
| `drops.py`, `gaps.py`, `classes.py` | characterise the failures | stdout |
| `replay_rules.py` | replays the old and new stall rules over the recorded rpm and link flags | `replay_rules_results.txt` |
| `sim_drops.py` | replays drive 2045's fault rates on the simulator against the old and new poller | `sim_drops_results.txt` |

**Code** is on branch `link-dropout-fix` in the worktree `..\hon-diy-data-linkfix`. Nothing is merged or pushed.

## 0. Bottom line

1. **The false stall is a rule problem.**
   - `link_loss` fired whenever the ECU had been silent for **1.0 s**. Its default is `link_down_s = 1.0` in `triggers/presets.py`.
   - The poller declared the link down after **one** block read went unanswered twice, which takes ~0.35 s. Its recovery path then often added 0.2–1 s of its own: the late-reply watch, then a wake frame that holds the line silent for 0.3 s.
   - So any ECU silence longer than about 0.6 s could become a "stall".
   - Drive 2045 had 80 link-down episodes. The longest was 1.30 s of `link == 0` (1.51 s with no reads), at 21:10:53. That one fired `stall_20261126_211054`. rpm was 726 before the silence and 729 after it.
2. **What a drop is on the wire:**
   - It is always a **no-reply timeout on two attempts in a row**: the request plus its retry 0.2 s later. It is never a checksum failure, and almost never a short reply.
   - The block that fails is random. Each block fails in proportion to how often it is read, so long 16-byte or 15-byte reads are not the cause.
   - The timing is Poisson-like (cv 0.8), about 2.8/min in drive 33 and 2.4/min in drive 34. Nothing comes before it: no latency creep, and no extra retries in the 5 s before.
   - Recovery takes 0.36–0.42 s in 80 % of cases, and 0.9–1.5 s when the first probe also fails and a wake frame is sent.
3. **The ECU can do better, as the sweep shows.**
   - The read-only sweep ran through the same `Link` code, cable and car. It had **no** drops in 3,409 requests. At the app's rate about 5 were expected; the chance of seeing none is about 0.7 %.
   - The sweep was not error-free, though. Its own counters show 49 retries (34 damaged-echo frames, 15 no-reply timeouts). Every one was rescued by the single retry.
   - Its errors cluster on the first request after its 0.42 s rest: 24 of the 210 first-of-sweep requests failed (11 %), against 0.8 % for the rest.
   - In steady streaming, the app fails 2.3 % of requests and the sweep 0.8 %. In the app, a retry after a no-reply also fails 34 % of the time; in the sweep it never did (0 of 15).
   - The difference is in **what the app sends, or how it sends it**. It is not the cable or the ECU. §4 lists the differences and the experiments that would separate them.
4. **Changes (branch `link-dropout-fix`):**
   - (a) `link_loss` now needs **3 s** of silence, and its window still starts 30 s before the silence.
   - (a) When the ECU answers again, the first rpm reading is checked. If it is above 400 and close to the rpm before the loss (below 1.25 × that + 100), the engine never stopped. The capture is then filed as **kind `dropout`** ("LINK DROPOUT (engine running)") instead of `stall`, even after it has been saved, and the drive's stall count goes down.
   - (a) Replayed over every recorded link loss, this removes all three artefacts (11-26 21:10:54, 11-25 19:35:01, 11-25 22:18:19). The six real engine-off events stay stalls, and so does the 18:17 key-off that came back in an after-start flare.
   - (b) The poller rides out short silences. The link is only declared down after 3 failed block reads, or 2 no-reply block reads with 0.6 s of silence. There is no wake frame in the first second of an outage. In simulation with drive 2045's fault rates, link-down episodes fall from 107–117 to 12–17 per 28 minutes and wake frames from 29–38 to 1.
   - (b) Windows serial reads no longer reprogram the port for every received byte. This is a suspected cause of the 1.7 % damaged echoes; it needs a check on the car.
   - (b) Every failed exchange is now logged as a `link_error` event, so the next drive can be characterised exactly.

## 1. Characterisation (drive 2045, confirmed in 2114)

### Error mix

The link counters (in the drive header, `hondalog-log info`) do not change much from drive to drive. The same pattern holds in every 2026-11-25 drive: 2–3 % failed exchanges, `usb_latency_ms` 16, `discarded_bytes` about 0.

| counter | drive 33 | per request | drive 34 |
|---|---|---|---|
| requests | 55,727 | | 42,886 |
| failed exchanges | 1,282 | 2.3 % | 858 (2.0 %) |
| no reply (`timeouts` − `short_replies`) | 335 | 0.60 % | 210 |
| short replies | 8 | 0.01 % | 4 |
| checksum failures | **0** | 0 | **0** |
| bad frames (bytes, but neither our echo nor a reply) | 938 | 1.7 % | 644 |
| echo mismatches | 948 | | 648 |
| bytes drained after a failure (`discarded_bytes`) | **0** | | **0** |
| FTDI latency timer | 16 ms (it could not be lowered on Windows) | | 16 ms |

The counters lead to three conclusions:

- **Bad frames are our own request coming back damaged, with the ECU then silent.** `bad_frames` ≈ `echo_mismatches`. When an echo is damaged but the reply is intact, the frame is still accepted; that happened 10 times.
- **The ECU's replies are never damaged.** There were zero checksum failures in ~100,000 replies, each 6–19 bytes long, while ~1.7 % of our 5-byte requests came back damaged. Electrical noise on the line would hit both directions. The damage therefore happens while **we** transmit.
- **A no-reply is a reply that never comes, not a late one.** After every miss, the line is watched for 0.2 s, and not one late byte was ever drained.

### Failure classes from the timing between good reads

Per-read timestamps make it possible to classify each gap between good reads. Normal exchanges take 20–37 ms. The FTDI 16 ms latency timer makes up most of the measured 13 ms "overhead".

| gap between good reads | what it is (from the Link code path) | n (drive 33) | per minute |
|---|---|---|---|
| 110–180 ms | damaged request, resync, retry OK | 533 | 18.6 |
| 210–300 ms | no reply, 0.2 s watch, retry OK (or two damaged requests in a row) | 361 | 12.6 |
| ≥ 330 ms | **drop**: no reply, retry also no reply, link declared down, probe | 108 (80 with a `link 0` flag) | 3.8 |

### The drops in detail

- **Duration.** Read silence (last good read → next good read) has median 0.405 s and p90 0.90 s. The histogram: 65 × 0.4 s, 3 × 0.5, 5 × 0.9, 4 × 1.0, 1 each at 1.2, 1.4 and 1.5 s. `link == 0` lasted a median 0.195 s and at most 1.30 s.
- **How a 0.4 s drop happens:**
  1. The request at t₀ gets no reply, and the attempt ends at t₀ + 80 ms.
  2. The line is watched until t₀ + 205 ms, then the retry goes out. It also gets no reply, so the link is declared down at t₀ + 360 ms and every value is blanked.
  3. The late-reply watch runs again until t₀ + 410 ms. The probe (00+3) answers, all 7 blocks are read again, and `link = 1` returns at about t₀ + 600 ms.
  4. So the ECU ignored requests at t₀ and t₀ + 0.2 s and answered at t₀ + 0.41 s.
- **The long drops (0.9–1.5 s)** are the same, plus one or two failed probes. From the second probe on, each one also sends the 11-byte wake frame and waits `init_delay_s` = 0.3 s.
- **Which block fails.** The block read just before a drop is 00+3, 12+4 or 20+7 about 27–32 % of the time each, which matches their 29 % share of reads. Slow blocks come before 15 % of drops and make up 12 % of reads. The block length makes no difference.
- **Timing.** The intervals between drops have mean 15.9 s and cv 0.79, close to random arrivals. There were no extra retries in the 5 s before a drop (0.05 against 0.06 expected). The median exchange time over the 60 reads before a drop was 28.0 ms, against 28.1 ms for the whole drive. Damaged-request retries did not cluster near drops (179 within 3 s against 201 expected).
- **Recorded latency.** `latency_ms` in `ecu_reads` is computed (bytes × byte time + ½ USB latency), not measured, so it is constant per block. The interval columns above are the real latency.
- **No data loss beyond the gap.** The rpm reads had no gap over 2 s anywhere. rpm read 726 before the 21:10:53 drop and 729 after it.

## 2. Why a 1.5 s drop became a stall

- `stall_rules()` builds `link_loss` as `when="link == 0"`, `hold_s=link_down_s=1.0`, armed by `rpm > 400`. That reproduces 0.1.0's stall catcher.
- The recorder feeds `link == 0` at every failed probe (`Poller._emit_down` → `LiveSession._on_sample` → `Recorder.feed_link`). The rule fires at the first failed probe that comes ≥ 1.0 s after the first one.
- Two things make silences longer than they need to be:
  - **The poller turned one unanswered block into "ECU gone".** It raised `_fails` by 3 for a `NoReply`, which equals `_FAILS_TO_NO_REPLY`, so the link went down at once. That blanked every value, restarted the scheduler and started probing.
  - **The probing itself kept the line quiet.** There is a 0.2 s late-reply watch before every probe. From the 2nd probe on there is the wake frame plus a 0.3 s wait.
- **Real engine deaths look different.** In the six 2026-11-25 engine-off captures, the ECU stayed silent until the next key-on, minutes later, and came back at rpm 0, cranking, or in an after-start flare. A silence of 1–3 s with the same idle rpm on both sides is a dropout.

## 3. The changes (worktree `..\hon-diy-data-linkfix`, branch `link-dropout-fix`)

### (a) The stall rule

`hondalog/triggers/engine.py`, `presets.py`, `logger.py`, `data/store.py`

- **`link_down_s` 1.0 → 3.0 s.**
  - All 11-26 drops (≤ 1.5 s) and the 11-25 19:35 one (2.5 s) no longer fire.
  - A power loss keeps the ECU silent until the restart, so 3 s delays nothing that matters.
  - `pre_s` grows by the hold time, so the window still covers 30 s before the silence.
- **Review on reconnect.** It uses new, generic rule fields: `cancel_when`, `review_s`, `cancel_kind`, `cancel_label` and `cancel_message`.
  - The condition is checked **once**: at the first reading after the firing in which every channel it names has been read again.
  - `before_<name>` holds the last value of a channel before the silence.
  - The link-loss rule uses `rpm > 400 and rpm < before_rpm * 1.25 + 100`, with `review_s` 60 s.
  - If the condition is true, the firing is revoked and an event "LINK DROPOUT (engine running)" is written. The capture is saved, or re-filed if it is already saved, as kind/reason `dropout` with `meta.cancelled = true`. `drives.stall_count` is corrected and the capture leaves the `stalls` view, but its data is kept.
  - If the condition is false (the ECU answers at rpm 0 at key-on, or the engine already restarted and is in a flare), or there is no verdict within 60 s, it stays a stall.
  - The capture window is not held open while waiting. A verdict that comes after the save goes through `DriveWriter.relabel_capture`.
- **Replay over the real data** (`work-link/replay_rules_results.txt`):

| capture | what it really was | old rule | new rule |
|---|---|---|---|
| 11-25 18:17:01 | user key-off (OBD2 mode, back at 1,446 rpm) | stall | stall (fires 3 s later) |
| 11-25 18:55, 19:29, 20:13, 21:16, 22:34, 22:44 | engine off / restart | stall | stall |
| 11-25 19:35:01 | 2.5 s dropout | stall | **not fired** |
| 11-25 22:18:19 | 18 s dropout, engine running | stall | fired, then **re-filed as dropout** at +14 s |
| 11-26 21:10:54 | 1.5 s dropout | stall | **not fired** |

- **Limits of the review:**
  - An ECU reset with the engine still spinning (a sub-second power blip) would also come back above 400 rpm, so it would be filed as a dropout. It is kept as a capture, not dropped, so it can still be found.
  - A dropout during a hard pull (rpm up by more than 25 % + 100 in 3 s) stays a stall. That errs on the safe side.
  - The handheld's own stall logic (`MAP_STALL.link_down_s`, default 1.0 in `handheld/mapfile.py`) is **not** changed. It only matters when the CYD polls the ECU itself.
  - A stricter key-on check using 0x27 (5 at key-on, 10 while cranking) or the starter bit was not used. Both live in the 1 Hz slow block, so they arrive after the first rpm reading.

### (b) The link

`hondalog/poller.py`, `transport.py`, `live.py`

- **The poller no longer drops the link for one unanswered block.**
  - Every failed block read now counts 1. The link goes down after 3 failed block reads, or after 2 no-reply block reads with no good read for 0.6 s. A real power loss is still noticed in about 0.9 s instead of 0.36 s; the stall rule waits 3 s anyway.
  - Meanwhile the scheduler moves on to the next block, which by the data answers in most cases. Values are not blanked, the scheduler is not restarted, and there is no `link 0`.
  - Wake frames are held back for the first 1.0 s of an outage that began with a working link. Outages after connect, port loss or OBD2 still wake on the next probe, as before.
- **Simulation** (`work-link/sim_drops.py`, same injected faults, 3 seeds × 1715 s, FTDI 16 ms):

| | old poller | new poller |
|---|---|---|
| link-down episodes | 107 / 113 / 117 | 17 / 17 / 12 |
| wake frames | 29 / 35 / 38 | 1 / 1 / 1 |
| rpm gaps > 1 s | 26 / 31 / 33 | 13 / 13 / 9 |
| time in rpm gaps > 0.15 s | 361–374 s | 364–389 s |

  The injected fault rates are 1.7 % damaged requests, 0.4 % single misses, and 0.15 % starts of 0.2–0.45 s deaf stretches (15 % of those 0.6–1.2 s). The total time lost stays the same, because the ECU's deaf time is injected. What goes away is the link-down machinery on top of it: blanked values, `link 0` flags and wake frames.
- **Windows serial reads.** On Windows, pyserial has no `fileno`, so `read_some` called `Serial.read(1)` after setting `Serial.timeout` to the time left. On win32, every timeout change runs `_reconfigure_port`: `SetCommTimeouts` plus a full `SetCommState` (baud, framing, DTR/RTS), which the FTDI driver turns into USB control requests.
  - This happened for **every received byte**, starting right after `write()`, while our 5 request bytes were still being shifted out.
  - A baud-generator reload in the middle of a byte would damage exactly what the counters show: our own TX (damaged echo, ECU silent) but not the ECU's replies.
  - Now the timeout is set once to a 2 ms poll slice. Waiting bytes are read with `in_waiting`, and the timeout is only reprogrammed when its whole-millisecond value actually changes.
  - **Unproven:** the sweep shows damaged echoes too (1.0 % against 1.7 %), and I could not test on the cable (no serial port access). The next drive's `bad_frames / requests` will tell. If it drops well below 1 %, this was it.
- **`link_error` events.** `Link.on_error` reports every failed attempt, retries included. The poller forwards them while the link is up, and the recorder stores them as `events.kind = 'link_error'`, for example "NoReply 12+4 #2" with `{addr, length, kind, attempt, message}`. They are only logged while a drive is open, never from the probing of an outage, and they never start a drive. The next drive will show directly which attempt failed how, and what bytes came back.
- **The late-reply watch was tried shorter and reverted.** I tried shortening it to the current response timeout. That broke `test_fault_soup_never_returns_wrong_data`: an ECU that answers 150 ms late then has its reply taken as the answer to the next request of the same length. On this car no reply has ever been late (`discarded_bytes` = 0), so `reply_timeout_s = 0.1` under `[protocol]` in the user map is a reasonable **experiment** (see §4). It is not a default change.

### Simulator and bench

`hondalog/sim.py`, `tools/bench_link.py`

- New `Faults` kinds:
  - `garble`: our request is damaged on the wire; bad echo, no reply.
  - `deaf` / `deaf_s`: the ECU ignores everything for a while.
- `Faults.p2p()` holds the fitted car rates, used by `--faults p2p` and the port `sim:p2p-car`.
- `tools/bench_link.py` crashed on the shipped 10400-baud map at HEAD: the simulated P2P wanted the wake frame and the bench never sent one. It now runs, and it reports link drops. Example: `python tools/bench_link.py --faults p2p --usb-latency-ms 16 -s 600` gave 0 drops, worst rpm gap 0.85 s.

## 4. Sweep vs app: what the app does differently

| | sweep (`work-sweep/sweep.py`) | app (poller) |
|---|---|---|
| code path | same `Link` (same timeouts, retries, Windows read path) | same |
| requests | 16 × `20 05 xx 10`, 16-byte rows | 7 blocks of 3–15 bytes (00+3, 12+4, 20+7 at ~9 Hz each; 03+15, 16+10, 27+14, 35+8 at 1 Hz) |
| pacing | bursts: 16 requests, 32.7 ms apart, then a **0.42 s rest** every second (58 % of the time on the line) | continuous, 23–29 ms per exchange, ~33 requests/s, 100 % on the line |
| process | one script, nothing else running | GUI, recorder/DB thread, math, CYD relay on COM23 at 921,600 baud, black box: the poller shares the GIL |
| after a failure | the `Link` retry only (it never needed the 4-in-a-row re-wake) | `Link` retry, then link down, probes, wake |
| failed requests | 1.44 % (34 damaged echo, 15 no-reply), all rescued by the retry | 2.3 % |
| in steady streaming | rows 1–15: 0.79 % | 2.3 % |
| first request after a quiet line | **11.4 %** (24 / 210 after the 0.42 s rest) | (only after a failure: the retry after the 0.2 s watch) |
| retry after a no-reply fails | 0 / 15 | **~34 %** (80 drops against ~158 rescued) |
| drops | 0 (≈ 5 expected at the app's rate) | 2.8/min |

What this says:

1. **The steady error rate is about 3× lower in the sweep.**
   - The candidates are the shorter request period (23–29 ms against 33 ms), the many very short exchanges (00+3 takes 11 bytes on the wire), and thread/GIL contention in the app.
   - The lower duty cycle is a candidate too, though the sweep was at 100 % within each burst.
   - These cannot be told apart from the logs.
2. **A quiet line seems to make the next request fail more often.** That shows in the sweep's 11 % first-of-sweep failures.
   - In the app the only quiet stretches are the ones the `Link` makes itself: the 0.2 s late-reply watch before a retry, and the wake wait.
   - That fits the app's high retry-failure rate. It fits the drops too: the retry is exactly a "first request after a quiet line".
   - This is a hypothesis.
3. **Experiments that would separate these**, each 5 minutes at idle, comparing the link counters and the new `link_error` events:
   1. The sweep script run **continuously** (period 0) with the app's 7-block plan, alone. Same pattern, no app, so this tests pattern against process.
   2. The app with `inter_request_delay_s = 0.010` under `[protocol]` in the user map. A longer gap means the ECU's receiver is less often caught right after its own reply.
   3. The app with `reply_timeout_s = 0.1`. This halves the quiet stretch before a retry; no late reply has ever been seen.
   4. The app with whole 16-byte rows (00+16, 10+16, 20+16, 30+13 at one rate): ~7.5 Hz for everything, against ~9 Hz fast / 1 Hz slow now. This may be worth it if it removes most of the errors, which cost ~10 % of the wire time today.
   5. The FTDI latency timer at 1–2 ms, set in Device Manager → port → Advanced. The header shows 16 ms, and it makes up most of the measured per-request overhead. It does not obviously cause errors, but it shifts every timing above.

**Address-map note, not changed.** The app's ECU-id read at 0x76, length 10, includes the **live idle-target word at 0x76–0x77**. The constant ECU id starts at **0x78** (8 bytes). The id string the app stores therefore changes with idle target; `ecu_id` should be read from 0x78+8.

## 5. Tests

These were run in the worktree with `..\hon-diy-data\.venv\Scripts\python.exe -m pytest -q`:

| | passed | failed | errors | skipped |
|---|---|---|---|---|
| unmodified (HEAD `ec2f32a`) | 1159 | 35 | 1 | 98 |
| `link-dropout-fix` (`c02b58e`) | 1172 (+13 new) | 36 | 0 | 98 |

- The same 35 tests fail on both sides: pty, minipro, Linux-path, firstcontact/FTDI and database-location tests that fail on this Windows machine at HEAD. None of them are link or trigger tests.
- The extra failure on the branch is a timing flake, and so is HEAD's error. Each full run had one, and each passes when run alone:
  - HEAD's error: `test_integration.py::test_histogram_on_rom_axes_and_mlg_export`, in its `stall_run` fixture.
  - On the branch: once `test_integration.py::test_live_path_records_every_read…` (the same fixture), once `test_ui_map.py::test_an_hour_pans_without_redrawing_the_track`.
- **New tests:**
  - `test_link_robust.py`: a 0.4 s deaf ECU keeps the link up with no wake; the P2P fault preset; `on_error` for every attempt; Windows `read_some` sets the timeout once.
  - `test_triggers.py`: short dropout, revoked dropout, rpm-0 key-on stands, flare stands, no verdict stands.
  - `test_recorder.py`: dropout filed as dropout, 18 s dropout re-filed after save, power loss stays a stall, `link_error` events.
- **Tests changed on purpose:**
  - key-off noticed within 1.1 s, not 0.4 s;
  - `test_scan_a_finished_log`: a 2 s gap no longer fires;
  - `test_black_box_stream_shares_the_capture` passes `link_down_s=1.0` explicitly.
