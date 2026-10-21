// DWG_Entourage.jsx - furniture, fixtures, plants, trees and people at the right drawing scale,
// as Illustrator symbols, plus a graphic scale bar and north arrow.
//
// Run it in your drawing. It reads the scale CleanMake2D stored in the file (or asks), then:
//   * makes one symbol per item in DWG_Entourage.json (Symbols panel: "Chair 18in @1/8in" ...)
//   * lays a library sheet out on the pasteboard right of the artboard (layer "_LIBRARY",
//     non-printing) - Alt/Option-drag copies from there onto ENTOURAGE or PEOPLE
//   * optionally drops a scale bar + north arrow on ANNOTATION.
// Everything is drawn at real size for that scale, so a chair is 18" wide on paper at 1/8" = 1'.
// Re-running with another scale adds a second set (names carry the scale).
//
// DWG_Entourage.json format (inches, real size, y up, origin = item centre):
//   {"rr":[x,y,w,h,radius]} rounded rect   {"r":[x,y,w,h]} rect      {"c":[cx,cy,r]} circle
//   {"e":[cx,cy,rx,ry]} ellipse             {"l":[x1,y1,x2,y2]} line   {"pl":[x1,y1,x2,y2,...]} polyline
//   {"pg":[x1,y1,...]} polygon              {"p":"M x y L x y C x1 y1 x2 y2 x y Z"} path (absolute)
//   {"use":"chair","at":[x,y],"rot":deg,"s":scale}  re-use a def
//   style keys: "f": "w" white (default for closed shapes) | "n" none | "k" black | 0-100 gray,
//               "s": "n" no stroke, "sw": stroke-weight multiplier. Later parts draw on top.
#target illustrator
#include "DWG_Common.jsxinc"

var ENT_STROKE = 0.25;     // pt, base line weight for all entourage
var ENT_GRAY = 100;        // stroke % black
var ENT_ERRORS = [];

(function () {
    if (!app.documents.length) { alert("Open a drawing first."); return; }
    var doc = app.activeDocument;
    var auto = $.global.DWG_AUTO;          // set by automation (run_illustrator.ps1 / tests)
    $.global.DWG_AUTO = undefined;         // one-shot: never leak into your next manual run
    var s = dwgLoadSettings(doc);
    var reg = dwgReadRegInfo(doc);
    var opts = {
        scale: reg.scale || s.scale || "1/8\" = 1'-0\"",
        symbols: true,
        scaleBar: true
    };
    if (auto) { for (var a in auto) opts[a] = auto[a]; }
    else if (!entDialog(opts)) return;

    var ratio = dwgParseRatio(opts.scale);
    if (!ratio) { alert("Couldn't read the scale \"" + opts.scale + "\". Try 1/8\" = 1'-0\", 1/4 or 1:100."); return; }

    var made = 0;
    if (opts.symbols) {
        var data = entLoadData();
        if (!data) return;
        made = entBuildLibrary(doc, data, ratio, opts.scale);
    }
    if (opts.scaleBar) entScaleBarAndNorth(doc, ratio, opts.scale);
    dwgSetActive(doc, "ENTOURAGE");

    var msg = (ENT_ERRORS.length ? "Skipped (check DWG_Entourage.json):\n  " + ENT_ERRORS.join("\n  ") + "\n\n" : "") +
              (opts.symbols ? made + " symbols at " + opts.scale + " (Symbols panel, and the _LIBRARY sheet right of the page).\n" : "") +
              (opts.scaleBar ? "Scale bar + north arrow on ANNOTATION (bottom left)." : "");
    if (auto) $.global.DWG_RESULT = msg; else alert(msg);
})();

function entDialog(o) {
    var w = new Window("dialog", "DWG Entourage");
    w.alignChildren = "fill";
    var g = w.add("group");
    g.add("statictext", undefined, "Drawing scale:");
    var t = g.add("edittext", undefined, o.scale); t.characters = 18;
    w.add("statictext", undefined, "e.g. 1/8\" = 1'-0\"   1/4   3/16   1:100   (read from the Rhino export when available)");
    var c1 = w.add("checkbox", undefined, "Build furniture / fixtures / plants / people symbols"); c1.value = o.symbols;
    var c2 = w.add("checkbox", undefined, "Add a graphic scale bar + north arrow"); c2.value = o.scaleBar;
    var b = w.add("group"); b.alignment = "right";
    b.add("button", undefined, "Cancel", { name: "cancel" });
    b.add("button", undefined, "OK", { name: "ok" });
    if (w.show() !== 1) return false;
    o.scale = t.text; o.symbols = c1.value; o.scaleBar = c2.value;
    return true;
}

