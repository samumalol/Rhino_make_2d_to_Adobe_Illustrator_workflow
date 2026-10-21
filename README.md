# Rhino → Illustrator: clean, minimal, layered drawings

Rhino makes the linework, already sorted by what each line *is* (cut, profile, edge, beyond...).
Illustrator applies one pen table to it and holds everything you add by hand: furniture,
people, trees, tone and text. When the design changes, you re-run Rhino and swap only the
linework. Your Illustrator work stays.

```
Rhino model ──CleanMake2D.py──► Plan L1.svg ──DWG_Setup──► Plan L1.ai (master)
   ▲  (same view, scale, page)                               │  + DWG_Entourage (furniture, people, trees,
   │                                                         │    scale bar, north arrow)
   └─ change design ─► Redraw ─► Plan L1.svg ──DWG_UpdateLinework──► master: only Rhino layers replaced
```

| File | Where it runs | What it does |
|---|---|---|
| `CleanMake2D.py` | Rhino 8 | Hidden-line drawing → named layers, cleaned linework, poché, true-scale SVG |
| `DWG_Setup.jsx` | Illustrator | Sorts the import into a fixed layer stack and applies the pen table. Run it again any time to "snap to standard" |
| `DWG_UpdateLinework.jsx` | Illustrator | Swaps new Rhino linework into the master file, lined up on the registration mark |
| `DWG_Entourage.jsx` + `.json` | Illustrator | 32 furniture / fixture / plant / tree / people symbols at your drawing scale, plus a scale bar and north arrow |
| `DWG_Common.jsxinc` | (shared) | **The pen table and layer stack. Edit weights here.** |

`examples/` has renders from the test model (furnished plan, section, entourage sheet).
`run_illustrator.ps1` runs the Illustrator scripts without dialogs, which is how Claude drives
them. Usage is at the top of the file.

