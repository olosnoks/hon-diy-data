/**
 * "Map a channel · guided": the desktop's wizard (hondalog/ui/wired.py
 * MapperSession.wizard / wizard_next / wizard_back / wizard_retry, drawn by
 * screens/rawtools.py WizardCard), on the web's verification scripts.
 *
 *   0 Target      pick the channel to prove (each has a short guided check)
 *   1 Baseline    the script's first phase, on the live sweeps
 *   2 Stimulus    its other phases
 *   3 Candidates  the verdict; when it did not pass, the addresses that fit the check
 *   4 Confirm     the map text with the channel marked verified, or pointed at
 *                 the picked address (unverified); the user saves it
 *
 * No UI here: `view()` describes the card, the actions move the steps.
 */
import { assignAddress, MapError, setChannel, type MapChannel, type ParsedMap } from './mapdoc';
import type { MemoryScanner } from './scanner';
import { FAIL, PASS, PLAUSIBLE, scriptsFor, verdictSummary, VerificationSession, type ChannelVerdict, type Script, type Suggestion } from './verify';

export const WIZARD_STEPS = ['Target', 'Baseline', 'Stimulus', 'Candidates', 'Confirm'] as const;

export interface WizardCandidate extends Suggestion { lo: number; hi: number }

export interface WizardView {
  steps: readonly string[];
  step: number;
  /** "Throttle (tps)" */
  target: string;
  title: string;
  body: string;
  column: string;
  /** step 0: the channels to pick from */
  targets: string[];
  targetIndex: number;
  /** step 3: addresses that fit the check */
  candidates: WizardCandidate[];
  primary: string;
  canBack: boolean;
  canRetry: boolean;
  /** 0..1 while a timed phase runs */
  busy: number | null;
}

const hex = (a: number) => `0x${a.toString(16).toUpperCase().padStart(2, '0')}`;

export class MapWizard {
  step = 0;
  targetIndex = 0;
  message = '';
  session: VerificationSession | null = null;
  channel = '';
  verdict: ChannelVerdict | null = null;
  matches: WizardCandidate[] = [];
  picked: number | null = null;

  constructor(readonly scanner: MemoryScanner, readonly clock: () => number = () => performance.now() / 1000) {}

  targets(map: ParsedMap | null): MapChannel[] {
    return map ? map.channels : [];
  }

  private script(map: ParsedMap | null): { ch: MapChannel | null; script: Script | null } {
    const all = this.targets(map);
    if (!all.length) return { ch: null, script: null };
    const ch = all[Math.min(this.targetIndex, all.length - 1)]!;
    return { ch, script: scriptsFor(ch.name)[0] ?? null };
  }

  available(): boolean {
    return this.scanner.available();
  }

  view(map: ParsedMap | null): WizardView {
    const { ch } = this.script(map);
    const w: WizardView = {
      steps: WIZARD_STEPS, step: this.step, target: this.step > 0 && this.channel ? this.label(map, this.channel) : ch ? `${ch.label} (${ch.name})` : '',
      title: '', body: '', column: 'FOLLOWS STIMULUS', targets: [], targetIndex: this.targetIndex, candidates: [],
      primary: 'Next', canBack: this.step > 0, canRetry: this.step >= 2, busy: null,
    };
    const session = this.session;
    if (this.step === 0) {
      const { script } = this.script(map);
      w.title = 'Pick the channel to prove';
      w.body = 'One channel at a time. Each has a short guided check; a pass is written to the address map with what was measured.'
        + (this.message ? `  ${this.message}` : '');
      w.column = 'CHANNEL';
      w.targets = this.targets(map).map((c) => `${c.label} (${c.name})  now ${hex(c.addr)}${scriptsFor(c.name).length ? '' : '  (no script)'}`);
      w.primary = !map ? 'No map' : !this.available() ? 'Connect first' : script ? 'Next' : 'No script';
    } else if ((this.step === 1 || this.step === 2) && session?.phase) {
      const phase = session.phase;
      const index = session.phaseIndex;
      w.title = `${phase.title}  (${index + 1} of ${session.script.phases.length})`;
      w.body = phase.instruction;
      w.busy = Math.min(1, session.phaseElapsed / Math.max(phase.minS, 0.1));
      const last = index === session.script.phases.length - 1;
      w.primary = !session.canAdvance ? 'Recording…' : last ? 'Finish' : 'Next step';
    } else if (this.step === 3) {
      const status = this.verdict?.status ?? 'skip';
      const summary = this.verdict ? verdictSummary(this.verdict) : '';
      w.title = `${this.channel}: ${status.toUpperCase()}`;
      w.candidates = this.matches;
      w.column = 'FITS THE CHECK';
      if (status === PASS) {
        w.body = summary;
        w.primary = 'Mark verified';
      } else if (status === PLAUSIBLE) {
        w.body = `${summary}. That only shows the value is sane, not that the address is right.`;
        w.primary = 'Accept anyway';
      } else if (this.matches.length) {
        w.body = `${summary} These addresses behaved the way the check expects. Click one to inspect it.`.trim();
        w.primary = `Use ${hex(this.picked ?? this.matches[0]!.addr)}`;
      } else {
        w.body = (summary ? `${summary}. ` : '') + 'Nothing in the sweep fits either. Try again.';
        w.primary = 'Try again';
      }
    } else {
      w.title = 'Done';
      w.body = this.message;
      w.primary = 'Done';
    }
    return w;
  }