function entLoadData() {
    var f = new File(new File($.fileName).parent + "/DWG_Entourage.json");
    if (!f.exists) { alert("DWG_Entourage.json not found next to this script:\n" + f.fsName); return null; }
    f.encoding = "UTF-8"; f.open("r"); var txt = f.read(); f.close();
    try { return eval("(" + txt + ")"); }
    catch (e) { alert("DWG_Entourage.json has an error: " + e); return null; }
}

// --- geometry: affine [a b c d e f]:  x' = a x + c y + e,  y' = b x + d y + f -------------

function mMul(m, n) {   // m after n
    return [m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
            m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
            m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5]];
}
function mRot(deg) { var r = deg * Math.PI / 180, c = Math.cos(r), s = Math.sin(r); return [c, s, -s, c, 0, 0]; }
function mMove(x, y) { return [1, 0, 0, 1, x, y]; }
function mScale(k) { return [k, 0, 0, k, 0, 0]; }
function mAp(m, p) { return [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]]; }

var K = 0.5522847;

function pt(x, y) { return { a: [x, y], l: [x, y], r: [x, y] }; }
function ptH(x, y, lx, ly, rx, ry) { return { a: [x, y], l: [lx, ly], r: [rx, ry] }; }

function subpathsOf(p) {   // -> [{pts:[...], closed:bool}]
    var v, i, out = [];
    if (p.r) { v = p.r; return [{ closed: true, pts: [pt(v[0], v[1]), pt(v[0] + v[2], v[1]), pt(v[0] + v[2], v[1] + v[3]), pt(v[0], v[1] + v[3])] }]; }
    if (p.rr) {
        v = p.rr; var x = v[0], y = v[1], w = v[2], h = v[3], r = Math.min(v[4], w / 2, h / 2), k = K * r;
        if (r <= 0) return subpathsOf({ r: [x, y, w, h] });
        return [{ closed: true, pts: [
            ptH(x + r, y, x + r - k, y, x + r, y), ptH(x + w - r, y, x + w - r, y, x + w - r + k, y),
            ptH(x + w, y + r, x + w, y + r - k, x + w, y + r), ptH(x + w, y + h - r, x + w, y + h - r, x + w, y + h - r + k),
            ptH(x + w - r, y + h, x + w - r + k, y + h, x + w - r, y + h), ptH(x + r, y + h, x + r, y + h, x + r - k, y + h),
            ptH(x, y + h - r, x, y + h - r + k, x, y + h - r), ptH(x, y + r, x, y + r, x, y + r - k)] }];
    }
    if (p.c || p.e) {
        v = p.c ? [p.c[0], p.c[1], p.c[2], p.c[2]] : p.e;
        var cx = v[0], cy = v[1], rx = v[2], ry = v[3];
        return [{ closed: true, pts: [
            ptH(cx + rx, cy, cx + rx, cy - K * ry, cx + rx, cy + K * ry),
            ptH(cx, cy + ry, cx + K * rx, cy + ry, cx - K * rx, cy + ry),
            ptH(cx - rx, cy, cx - rx, cy + K * ry, cx - rx, cy - K * ry),
            ptH(cx, cy - ry, cx - K * rx, cy - ry, cx + K * rx, cy - ry)] }];
    }
    if (p.l) return [{ closed: false, pts: [pt(p.l[0], p.l[1]), pt(p.l[2], p.l[3])] }];
    if (p.pl || p.pg) {
        v = p.pl || p.pg; var pts = [];
        for (i = 0; i + 1 < v.length; i += 2) pts.push(pt(v[i], v[i + 1]));
        return [{ closed: !!p.pg, pts: pts }];
    }
    if (p.p) return parsePath(p.p);
    return out;
}

function parsePath(d) {
    var tok = d.replace(/([MLCZmlcz])/g, " $1 ").replace(/,/g, " ").split(/\s+/), i = 0, cmd = "", subs = [], cur = null;
    function num() { return parseFloat(tok[i++]); }
    while (i < tok.length) {
        var t = tok[i];
        if (t === "") { i++; continue; }
        if (/^[MLCZmlcz]$/.test(t)) { cmd = t.toUpperCase(); i++; if (cmd === "Z") { if (cur) { closeSub(cur); cur = null; } } continue; }
        if (cmd === "M") { cur = { closed: false, pts: [] }; subs.push(cur); var x = num(), y = num(); cur.pts.push(pt(x, y)); cmd = "L"; }
        else if (cmd === "L") { var lx = num(), ly = num(); cur.pts.push(pt(lx, ly)); }
        else if (cmd === "C") {
            var x1 = num(), y1 = num(), x2 = num(), y2 = num(), x3 = num(), y3 = num();
            cur.pts[cur.pts.length - 1].r = [x1, y1];
            cur.pts.push(ptH(x3, y3, x2, y2, x3, y3));
        } else i++;
    }
    return subs;
}

