// DWG_Setup.jsx - turn a Rhino export (CleanMake2D SVG, Make2D .ai/.svg/.dwg) into a
// layered drawing with a consistent pen table.
//
// Run with the drawing open:  File > Scripts > Other Script...  (or put the DWG_ files in
// Illustrator's Presets\<language>\Scripts folder so they show up in File > Scripts).
// Safe to run again at any time: it re-sorts layers and re-applies the pen table, so it is
// also the "snap everything back to standard" button.
#target illustrator
#include "DWG_Common.jsxinc"

(function () {
    if (!app.documents.length) { alert("Open the drawing first."); return; }
    var doc = app.activeDocument;
    var s = dwgLoadSettings(doc);
    var reg = dwgReadRegInfo(doc);
    if (reg.src) s.src = reg.src;
    if (reg.ratio) s.ratio = reg.ratio;
    if (reg.scale) s.scale = reg.scale;

    var auto = $.global.DWG_AUTO;          // set by automation (run_illustrator.ps1 / tests)
    $.global.DWG_AUTO = undefined;         // one-shot: never leak into your next manual run
    if (auto) { for (var a in auto) s[a] = auto[a]; }
    else if (!dwgSetupDialog(s)) return;

    var moved = dwgNormalize(doc);
    dwgOrderLayers(doc);
    var report = dwgApplyPens(doc, s);
    dwgFinishLayers(doc, s);
    dwgSaveSettings(doc, s);

    var msg = "Drawing set up (" + s.preset + ", weights x" + s.mult + ").\n" +
              (moved ? moved + " item(s) sorted into drawing layers.\n" : "") +
              (report.length ? "Styled: " + report.join(", ") : "No linework found to style.") +
              (s.scale ? "\nScale: " + s.scale : "");
    if (auto) $.global.DWG_RESULT = msg; else alert(msg);
})();

function dwgSetupDialog(s) {
    var w = new Window("dialog", "DWG Setup");
    w.alignChildren = "fill";
    var g1 = w.add("group");
    g1.add("statictext", undefined, "Style:");
    var names = []; for (var k in DWG_PRESETS) names.push(k);
    var dd = g1.add("dropdownlist", undefined, names);
    dd.selection = 0;
    for (var i = 0; i < names.length; i++) if (names[i] === s.preset) dd.selection = i;

    var g2 = w.add("group");
    g2.add("statictext", undefined, "Line weight multiplier:");
    var mult = g2.add("edittext", undefined, String(s.mult));
    mult.characters = 5;
    g2.add("statictext", undefined, "(0.8 for 1/16\", 1.2 for 1/2\")");

    var lock = w.add("checkbox", undefined, "Lock the Rhino linework layers (you draw on ENTOURAGE / PEOPLE / TONE / ANNOTATION)");
    lock.value = !!s.lockLinework;

    if (s.scale) w.add("statictext", undefined, "Scale from Rhino: " + s.scale);

    var b = w.add("group"); b.alignment = "right";
    b.add("button", undefined, "Cancel", { name: "cancel" });
    b.add("button", undefined, "OK", { name: "ok" });
    if (w.show() !== 1) return false;
    s.preset = dd.selection.text;
    s.mult = parseFloat(mult.text) || 1.0;
    s.lockLinework = lock.value;
    return true;
}
