  hlSetup() {
    if (this._hl) return;
    this._hl = true;
    this.T0 = Date.now() / 1000;
    this.simT = 0;
    this.marks = 2;
    this.markT = -14;
    const V = (id, label, u, d, min, max, o) => Object.assign({ id, label, u, d, min, max, k: 'v', src: 'ECU', ver: false }, o || {});
    const F = (id, label, o) => Object.assign({ id, label, u: '', d: 0, min: 0, max: 1, k: 'f', src: 'ECU', ver: false }, o || {});
    this.CH = [
      V('rpm', 'RPM', 'rpm', 0, 0, 8000, { whi: 6500, hi: 6900, ver: true }),
      V('vss', 'Speed', 'km/h', 0, 0, 200),
      V('map', 'MAP', 'kPa', 0, 0, 110),
      V('tps', 'Throttle', '%', 0, 0, 100, { ver: true }),
      V('inj', 'Injector', 'ms', 2, 0, 16),
      V('ign', 'Ign BTDC', '°', 1, -10, 50),
      V('o2', 'O2', 'V', 2, 0, 1.2),
      V('stft', 'Short trim', '%', 1, -25, 25, { wlo: -15, whi: 15, sg: 1 }),
      V('ltft', 'Long trim', '%', 1, -25, 25, { wlo: -12, whi: 12, sg: 1 }),
      V('ect', 'Coolant', '°C', 0, -20, 130, { whi: 100, hi: 108 }),
      V('iat', 'Intake air', '°C', 0, -20, 80, { whi: 45 }),
      V('baro', 'Baro', 'kPa', 0, 60, 110),
      V('batt', 'Battery', 'V', 1, 8, 16, { wlo: 12.8, lo: 11.5, hi: 15.2, ver: true }),
      V('altfr', 'Alt load', '%', 0, 0, 100),
      V('eld', 'Elec load', 'A', 0, 0, 100),
      V('iacv', 'Idle valve', '%', 0, 0, 100),
      V('knock', 'Knock', 'V', 2, 0, 5, { whi: 2 }),
      V('ignlim', 'Ign limit', '°', 1, 0, 20),
      F('cl', 'Closed loop'), F('vtec', 'VTEC'), F('ac', 'A/C'), F('fan', 'Rad fan'), F('fp', 'Fuel pump'),
      F('mil', 'MIL', { hi: 0.5 }),
      V('bbatt', 'Battery', 'V', 2, 8, 16, { lo: 11, src: 'BB', ver: true }),
      V('ecub', 'ECU +B', 'V', 2, 8, 16, { lo: 11, src: 'BB', ver: true }),
      V('fpv', 'FP feed', 'V', 2, 0, 16, { lo: 11, src: 'BB', ver: true }),
      V('ig1', 'IG1', 'V', 2, 8, 16, { lo: 11, src: 'BB', ver: true }),
      V('tach', 'Tach', 'Hz', 0, 0, 250, { src: 'BB', ver: true }),
      V('igt', 'Ign trigger', 'Hz', 0, 0, 250, { src: 'BB', ver: true }),
      V('injp', 'Inj pulse', 'ms', 2, 0, 16, { src: 'BB', ver: true }),
      F('mark', 'Marker', { src: 'BB', ver: true })
    ];
    this.hist = {};
    this.stat = {};
    for (const c of this.CH) this.hist[c.id] = [];
    for (let k = 120; k >= 1; k--) {
      const s = this.hlSim(-k * 0.5);
      for (const c of this.CH) this.hlPush(c, s[c.id]);
    }
    this.cur = this.hlSim(0);
    this.toggleTheme = () => this.setState({ themeO: this.hlTheme() === 'night' ? 'day' : 'night' });
    this.doMark = () => { this.marks++; this.markT = this.simT; this.setState({ markN: this.marks }); };
    this.setCv = (el) => { if (el) this._cv = el; };
    this.pd = (e) => {
      const g = this.g; if (!g) return;
      g.user = true;
      g.drag = { x: e.clientX, y: e.clientY, az: g.az, el: g.el };
      if (e.currentTarget && e.currentTarget.setPointerCapture) { try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) {} }
    };
    this.pm = (e) => {
      const g = this.g; if (!g || !g.drag) return;
      g.az = g.drag.az - (e.clientX - g.drag.x) * 0.008;
      g.el = Math.max(0.08, Math.min(1.5, g.drag.el + (e.clientY - g.drag.y) * 0.006));
    };
    this.pu = () => { if (this.g) this.g.drag = null; };
  }

  hlSim(t) {
    const n = (k, f) => 0.6 * Math.sin(t * f + k * 12.9898) + 0.4 * Math.sin(t * f * 2.71 + k * 4.1);
    const u = 0.5 + 0.5 * Math.sin(t * 0.21);
    const rpm = 1400 + 2600 * u + n(1, 3.1) * 18;
    const inj = 2.2 + 4.6 * u + n(4, 2.2) * 0.04;
    const ph = ((t % 34) + 34) % 34;
    const dip = ph > 29 && ph < 31.5;
    return {
      rpm, vss: 18 + 62 * u + n(2, 0.9) * 0.4, map: 30 + 55 * Math.pow(u, 1.2) + n(3, 2.3) * 0.8,
      tps: 8 + 30 * u + n(5, 1.7) * 0.5, inj, ign: 34 - 16 * u + n(6, 2.9) * 0.4,
      o2: 0.46 + 0.36 * Math.tanh(3 * Math.sin(t * 5.2)), stft: n(7, 1.3) * 4.2, ltft: 2.3 + n(8, 0.05) * 0.2,
      ect: 88 + n(9, 0.07) * 1.2, iat: 46.4 + n(10, 0.05) * 0.5, baro: 101, batt: 14.1 + n(11, 1.9) * 0.06,
      altfr: 42 + 20 * u + n(12, 1.1) * 2, eld: 24 + n(13, 0.8) * 3, iacv: 31 + 6 * (1 - u) + n(14, 2.4) * 0.6,
      knock: 0.08 + Math.abs(n(15, 4.4)) * 0.05, ignlim: 0,
      cl: 1, vtec: rpm > 5500 ? 1 : 0, ac: ((t % 46) + 46) % 46 > 30 ? 1 : 0, fan: ((t % 58) + 58) % 58 > 41 ? 1 : 0, fp: 1, mil: 0,
      bbatt: 14.05 + n(16, 1.9) * 0.05, ecub: 13.92 + n(17, 2.1) * 0.04, fpv: dip ? 9.8 + n(18, 9) * 0.3 : 13.62 + n(18, 2.6) * 0.05,
      ig1: 13.84 + n(19, 2.2) * 0.04, tach: rpm / 30, igt: rpm / 30, injp: inj + 0.06,
      mark: this.markT != null && t - this.markT >= 0 && t - this.markT < 1 ? 1 : 0
    };
  }

  hlPush(c, v) {
    const h = this.hist[c.id];
    h.push(v);
    if (h.length > 120) h.shift();
    const s = this.stat[c.id] || (this.stat[c.id] = { min: v, max: v, chg: 0, last: v });
    if (v < s.min) s.min = v;
    if (v > s.max) s.max = v;
    if (c.k === 'f' && v !== s.last) s.chg++;
    s.last = v;
  }

  hlTick() {
    this.simT = Date.now() / 1000 - this.T0;
    this.cur = this.hlSim(this.simT);
    this._n = (this._n || 0) + 1;
    if (this._n % 2 === 0) for (const c of this.CH) this.hlPush(c, this.cur[c.id]);
    if (this.onTick) this.onTick();
    this.setState({ tick: this._n });
  }

  hlStart() {
    this.hlSetup();
    this._iv = setInterval(() => this.hlTick(), 250);
  }

  hlStop() {
    clearInterval(this._iv);
    clearTimeout(this._retry);
    if (this._raf) cancelAnimationFrame(this._raf);
    if (this.g && this.g.r) { try { this.g.r.dispose(); } catch (err) {} }
  }

  hlTheme() {
    return (this.state && this.state.themeO) || this.props.theme || '@@THEME@@';
  }

  hlFmt(c, v) {
    if (v == null || v !== v) return '---';
    const e = 0.5 * Math.pow(10, -c.d);
    if (Math.abs(v) < e) v = 0;
    const s = Math.abs(v).toFixed(c.d);
    return (v < 0 ? '−' : (c.sg && v > 0 ? '+' : '')) + s;
  }

  hlState(c, v) {
    if ((c.lo != null && v < c.lo) || (c.hi != null && v > c.hi)) return 'alarm';
    if ((c.wlo != null && v < c.wlo) || (c.whi != null && v > c.whi)) return 'warn';
    return '';
  }

  hlTile(c) {
    const v = this.cur[c.id], s = this.stat[c.id], st = this.hlState(c, v), h = this.hist[c.id];
    let lo = Infinity, hi = -Infinity;
    for (const x of h) { if (x < lo) lo = x; if (x > hi) hi = x; }
    const span = Math.max(hi - lo, (c.max - c.min) * 0.04) || 1, mid = (hi + lo) / 2;
    let pts = '';
    for (let i = 0; i < h.length; i++) pts += i + ',' + (15 - (h[i] - mid) / span * 22).toFixed(1) + ' ';
    const pc = (x) => Math.max(0, Math.min(100, (x - c.min) / (c.max - c.min) * 100));
    const flag = c.k === 'f';
    return {
      id: c.id, label: c.label, units: c.u, src: c.src,
      val: flag ? (v ? 'ON' : 'OFF') : this.hlFmt(c, v),
      cls: 'tile ' + st + (flag ? (v ? ' fon' : ' foff') : ''),
      isWarn: st === 'warn', isAlarm: st === 'alarm', unv: !c.ver, ver: !!c.ver, isVal: !flag, isFlag: flag,
      pct: pc(v).toFixed(1), minP: pc(s.min).toFixed(1), maxP: pc(s.max).toFixed(1),
      min: this.hlFmt(c, s.min), max: this.hlFmt(c, s.max), pts, chg: s.chg, cap: ''
    };
  }

  hlChrome() {
    this.hlSetup();
    const night = this.hlTheme() === 'night';
    const al = [], wa = [];
    for (const c of this.CH) {
      const v = this.cur[c.id], st = this.hlState(c, v);
      if (!st) continue;
      const lim = st === 'alarm' ? (c.lo != null && v < c.lo ? 'below ' + c.lo : 'above ' + c.hi) : (c.wlo != null && v < c.wlo ? 'below ' + c.wlo : 'above ' + c.whi);
      const row = { src: c.src === 'BB' ? 'BLACK BOX' : 'ECU', name: c.label.toUpperCase(), val: this.hlFmt(c, v) + ' ' + c.u, lim: lim + ' ' + c.u, sev: st === 'alarm' ? 'ALARM' : 'WARN', isAlarm: st === 'alarm', isWarn: st === 'warn', cls: 'arow ' + st };
      (st === 'alarm' ? al : wa).push(row);
    }
    const sec = Math.floor(26 + this.simT), mm = Math.floor(sec / 60), ss = sec % 60;
    const ago = Math.max(0, Math.floor(this.simT - this.markT));
    const one = (r) => r.src + ' ' + r.name + ' ' + r.val + ' — ' + r.lim;
    return {
      themeClass: night ? 'hl night' : 'hl', themeName: night ? 'NIGHT' : 'DAYLIGHT', toggleTheme: this.toggleTheme,
      recTime: (mm < 10 ? '0' : '') + mm + ':' + (ss < 10 ? '0' : '') + ss,
      doMark: this.doMark, marks: this.marks, markAgo: ago < 60 ? ago + ' s ago' : Math.floor(ago / 60) + ' min ago',
      hasAlarm: al.length > 0, noAlarm: al.length === 0 && wa.length === 0, hasWarn: wa.length > 0,
      alarmMsg: al.length ? one(al[0]) + (al.length > 1 ? '  (+' + (al.length - 1) + ')' : '') : '',
      warnMsg: wa.length ? one(wa[0]) + (wa.length > 1 ? '  (+' + (wa.length - 1) + ')' : '') : '',
      activeAlarms: al.concat(wa)
    };
  }

  /* ---------- 3D map scene (three.js r149, canvas 2D fallback) ---------- */

  hl3dData() {
    if (this.M) return this.M;
    const NX = 20, NY = 10;
    const M = this.M = { NX, NY, fuel: [], ign: [], trim: [], hits: [], rpm: [], map: [], seen: 0 };
    for (let i = 0; i < NX; i++) M.rpm.push(Math.round((500 + i * 7000 / (NX - 1)) / 50) * 50);
    for (let j = 0; j < NY; j++) M.map.push(Math.round(17 + j * 84 / (NY - 1)));
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
      const r = i / (NX - 1), l = j / (NY - 1);
      const ve = 0.72 + 0.28 * Math.exp(-Math.pow((r - 0.62) / 0.33, 2));
      M.fuel.push(1.1 + 7.4 * Math.pow(l, 1.15) * ve + 0.5 * r);
      M.ign.push(10 + 30 * Math.pow(r, 0.55) - 17 * Math.pow(l, 1.4) + (l < 0.2 ? 4 : 0));
      M.trim.push(7 * Math.sin(r * 5.1 + 1) * Math.cos(l * 3.7) + 2 * Math.sin(r * 13 + l * 7));
      const h = 900 * Math.exp(-(Math.pow((r - 0.04) / 0.07, 2) + Math.pow((l - 0.2) / 0.14, 2)))
        + 420 * Math.exp(-(Math.pow((r - 0.14 - 0.42 * l) / 0.1, 2) + Math.pow((l - 0.45) / 0.34, 2)));
      const hv = h < 5 ? 0 : Math.round(h);
      M.hits.push(hv);
      if (hv) M.seen++;
    }
    M.rng = { fuel: [0, 10], ign: [-5, 45], trim: [-12, 12] };
    M.zName = { fuel: 'INJ ms', ign: 'IGN °BTDC', trim: 'TRIM %' };
    return M;
  }

  hl3dRamp(stops, t) {
    t = Math.max(0, Math.min(1, t)) * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(t)), f = t - i, a = stops[i], b = stops[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  }

  hl3dSeq(t) {
    return this.hl3dRamp([[0, 0.133, 0.306], [0.208, 0.271, 0.424], [0.4, 0.412, 0.439], [0.58, 0.557, 0.467], [0.784, 0.722, 0.4], [0.996, 0.91, 0.22]], t);
  }

  hl3dDiv(t) {
    return this.hl3dRamp([[0.02, 0.19, 0.38], [0.13, 0.4, 0.675], [0.97, 0.97, 0.97], [0.88, 0.51, 0.08], [0.5, 0.23, 0.03]], t);
  }

  hl3dArr() {
    const g = this.g, M = this.M;
    return this.zArr ? this.zArr() : M[g.zKey];
  }

  hl3dH(idx) {
    const g = this.g, M = this.M;
    if (g.mode === 'hist') return Math.log(1 + M.hits[idx]) / Math.log(901);
    const r = M.rng[g.zKey];
    return Math.max(0, Math.min(1, (this.hl3dArr()[idx] - r[0]) / (r[1] - r[0])));
  }

  hl3dCol(idx) {
    const g = this.g, M = this.M, night = this.hlTheme() === 'night';
    const grey = night ? [0.24, 0.27, 0.3] : [0.8, 0.82, 0.84];
    if (g.mode === 'stall') return grey;
    if (g.logged && !M.hits[idx]) return grey;
    if (g.mode === 'hist' || g.colorBy === 'hits') return this.hl3dSeq(Math.log(1 + M.hits[idx]) / Math.log(901));
    if (g.colorBy === 'trim') return this.hl3dDiv((M.trim[idx] + 12) / 24);
    return this.hl3dSeq(this.hl3dH(idx));
  }

  hl3dXZ(i, j) {
    const M = this.M;
    return [-1 + 2 * i / (M.NX - 1), -0.7 + 1.4 * j / (M.NY - 1)];
  }

  hl3dAt(rpm, map) {
    const M = this.M;
    const fx = Math.max(0, Math.min(M.NX - 1.001, (rpm - 500) / 7000 * (M.NX - 1)));
    const fy = Math.max(0, Math.min(M.NY - 1.001, (map - 17) / 84 * (M.NY - 1)));
    const i = Math.floor(fx), j = Math.floor(fy), a = fx - i, b = fy - j, k = j * M.NX + i;
    const h = (q) => this.hl3dH(q);
    const y = (h(k) * (1 - a) + h(k + 1) * a) * (1 - b) + (h(k + M.NX) * (1 - a) + h(k + M.NX + 1) * a) * b;
    const A = this.hl3dArr();
    const v = (A[k] * (1 - a) + A[k + 1] * a) * (1 - b) + (A[k + M.NX] * (1 - a) + A[k + M.NX + 1] * a) * b;
    const ni = Math.round(fx), nj = Math.round(fy);
    return { x: -1 + 2 * fx / (M.NX - 1), y: y * 0.9, z: -0.7 + 1.4 * fy / (M.NY - 1), i: ni, j: nj, idx: nj * M.NX + ni, v };
  }

  hl3dStart(opts) {
    this.hlSetup();
    this.hl3dData();
    const cv = this._cv || document.querySelector('canvas[data-hl3d]');
    if (!cv) {
      this._tries = (this._tries || 0) + 1;
      if (this._tries < 40) this._retry = setTimeout(() => this.hl3dStart(opts), 150);
      return;
    }
    const W = cv.width, H = cv.height;
    const g = this.g = Object.assign({ cv, W, H, az: -0.62, az0: -0.62, el: 0.5, dist: 4.6, mode: 'surface', zKey: 'fuel', colorBy: 'z', logged: true, tr: [] }, opts || {});
    if (window.THREE) {
      try {
        const r = new THREE.WebGLRenderer({ canvas: cv, antialias: true });
        r.setPixelRatio(1);
        r.setSize(W, H, false);
        g.r = r;
      } catch (err) { g.r = null; }
    }
    if (!g.r) g.ctx = cv.getContext('2d');
    const loop = () => { this._raf = requestAnimationFrame(loop); this.hl3dFrame(); };
    loop();
  }

  hl3dBuild() {
    const T = THREE, g = this.g, M = this.M, NX = M.NX, NY = M.NY, night = this.hlTheme() === 'night';
    const fg = night ? 0xffffff : 0x000000, bg = night ? 0x000000 : 0xffffff, rule = night ? 0x8b96a1 : 0x5a6570;
    if (g.scene) g.scene.traverse((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
    });
    const sc = g.scene = new T.Scene();
    sc.background = new T.Color(bg);
    g.cam = new T.PerspectiveCamera(30, g.W / g.H, 0.1, 60);
    sc.add(new T.AmbientLight(0xffffff, 0.72));
    const sun = new T.DirectionalLight(0xffffff, 0.45);
    sun.position.set(-1.5, 3, 2);
    sc.add(sun);
    const seg = (pts, color) => {
      const ge = new T.BufferGeometry();
      ge.setAttribute('position', new T.BufferAttribute(new Float32Array(pts), 3));
      const l = new T.LineSegments(ge, new T.LineBasicMaterial({ color }));
      sc.add(l);
      return l;
    };
    const fl = [];
    for (let i = 0; i < NX; i++) { const x = this.hl3dXZ(i, 0)[0]; fl.push(x, 0, -0.7, x, 0, 0.7); }
    for (let j = 0; j < NY; j++) { const z = this.hl3dXZ(0, j)[1]; fl.push(-1, 0, z, 1, 0, z); }
    for (let k = 1; k <= 4; k++) { const y = k * 0.225; fl.push(-1, y, -0.7, 1, y, -0.7, -1, y, -0.7, -1, y, 0.7); }
    fl.push(-1, 0, -0.7, -1, 0.9, -0.7, 1, 0, -0.7, 1, 0.9, -0.7, -1, 0, 0.7, -1, 0.9, 0.7);
    seg(fl, rule);

    const n = NX * NY, pos = new Float32Array(n * 3), col = new Float32Array(n * 3), idx = [];
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
      const k = j * NX + i, xz = this.hl3dXZ(i, j), c = this.hl3dCol(k);
      pos[k * 3] = xz[0]; pos[k * 3 + 1] = this.hl3dH(k) * 0.9; pos[k * 3 + 2] = xz[1];
      col[k * 3] = c[0]; col[k * 3 + 1] = c[1]; col[k * 3 + 2] = c[2];
    }
    for (let j = 0; j < NY - 1; j++) for (let i = 0; i < NX - 1; i++) { const a = j * NX + i; idx.push(a, a + NX, a + 1, a + 1, a + NX, a + NX + 1); }
    const hist = g.mode === 'hist';
    if (!hist) {
      const sg = new T.BufferGeometry();
      sg.setAttribute('position', new T.BufferAttribute(pos, 3));
      sg.setAttribute('color', new T.BufferAttribute(col, 3));
      sg.setIndex(idx);
      sc.add(new T.Mesh(sg, new T.MeshBasicMaterial({ vertexColors: true, side: T.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })));
      const w = [];
      const P = (k) => w.push(pos[k * 3], pos[k * 3 + 1], pos[k * 3 + 2]);
      for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
        const k = j * NX + i;
        if (i < NX - 1) { P(k); P(k + 1); }
        if (j < NY - 1) { P(k); P(k + NX); }
      }
      seg(w, fg);
    } else {
      const bgm = new T.BoxGeometry(1, 1, 1);
      bgm.translate(0, 0.5, 0);
      const bars = new T.InstancedMesh(bgm, new T.MeshLambertMaterial({ color: 0xffffff }), n);
      const m = new T.Matrix4(), c3 = new T.Color();
      for (let k = 0; k < n; k++) {
        const hgt = M.hits[k] ? Math.max(0.012, this.hl3dH(k) * 0.9) : 0.0001, c = this.hl3dCol(k);
        m.makeScale(M.hits[k] ? 0.082 : 0.0001, hgt, M.hits[k] ? 0.12 : 0.0001);
        m.setPosition(pos[k * 3], 0, pos[k * 3 + 2]);
        bars.setMatrixAt(k, m);
        bars.setColorAt(k, c3.setRGB(c[0], c[1], c[2]));
      }
      bars.instanceMatrix.needsUpdate = true;
      if (bars.instanceColor) bars.instanceColor.needsUpdate = true;
      sc.add(bars);
    }

    g.trail = new T.InstancedMesh(new T.SphereGeometry(1, 8, 6), new T.MeshBasicMaterial({ color: fg }), 60);
    sc.add(g.trail);
    g.pt = new T.Mesh(new T.SphereGeometry(0.036, 16, 12), new T.MeshBasicMaterial({ color: 0xe6007a }));
    g.ptO = new T.Mesh(new T.SphereGeometry(0.052, 16, 12), new T.MeshBasicMaterial({ color: fg, side: T.BackSide }));
    sc.add(g.pt); sc.add(g.ptO);
    g.drop = seg(new Array(18).fill(0), fg);
    g.sel = new T.Mesh(new T.OctahedronGeometry(0.045), new T.MeshBasicMaterial({ color: night ? 0x6cb6ff : 0x0a3fa8 }));
    g.sel.visible = false;
    sc.add(g.sel);

    if (g.mode === 'stall') {
      const cols = night ? [0x6cb6ff, 0xff9d4d, 0xd2a8ff] : [0x0a3fa8, 0x963200, 0x6a1b9a];
      const m = new T.Matrix4();
      for (let p = 0; p < 3; p++) {
        const beads = new T.InstancedMesh(new T.SphereGeometry(1, 8, 6), new T.MeshBasicMaterial({ color: cols[p] }), 40);
        let last = null;
        for (let q = 0; q < 40; q++) {
          const s = q / 39;
          const rpm = 3300 - p * 350 - (2700 - p * 350) * Math.pow(s, 0.8) - (s > 0.85 ? (s - 0.85) * 2600 : 0) + 60 * Math.sin(q * 0.9 + p);
          const map = 62 - 34 * Math.sin(Math.min(1, s * 1.5) * Math.PI / 2) + (s > 0.72 ? (s - 0.72) * 235 : 0) + p * 2.5;
          const o = this.hl3dAt(Math.max(500, rpm), Math.min(101, map)), sz = 0.011 + 0.014 * s;
          m.makeScale(sz, sz, sz);
          m.setPosition(o.x, o.y + 0.025, o.z);
          beads.setMatrixAt(q, m);
          last = o;
        }
        beads.instanceMatrix.needsUpdate = true;
        sc.add(beads);
        const end = new T.Mesh(new T.OctahedronGeometry(0.06), new T.MeshBasicMaterial({ color: cols[p] }));
        end.position.set(last.x, last.y + 0.09 + p * 0.02, last.z);
        sc.add(end);
      }
    }

    const lab = (txt, x, y, z, s) => {
      const c = document.createElement('canvas');
      c.width = 256; c.height = 64;
      const k = c.getContext('2d');
      k.font = '700 40px Inter, DejaVu Sans, sans-serif';
      k.textAlign = 'center'; k.textBaseline = 'middle';
      k.fillStyle = night ? '#ffffff' : '#000000';
      k.fillText(txt, 128, 34);
      const sp = new T.Sprite(new T.SpriteMaterial({ map: new T.CanvasTexture(c), depthTest: false, transparent: true }));
      sp.position.set(x, y, z);
      sp.scale.set(0.5 * s, 0.125 * s, 1);
      sc.add(sp);
    };
    for (let r = 1000; r <= 7000; r += 1000) lab(String(r), -1 + 2 * (r - 500) / 7000, -0.03, 0.84, 0.78);
    lab('RPM', 0, -0.03, 1.02, 0.95);
    for (const mv of [20, 40, 60, 80, 100]) lab(String(mv), 1.15, -0.02, -0.7 + 1.4 * (mv - 17) / 84, 0.78);
    lab('MAP kPa', 1.42, -0.02, 0, 0.95);
    const rg = hist ? [0, 900] : M.rng[g.zKey];
    for (let k = 0; k <= 4; k++) {
      const t = k / 4;
      const val = hist ? Math.round(Math.exp(t * Math.log(901)) - 1) : (rg[0] + (rg[1] - rg[0]) * t);
      lab(hist ? String(val) : (Math.round(val * 10) / 10).toString(), -1.16, k * 0.225, -0.7, 0.78);
    }
    lab(hist ? 'SAMPLES' : M.zName[g.zKey], -1.1, 1.04, -0.7, 0.95);
  }

  hl3dFrame() {
    const g = this.g;
    if (!g) return;
    const key = [this.hlTheme(), g.mode, g.zKey, g.colorBy, this._rev || 0].join('|');
    if (key !== g.key) { g.key = key; g.tr = []; if (g.r) this.hl3dBuild(); }
    const t = this.simT || 0;
    if (!g.user) g.az = g.az0 + 0.2 * Math.sin(t * 0.17);
    const ce = Math.cos(g.el), ty = 0.3;
    const cx = g.dist * ce * Math.sin(g.az), cy = ty + g.dist * Math.sin(g.el), cz = g.dist * ce * Math.cos(g.az);
    const p = this.hl3dAt(this.cur.rpm, this.cur.map);
    if (!g.trT || t - g.trT > 0.12) { g.trT = t; g.tr.push(p); if (g.tr.length > 60) g.tr.shift(); }
    if (g.r) {
      g.cam.position.set(cx, cy, cz);
      g.cam.lookAt(0, ty, 0);
      const live = g.mode !== 'stall';
      g.pt.visible = g.ptO.visible = g.drop.visible = g.trail.visible = live;
      g.pt.position.set(p.x, p.y + 0.03, p.z);
      g.ptO.position.copy(g.pt.position);
      const d = g.drop.geometry.attributes.position;
      d.setXYZ(0, p.x, 0, p.z); d.setXYZ(1, p.x, p.y, p.z);
      d.setXYZ(2, p.x, 0, 0.7); d.setXYZ(3, p.x, 0, p.z);
      d.setXYZ(4, 1, 0, p.z); d.setXYZ(5, p.x, 0, p.z);
      d.needsUpdate = true;
      const m = g.m4 || (g.m4 = new THREE.Matrix4());
      for (let i = 0; i < 60; i++) {
        const q = g.tr[i], s = q ? 0.007 + 0.013 * i / 60 : 0.00001;
        m.makeScale(s, s, s);
        if (q) m.setPosition(q.x, q.y + 0.03, q.z); else m.setPosition(0, -5, 0);
        g.trail.setMatrixAt(i, m);
      }
      g.trail.instanceMatrix.needsUpdate = true;
      if (this.selIdx != null && g.mode === 'surface') {
        const M = this.M, i = this.selIdx % M.NX, j = Math.floor(this.selIdx / M.NX), xz = this.hl3dXZ(i, j);
        g.sel.visible = true;
        g.sel.position.set(xz[0], this.hl3dH(this.selIdx) * 0.9 + 0.07, xz[1]);
      } else g.sel.visible = false;
      g.r.render(g.scene, g.cam);
    } else if (g.ctx) this.hl3dDraw2d(cx, cy, cz, ty, p);
  }

  hl3dDraw2d(cx, cy, cz, ty, p) {
    const g = this.g, k = g.ctx, M = this.M, NX = M.NX, NY = M.NY, night = this.hlTheme() === 'night';
    let fx = -cx, fy = ty - cy, fz = -cz;
    const fl = Math.hypot(fx, fy, fz); fx /= fl; fy /= fl; fz /= fl;
    let rx = -fz, rz = fx;
    const rl = Math.hypot(rx, rz); rx /= rl; rz /= rl;
    const ux = -rz * fy, uy = rz * fx - rx * fz, uz = rx * fy;
    const f = (g.H / 2) / Math.tan(15 * Math.PI / 180);
    const pr = (x, y, z) => {
      const dx = x - cx, dy = y - cy, dz = z - cz, zc = dx * fx + dy * fy + dz * fz, s = f / zc;
      return [g.W / 2 + (dx * rx + dz * rz) * s, g.H / 2 - (dx * ux + dy * uy + dz * uz) * s, zc];
    };
    k.fillStyle = night ? '#000' : '#fff';
    k.fillRect(0, 0, g.W, g.H);
    k.lineWidth = 2;
    k.strokeStyle = night ? '#8b96a1' : '#5a6570';
    const c0 = [pr(-1, 0, -0.7), pr(1, 0, -0.7), pr(1, 0, 0.7), pr(-1, 0, 0.7)];
    k.beginPath(); c0.forEach((q, i) => (i ? k.lineTo(q[0], q[1]) : k.moveTo(q[0], q[1]))); k.closePath(); k.stroke();
    const V = [];
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) { const xz = this.hl3dXZ(i, j), q = j * NX + i; V.push(pr(xz[0], this.hl3dH(q) * 0.9, xz[1])); }
    const quads = [];
    for (let j = 0; j < NY - 1; j++) for (let i = 0; i < NX - 1; i++) {
      const a = j * NX + i, c = this.hl3dCol(a);
      quads.push([V[a], V[a + 1], V[a + NX + 1], V[a + NX], c]);
    }
    quads.sort((a, b) => (b[0][2] + b[2][2]) - (a[0][2] + a[2][2]));
    k.lineWidth = 1.5;
    k.strokeStyle = night ? '#fff' : '#000';
    for (const q of quads) {
      const c = q[4];
      k.fillStyle = 'rgb(' + Math.round(c[0] * 255) + ',' + Math.round(c[1] * 255) + ',' + Math.round(c[2] * 255) + ')';
      k.beginPath(); k.moveTo(q[0][0], q[0][1]); k.lineTo(q[1][0], q[1][1]); k.lineTo(q[2][0], q[2][1]); k.lineTo(q[3][0], q[3][1]); k.closePath();
      k.fill(); k.stroke();
    }
    if (g.mode !== 'stall') {
      const s = pr(p.x, p.y + 0.03, p.z);
      k.fillStyle = '#e6007a'; k.lineWidth = 4;
      k.beginPath(); k.arc(s[0], s[1], 9, 0, 6.3); k.fill(); k.stroke();
    }
    k.fillStyle = night ? '#fff' : '#000';
    k.font = '700 16px Inter, sans-serif';
    k.fillText('RPM →', c0[3][0] + 40, c0[3][1] + 24);
    k.fillText('MAP kPa', c0[2][0] + 10, c0[2][1] - 6);
  }