**From Claude, in any session:** type `/rhino-illustrator-drawings`, or just ask, for example
"make a 1/8" plan of level 1 from my Rhino model and set it up in Illustrator with solid poché".
The skill lives in `C:\Users\Sam\.claude\skills\rhino-illustrator-drawings\` and works from this
folder's copy of the scripts. With the Rhino MCP running, Claude can drive your open Rhino
directly.

---

## 0. One-time setup

**Rhino:** Tools › Options › Aliases › New, name `CleanMake2D`, command:

```
! _-RunPythonScript "C:\Users\Sam\Desktop\201 Atwood\drawing-workflow\CleanMake2D.py"
```

**Illustrator:** run the scripts with **File › Scripts › Other Script… (Ctrl+F12)**. To get them
on the File › Scripts menu instead, copy *all six* `DWG_*` files into
`C:\Program Files\Adobe\Adobe Illustrator 2026\Presets\en_US\Scripts\` and restart Illustrator.
The `.jsx` files need the `.jsxinc` and `.json` next to them.

**Illustrator preferences:**
- *General › Scale Strokes & Effects*: **off**, so a resized chair keeps its 0.25 pt line.
- *General › Constrain Angle*: set it to your building's rotation for skewed plans like your
  references. Shift-drag, rectangles and text then follow the grid.

---

## 1. Model for clean linework (most of the quality happens here)

- **Anything that gets cut is a closed solid**: walls, slabs, columns, stairs, roofs.
  Check with `ShowEdges` › Naked edges. An open solid gets a cut line but no poché, and the
  script warns you.
- `MergeAllFaces` on polysurfaces removes coplanar seams, which would otherwise show up as EDGE lines.
- Overlapping wall boxes are fine. The script unions the cut, so poché comes out as one piece
  with no seams. Running `BooleanUnion` in the model still keeps elevations cleaner.
- Keep meshes (scans, topo, cadmapper) on a **Context** layer, because their triangle edges are noisy.
- Model only what reads at the drawing scale. At 1/8" a window is a frame and a pane, not mullion
  profiles. Put detail you don't want drawn on a layer whose name includes `NoDraw`.
- **Layer names steer the line type.** Words are matched anywhere in the full layer path,
  as whole words, ignoring case:

| Layer name contains | Drawn as |
|---|---|
| `glass` `glazing` `window` `mullion` `curtain wall` | GLAZING (thin, even when cut) |
| `furniture` `chair` `table` `desk` `sofa` `bed` `casework` `millwork` `fixture` | FURNITURE (thin grey) |
| `tree` `plant` `landscape` `vegetation` `shrub` | PLANTING |
| `context` `neighbor` `cadmapper` `topo` `contour` `road` `street` `sidewalk` | CONTEXT (grey) |
| `overhead` `above` | dashed over the plan (plan views only, e.g. roof edge, skylights) |
| `nodraw` `ignore` | skipped |
| anything else | building: CUT + POCHE / PROFILE / OUTLINE / EDGE / BEYOND |

Edit the lists at the top of `CleanMake2D.py` (`CATEGORY_KEYWORDS`).

---

## 2. Make the drawing in Rhino: `CleanMake2D`

1. Set up a **parallel** viewport:
   - **Plan**: Top view plus a clipping plane about 4' above the floor, arrow pointing **down**
     (the viewport should show the plan).
   - **Section**: Front/Right view plus a clipping plane, arrow pointing **away from you**
     (again, the viewport shows the section).
   - **Elevation / axon**: no clipping plane needed.
2. Run `CleanMake2D` › **+ New drawing** › name it › pick the **scale** › pick the **page**
   (ARCH D 36×24, Tabloid, A1 … or *Fit to drawing*) › set the options:
   - *Poché*: solid fill inside closed cut solids.
   - *Depth fade*: lines more than 8 ft behind the cut go to BEYOND, more than 30 ft to BEYOND2.
     Change `BEYOND_FT` / `BEYOND2_FT` in the script.
   - *Hidden lines* (dashed), *Tangent edges* (usually off), *Portrait*, *Draw into Rhino too*.
3. Select objects, or press **Enter for everything visible**.

You get:
- `…\<your 3dm name> drawings\<drawing name>.svg`: the page at true scale, with layers.
- A copy in Rhino on `CleanMake2D::<drawing name>::CUT / POCHE / …`, placed to the right of the
  model with print widths set, so it also prints straight from Rhino.

**After a design change:** `CleanMake2D` › pick the drawing › **Redraw**. It reuses the same
camera, clipping planes, scale, page and alignment, so the new SVG lands exactly on the old one.
Other choices in that menu:
- *Re-export from Rhino linework*: if you deleted or fixed lines on the CleanMake2D layers in
  Rhino, this writes them out without recomputing.
- *Change scale / page*, *Re-capture view*, *Delete*.

The script stores each drawing's settings inside the .3dm and a named view called `DWG - <name>`.

**Plain Make2D still works.** If you'd rather use Rhino's own Make2D (Current view, *Maintain
source layers*, *Show clipping plane intersections* on, tangents off), then *Export Selected*
to `.ai` or `.svg`. `DWG_Setup` maps Make2D's layer names (Visible / Hidden / Silhouettes /
Clipping Plane Intersections …) by keyword. That path hasn't been tested on a real Make2D
export yet. You lose automatic poché, depth fade and update alignment.

---

## 3. Illustrator: `DWG_Setup`

Open the SVG (File › Open), run **DWG_Setup**, pick a style, then **Save As .ai**. That file is
your master.

| Style | Look |
|---|---|
| **Solid poché** | black cut fill, 0.8 pt cut line: high contrast, like your second reference |
| **Outline cut** | no fill, 1.0 pt cut line: airy, like your fourth reference |
| **Grey poché** | 25 % fill, 0.6 pt line: large-scale sections |

The *weight multiplier* scales the whole pen table: about 0.8 at 1/16", 1.2 at 1/2".

Layer stack (top → bottom). Rhino layers (grey) get replaced on update and are locked by default.
**Your layers (blue) are never touched:**

```
ANNOTATION   yours: text, labels, scale bar, north arrow
REG          Rhino: registration mark + drawing info (hidden, non-printing, don't move)
OVERHEAD     Rhino  0.25 dashed
CUT          Rhino  0.8 (style)
POCHE        Rhino  fill (style)
PEOPLE       yours
ENTOURAGE    yours: furniture, trees, plants (this is the active layer after every script)
PROFILE      Rhino  0.50   outline of the scene against empty space
OUTLINE      Rhino  0.30   object outlines
GLAZING      Rhino  0.15
FURNITURE    Rhino  0.18 / 85 %   furniture you modelled in Rhino
EDGE         Rhino  0.15   interior edges
HIDDEN       Rhino  0.20 dashed
BEYOND       Rhino  0.12 / 70 %
BEYOND2      Rhino  0.10 / 45 %
PLANTING     Rhino  0.15 / 80 %
CONTEXT      Rhino  0.20 / 50 %
TONE         yours: floor patterns, hatches, shadows, tone fills
```

Change any weight or grey in `DWG_Common.jsxinc` (`DWG_BASE_PENS`, `DWG_PRESETS`), then re-run
Setup on the open file.

---

## 4. Furniture, people, trees: `DWG_Entourage`

Run **DWG_Entourage** in the master. It reads the scale Rhino stored in the file and makes:
- **Symbols** (Symbols panel) such as `Chair 18in @1/8in`, `Dining 4 - round 42in @1/8in`, `Tree 20ft`,
  `Standing 5ft9 (elev)`. All are real size at your scale: chairs, armchair, sofa, dining sets,
  café, desk, lounge and living sets, beds, nightstand, shelf, WC, lavatory, shower, tub,
  sink, range, fridge, starburst plants, plan trees, plan people, and elevation people
  (man, woman, child, couple).
- A **library sheet** off the page, right of the artboard (layer `_LIBRARY @1/8in`, non-printing).
  **Alt-drag** pieces from it onto ENTOURAGE or PEOPLE.
- A **scale bar + north arrow** on ANNOTATION, bottom left. Move them anywhere.

Working with them:
- Rotate pieces to match a skewed plan with the Rotate tool (R), or set Constrain Angle (see setup).
- Pieces have a **white fill**, so they mask floor patterns and lines beneath them, as in your
  references. Floor patterns therefore go on TONE.
- *Break Link to Symbol* to edit one copy. Double-click the symbol to change every copy.
- Running it at another scale adds a second set; the names carry the scale.
- **Add your own:** edit `DWG_Entourage.json` (real inches, format at the top of
  `DWG_Entourage.jsx`), or draw a piece at real size and make it a symbol (F8).
  For elevation trees and more people, trace a photo with the Pen tool: 0.25 pt line, white fill.
  That keeps the same minimal hand.

---

## 5. Tone, patterns, text

- **Floor patterns (TONE):** draw room shapes, then fill them with a pattern swatch (Swatches ›
  Open Swatch Library › Patterns › Basic Graphics › Lines, or your own). Keep lines 0.1–0.15 pt
  at 30–50 % black. To rotate only the pattern with a skewed plan: Object › Transform › Rotate,
  with only *Transform Patterns* ticked.
- **Shadows / tone:** 10–25 % grey fills on TONE, blending mode Multiply.
- **Text (ANNOTATION):** one typeface, room names at 6–7 pt, titles at 9–10 pt.

---

## 6. The design changed

1. Rhino: `CleanMake2D` › pick the drawing › **Redraw**.
2. Illustrator: open the master .ai › **DWG_UpdateLinework** › **Yes**. It remembers which SVG
   the drawing came from.
3. Only the Rhino layers are replaced, lined up on REG, and the pens are re-applied.
   Furniture, people, tone and text stay where they were.

To move the whole drawing on the page, unlock and move **every** layer, including REG.

---

## 7. Print / PDF

File › Save a Copy › PDF (High Quality Print). `_LIBRARY`, REG and `_DWG settings` don't print.
0.1 pt prints fine on laser printers. For plotters, stay at 0.13 pt (0.05 mm) or above.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| A wall has no poché | The solid is open: `ShowEdges`, then `Cap` / `Join`. The command line lists open outlines |
| The section shows the wrong side | Flip the clipping plane (`Flip`). The drawing matches what the viewport shows |
| Everything is light grey | The cut plane isn't facing the view, so depth is measured from the front-most line. Check the clipping plane, or turn Depth fade off |
| A line is on the wrong layer | Check the layer-name keywords (section 1) |
| "Drawing runs off the page" | Pick a bigger page or a smaller scale (Change scale / page) |
| Slow on a big model | Select only what the drawing needs. Hide context and furniture you don't draw |
| Illustrator: can't draw anywhere | The active layer is locked. Click ENTOURAGE in the Layers panel (the scripts always leave it active) |