function closeSub(sp) {
    sp.closed = true;
    var a = sp.pts[0], b = sp.pts[sp.pts.length - 1];
    if (sp.pts.length > 1 && Math.abs(a.a[0] - b.a[0]) < 1e-6 && Math.abs(a.a[1] - b.a[1]) < 1e-6) {
        a.l = b.l; sp.pts.pop();
    }
}

function entDrawParts(group, parts, defs, m, depth) {
    for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        if (p.use) {
            if (!defs[p.use] || depth > 6) continue;
            var at = p.at || [0, 0];
            var mm = mMul(m, mMul(mMove(at[0], at[1]), mMul(mRot(p.rot || 0), mScale(p.s || 1))));
            entDrawParts(group, defs[p.use], defs, mm, depth + 1);
            continue;
        }
        var subs = subpathsOf(p);
        var closed = subs.length && subs[0].closed;
        for (var j = 0; j < subs.length; j++) entDrawSub(group, subs[j], p, m, closed);
    }
}

function entDrawSub(group, sp, p, m, closedShape) {
    var path = group.pathItems.add();
    var smooth = false;
    for (var q = 0; q < sp.pts.length; q++) {
        var P = sp.pts[q];
        if (P.l[0] !== P.a[0] || P.l[1] !== P.a[1] || P.r[0] !== P.a[0] || P.r[1] !== P.a[1]) { smooth = true; break; }
    }
    if (!smooth) {
        var arr = [];
        for (var k = 0; k < sp.pts.length; k++) arr.push(mAp(m, sp.pts[k].a));
        path.setEntirePath(arr);
    } else {
        for (var n = 0; n < sp.pts.length; n++) {
            var pp = path.pathPoints.add();
            pp.anchor = mAp(m, sp.pts[n].a);
            pp.leftDirection = mAp(m, sp.pts[n].l);
            pp.rightDirection = mAp(m, sp.pts[n].r);
            pp.pointType = PointType.CORNER;
        }
    }
    path.closed = sp.closed;
    var f = p.f !== undefined ? String(p.f) : (closedShape ? "w" : "n");
    if (f === "n") path.filled = false;
    else {
        var gv = f === "w" ? 0 : f === "k" ? 100 : parseFloat(f);
        if (isNaN(gv)) gv = 0;
        path.filled = true;
        path.fillColor = dwgGray(gv);
    }
    if (p.s === "n") path.stroked = false;
    else {
        path.stroked = true;
        path.strokeColor = dwgGray(ENT_GRAY);
        path.strokeWidth = ENT_STROKE * (p.sw || 1);
        path.strokeJoin = StrokeJoin.ROUNDENDJOIN;
        path.strokeCap = StrokeCap.ROUNDENDCAP;
    }
}