  private label(map: ParsedMap | null, name: string): string {
    const c = map?.channels.find((x) => x.name === name);
    return c ? `${c.label} (${c.name})` : name;
  }

  pickTarget(i: number): void {
    if (this.step === 0) this.targetIndex = Math.max(0, i);
  }

  /** Step 3: inspect a candidate (it becomes the one "Use" writes). */
  pickCandidate(addr: number): void {
    if (this.step === 3 && this.matches.some((m) => m.addr === addr)) this.picked = addr;
  }

  /**
   * The primary button. Returns the edited map text when this step changed
   * the map (step 3), else null.
   */
  next(map: ParsedMap | null, mapText: string): string | null {
    if (this.step === 0) {
      const { ch, script } = this.script(map);
      if (!map || !this.available() || !ch || !script) return null;
      this.channel = ch.name;
      this.message = '';
      this.session = new VerificationSession(script, map, this.clock);
      this.session.start();
      if (!this.scanner.sweeping) this.scanner.setSweeping(true);
      this.step = 1;
    } else if (this.step === 1 || this.step === 2) {
      const s = this.session;
      if (!s || !s.canAdvance) return null;
      if (s.nextPhase() === null) this.finish();
      else this.step = 2;
    } else if (this.step === 3) {
      return this.confirm(map, mapText);
    } else {
      this.reset();
    }
    return null;
  }

  private finish(): void {
    const s = this.session!;
    const report = s.finish();
    this.verdict = report.verdicts[this.channel] ?? null;
    this.matches = [];
    this.picked = null;
    if (!this.verdict || (this.verdict.status !== PASS && this.verdict.status !== PLAUSIBLE)) {
      const seen = new Set<number>();
      for (const m of s.suggestMatches(this.channel)) {
        if (seen.has(m.addr)) continue;
        seen.add(m.addr);
        this.matches.push({ ...m, lo: this.scanner.minv[m.addr] ?? 0, hi: this.scanner.maxv[m.addr] ?? 0 });
      }
    }
    this.step = 3;
  }

  private confirm(map: ParsedMap | null, mapText: string): string | null {
    const s = this.session;
    const status = this.verdict?.status ?? 'skip';
    if (!s || !map) { this.reset(); return null; }
    let text: string | null = null;
    try {
      if (status === PASS || status === PLAUSIBLE) {
        text = s.confirmEdit(this.channel, mapText, '', status === PLAUSIBLE);
        this.message = `${this.channel} is marked verified in the map, with the measurements as evidence. Save it on the Address map tab.`;
      } else if (this.matches.length) {
        const m = this.matches.find((x) => x.addr === this.picked) ?? this.matches[0]!;
        this.picked = m.addr;
        text = assignAddress(mapText, this.channel, m.fields.addr, m.fields.length ?? 1, m.fields.endian ?? 'big');
        if (m.fields.formula) text = setChannel(text, this.channel, { formula: m.fields.formula });
        this.message = `${this.channel} now reads ${hex(m.addr)} in the map (still unverified)${status === FAIL ? ', moved after a failed check' : ''}. `
          + 'Save it on the Address map tab, reconnect, then run the check again.';
      } else {
        this.retry(map);
        return null;
      }
    } catch (e) {
      this.message = `Not written: ${e instanceof MapError || e instanceof Error ? e.message : String(e)}`;
      text = null;
    }
    this.step = 4;
    return text;
  }

  back(): void {
    this.reset();
  }

  /** "Not it, try again": the same channel's check from the start. */
  retry(map: ParsedMap | null): void {
    if (!this.available()) return;
    const keep = this.channel;
    this.reset();
    const i = this.targets(map).findIndex((c) => c.name === keep);
    if (i >= 0) this.targetIndex = i;
    this.next(map, '');
  }

  /** The link went away during a check: stop it (desktop _wizard_tick). */
  tick(): boolean {
    if ((this.step === 1 || this.step === 2) && this.session && !this.available()) {
      this.reset();
      this.message = 'The link went away; the check was stopped.';
      return true;
    }
    return false;
  }

  private reset(): void {
    this.step = 0;
    this.session = null;
    this.verdict = null;
    this.matches = [];
    this.picked = null;
  }

  /** A sweep from the scanner (the component forwards them). */
  addSweep(mem: ArrayLike<number>): void {
    this.session?.addSweep(mem);
  }
}
