/*
 * ContourNest – True-Shape-Nesting-Engine (No-Fit-Polygon + genetischer Algorithmus)
 * Eigener Code für das Fusion-Add-In ContourNest (MIT-Lizenz, siehe LICENSE_ContourNest.txt).
 * Polygon-Boolesche Operationen, Offsets und Minkowski-Summen: Clipper 6.4.2 (Angus Johnson,
 * Boost Software License 1.0, siehe LICENSE_clipper_BSL-1.0.txt).
 *
 * Läuft ohne Web Worker: Arbeit wird über einen Generator in kleine Schritte zerlegt
 * (step(budgetMs)), damit die Palette per setTimeout reaktiv bleibt. Gleiches Modul läuft in node.
 *
 * Einheiten: Eingabe/Ausgabe in mm. Intern ganzzahlig (SCALE = 100 -> 0,01 mm).
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory(require('./clipper.js'));
  } else {
    root.ContourNestEngine = factory(root.ClipperLib);
  }
}(typeof self !== 'undefined' ? self : this, function (ClipperLib) {
  'use strict';

  var SCALE = 100;            // 1 mm = 100 Einheiten
  var ARC_TOL = 0.05;         // mm, Bogen-Toleranz beim Offset
  var SIMPLIFY_TOL = 0.15;    // mm, Douglas-Peucker nach dem Offset
  var C = ClipperLib.Clipper;

  // ---------------------------------------------------------------- Hilfen
  function mulberry32(a) {
    return function () {
      a |= 0; a = a + 0x6D2B79F5 | 0;
      var t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function areaMM(pts) {        // [[x,y],...] -> vorzeichenbehaftet
    var a = 0, n = pts.length;
    for (var i = 0; i < n; i++) {
      var p = pts[i], q = pts[(i + 1) % n];
      a += p[0] * q[1] - q[0] * p[1];
    }
    return a / 2;
  }
  function centroidMM(outer, holes) {
    function c(pts) {
      var a = 0, cx = 0, cy = 0, n = pts.length;
      for (var i = 0; i < n; i++) {
        var p = pts[i], q = pts[(i + 1) % n], k = p[0] * q[1] - q[0] * p[1];
        a += k; cx += (p[0] + q[0]) * k; cy += (p[1] + q[1]) * k;
      }
      a /= 2;
      return a === 0 ? { a: 0, x: 0, y: 0 } : { a: a, x: cx / (6 * a), y: cy / (6 * a) };
    }
    var o = c(outer), A = Math.abs(o.a), X = o.x * A, Y = o.y * A;
    (holes || []).forEach(function (h) {
      var r = c(h), ah = Math.abs(r.a); A -= ah; X -= r.x * ah; Y -= r.y * ah;
    });
    return A > 0 ? [X / A, Y / A] : [o.x, o.y];
  }
  function rotPts(pts, deg) {
    var r = deg * Math.PI / 180, c = Math.cos(r), s = Math.sin(r);
    return pts.map(function (p) { return [p[0] * c - p[1] * s, p[0] * s + p[1] * c]; });
  }
  function mirrorPts(pts) { return pts.map(function (p) { return [p[0], -p[1]]; }); }
  // Lage eines Teils: lokale Kontur q -> (gespiegelt: (u,-v)) -> gedreht -> verschoben
  function placePts(pts, rot, flip, x, y) {
    return rotPts(flip ? mirrorPts(pts) : pts, rot).map(function (p) { return [p[0] + x, p[1] + y]; });
  }
  function toPath(pts) {
    return pts.map(function (p) { return { X: Math.round(p[0] * SCALE), Y: Math.round(p[1] * SCALE) }; });
  }
  function fromPath(path) { return path.map(function (p) { return [p.X / SCALE, p.Y / SCALE]; }); }
  function orient(path, ccw) {           // ccw=true -> positive Orientierung
    if (C.Orientation(path) !== ccw) path = path.slice().reverse();
    return path;
  }
  function translatePath(path, dx, dy) {
    return path.map(function (p) { return { X: p.X + dx, Y: p.Y + dy }; });
  }
  function boundsOf(path) {
    var b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    for (var i = 0; i < path.length; i++) {
      var p = path[i];
      if (p.X < b.x0) b.x0 = p.X; if (p.X > b.x1) b.x1 = p.X;
      if (p.Y < b.y0) b.y0 = p.Y; if (p.Y > b.y1) b.y1 = p.Y;
    }
    return b;
  }
  // Douglas-Peucker für geschlossene Polygone (mm)
  function simplifyClosed(pts, tol) {
    if (pts.length < 8 || tol <= 0) return pts;
    // Start am weitesten entfernten Punktpaar, damit der Ring sinnvoll geteilt wird
    var i0 = 0, best = -1, i;
    for (i = 1; i < pts.length; i++) {
      var d = (pts[i][0] - pts[0][0]) * (pts[i][0] - pts[0][0]) + (pts[i][1] - pts[0][1]) * (pts[i][1] - pts[0][1]);
      if (d > best) { best = d; i0 = i; }
    }
    function dp(seq) {
      var keep = new Array(seq.length).fill(false);
      keep[0] = keep[seq.length - 1] = true;
      var stack = [[0, seq.length - 1]];
      while (stack.length) {
        var s = stack.pop(), a = seq[s[0]], b = seq[s[1]], dmax = -1, idx = -1;
        var dx = b[0] - a[0], dy = b[1] - a[1], L = Math.sqrt(dx * dx + dy * dy);
        for (var k = s[0] + 1; k < s[1]; k++) {
          var p = seq[k], dd;
          if (L < 1e-12) dd = Math.hypot(p[0] - a[0], p[1] - a[1]);
          else dd = Math.abs(dx * (a[1] - p[1]) - dy * (a[0] - p[0])) / L;
          if (dd > dmax) { dmax = dd; idx = k; }
        }
        if (dmax > tol) { keep[idx] = true; stack.push([s[0], idx]); stack.push([idx, s[1]]); }
      }
      return seq.filter(function (_, k) { return keep[k]; });
    }
    var a1 = pts.slice(0, i0 + 1), a2 = pts.slice(i0).concat([pts[0]]);
    var r1 = dp(a1), r2 = dp(a2);
    var out = r1.concat(r2.slice(1, r2.length - 1));
    return out.length >= 3 ? out : pts;
  }

  // ---------------------------------------------------------------- Clipper-Wrapper
  function clip(subj, clipPaths, type) {
    var c = new ClipperLib.Clipper(), sol = new ClipperLib.Paths();
    if (subj.length) c.AddPaths(subj, ClipperLib.PolyType.ptSubject, true);
    if (clipPaths && clipPaths.length) c.AddPaths(clipPaths, ClipperLib.PolyType.ptClip, true);
    c.Execute(type, sol, ClipperLib.PolyFillType.pftNonZero, ClipperLib.PolyFillType.pftNonZero);
    return sol;
  }
  function union(paths) { return clip(paths, null, ClipperLib.ClipType.ctUnion); }
  function difference(a, b) { return clip(a, b, ClipperLib.ClipType.ctDifference); }

  // Polygon mit Löchern um delta (mm) vergrößern; Löcher schrumpfen entsprechend.
  function inflate(outerMM, holesMM, deltaMM, arcTolMM) {
    var co = new ClipperLib.ClipperOffset(2, (arcTolMM || ARC_TOL) * SCALE);
    var outer = orient(toPath(outerMM), true);
    co.AddPath(outer, ClipperLib.JoinType.jtRound, ClipperLib.EndType.etClosedPolygon);
    (holesMM || []).forEach(function (h) {
      co.AddPath(orient(toPath(h), false), ClipperLib.JoinType.jtRound, ClipperLib.EndType.etClosedPolygon);
    });
    var tree = new ClipperLib.PolyTree();
    co.Execute(tree, deltaMM * SCALE);
    var best = null, bestA = -1;
    tree.Childs().forEach(function (n) {
      var a = Math.abs(C.Area(n.Contour()));
      if (a > bestA) { bestA = a; best = n; }
    });
    if (!best) return null;
    return {
      outer: fromPath(best.Contour()),
      holes: best.Childs().map(function (h) { return fromPath(h.Contour()); })
    };
  }

  // ================================================================ Rohteile je Blatt (1.10.0)
  // Jedes Blatt hat eine Definition (def): Standardformat oder „Abschnitt“ (eigenes Maß, Faser, Besäumung,
  // optional Material/Dicke), Ausrichtung längs/quer und Nullpunkt. Koordinaten der Palette = Maschinentisch
  // (X nach rechts, Y nach hinten). „quer“ = Platte um 90° gedreht: X-Ausdehnung = Plattenbreite.
  // Materialsystem (Fusion-Komponente „Rohteil NN“): u entlang der Plattenlänge, v entlang der Breite;
  // quer: Maschine (x, y) = (Breite − v, u)  <=>  u = y, v = Breite − x.
  var ZERO_CORNER_TXT = { lu: 'unten links', ru: 'unten rechts', lh: 'oben links', rh: 'oben rechts', mitte: 'Mitte' };
  var ZERO_SIDE_TXT = { unten: 'Unterseite', oben: 'Oberseite' };
  var ZERO_KEYS = [];
  ['lu', 'ru', 'lh', 'rh', 'mitte'].forEach(function (c) { ['unten', 'oben'].forEach(function (sd) { ZERO_KEYS.push(c + '_' + sd); }); });
  function validZero(z) { return ZERO_KEYS.indexOf(z) >= 0 ? z : null; }
  function num(v) { v = +v; return isFinite(v) ? v : null; }
  function resolveSheet(def, std, material, defaultZero) {
    var d = def || {}, s = std || {}, ab = d.kind === 'abschnitt';
    var pick = function (k, ok) { var v = num(d[k]); return (ab && v !== null && ok(v)) ? v : num(s[k]); };
    var length = pick('length', function (v) { return v > 0; }), width = pick('width', function (v) { return v > 0; });
    var frame = pick('frame', function (v) { return v >= 0; }), thickness = pick('thickness', function (v) { return v > 0; });
    var grain = (ab && (d.grain === 'X' || d.grain === 'Y')) ? d.grain : (s.grain === 'Y' ? 'Y' : 'X');
    var quer = d.orient === 'quer';
    var gAxis = ((grain === 'Y') !== quer) ? 'Y' : 'X';     // Faser im Maschinensystem
    return {
      id: d.id || '', kind: ab ? 'abschnitt' : 'standard', length: length || 0, width: width || 0,
      thickness: thickness || 0, frame: frame || 0, grain: grain,
      material: (ab && d.material) ? String(d.material) : (material || ''),
      orient: quer ? 'quer' : 'laengs', zero: validZero(d.zero) || validZero(defaultZero) || 'lu_unten',
      W: quer ? (width || 0) : (length || 0), H: quer ? (length || 0) : (width || 0),
      grainAxis: gAxis, G: gAxis === 'Y' ? 90 : 0
    };
  }
  // speicherbare Definition (nur die Werte, die das Blatt wirklich bestimmt)
  function sheetDefOut(r) {
    var o = { id: r.id || '', kind: r.kind, orient: r.orient, zero: r.zero };
    if (r.kind === 'abschnitt') {
      o.length = r.length; o.width = r.width; o.grain = r.grain; o.frame = r.frame;
      o.thickness = r.thickness; if (r.material) o.material = r.material;
    }
    return o;
  }
  function sheetRec(r, spacing) { return { length: r.W, width: r.H, frame: r.frame, spacing: spacing || 0, grain: r.grainAxis }; }
  // Nullpunkt: Maschinenkoordinaten (mm) + Lage im Materialsystem (Fusion-Komponente)
  function zeroPoint(r) {
    var z = validZero(r.zero) || 'lu_unten', c = z.split('_')[0], sd = z.split('_')[1];
    var x = (c === 'ru' || c === 'rh') ? r.W : (c === 'mitte' ? r.W / 2 : 0);
    var y = (c === 'lh' || c === 'rh') ? r.H : (c === 'mitte' ? r.H / 2 : 0);
    var u = x, v = y;
    if (r.orient === 'quer') { u = y; v = r.width - x; }
    return { key: z, corner: c, side: sd, x: x, y: y, z: sd === 'oben' ? r.thickness : 0, u: u, v: v };
  }
  function zeroText(z) {
    z = validZero(z) || 'lu_unten';
    return ZERO_CORNER_TXT[z.split('_')[0]] + ', ' + ZERO_SIDE_TXT[z.split('_')[1]];
  }
  function deNum(x) {
    var r = Math.round(x * 10) / 10, neg = r < 0, a = Math.abs(r);
    var ip = Math.floor(a + 1e-9), fr = Math.round((a - ip) * 10);
    return (neg ? '-' : '') + String(ip) + (fr ? ',' + fr : '');
  }
  // Beschriftung (Palette + Faserskizze in Fusion, gleiche Regeln wie cn_geom.sheet_label)
  function sheetLabel(r, no) {
    return 'Rohteil ' + String(no).padStart(2, '0') + ': ' + deNum(r.W) + ' × ' + deNum(r.H) + ' × ' + deNum(r.thickness) +
      ' mm, ' + (r.orient === 'quer' ? 'quer' : 'längs') + ', Faser ' + r.grainAxis + ', ' +
      (r.kind === 'abschnitt' ? 'Abschnitt' : 'Standard') + ' | Nullpunkt: ' + zeroText(r.zero);
  }
  // Blatt drehen (längs <-> quer): Lage eines Teils (Bezugspunkt x/y, Drehung) mit dem Blatt mitdrehen.
  // r = Blatt VOR dem Drehen. längs -> quer: +90°, quer -> längs: −90° (genau die Umkehrung).
  function turnPose(p, r) {
    if (r.orient === 'quer') return { x: p.y, y: r.W - p.x, rot: norm360(p.rot - 90) };
    return { x: r.H - p.y, y: p.x, rot: norm360(p.rot + 90) };
  }
  // Maschine -> Materialsystem (für Fusion): Umkehrung der Drehung bei „quer“
  function toMaterial(p, r) {
    if (r.orient !== 'quer') return { x: p.x, y: p.y, rot: norm360(p.rot) };
    return { x: p.y, y: r.W - p.x, rot: norm360(p.rot - 90) };
  }

  // ---------------------------------------------------------------- Engine
  function Nester(job, opts) {
    opts = opts || {};
    this.job = job;
    this.rand = mulberry32(opts.seed || job.seed || 12345);
    var sh = job.sheet;
    this.W = sh.length; this.H = sh.width;
    this.frame = sh.frame || 0; this.spacing = sh.spacing || 0;
    this.sheetGrain = (sh.grain === 'Y') ? 90 : 0;
    // 1.10.0: Blatt-Definitionen in Füllreihenfolge (Abschnitte, Ausrichtung, Nullpunkt); danach Standardformat
    this.defs = (job.sheets || []).map(function (d) { return resolveSheet(d, sh, job.material, job.defaultZero); });
    this.stdSheet = resolveSheet({ kind: 'standard' }, sh, job.material, job.defaultZero);
    this.maxSheets = Math.max(1, job.maxSheets || 1, this.defs.length);
    this.rotStep = job.rotStep || 90;
    this.vacW = Math.max(0, Math.min(100, job.vacuumWeight || 0)) / 100;
    this.minVacArea = job.minVacArea || 0;          // mm²
    this.popSize = Math.max(2, job.population || 10);
    this.maxGen = Math.max(1, job.maxGenerations || 30);
    this.timeLimit = (job.timeLimit || 60) * 1000;
    this.mutRate = job.mutationRate || 0.1;
    this.nfpCache = {};
    this.shapeCache = {};
    this.stats = { nfp: 0, evaluated: 0, generation: 0 };
    this.prepare();
    this.population = [];
    this.pending = [];
    this.best = null;
    this.done = false;
    this.t0 = null;
  }

  Nester.prototype.prepare = function () {
    var self = this, delta = this.spacing / 2 + ARC_TOL + SIMPLIFY_TOL;
    this.types = [];
    this.typeById = {};
    this.instances = [];
    var areas = [], ov = this.job.overrides || {};
    this.job.parts.forEach(function (p) {
      if (p.enabled === false || !(p.qty > 0)) return;
      var outer = p.outer.slice(), holes = (p.holes || []).slice();
      if (areaMM(outer) < 0) outer.reverse();
      holes = holes.map(function (h) { return areaMM(h) > 0 ? h.slice().reverse() : h; });
      var realArea = Math.abs(areaMM(outer)) - holes.reduce(function (s, h) { return s + Math.abs(areaMM(h)); }, 0);
      var inf = inflate(outer, holes, delta);
      if (!inf) return;
      inf.outer = simplifyClosed(inf.outer, SIMPLIFY_TOL);
      inf.holes = inf.holes.map(function (h) { return simplifyClosed(h, SIMPLIFY_TOL); })
        .filter(function (h) { return h.length >= 3; });
      var t = {
        idx: self.types.length, id: p.id, name: p.name || p.id, outer: outer, holes: holes,
        area: realArea, centroid: centroidMM(outer, holes), inflated: inf,
        grainMode: p.grainMode || 'frei', grainAngle: p.grainAngle || 0,
        hasHoles: holes.length > 0,
        // Bearbeitungen (Taschen/Nuten/Sacklöcher) – NUR Anzeige, nie Teil der Kollisionsgeometrie
        features: (p.features || []).filter(function (f) { return f && f.pts && f.pts.length >= 3; })
      };
      self.types.push(t);
      self.typeById[t.id] = t;
      for (var k = 0; k < p.qty; k++) {
        var key = p.id + '#' + k, o = ov[key] || {};
        if (o.excluded) continue;
        var inst = {
          type: t.idx, copy: k, key: key, flip: !!o.flip,
          grainMode: o.grainMode || t.grainMode, ignoreGrain: !!o.ignoreGrain,
          pinned: o.pinned ? { sheet: Math.max(1, o.sheet | 0), x: +o.x, y: +o.y, rot: +o.rot } : null,
          // feste Drehung ohne Fixierung: Teil darf verschoben, aber NICHT gedreht werden (auch keine 180°-Alternative)
          fixRot: (o.fixRot !== undefined && o.fixRot !== null && isFinite(+o.fixRot)) ? norm360(+o.fixRot) : null
        };
        inst.rotations = inst.fixRot !== null ? [inst.fixRot]
          : self.allowedRotations(t, inst.grainMode, inst.flip, inst.ignoreGrain);
        self.instances.push(inst);
        areas.push(realArea);
      }
    });
    // Bezugsfläche = mittlere Teilefläche (aller Stück)
    this.refArea = areas.length ? areas.reduce(function (s, a) { return s + a; }, 0) / areas.length : 1;
    this.types.forEach(function (t) {
      // Kleinteil-Faktor ~ 1/Fläche_normiert: 0 für Teile >= Mittelwert, max. 10;
      // Teile unter "Mindestfläche Vakuum" (kritisch) bekommen mindestens 1.
      t.critical = self.minVacArea > 0 && t.area < self.minVacArea;
      t.smallFactor = Math.min(10, Math.max(t.critical ? 1 : 0, self.refArea / Math.max(t.area, 1e-6) - 1));
    });
  };

  function norm360(a) { a = a % 360; if (a < 0) a += 360; return Math.round(a * 1e6) / 1e6; }

  // erlaubte Drehungen; gewendete Teile haben die gespiegelte Faser (-g)
  Nester.prototype.allowedRotations = function (t, mode, flip, ignoreGrain, sheetG) {
    var G = (sheetG === undefined || sheetG === null) ? this.sheetGrain : sheetG, g = flip ? -t.grainAngle : t.grainAngle, base;
    mode = ignoreGrain ? 'frei' : (mode || t.grainMode);
    if (mode === 'laengs') base = G - g;
    else if (mode === 'quer') base = G + 90 - g;
    if (base !== undefined) return [norm360(base), norm360(base + 180)];
    var step = this.rotStep;
    if (!step || step <= 0) return [0];
    var r = [], n = Math.max(1, Math.round(360 / step));
    for (var k = 0; k < n; k++) r.push(norm360(k * 360 / n));
    return r;
  };

  Nester.prototype.shape = function (ti, rot, flip) {
    var key = ti + '@' + rot + (flip ? 'F' : '');
    var s = this.shapeCache[key];
    if (s) return s;
    var t = this.types[ti];
    var tr = function (pts) { return rotPts(flip ? mirrorPts(pts) : pts, rot); };
    var outer = orient(toPath(tr(t.inflated.outer)), true);
    var holes = t.inflated.holes.map(function (h) { return orient(toPath(tr(h)), false); });
    s = { key: key, outer: outer, holes: holes, b: boundsOf(outer) };
    this.shapeCache[key] = s;
    return s;
  };

  // NFP: Menge der Positionen p, für die (B + p) die feststehende Form A (im Ursprung) schneidet.
  // = (∂A ⊕ ∂(−B)) ∪ (A − b0) ∪ (a0 − B)  abzüglich der Lochbereiche, in die B vollständig passt.
  Nester.prototype.nfp = function (ta, ra, fa, tb, rb, fb) {
    var A = this.shape(ta, ra, fa), B = this.shape(tb, rb, fb);
    var key = A.key + '|' + B.key;
    var hit = this.nfpCache[key];
    if (hit) return hit;
    this.stats.nfp++;
    var forb = nfpCore(A, B, false);
    this.nfpCache[key] = forb;
    return forb;
  };

  // Bereich der Verschiebungen p, für die (Teil + p) vollständig im Loch H liegt (Teil im Ursprung)
  function innerFit(H, part) {
    var hb = boundsOf(H);
    if (hb.x1 - hb.x0 < part.b.x1 - part.b.x0 || hb.y1 - hb.y0 < part.b.y1 - part.b.y0) return [];   // passt nicht
    var neg = part.outer.map(function (p) { return { X: -p.X, Y: -p.Y }; }), b0 = part.outer[0];
    var region = [orient(translatePath(H, -b0.X, -b0.Y), true)];
    return difference(region, union(C.Minkowski(neg, H, true, true)));
  }
  // withInner: auch A in einem Loch von B zulassen (manuelles Platzieren: Rahmen über ein Teil ziehen)
  function nfpCore(A, B, withInner) {
    var negB = B.outer.map(function (p) { return { X: -p.X, Y: -p.Y }; });
    var b0 = B.outer[0], a0 = A.outer[0];
    var pieces = C.Minkowski(negB, A.outer, true, true);
    pieces.push(orient(translatePath(A.outer, -b0.X, -b0.Y), true));
    pieces.push(orient(translatePath(negB, a0.X, a0.Y), true));
    var forb = union(pieces);
    for (var h = 0; h < A.holes.length; h++) {
      var ero = innerFit(A.holes[h], B);
      if (ero.length) forb = difference(forb, ero);
    }
    if (withInner && B.holes) {
      for (var g = 0; g < B.holes.length; g++) {
        // A + q ⊂ Loch(B)  ⇔  B + p umschließt A mit p = −q (Spiegelung am Ursprung = Drehung 180°)
        var q = innerFit(B.holes[g], A);
        if (q.length) forb = difference(forb, q.map(function (path) { return path.map(function (pt) { return { X: -pt.X, Y: -pt.Y }; }); }));
      }
    }
    return forb;
  }

  // Geometrie des Blatts si (0-basiert, Füllreihenfolge): Definition oder Standardformat (längs)
  Nester.prototype.geom = function (si) {
    return (si !== undefined && si < this.defs.length) ? this.defs[si] : this.stdSheet;
  };
  Nester.prototype.newSheet = function (gm) {
    if (gm === undefined || typeof gm === 'number') gm = this.geom(gm);
    return { placed: [], b: null, area: 0, gm: gm };
  };
  // Drehung einer Instanz auf einem Blatt: Gene sind im Standard-Fasersystem; an Faser gebundene Teile drehen
  // mit der Faser des Blatts (quer/Abschnitt mit anderer Faser), freie Teile behalten ihren Winkel.
  Nester.prototype.mapRot = function (inst, r, gm) {
    if (inst.fixRot !== null && inst.fixRot !== undefined) return inst.fixRot;
    if (!gm || gm.G === this.sheetGrain) return r;
    var mode = inst.ignoreGrain ? 'frei' : inst.grainMode;
    if (mode !== 'laengs' && mode !== 'quer') return r;
    return norm360(r + gm.G - this.sheetGrain);
  };

  // Beste Position für Instanz auf Blatt (oder null)
  Nester.prototype.tryPlace = function (sheet, ti, rot, flip) {
    var B = this.shape(ti, rot, flip), S = SCALE, gm = sheet.gm || this.stdSheet;
    var ux0 = Math.round(gm.frame * S), uy0 = ux0;
    var ux1 = Math.round((gm.W - gm.frame) * S), uy1 = Math.round((gm.H - gm.frame) * S);
    var x0 = ux0 - B.b.x0, x1 = ux1 - B.b.x1, y0 = uy0 - B.b.y0, y1 = uy1 - B.b.y1;
    if (x0 > x1 || y0 > y1) return null;
    if (x1 - x0 < 2) x1 = x0 + 2;           // entarteter Bereich (Teil passt exakt)
    if (y1 - y0 < 2) y1 = y0 + 2;
    var ifp = [[{ X: x0, Y: y0 }, { X: x1, Y: y0 }, { X: x1, Y: y1 }, { X: x0, Y: y1 }]];
    var feas;
    if (!sheet.placed.length) {
      feas = ifp;
    } else {
      var all = [];
      for (var i = 0; i < sheet.placed.length; i++) {
        var pl = sheet.placed[i];
        var pb = pl.bb;     // grobe Vorprüfung: liegt das NFP überhaupt im IFP?
        if (pb.x1 - B.b.x0 < x0 || pb.x0 - B.b.x1 > x1 || pb.y1 - B.b.y0 < y0 || pb.y0 - B.b.y1 > y1) continue;
        var nf = this.nfp(pl.type, pl.rot, pl.flip, ti, rot, flip);
        for (var k = 0; k < nf.length; k++) all.push(translatePath(nf[k], pl.x, pl.y));
      }
      feas = all.length ? difference(ifp, union(all)) : ifp;
    }
    if (!feas.length) return null;
    var t = this.types[ti], best = null, bestS = Infinity;
    var cxS = gm.W * S / 2, cyS = gm.H * S / 2;
    var cr = rotPts(flip ? mirrorPts([t.centroid]) : [t.centroid], rot)[0];
    var lam = this.vacW * t.smallFactor;
    var sb = sheet.b;
    for (var f = 0; f < feas.length; f++) {
      var path = feas[f];
      for (var v = 0; v < path.length; v++) {
        var p = path[v];
        var bx0 = B.b.x0 + p.X, bx1 = B.b.x1 + p.X, by0 = B.b.y0 + p.Y, by1 = B.b.y1 + p.Y;
        if (sb) {
          bx0 = Math.min(bx0, sb.x0); bx1 = Math.max(bx1, sb.x1);
          by0 = Math.min(by0, sb.y0); by1 = Math.max(by1, sb.y1);
        }
        // Schwerkraft links-unten (Breite doppelt gewichtet, wie SVGnest) in mm
        var score = ((bx1 - ux0) * 2 + (by1 - uy0)) / S;
        if (lam > 0) {
          var dx = cr[0] * S + p.X - cxS, dy = cr[1] * S + p.Y - cyS;
          score += lam * Math.sqrt(dx * dx + dy * dy) / S;
        }
        score += (p.X * 1e-6 + p.Y * 1e-7) / S;
        if (score < bestS) { bestS = score; best = { x: p.X, y: p.Y, score: score }; }
      }
    }
    return best;
  };

  Nester.prototype.commit = function (sheet, ti, rot, pos, inst, flip) {
    var B = this.shape(ti, rot, flip);
    var bb = { x0: B.b.x0 + pos.x, x1: B.b.x1 + pos.x, y0: B.b.y0 + pos.y, y1: B.b.y1 + pos.y };
    sheet.placed.push({ type: ti, rot: rot, flip: !!flip, x: pos.x, y: pos.y, bb: bb, inst: inst });
    sheet.b = sheet.b ? {
      x0: Math.min(sheet.b.x0, bb.x0), x1: Math.max(sheet.b.x1, bb.x1),
      y0: Math.min(sheet.b.y0, bb.y0), y1: Math.max(sheet.b.y1, bb.y1)
    } : bb;
    sheet.area += this.types[ti].area;
  };

  // Generator: legt ein Individuum (Reihenfolge + Drehungen) aus, yield nach jedem Teil
  Nester.prototype.evaluate = function* (ind) {
    var sheets = [], unplaced = [], vac = 0, self0 = this;
    // fixierte Teile zuerst, exakt an ihrer Position (werden nicht geprüft/verschoben)
    for (var s0 = 0; s0 < this.pinnedSheets; s0++) sheets.push(this.newSheet(s0));
    this.instances.forEach(function (inst, ii) {
      if (!inst.pinned) return;
      var pn = inst.pinned;
      self0.commit(sheets[pn.sheet - 1], inst.type, pn.rot,
        { x: Math.round(pn.x * SCALE), y: Math.round(pn.y * SCALE) }, ii, inst.flip);
    });
    for (var g = 0; g < ind.order.length; g++) {
      var inst = this.instances[ind.order[g]];
      var rots = [ind.rots[ind.order[g]]].concat(inst.rotations.filter(function (r) { return r !== ind.rots[ind.order[g]]; }));
      var done = false;
      for (var ri = 0; ri < rots.length && !done; ri++) {
        for (var s = 0; s < sheets.length && !done; s++) {
          var rr = this.mapRot(inst, rots[ri], sheets[s].gm);
          var pos = this.tryPlace(sheets[s], inst.type, rr, inst.flip);
          if (pos) { this.commit(sheets[s], inst.type, rr, pos, ind.order[g], inst.flip); done = true; }
        }
      }
      // neues Blatt in Füllreihenfolge: definierte Blätter (Abschnitte) bleiben auch leer stehen, damit spätere
      // Teile sie noch füllen können; ein Standardblatt, auf das das Teil nicht passt, wird verworfen
      while (!done && sheets.length < this.maxSheets) {
        var ns = this.newSheet(sheets.length), isDef = sheets.length < this.defs.length;
        for (ri = 0; ri < rots.length && !done; ri++) {
          var r2 = this.mapRot(inst, rots[ri], ns.gm);
          var p2 = this.tryPlace(ns, inst.type, r2, inst.flip);
          if (p2) { this.commit(ns, inst.type, r2, p2, ind.order[g], inst.flip); done = true; }
        }
        if (done || isDef) sheets.push(ns);
        if (!isDef) break;
      }
      if (!done) unplaced.push(ind.order[g]);
      yield g;
    }
    // Fitness
    var S = SCALE, self = this, grav = 0;
    sheets = sheets.filter(function (sh) { return sh.placed.length; });
    sheets.forEach(function (sh, i) {
      var ux0 = sh.gm.frame * S;
      var gv = ((sh.b.x1 - ux0) * 2 + (sh.b.y1 - ux0)) / S;
      grav += (i === sheets.length - 1) ? gv : 0.1 * gv;
      sh.placed.forEach(function (pl) {
        var t = self.types[pl.type];
        if (t.smallFactor > 0) {
          var c = rotPts(pl.flip ? mirrorPts([t.centroid]) : [t.centroid], pl.rot)[0];
          var dx = c[0] + pl.x / S - sh.gm.W / 2, dy = c[1] + pl.y / S - sh.gm.H / 2;
          vac += t.smallFactor * Math.sqrt(dx * dx + dy * dy);
        }
      });
    });
    var unArea = unplaced.reduce(function (s, i) { return s + self.types[self.instances[i].type].area; }, 0);
    ind.fitness = unplaced.length * 1e10 + unArea + sheets.length * 1e7 + grav + this.vacW * vac;
    ind.sheets = sheets; ind.unplaced = unplaced; ind.vacuumPenalty = vac;
    return ind;
  };

  // ---------------------------------------------------------------- GA
  Nester.prototype.initialIndividual = function () {
    var self = this;
    var order = this.genes.slice();
    order.sort(function (a, b) {
      return self.types[self.instances[b].type].area - self.types[self.instances[a].type].area;
    });
    var rots = this.instances.map(function (inst) { return inst.rotations[0]; });
    return { order: order, rots: rots };
  };
  Nester.prototype.mutate = function (ind, rate) {
    var o = ind.order.slice(), r = ind.rots.slice(), self = this;
    for (var i = 0; i < o.length; i++) {
      if (this.rand() < rate && i + 1 < o.length) { var tmp = o[i]; o[i] = o[i + 1]; o[i + 1] = tmp; }
      if (this.rand() < rate) {
        var rs = self.instances[o[i]].rotations;
        r[o[i]] = rs[Math.floor(this.rand() * rs.length)];
      }
    }
    return { order: o, rots: r };
  };
  Nester.prototype.crossover = function (a, b) {
    var n = a.order.length, cut = Math.round(Math.min(Math.max(this.rand(), 0.1), 0.9) * Math.max(0, n - 1));
    var nInst = this.instances.length;
    function child(p, q) {
      var head = p.order.slice(0, cut), seen = {};
      head.forEach(function (i) { seen[i] = 1; });
      var o = head.concat(q.order.filter(function (i) { return !seen[i]; }));
      var r = new Array(nInst);
      for (var k = 0; k < nInst; k++) r[k] = (seen[k] ? p.rots[k] : q.rots[k]);
      return { order: o, rots: r };
    }
    return [child(a, b), child(b, a)];
  };
  Nester.prototype.pick = function (pop) {     // rangbasierte Auswahl
    var n = pop.length, r = this.rand(), w = 0, tot = n * (n + 1) / 2;
    for (var i = 0; i < n; i++) { w += (n - i) / tot; if (r < w) return pop[i]; }
    return pop[n - 1];
  };
  Nester.prototype.nextGeneration = function () {
    var pop = this.population.slice().sort(function (a, b) { return a.fitness - b.fitness; });
    var next = [pop[0]];                // Elitismus (bereits bewertet)
    while (next.length + this.pending.length < this.popSize) {
      var kids = this.crossover(this.pick(pop), this.pick(pop));
      this.pending.push(this.mutate(kids[0], this.mutRate));
      if (next.length + this.pending.length < this.popSize) this.pending.push(this.mutate(kids[1], this.mutRate));
    }
    this.population = next;
    this.stats.generation++;
  };

  // 2.0.5: Fixierungen vor dem Rechnen prüfen – ein fixiertes Teil außerhalb des nutzbaren Bereichs (Rand/
  // Blattmaß geändert) oder in Kollision mit einem bereits angenommenen fixierten Teil wird NICHT mehr ungeprüft
  // übernommen, sondern als freies Gen neu angeordnet (Drehung/Wenden/Faser bleiben). Ergebnis: this.demoted.
  Nester.prototype.validatePins = function () {
    var self = this, bySheet = {};
    this.demoted = [];
    this.instances.forEach(function (inst) {
      if (!inst.pinned) return;
      var pn = inst.pinned, t = self.types[inst.type];
      if (!(isFinite(pn.x) && isFinite(pn.y) && isFinite(pn.rot))) { inst.pinned = null; self.demoted.push(inst.key); return; }
      var gm = self.geom(pn.sheet - 1), rec = self.partRecord(t, inst, pn.rot, inst.flip, pn.x, pn.y, gm);
      var acc = bySheet[pn.sheet] || (bySheet[pn.sheet] = []);
      var c = layoutConflicts(sheetRec(gm, self.spacing), acc.concat([rec]), rec.key);
      if (c[rec.key]) { inst.pinned = null; self.demoted.push(inst.key); return; }
      acc.push(rec);
    });
    return this.demoted;
  };

  Nester.prototype.start = function () {
    this.t0 = Date.now();
    var self = this;
    this.genes = [];
    this.pinnedSheets = 0;
    try { this.validatePins(); } catch (e) { this.demoted = this.demoted || []; }
    this.instances.forEach(function (inst, i) {
      if (inst.pinned) self.pinnedSheets = Math.max(self.pinnedSheets, inst.pinned.sheet);
      else self.genes.push(i);
    });
    if (!this.instances.length) { this.done = true; return; }
    if (!this.genes.length) { this.popSize = 1; this.maxGen = 1; }
    var first = this.initialIndividual();
    this.pending = [first];
    for (var i = 1; i < this.popSize; i++) this.pending.push(this.mutate(first, 0.3));
  };

  // Rechnet maximal budgetMs; gibt true zurück, solange weitere Arbeit ansteht.
  Nester.prototype.step = function (budgetMs) {
    if (this.t0 === null) this.start();
    if (this.done) return false;
    var tEnd = Date.now() + (budgetMs || 30);
    while (Date.now() < tEnd) {
      if (!this.current) {
        if (!this.pending.length) {
          if (this.stats.generation + 1 >= this.maxGen || Date.now() - this.t0 > this.timeLimit) {
            this.done = true; return false;
          }
          this.nextGeneration();
          continue;
        }
        var ind = this.pending.shift();
        this.current = { ind: ind, gen: this.evaluate(ind) };
      }
      var r = this.current.gen.next();
      if (r.done) {
        var ev = this.current.ind;
        this.current = null;
        this.stats.evaluated++;
        this.population.push(ev);
        if (!this.best || ev.fitness < this.best.fitness) { this.best = ev; this.bestChanged = true; }
        if (Date.now() - this.t0 > this.timeLimit && this.best) { this.done = true; return false; }
      }
    }
    return true;
  };

  Nester.prototype.stop = function () { this.done = true; };

  // Ergebnis als JSON-fähiges Objekt (mm, Grad)
  Nester.prototype.result = function () {
    var b = this.best, self = this, S = SCALE;
    if (!b) return null;
    var totalArea = 0, usedArea = 0, usedDefs = {};
    var sheets = b.sheets.map(function (sh, si) {
      var gm = sh.gm, sa = Math.max(1e-9, gm.W * gm.H);
      if (gm !== self.stdSheet) usedDefs[self.defs.indexOf(gm)] = 1;
      var parts = sh.placed.map(function (pl) {
        var t = self.types[pl.type], inst = self.instances[pl.inst];
        totalArea += t.area;
        if (inst.pinned)    // fixierte Teile exakt zurückgeben (intern 0,01-mm-Raster)
          return self.partRecord(t, inst, inst.pinned.rot, pl.flip, inst.pinned.x, inst.pinned.y, gm);
        return self.partRecord(t, inst, pl.rot, pl.flip, pl.x / S, pl.y / S, gm);
      });
      usedArea += sa;
      return { index: si + 1, utilisation: sh.area / sa, parts: parts, def: sheetDefOut(gm) };
    });
    // definierte Abschnitte ohne Teile bleiben (leer, am Ende) – ihre Definition geht nicht verloren
    this.defs.forEach(function (gm, k) {
      if (!usedDefs[k] && gm.kind === 'abschnitt')
        sheets.push({ index: sheets.length + 1, utilisation: 0, parts: [], def: sheetDefOut(gm) });
    });
    var unplaced = {};
    b.unplaced.forEach(function (i) {
      var t = self.types[self.instances[i].type];
      unplaced[t.id] = unplaced[t.id] || { id: t.id, name: t.name, count: 0, keys: [] };
      unplaced[t.id].count++;
      unplaced[t.id].keys.push(self.instances[i].key);
    });
    return {
      sheet: { length: this.W, width: this.H, frame: this.frame, spacing: this.spacing, grain: this.job.sheet.grain },
      rotStep: this.rotStep,
      sheets: sheets,
      unplaced: Object.keys(unplaced).map(function (k) { return unplaced[k]; }),
      excluded: Object.keys(this.job.overrides || {}).filter(function (k) { return self.job.overrides[k].excluded; }),
      utilisation: usedArea ? totalArea / usedArea : 0,
      fitness: b.fitness, vacuumPenalty: b.vacuumPenalty, demoted: (this.demoted || []).slice(),
      generation: this.stats.generation, evaluated: this.stats.evaluated, nfpCount: this.stats.nfp,
      elapsed: (Date.now() - this.t0) / 1000
    };
  };

  // Datensatz eines platzierten Teils (mm, Grad) – auch für manuelle Änderungen in der Palette
  Nester.prototype.partRecord = function (t, inst, rot, flip, x, y, gm) {
    var c = placePts([t.centroid], rot, flip, x, y)[0];
    gm = gm || this.stdSheet;
    return {
      key: inst.key, id: t.id, name: t.name, copy: inst.copy, rot: rot, flip: !!flip, x: x, y: y,
      area: t.area, cx: c[0], cy: c[1], dist: Math.hypot(c[0] - gm.W / 2, c[1] - gm.H / 2),
      grain: inst.ignoreGrain ? 'frei' : inst.grainMode, grainMode: inst.grainMode, ignoreGrain: !!inst.ignoreGrain,
      grainAngle: t.grainAngle, pinned: !!inst.pinned, hasHoles: t.hasHoles,
      fixRot: (inst.fixRot !== null && inst.fixRot !== undefined) ? inst.fixRot : null,
      small: t.smallFactor >= 1, smallFactor: t.smallFactor, critical: t.critical,
      outer: placePts(t.outer, rot, flip, x, y),
      holes: t.holes.map(function (h) { return placePts(h, rot, flip, x, y); }),
      // Wenden tauscht oben/unten
      features: (t.features || []).map(function (f) {
        return { side: flip ? (f.side === 'top' ? 'bottom' : 'top') : f.side, kind: f.kind, depth: f.depth,
                 pts: placePts(f.pts, rot, flip, x, y),
                 islands: (f.islands || []).map(function (h) { return placePts(h, rot, flip, x, y); }) };
      })
    };
  };
  Nester.prototype.instanceByKey = function (key) {
    for (var i = 0; i < this.instances.length; i++) if (this.instances[i].key === key) return this.instances[i];
    return null;
  };
  // Freie Position auf einem Blatt mit vorhandenen Teilen (Layout-Datensätze) suchen
  Nester.prototype.findSpot = function (sheetParts, id, rots, flip, gm) {
    var sh = this.newSheet(gm || this.stdSheet), self = this, ti = this.typeById[id] ? this.typeById[id].idx : -1;
    if (ti < 0) return null;
    sheetParts.forEach(function (p) {
      var t = self.typeById[p.id];
      if (t) self.commit(sh, t.idx, p.rot, { x: Math.round(p.x * SCALE), y: Math.round(p.y * SCALE) }, -1, p.flip);
    });
    for (var i = 0; i < rots.length; i++) {
      var pos = this.tryPlace(sh, ti, rots[i], flip);
      if (pos) return { rot: rots[i], x: pos.x / SCALE, y: pos.y / SCALE };
    }
    return null;
  };

  // Kollisionsprüfung eines Layouts (reale Konturen in mm). Liefert {key: 'Abstand'|'Rand'}.
  // only: optional Schlüssel – nur Paare mit diesem Teil prüfen (schnell beim Ziehen)
  function layoutConflicts(sheet, parts, only) {
    var s = sheet.spacing || 0, f = sheet.frame || 0, tol = 0.02, EPS = 0.05 * SCALE * SCALE;
    var res = {}, inf = parts.map(function (p) {
      var g = inflate(p.outer, p.holes, Math.max(0, s / 2 - tol));
      var paths = g ? [orient(toPath(g.outer), true)].concat(g.holes.map(function (h) { return orient(toPath(h), false); })) : [];
      return { p: p, paths: paths, b: paths.length ? boundsOf(paths[0]) : null };
    });
    var rect = [[{ X: f * SCALE, Y: f * SCALE }, { X: (sheet.length - f) * SCALE, Y: f * SCALE },
      { X: (sheet.length - f) * SCALE, Y: (sheet.width - f) * SCALE }, { X: f * SCALE, Y: (sheet.width - f) * SCALE }]];
    function area(paths) { return paths.reduce(function (a, q) { return a + C.Area(q); }, 0); }
    inf.forEach(function (a) {
      if (!a.b || (only && a.p.key !== only)) return;
      if (area(difference([a.paths[0]], rect)) > EPS) res[a.p.key] = 'Rand';
    });
    for (var i = 0; i < inf.length; i++) {
      for (var j = i + 1; j < inf.length; j++) {
        var A = inf[i], B = inf[j];
        if (!A.b || !B.b) continue;
        if (only && A.p.key !== only && B.p.key !== only) continue;
        if (A.b.x1 < B.b.x0 || B.b.x1 < A.b.x0 || A.b.y1 < B.b.y0 || B.b.y1 < A.b.y0) continue;
        if (area(clip(A.paths, B.paths, ClipperLib.ClipType.ctIntersection)) > EPS) {
          res[A.p.key] = res[A.p.key] || 'Abstand'; res[B.p.key] = res[B.p.key] || 'Abstand';
        }
      }
    }
    return res;
  }

  // 2.0.4: behaltene Lagen (Bearbeiten) prüfen: NICHT fixierte Teile mit Konflikt (Abstand/Rand/Überlappung –
  // z. B. nach geändertem Abstand, Randbesäumung, Kontur oder Plattenmaß) werden zum Neu-Platzieren freigegeben.
  // Fixierte Teile (manuell verschoben) bleiben, wo der Anwender sie hingelegt hat. Kleinste zuerst raus.
  // Rückgabe: Schlüssel der freigegebenen Teile (parts bleibt unverändert).
  function evictConflicts(sheet, parts) {
    var keep = parts.slice(), out = [];
    for (var guard = 0; guard < parts.length + 1; guard++) {
      var c = layoutConflicts(sheet, keep), cand = keep.filter(function (p) { return c[p.key] && !p.pinned; });
      if (!cand.length) break;
      var rand = cand.filter(function (p) { return c[p.key] === 'Rand'; });
      var pick = (rand.length ? rand : cand).reduce(function (a, b) { return (b.area || 0) < (a.area || 0) ? b : a; });
      out.push(pick.key);
      keep.splice(keep.indexOf(pick), 1);
    }
    return out;
  }

  // ================================================================ Manuelle Platzierung (Sperrzonen)
  // Sperrzone um jedes andere Teil = Teileabstand, zum Rand = Randbesäumung + ½ Teileabstand (wie die
  // automatische Anordnung und die Konfliktprüfung). Gleiche Minkowski-/NFP-Logik wie die Engine, aber exakt:
  //   {p : Abstand(A, B + p) < s}  =  NFP_roh(A, B)  ⊕  Kreis(s)
  // d. h. NFP der echten Konturen (inkl. Löcher in beide Richtungen) und danach EIN Offset um den Abstand.
  // Das ist mathematisch identisch mit dem NFP der um ½ s aufgedickten Teile, aber viel schneller (wenige
  // Punkte im Minkowski-Schritt) und ohne den Sicherheitszuschlag der Automatik: Anschieben auf Abstand + 0,01 mm.
  var MAN_ARC = 0.005;    // mm Bogen-Toleranz des Offsets (Sehnen liegen höchstens so weit innen)
  var MAN_EXTRA = 0.01;   // mm Zuschlag (Sehnenfehler + 0,01-mm-Raster)
  function offsetPaths(paths, deltaUnits) {
    if (!deltaUnits) return paths;
    var co = new ClipperLib.ClipperOffset(2, MAN_ARC * SCALE), sol = new ClipperLib.Paths();
    co.AddPaths(paths, ClipperLib.JoinType.jtRound, ClipperLib.EndType.etClosedPolygon);
    co.Execute(sol, deltaUnits);
    return sol;
  }
  // gm: Blatt (resolveSheet) – Maß/Besäumung je Rohteil (1.10.0); ohne gm das Standardformat.
  // Konturen und Sperrzonen-NFPs hängen nicht vom Blatt ab und liegen im gemeinsamen Speicher des Nesters.
  function Placer(nester, gm) {
    this.n = nester;
    gm = gm || nester.stdSheet || { W: nester.W, H: nester.H, frame: nester.frame };
    this.gm = gm;
    this.W = gm.W; this.H = gm.H; this.frame = gm.frame; this.spacing = nester.spacing;
    this.zone = this.spacing > 0 ? this.spacing + MAN_EXTRA : 0;       // mm, Teil zu Teil
    this.edge = this.frame + (this.spacing > 0 ? this.spacing / 2 + MAN_EXTRA / 2 : 0);   // mm, Teil zu Plattenkante
    var cache = nester._placerCache || (nester._placerCache = { shapes: {}, nfps: {}, stats: { nfp: 0, nfpHits: 0, feasible: 0 } });
    this.shapes = cache.shapes; this.nfps = cache.nfps;
    this.stats = cache.stats;
  }
  Placer.prototype.sheet = function () { return { length: this.W, width: this.H, frame: this.frame, spacing: this.spacing }; };
  // echte Kontur (0,01-mm-Raster), gedreht/gewendet, Bezugspunkt = Teil-Ursprung
  Placer.prototype.shape = function (id, rot, flip) {
    var key = id + '@' + norm360(rot) + (flip ? 'F' : '');
    var s = this.shapes[key];
    if (s) return s;
    var t = this.n.typeById[id];
    if (!t) return null;
    var tr = function (pts) { return rotPts(flip ? mirrorPts(pts) : pts, rot); };
    var outer = orient(toPath(tr(t.outer)), true);
    s = { key: key, outer: outer, holes: t.holes.map(function (h) { return orient(toPath(tr(h)), false); }), b: boundsOf(outer) };
    this.shapes[key] = s;
    return s;
  };
  // Sperrzone (NFP ⊕ Abstand) je Teilepaar/Drehung/Wenden zwischengespeichert (Ziehen bleibt flüssig)
  Placer.prototype.nfp = function (A, B) {
    var key = A.key + '|' + B.key, hit = this.nfps[key];
    if (hit) { this.stats.nfpHits++; return hit; }
    this.stats.nfp++;
    hit = { paths: offsetPaths(nfpCore(A, B, true), Math.round(this.zone * SCALE)) };
    hit.b = hit.paths.length ? boundsOf([].concat.apply([], hit.paths)) : null;
    this.nfps[key] = hit;
    return hit;
  };
  // Zulässige Lagen (Bezugspunkt, 0,01 mm) für Teil m = {key,id,rot,flip} zwischen den übrigen Teilen
  Placer.prototype.feasible = function (parts, m) {
    var B = this.shape(m.id, m.rot, m.flip), S = SCALE, e = Math.round(this.edge * S);
    if (!B) return { paths: [], B: null, empty: true };
    var x0 = e - B.b.x0, x1 = Math.round(this.W * S) - e - B.b.x1;
    var y0 = e - B.b.y0, y1 = Math.round(this.H * S) - e - B.b.y1;
    this.stats.feasible++;
    if (x0 > x1 || y0 > y1) return { paths: [], B: B, empty: true };
    var ifp = [[{ X: x0, Y: y0 }, { X: x1, Y: y0 }, { X: x1, Y: y1 }, { X: x0, Y: y1 }]];
    var all = [], self = this;
    parts.forEach(function (p) {
      if (p.key === m.key) return;
      var A = self.shape(p.id, p.rot, p.flip);
      if (!A) return;
      var nf = self.nfp(A, B), dx = Math.round(p.x * S), dy = Math.round(p.y * S);
      if (!nf.b || nf.b.x1 + dx < x0 || nf.b.x0 + dx > x1 || nf.b.y1 + dy < y0 || nf.b.y0 + dy > y1) return;
      for (var k = 0; k < nf.paths.length; k++) all.push(translatePath(nf.paths[k], dx, dy));
    });
    var feas = all.length ? difference(ifp, all) : ifp;
    return { paths: feas, B: B, empty: !feas.length };
  };
  // Winding-Regel (nonzero) wie Clipper; Rand gilt als zulässig (exakt auf Abstand)
  function insideRegion(paths, X, Y) {
    var w = 0, pt = { X: X, Y: Y };
    for (var i = 0; i < paths.length; i++) {
      var r = C.PointInPolygon(pt, paths[i]);
      if (r === -1) return true;
      if (r) w += C.Orientation(paths[i]) ? 1 : -1;
    }
    return w !== 0;
  }
  function nearestOnBoundary(paths, X, Y) {
    var best = null, bd = Infinity;
    for (var i = 0; i < paths.length; i++) {
      var P = paths[i], n = P.length;
      for (var k = 0; k < n; k++) {
        var a = P[k], b = P[(k + 1) % n], vx = b.X - a.X, vy = b.Y - a.Y, ll = vx * vx + vy * vy;
        var t = ll ? ((X - a.X) * vx + (Y - a.Y) * vy) / ll : 0;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        var qx = a.X + t * vx, qy = a.Y + t * vy, d = (qx - X) * (qx - X) + (qy - Y) * (qy - Y);
        if (d < bd) { bd = d; best = { X: qx, Y: qy }; }
      }
    }
    return best;
  }
  // Wunschlage (mm) -> nächste zulässige Lage. {x,y,free (Wunsch war frei), ok (es gibt eine Lage), dist (mm)}
  Placer.prototype.project = function (feas, x, y) {
    var S = SCALE, X = x * S, Y = y * S;
    if (!feas || feas.empty) return { ok: false, free: false, x: x, y: y, dist: Infinity };
    if (insideRegion(feas.paths, X, Y)) return { ok: true, free: true, x: x, y: y, dist: 0 };
    var q = nearestOnBoundary(feas.paths, X, Y);
    return { ok: true, free: false, x: q.X / S, y: q.Y / S, dist: Math.hypot(q.X - X, q.Y - Y) / S };
  };
  // Zieh-Sitzung: NFPs erst berechnen, wenn das Teil in die Nähe eines Nachbarn kommt (Rahmen-Test), und
  // die zulässige Fläche nur in einem Fenster um die Maus bilden. Bei jeder Mausbewegung: freier Raum = nur
  // Punkt-in-Polygon-Tests; in einer Zone: nächste zulässige Lage im Fenster (Fenster wächst bei Bedarf).
  Placer.prototype.session = function (parts, m) {
    var self = this, S = SCALE, B = this.shape(m.id, m.rot, m.flip), z = Math.round(this.zone * S);
    var ses = { m: m, B: B, empty: true, entries: [], stats: { blocked: 0, windows: 0 } };
    if (!B) return ses;
    var e = Math.round(this.edge * S);
    var ifp = { x0: e - B.b.x0, x1: Math.round(this.W * S) - e - B.b.x1, y0: e - B.b.y0, y1: Math.round(this.H * S) - e - B.b.y1 };
    ses.ifp = ifp;
    ses.empty = ifp.x0 > ifp.x1 || ifp.y0 > ifp.y1;
    parts.forEach(function (p) {
      if (p.key === m.key) return;
      var A = self.shape(p.id, p.rot, p.flip);
      if (!A) return;
      var dx = Math.round(p.x * S), dy = Math.round(p.y * S);
      ses.entries.push({ A: A, dx: dx, dy: dy, nf: null,
        b: { x0: A.b.x0 - B.b.x1 - z + dx, x1: A.b.x1 - B.b.x0 + z + dx, y0: A.b.y0 - B.b.y1 - z + dy, y1: A.b.y1 - B.b.y0 + z + dy } });
    });
    function paths(en) {
      if (!en.nf) {
        var nf = self.nfp(en.A, B);
        en.nf = nf.paths.map(function (pa) { return translatePath(pa, en.dx, en.dy); });
      }
      return en.nf;
    }
    function hits(b, X0, Y0, X1, Y1) { return !(b.x1 < X0 || b.x0 > X1 || b.y1 < Y0 || b.y0 > Y1); }
    var span = Math.max(B.b.x1 - B.b.x0, B.b.y1 - B.b.y0, 50 * S);
    ses.project = function (x, y) {
      var X = x * S, Y = y * S;
      if (ses.empty) return { ok: false, free: false, x: x, y: y, dist: Infinity };
      var free = X >= ifp.x0 && X <= ifp.x1 && Y >= ifp.y0 && Y <= ifp.y1;
      for (var i = 0; free && i < ses.entries.length; i++) {
        var en = ses.entries[i];
        if (X < en.b.x0 || X > en.b.x1 || Y < en.b.y0 || Y > en.b.y1) continue;
        var pp = paths(en);
        for (var k = 0, w = 0; k < pp.length; k++) {
          var r0 = C.PointInPolygon({ X: X, Y: Y }, pp[k]);
          if (r0 === -1) { w = 0; break; }                 // genau auf der Zone = zulässig
          if (r0) w += C.Orientation(pp[k]) ? 1 : -1;
        }
        if (w !== 0) free = false;
      }
      if (free) return { ok: true, free: true, x: x, y: y, dist: 0 };
      ses.stats.blocked++;
      var r = span;
      for (;;) {
        ses.stats.windows++;
        var wx0 = Math.max(ifp.x0, X - r), wx1 = Math.min(ifp.x1, X + r), wy0 = Math.max(ifp.y0, Y - r), wy1 = Math.min(ifp.y1, Y + r);
        var whole = wx0 === ifp.x0 && wx1 === ifp.x1 && wy0 === ifp.y0 && wy1 === ifp.y1;
        var feas = [];
        if (wx0 <= wx1 && wy0 <= wy1) {
          if (wx1 - wx0 < 2) wx1 = wx0 + 2;
          if (wy1 - wy0 < 2) wy1 = wy0 + 2;
          var all = [];
          ses.entries.forEach(function (en) {
            if (!hits(en.b, wx0, wy0, wx1, wy1)) return;
            var pp = paths(en);
            for (var k = 0; k < pp.length; k++) all.push(pp[k]);
          });
          var win = [[{ X: wx0, Y: wy0 }, { X: wx1, Y: wy0 }, { X: wx1, Y: wy1 }, { X: wx0, Y: wy1 }]];
          feas = all.length ? difference(win, all) : win;
        }
        if (feas.length) {
          var q = nearestOnBoundary(feas, X, Y), d = Math.hypot(q.X - X, q.Y - Y);
          // Punkte mit d < r liegen sicher nicht auf dem künstlichen Fensterrand (außer am echten Plattenrand)
          if (d < r || whole) return { ok: true, free: false, x: q.X / S, y: q.Y / S, dist: d / S };
        } else if (whole) {
          return { ok: false, free: false, x: x, y: y, dist: Infinity };
        }
        r *= 2;
      }
    };
    return ses;
  };
  Placer.prototype.place = function (parts, m, x, y) { return this.session(parts, m).project(x, y); };
  // Sperrzonen der Nachbarn zum Anzeigen (reale Kontur + Teileabstand) und Randlinie (Besäumung + ½ Abstand)
  Placer.prototype.zoneOutline = function (p) {
    var g = inflate(p.outer, p.holes, this.spacing);
    return g ? [g.outer].concat(g.holes) : [];
  };
  Placer.prototype.edgeLine = function () {
    var e = this.edge;
    return [[e, e], [this.W - e, e], [this.W - e, this.H - e], [e, this.H - e]];
  };
  // Verstöße eines Blatts (gleiche Prüfung wie die Konfliktanzeige)
  Placer.prototype.violations = function (parts) { return layoutConflicts(this.sheet(), parts); };
  // „Abstände korrigieren“: verletzende Teile minimal verschieben (nächste zulässige Lage, Drehung bleibt).
  // Gierig: zuerst bewegliche Teile (mit Strg übersteuerte und nicht fixierte), danach fixierte; innerhalb
  // einer Gruppe immer das Teil mit dem kürzesten Weg. rebuild(p, x, y) liefert den neuen Datensatz.
  Placer.prototype.correct = function (parts, rebuild) {
    var self = this, moved = [], failed = [], sheet = this.sheet();
    function idx(key) { for (var k = 0; k < parts.length; k++) if (parts[k].key === key) return k; return -1; }
    for (var guard = 0; guard < parts.length * 2 + 2; guard++) {
      var bad = layoutConflicts(sheet, parts), keys = Object.keys(bad).filter(function (k) { return failed.indexOf(k) < 0; });
      if (!keys.length) break;
      var best = null;
      [0, 1].forEach(function (cls) {
        if (best) return;
        keys.forEach(function (k) {
          var p = parts[idx(k)];
          if (((p.pinned && !p.forced) ? 1 : 0) !== cls) return;
          var r = self.place(parts, p, p.x, p.y);
          if (!r.ok) { failed.push(k); return; }
          if (r.free) r.dist = 0;
          if (!best || r.dist < best.r.dist) best = { p: p, r: r };
        });
      });
      if (!best) continue;
      var p = best.p, r = best.r;
      if (r.free) { failed.push(p.key); continue; }     // Konflikt nur nach der strengeren Prüfung – nicht anfassen
      parts[idx(p.key)] = rebuild(p, r.x, r.y);
      moved.push({ key: p.key, dx: r.x - p.x, dy: r.y - p.y, dist: r.dist });
    }
    var rest = Object.keys(layoutConflicts(sheet, parts));
    return { moved: moved, failed: failed.filter(function (k) { return rest.indexOf(k) >= 0; }), remaining: rest };
  };

  // Synchroner Komplettlauf (node/Tests)
  Nester.prototype.runSync = function () {
    while (this.step(1000)) { /* weiter */ }
    return this.result();
  };

  return {
    Nester: Nester, inflate: inflate, simplifyClosed: simplifyClosed, areaMM: areaMM,
    layoutConflicts: layoutConflicts, evictConflicts: evictConflicts, Placer: Placer, placePts: placePts, rotPts: rotPts, mirrorPts: mirrorPts, centroid: centroidMM,
    resolveSheet: resolveSheet, sheetDefOut: sheetDefOut, sheetRec: sheetRec, zeroPoint: zeroPoint, zeroText: zeroText,
    sheetLabel: sheetLabel, turnPose: turnPose, toMaterial: toMaterial, deNum: deNum, ZERO_KEYS: ZERO_KEYS,
    ZERO_CORNER_TXT: ZERO_CORNER_TXT, ZERO_SIDE_TXT: ZERO_SIDE_TXT, norm360: norm360,
    SCALE: SCALE, version: '1.1.0'
  };
}));