function entScaleTag(label) {
    return "@" + String(label).replace(/\s*=\s*1'-0"?/, "").replace(/"/g, "in").replace(/\s+/g, "");
}

function entFindSymbol(doc, name) {
    for (var i = 0; i < doc.symbols.length; i++) if (doc.symbols[i].name === name) return doc.symbols[i];
    return null;
}

function entBuildLibrary(doc, data, ratio, label) {
    var k = 72 / ratio;                         // pt per real inch
    var tag = entScaleTag(label);
    var lib = dwgFindLayer(doc, "_LIBRARY " + tag);
    if (!lib) { lib = doc.layers.add(); lib.name = "_LIBRARY " + tag; }
    lib.locked = false; lib.visible = true;
    doc.activeLayer = lib;
    while (lib.pageItems.length) lib.pageItems[0].remove();

    var ab = doc.artboards[doc.artboards.getActiveArtboardIndex()].artboardRect; // l, t, r, b
    var x0 = ab[2] + 72, y0 = ab[1], x = x0, y = y0, rowH = 0, maxW = 9 * 72;
    var lastCat = null, made = 0;
    var base = mScale(k);

    for (var i = 0; i < data.items.length; i++) {
        var it = data.items[i];
        if (it.cat !== lastCat) {                       // category header on a new row
            if (lastCat !== null) { y -= rowH + 30; x = x0; rowH = 0; }
            entLabel(lib, it.cat.toUpperCase(), x, y + 14, 7);
            lastCat = it.cat;
        }
        var name = it.name + " " + tag;
        var sym = entFindSymbol(doc, name);
        if (!sym) {
            var g = lib.groupItems.add();
            try {
                entDrawParts(g, it.parts, data.defs, base, 0);
            } catch (e) {
                g.remove();
                ENT_ERRORS.push(it.name + ": " + e + " (line " + e.line + ")");
                continue;
            }
            g.name = name;
            sym = doc.symbols.add(g);
            sym.name = name;
            g.remove();
            made++;
        }
        var inst = lib.symbolItems.add(sym);
        var w = inst.width, h = inst.height;
        if (x > x0 && x + w > x0 + maxW) { y -= rowH + 22; x = x0; rowH = 0; }
        inst.position = [x, y];
        entLabel(lib, it.name, x, y - h - 4, 4.5);
        x += Math.max(w, 30) + 18;
        rowH = Math.max(rowH, h);
    }
    lib.printable = false;
    return made;
}

function entLabel(layer, txt, x, y, size) {
    var t = layer.textFrames.add();
    t.contents = txt;
    t.textRange.characterAttributes.size = size;
    t.textRange.characterAttributes.fillColor = dwgGray(60);
    t.position = [x, y];
    return t;
}

function entScaleBarAndNorth(doc, ratio, label) {
    var L = dwgSetActive(doc, "ANNOTATION");
    for (var i = L.pageItems.length - 1; i >= 0; i--) {
        var nm = L.pageItems[i].name;
        if (nm === "DWG scale bar" || nm === "DWG north arrow") L.pageItems[i].remove();
    }
    var metric = dwgIsMetric(label);
    var unitIn = metric ? 39.3701 : 12;                // real inches per m / ft
    var ptPerUnit = unitIn * 72 / ratio;
    var cands = metric ? [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]
                       : [2, 4, 8, 10, 16, 20, 32, 40, 50, 64, 80, 100, 160, 200, 400, 800];
    var total = cands[0];
    for (var c = 0; c < cands.length; c++) if (cands[c] * ptPerUnit <= 2.5 * 72) total = cands[c];
    var ab = doc.artboards[doc.artboards.getActiveArtboardIndex()].artboardRect;
    var x = ab[0] + 36, y = ab[3] + 48, h = 3.5;

    var g = L.groupItems.add(); g.name = "DWG scale bar";
    var stops = [0, total / 4, total / 2, total];
    for (var s = 0; s < 3; s++) {
        var r = g.pathItems.rectangle(y, x + stops[s] * ptPerUnit, (stops[s + 1] - stops[s]) * ptPerUnit, h);
        r.stroked = true; r.strokeWidth = 0.35; r.strokeColor = dwgGray(100);
        r.filled = true; r.fillColor = dwgGray(s % 2 === 0 ? 100 : 0);
    }
    for (var t = 0; t < stops.length; t++) {
        var v = Math.round(stops[t] * 100) / 100;
        var tf = g.textFrames.add();
        tf.contents = v + (t === stops.length - 1 ? (metric ? " m" : "'") : "");
        tf.textRange.characterAttributes.size = 5.5;
        tf.position = [x + stops[t] * ptPerUnit - 2, y - h - 2];
    }
    var cap = g.textFrames.add();
    cap.contents = String(label).toUpperCase();
    cap.textRange.characterAttributes.size = 5.5;
    cap.position = [x, y + 11];

    var nx = x + total * ptPerUnit + 36, ny = y - h / 2, R = 9;
    var n = L.groupItems.add(); n.name = "DWG north arrow";
    var circle = n.pathItems.ellipse(ny + R, nx - R, 2 * R, 2 * R);
    circle.stroked = true; circle.strokeWidth = 0.35; circle.strokeColor = dwgGray(100); circle.filled = false;
    var tri = n.pathItems.add();
    tri.setEntirePath([[nx, ny + R + 4], [nx + 3.2, ny - R + 2], [nx, ny - R + 5], [nx - 3.2, ny - R + 2]]);
    tri.closed = true; tri.stroked = false; tri.filled = true; tri.fillColor = dwgGray(100);
    var nt = n.textFrames.add(); nt.contents = "N"; nt.textRange.characterAttributes.size = 6;
    nt.position = [nx - 2, ny + R + 12];
}
