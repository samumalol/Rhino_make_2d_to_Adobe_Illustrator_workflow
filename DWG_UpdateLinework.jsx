// DWG_UpdateLinework.jsx - swap fresh Rhino linework into your finished Illustrator drawing.
//
// You changed the model, re-ran CleanMake2D (Redraw) and got a new SVG. Open your master .ai
// (the one with furniture, people, text...), run this, and it:
//   1. opens the new SVG (it remembers where it came from - the path is stored in the file),
//   2. replaces ONLY the Rhino linework layers (CUT, POCHE, PROFILE, OUTLINE, EDGE, BEYOND...),
//   3. lines it up on the registration mark, re-applies the pen table, closes the SVG.
// ANNOTATION, PEOPLE, ENTOURAGE and TONE are never touched.
#target illustrator
#include "DWG_Common.jsxinc"

(function () {
    if (!app.documents.length) { alert("Open your master drawing first."); return; }
    var master = app.activeDocument;
    var s = dwgLoadSettings(master);
    var reg = dwgReadRegInfo(master);
    if (!s.src && reg.src) s.src = reg.src;
    var auto = $.global.DWG_AUTO;          // set by automation (run_illustrator.ps1 / tests)
    $.global.DWG_AUTO = undefined;         // one-shot: never leak into your next manual run

    var f = null;
    if (auto && auto.update) f = new File(auto.update);
    else {
        var known = s.src ? new File(s.src) : null;
        if (known && known.exists &&
            confirm("Update linework from:\n" + known.fsName + " ?\n\n(No = pick a different file)")) f = known;
        else f = File.openDialog("Pick the updated drawing from Rhino", "Drawings:*.svg;*.ai;*.pdf;*.dwg,All files:*.*");
    }
    if (!f || !f.exists) return;

    var masterReg = dwgRegCenter(master);
    var src = app.open(f);
    dwgNormalize(src);
    var srcReg = dwgRegCenter(src);
    var dx = 0, dy = 0;
    if (masterReg && srcReg) { dx = masterReg[0] - srcReg[0]; dy = masterReg[1] - srcReg[1]; }

    var report = [];
    for (var i = 0; i < DWG_STACK.length; i++) {
        var e = DWG_STACK[i];
        if (e.role !== "line") continue;
        var from = dwgFindLayer(src, e.name);
        var to = dwgFindLayer(master, e.name);
        if (!from && !to) continue;
        if (!to) to = dwgEnsureLayer(master, e.name);
        var wasLocked = to.locked, wasVisible = to.visible;
        dwgUnlock(to);
        master.activeLayer = to;          // a locked active layer blocks duplicate()
        while (to.pageItems.length) to.pageItems[0].remove();
        var n = 0;
        if (from) {
            dwgUnlock(from);
            var items = [];
            for (var j = 0; j < from.pageItems.length; j++) items.push(from.pageItems[j]);
            for (var k = 0; k < items.length; k++) {
                var dup = items[k].duplicate(to, ElementPlacement.PLACEATEND);
                if (dx || dy) dup.translate(dx, dy);
                n++;
            }
        }
        dwgSetActive(master, "ENTOURAGE");
        to.visible = wasVisible; to.locked = wasLocked;
        report.push(e.name + (n ? "" : " (now empty)"));
    }
    src.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = master;

    var newReg = dwgReadRegInfo(master);
    s.src = f.fsName.replace(/\\/g, "/");
    if (newReg.ratio) s.ratio = newReg.ratio;
    if (newReg.scale) s.scale = newReg.scale;

    dwgOrderLayers(master);
    dwgApplyPens(master, s);
    dwgFinishLayers(master, s);
    dwgSaveSettings(master, s);

    var msg = "Linework updated from " + decodeURI(f.name) + "\nReplaced: " + report.join(", ") +
              ((dx || dy) ? "\nShifted to registration by " + Math.round(dx * 100) / 100 + ", " +
                            Math.round(dy * 100) / 100 + " pt" : "");
    if (auto) $.global.DWG_RESULT = msg; else alert(msg);
})();
