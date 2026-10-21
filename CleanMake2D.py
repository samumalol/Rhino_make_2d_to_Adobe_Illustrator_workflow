#! python3
# -*- coding: utf-8 -*-
"""
CleanMake2D  -  clean, layered hidden-line drawings from Rhino 8, written straight
to an Illustrator-ready SVG at true print scale.

What it does (vs. plain Make2D):
  * sorts every line into a drawing layer by what it IS, not just visible/hidden:
        CUT       section-cut lines from clipping planes            (heaviest)
        POCHE     solid fill inside closed cut solids
        PROFILE   outline of the whole scene against empty space
        OUTLINE   outlines of individual objects
        EDGE      interior edges (both faces visible)
        BEYOND /  the same, but far behind the cut  -> lighter
        BEYOND2
        HIDDEN    hidden lines (optional, dashed)
        OVERHEAD  objects on an "Overhead" layer, drawn dashed over a plan
        GLAZING / FURNITURE / PLANTING / CONTEXT   by source layer name
  * joins, simplifies and de-fragments the linework (fewer, longer paths)
  * remembers each drawing (camera, clipping planes, scale, page, alignment), so
    "Redraw" after a design change lands in exactly the same place on the page
    and DWG_UpdateLinework.jsx can swap it into your Illustrator file.

Run:  _-RunPythonScript "<this folder>\\CleanMake2D.py"     (see README.md for an alias)
"""
from __future__ import print_function, division

import io
import json
import math
import os
import re

import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System
from System.Collections.Generic import List


# ----------------------------------------------------------------------------
# SETTINGS YOU MAY WANT TO EDIT
# ----------------------------------------------------------------------------

ROOT = "CleanMake2D"       # parent Rhino layer that holds every drawing's linework
SECTION = "CleanMake2D"    # document user-text section that stores drawing settings

# label, ratio (real size / paper size)
SCALES = [
    ('1/32" = 1\'-0"', 384), ('1/16" = 1\'-0"', 192), ('3/32" = 1\'-0"', 128),
    ('1/8" = 1\'-0"', 96), ('3/16" = 1\'-0"', 64), ('1/4" = 1\'-0"', 48),
    ('3/8" = 1\'-0"', 32), ('1/2" = 1\'-0"', 24), ('3/4" = 1\'-0"', 16),
    ('1" = 1\'-0"', 12), ('1 1/2" = 1\'-0"', 8), ('3" = 1\'-0"', 4),
    ('1:1000', 1000), ('1:500', 500), ('1:200', 200), ('1:100', 100),
    ('1:50', 50), ('1:20', 20), ('1:10', 10),
]

# label, (width_in, height_in) landscape. None = fit the drawing (+1" margin)
PAPERS = [
    ("Fit to drawing", None),
    ("Letter 11 x 8.5", (11.0, 8.5)),
    ("Tabloid 17 x 11", (17.0, 11.0)),
    ("Super B 19 x 13", (19.0, 13.0)),
    ("ARCH C 24 x 18", (24.0, 18.0)),
    ("ARCH D 36 x 24", (36.0, 24.0)),
    ("ARCH E1 42 x 30", (42.0, 30.0)),
    ("ARCH E 48 x 36", (48.0, 36.0)),
    ("A3 420 x 297 mm", (16.535, 11.693)),
    ("A2 594 x 420 mm", (23.386, 16.535)),
    ("A1 841 x 594 mm", (33.110, 23.386)),
    ("A0 1189 x 841 mm", (46.811, 33.110)),
]

# Drawing layers, bottom -> top. weight in points at print size,
# gray 0 = black .. 1 = white, dash in points. Illustrator's DWG_Common.jsxinc
# re-applies its own pen table, so these mostly matter for the SVG preview and
# for printing straight from Rhino.
LAYERS = [
    ("CONTEXT",   0.20, 0.50, None),
    ("PLANTING",  0.15, 0.20, None),
    ("BEYOND2",   0.10, 0.55, None),
    ("BEYOND",    0.13, 0.30, None),
    ("HIDDEN",    0.25, 0.00, (2.0, 1.5)),
    ("EDGE",      0.18, 0.00, None),
    ("FURNITURE", 0.18, 0.15, None),
    ("GLAZING",   0.15, 0.00, None),
    ("OUTLINE",   0.35, 0.00, None),
    ("PROFILE",   0.60, 0.00, None),
    ("POCHE",     0.00, 0.00, None),
    ("CUT",       1.00, 0.00, None),
    ("OVERHEAD",  0.25, 0.00, (4.0, 2.0)),
    ("REG",       0.25, None, None),
]
LAYER_NAMES = [l[0] for l in LAYERS]
PEN = dict((l[0], l) for l in LAYERS)

# Source-layer keywords -> category. Matched as whole words against the object's
# full layer path (case-insensitive, "_" and "::" count as spaces). First match wins.
CATEGORY_KEYWORDS = [
    ("NODRAW",    r"nodraw|no draw|ignore"),
    ("OVERHEAD",  r"overhead|above"),
    ("GLAZING",   r"glass|glazing|glazed|windows?|mullions?|curtain ?wall"),
    ("FURNITURE", r"furn\w*|chairs?|tables?|desks?|sofas?|beds?|casework|millwork|fixtures?"),
    ("PLANTING",  r"trees?|plants?|planting|landscape|vegetation|shrubs?"),
    ("CONTEXT",   r"context|neighbou?rs?|cadmapper|topo\w*|contours?|roads?|streets?|sidewalks?"),
]

# Depth fade: lines further than this behind the cut plane (or behind the
# front-most line, when there is no clipping plane) go to BEYOND / BEYOND2.
BEYOND_FT = 8.0
BEYOND2_FT = 30.0

DEBUG = None          # set to a list to collect per-segment diagnostics

MIN_SEG_PT = 0.15     # drop line fragments shorter than this on paper (points)
MARGIN_IN = 1.0       # margin used by "Fit to drawing"


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

SIL = rg.SilhouetteType
VIS = rg.HiddenLineDrawingSegment.Visibility


def no_silhouette(st):
    # SilhouetteType.None (= 0) can't be spelled in Python 3, so compare by value
    try:
        return int(st) == 0
    except Exception:
        return str(st) == "None"


EDGE_TYPES = (SIL.NonSilhouetteCrease, SIL.NonSilhouetteTangent, SIL.NonSilhouetteSeam,
              SIL.MiscellaneousFeature, SIL.DraftCurve, SIL.TangentProjects)


def mid_z(crv):
    """Depth (HLD z) at the middle of a segment. PointAtNormalizedLength returns Unset for
    some HLD segments, so evaluate at the parameter midpoint and fall back to the ends."""
    p = crv.PointAt(crv.Domain.Mid)
    if p.IsValid:
        return p.Z
    return 0.5 * (crv.PointAtStart.Z + crv.PointAtEnd.Z)


def doc_tol():
    return sc.doc.ModelAbsoluteTolerance


def unit_scale(to_unit):
    return Rhino.RhinoMath.UnitScale(sc.doc.ModelUnitSystem, to_unit)


def netlist(items, kind=rg.Curve):
    out = List[kind]()
    for i in items:
        if i is not None:
            out.Add(i)
    return out


def clean_name(s):
    s = re.sub(r"[^A-Za-z0-9 _\-\.]+", "", s or "").strip()
    return s[:60]


def category_for_layer(path):
    words = " " + re.sub(r"[^a-z0-9]+", " ", path.lower()) + " "
    for cat, pat in CATEGORY_KEYWORDS:
        if re.search(r"\b(?:%s)\b" % pat, words):
            return cat
    return "BUILDING"


def gray_hex(g):
    v = int(round(max(0.0, min(1.0, g)) * 255))
    return "#%02x%02x%02x" % (v, v, v)


# ----------------------------------------------------------------------------
# drawing registry (stored inside the .3dm)
# ----------------------------------------------------------------------------

def load_drawings():
    out = {}
    for n in (sc.doc.Strings.GetEntryNames(SECTION) or []):
        try:
            out[n] = json.loads(sc.doc.Strings.GetValue(SECTION, n))
        except Exception:
            pass
    return out


def save_drawing(s):
    sc.doc.Strings.SetString(SECTION, s["name"], json.dumps(s))


def default_svg_path(name):
    folder = sc.doc.Path and os.path.dirname(sc.doc.Path)
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), "Desktop")
    base = os.path.splitext(sc.doc.Name or "drawings")[0]
    return os.path.join(folder, base + " drawings", name + ".svg")


# ----------------------------------------------------------------------------
# view + clipping planes
# ----------------------------------------------------------------------------

def clips_for_viewport(vp_id):
    ids = []
    objs = sc.doc.Objects.FindByObjectType(Rhino.DocObjects.ObjectType.ClipPlane) or []
    for obj in objs:
        try:
            vids = [str(v) for v in obj.Geometry.ViewportIds()]
        except Exception:
            vids = []
        if str(vp_id) in vids:
            ids.append(str(obj.Id))
    return ids


def capture_view(s):
    view = sc.doc.Views.ActiveView
    if isinstance(view, Rhino.Display.RhinoPageView):
        rs.MessageBox("Run CleanMake2D from a model viewport (not a layout).", 0, "CleanMake2D")
        return False
    vp = view.ActiveViewport
    vname = "DWG - " + s["name"]
    sc.doc.NamedViews.Add(vname, vp.Id)
    s["view"] = vname
    s["clips"] = clips_for_viewport(vp.Id)
    s["parallel"] = bool(vp.IsParallelProjection)
    return True


def viewport_info(s):
    idx = sc.doc.NamedViews.FindByName(s.get("view", ""))
    if idx >= 0:
        return sc.doc.NamedViews[idx].Viewport
    return Rhino.DocObjects.ViewportInfo(sc.doc.Views.ActiveView.ActiveViewport)


def clip_planes(s):
    """Clipping planes as the hidden-line solver wants them. The viewport keeps the side the
    clipping plane's arrow points to; HiddenLineDrawing keeps the other side, so flip."""
    planes = []
    for sid in s.get("clips", []):
        obj = sc.doc.Objects.FindId(System.Guid(sid))
        if obj is not None and not obj.IsDeleted:
            pl = rg.Plane(obj.Geometry.Plane)
            pl.Flip()
            planes.append(pl)
    return planes


# ----------------------------------------------------------------------------
# collect source geometry
# ----------------------------------------------------------------------------

def layer_path(index):
    try:
        return sc.doc.Layers[index].FullPath
    except Exception:
        return ""


def explode_into(obj_geo, xform, lpath, out, depth=0):
    """Append (geometry, xform, layer_path) for drawable geometry; blocks are expanded."""
    if isinstance(obj_geo, Rhino.DocObjects.InstanceObject):
        if depth > 8:
            return
        xf = xform * obj_geo.InstanceXform
        for sub in obj_geo.InstanceDefinition.GetObjects():
            sub_path = layer_path(sub.Attributes.LayerIndex)
            if sub_path.lower() in ("default", "0", ""):
                sub_path = lpath
            explode_into(sub, xf, sub_path, out, depth + 1)
        return
    geo = obj_geo.Geometry if isinstance(obj_geo, Rhino.DocObjects.RhinoObject) else obj_geo
    if isinstance(geo, rg.SubD):
        geo = geo.ToBrep(rg.SubDToBrepOptions.Default)
    if isinstance(geo, (rg.Brep, rg.Extrusion, rg.Mesh, rg.Surface, rg.Curve)) and geo is not None:
        out.append((geo, xform, lpath))


def collect_objects(ids):
    """Return list of (geometry, xform, layer_path) for the chosen objects."""
    out = []
    if ids:
        objs = [sc.doc.Objects.FindId(i) for i in ids]
    else:
        st = Rhino.DocObjects.ObjectEnumeratorSettings()
        st.VisibleFilter = True
        st.HiddenObjects = False
        st.LockedObjects = True
        objs = list(sc.doc.Objects.GetObjectList(st))
    for o in objs:
        if o is None or o.IsDeleted:
            continue
        if isinstance(o, (Rhino.DocObjects.ClippingPlaneObject,)):
            continue
        lp = layer_path(o.Attributes.LayerIndex)
        if lp == ROOT or lp.startswith(ROOT + "::"):
            continue   # never draw previous drawings
        explode_into(o, rg.Transform.Identity, lp, out)
    return out


# ----------------------------------------------------------------------------
# hidden-line computation + classification
# ----------------------------------------------------------------------------

def compute_hld(items, vpinfo, planes, s):
    p = rg.HiddenLineDrawingParameters()
    p.AbsoluteTolerance = doc_tol()
    p.IncludeTangentEdges = bool(s.get("tangent", False))
    p.IncludeTangentSeams = False
    p.IncludeHiddenCurves = True   # needed to close cut loops; hidden lines are filtered later
    p.Flatten = False          # keep depth (HLD z) so lines can be faded by distance
    try:
        p.OccludingSectionOption = True   # cut solids hide what is behind the cut face
    except Exception:
        pass
    p.SetViewport(vpinfo)
    for pl in planes:
        p.AddClippingPlane(pl)
    added = 0
    for i, it in enumerate(items):
        geo, xf = it[0], it[1]
        try:
            ok = p.AddGeometry(geo, xf, i, True)
        except Exception:
            ok = p.AddGeometry(geo, xf, i)
        if ok:
            added += 1
    if not added:
        return None
    hld = rg.HiddenLineDrawing.Compute(p, True)
    if hld is not None:
        try:
            hld.RejoinCompatibleVisible()
        except Exception:
            pass
    return hld


def depth_function(hld, vpinfo, planes, s):
    """Returns f(z_hld) -> distance (real feet) behind the reference plane, or None."""
    if not s.get("depth", True) or not vpinfo.IsParallelProjection:
        return None
    w2h = hld.WorldToHiddenLine
    cam = vpinfo.CameraLocation
    d = rg.Vector3d(vpinfo.CameraDirection)
    d.Unitize()
    a = w2h * cam
    b = w2h * (cam + d)
    step = a.Z - b.Z                        # HLD z per model unit of depth
    if abs(step) < 1e-12:
        return None
    to_ft = unit_scale(Rhino.UnitSystem.Feet)

    def depth_of_world(pt):
        return (a.Z - (w2h * pt).Z) / step

    ref = None
    for pl in planes:                        # cut planes facing the camera
        if abs(pl.ZAxis * d) > 0.95:
            dd = depth_of_world(pl.Origin)
            ref = dd if ref is None else min(ref, dd)
    if ref is None:
        zs = []
        for seg in hld.Segments:
            if seg is not None and seg.SegmentVisibility == VIS.Visible and seg.CurveGeometry is not None:
                zs.append(mid_z(seg.CurveGeometry))
        if not zs:
            return None
        ref = (a.Z - (max(zs) if step > 0 else min(zs))) / step   # front-most line
    if DEBUG is not None:
        DEBUG.append(("depth", a.Z, b.Z, step, ref, to_ft, [str(p.ZAxis) for p in planes], str(d)))

    def f(z):
        return ((a.Z - z) / step - ref) * to_ft
    return f


def classify(hld, items, depth_fn, s):
    """-> dict layer -> list of curves (HLD xy, z flattened), list of cut curves for poche."""
    flat = rg.Transform.PlanarProjection(rg.Plane.WorldXY)
    out = dict((n, []) for n in LAYER_NAMES)
    poche_src = []
    want_hidden = bool(s.get("hidden", False))
    stats = {"segments": 0, "kept": 0}
    for seg in hld.Segments:
        if seg is None:
            continue
        pc = seg.ParentCurve
        if pc is None or no_silhouette(pc.SilhouetteType):
            continue
        crv = seg.CurveGeometry
        if crv is None:
            continue
        stats["segments"] += 1
        vis = seg.SegmentVisibility
        st = pc.SilhouetteType
        try:
            cat = items[int(pc.SourceObject.Tag)][3]
        except Exception:
            continue
        is_cut = (st == SIL.SectionCut)

        if is_cut and cat == "BUILDING" and vis != VIS.Clipped:
            c = crv.DuplicateCurve(); c.Transform(flat)
            poche_src.append(c)

        if vis == VIS.Visible:
            if cat in ("GLAZING", "FURNITURE", "PLANTING", "CONTEXT"):
                layer = cat
            elif is_cut:
                layer = "CUT"
            else:
                if seg.IsSceneSilhouette:
                    layer = "PROFILE"
                elif st in EDGE_TYPES:
                    layer = "EDGE"
                else:
                    layer = "OUTLINE"
                if depth_fn is not None:
                    dft = depth_fn(mid_z(crv))
                    if dft > BEYOND2_FT:
                        layer = "BEYOND2"
                    elif dft > BEYOND_FT:
                        layer = "BEYOND"
        elif vis == VIS.Hidden and want_hidden and cat == "BUILDING" and not is_cut:
            layer = "HIDDEN"
        else:
            continue
        c = crv.DuplicateCurve()
        c.Transform(flat)
        out[layer].append(c)
        stats["kept"] += 1
        if DEBUG is not None:
            z = mid_z(crv)
            DEBUG.append(("seg", layer, cat, str(st), str(vis), round(z, 2),
                          round(depth_fn(z), 2) if depth_fn else None))
    return out, poche_src, stats


def overhead_lines(items, vpinfo, s):
    over = [it for it in items if it[3] == "OVERHEAD"]
    if not over:
        return []
    hld = compute_hld(over, vpinfo, [], dict(s, hidden=False, tangent=False))
    if hld is None:
        return []
    flat = rg.Transform.PlanarProjection(rg.Plane.WorldXY)
    res = []
    for seg in hld.Segments:
        if seg is None or seg.ParentCurve is None or seg.CurveGeometry is None:
            continue
        if no_silhouette(seg.ParentCurve.SilhouetteType) or seg.SegmentVisibility != VIS.Visible:
            continue
        c = seg.CurveGeometry.DuplicateCurve(); c.Transform(flat)
        res.append(c)
    return res


# ----------------------------------------------------------------------------
# cleanup
# ----------------------------------------------------------------------------

def tidy(curves, k_pt_per_unit):
    """Drop slivers, join end-to-end pieces, simplify (lines -> lines, arcs -> arcs)."""
    if not curves:
        return []
    min_len = MIN_SEG_PT / k_pt_per_unit
    join_tol = max(doc_tol(), 0.05 / k_pt_per_unit)
    keep = [c for c in curves if c is not None and c.GetLength() > min_len]
    joined = rg.Curve.JoinCurves(netlist(keep), join_tol, False) or []
    ang = sc.doc.ModelAngleToleranceRadians
    out = []
    for c in joined:
        simple = c.Simplify(rg.CurveSimplifyOptions.All, doc_tol(), ang)
        out.append(simple if simple is not None else c)
    return out


def cut_regions(cut_curves, k_pt_per_unit, warn, want_poche):
    """Section-cut pieces -> (clean cut outlines, solid hatches).

    Walls modelled as separate overlapping boxes give overlapping loops; filling those
    directly punches holes where they overlap and leaves seam lines inside the wall. So the
    closed loops are boolean-unioned first and the CUT lines are the union's boundary."""
    if not cut_curves:
        return [], []
    join_tol = max(doc_tol() * 2, 0.1 / k_pt_per_unit)
    joined = rg.Curve.JoinCurves(netlist(cut_curves), join_tol, False) or []
    closed, open_pieces = [], []
    for c in joined:
        if c.IsClosed:
            closed.append(c)
        elif c.PointAtStart.DistanceTo(c.PointAtEnd) < join_tol * 4 and c.GetLength() > join_tol * 10:
            c2 = c.DuplicateCurve()
            if c2.MakeClosed(join_tol * 4):
                closed.append(c2)
            else:
                open_pieces.append(c)
        else:
            open_pieces.append(c)
    if open_pieces:
        warn.append("%d cut outline(s) are open, so they get no poche. Usually the solid is "
                    "not closed: check it with ShowEdges (naked edges) or Cap." % len(open_pieces))
    outlines = closed
    if len(closed) > 1:
        try:
            union = rg.Curve.CreateBooleanUnion(netlist(closed), doc_tol())
            if union and len(union):
                outlines = list(union)
        except Exception:
            pass
    hatches = []
    if want_poche and outlines:
        hatches = list(rg.Hatch.Create(netlist(outlines), solid_hatch_index(), 0.0, 1.0, doc_tol()) or [])
    return outlines + open_pieces, hatches


def solid_hatch_index():
    hp = sc.doc.HatchPatterns.FindName("Solid")
    if hp is not None:
        return hp.Index
    return sc.doc.HatchPatterns.Add(Rhino.DocObjects.HatchPattern.Defaults.Solid)


# ----------------------------------------------------------------------------
# Rhino output layers
# ----------------------------------------------------------------------------

def ensure_layer(path):
    idx = sc.doc.Layers.FindByFullPath(path, -1)
    if idx >= 0:
        return idx
    parent = None
    cur = ""
    for part in path.split("::"):
        cur = part if not cur else cur + "::" + part
        idx = sc.doc.Layers.FindByFullPath(cur, -1)
        if idx < 0:
            L = Rhino.DocObjects.Layer()
            L.Name = part
            if parent is not None:
                L.ParentLayerId = sc.doc.Layers[parent].Id
            idx = sc.doc.Layers.Add(L)
        parent = idx
    return idx


def style_layer(idx, name):
    _, w, g, dash = PEN[name]
    L = sc.doc.Layers[idx]
    if name == "REG":
        L.Color = System.Drawing.Color.FromArgb(0, 174, 239)
        L.PlotWeight = -1.0                      # never prints
    else:
        v = int(round((g or 0) * 255))
        L.Color = System.Drawing.Color.FromArgb(v, v, v)
        L.PlotColor = L.Color
        L.PlotWeight = max(w, 0.05) * 25.4 / 72.0  # pt -> mm
    if dash:
        lt = sc.doc.Linetypes.Find("Dashed")
        if lt >= 0:
            L.LinetypeIndex = lt
    sc.doc.Layers.Modify(L, idx, True)


def drawing_layer(s, name):
    return "%s::%s::%s" % (ROOT, s["name"], name)


def clear_drawing_layers(s):
    for name in LAYER_NAMES:
        idx = sc.doc.Layers.FindByFullPath(drawing_layer(s, name), -1)
        if idx < 0:
            continue
        for o in (sc.doc.Objects.FindByLayer(sc.doc.Layers[idx]) or []):
            sc.doc.Objects.Delete(o, True)


def write_to_rhino(s, layers, hatches, reg_curves):
    """layers: dict name -> curves in drawing coords. Moves them to s['place'] in world XY."""
    ax, ay = s["anchor2d"]
    px, py = s["place"]
    move = rg.Transform.Translation(px - ax, py - ay, 0)
    clear_drawing_layers(s)
    for name in LAYER_NAMES:
        idx = ensure_layer(drawing_layer(s, name))
        style_layer(idx, name)
        att = Rhino.DocObjects.ObjectAttributes()
        att.LayerIndex = idx
        if name == "POCHE":
            for h in hatches:
                h2 = h.Duplicate(); h2.Transform(move)
                sc.doc.Objects.AddHatch(h2, att)
            continue
        src = reg_curves if name == "REG" else layers.get(name, [])
        for c in src:
            c2 = c.DuplicateCurve(); c2.Transform(move)
            sc.doc.Objects.AddCurve(c2, att)


def read_from_rhino(s):
    """Inverse of write_to_rhino: linework currently on the drawing's Rhino layers."""
    ax, ay = s["anchor2d"]
    px, py = s["place"]
    back = rg.Transform.Translation(ax - px, ay - py, 0)
    layers = dict((n, []) for n in LAYER_NAMES)
    hatches = []
    for name in LAYER_NAMES:
        idx = sc.doc.Layers.FindByFullPath(drawing_layer(s, name), -1)
        if idx < 0:
            continue
        for o in (sc.doc.Objects.FindByLayer(sc.doc.Layers[idx]) or []):
            g = o.Geometry.Duplicate()
            g.Transform(back)
            if isinstance(g, rg.Hatch):
                hatches.append(g)
            elif isinstance(g, rg.Curve) and name != "REG":
                layers[name].append(g)
    return layers, hatches


# ----------------------------------------------------------------------------
# SVG writer
# ----------------------------------------------------------------------------

class Page(object):
    def __init__(self, s, k):
        self.w_in, self.h_in = s["paper_in"]
        self.w = self.w_in * 72.0
        self.h = self.h_in * 72.0
        self.k = k
        self.ax, self.ay = s["anchor2d"]

    def xy(self, p):
        return (self.w / 2.0 + (p.X - self.ax) * self.k,
                self.h / 2.0 - (p.Y - self.ay) * self.k)


def fmt(v):
    s = "%.3f" % v
    s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def curve_d(crv, page, tol):
    segs = list(crv.DuplicateSegments() or [])
    if not segs:
        segs = [crv]
    closed = crv.IsClosed
    x, y = page.xy(crv.PointAtStart)
    d = ["M%s %s" % (fmt(x), fmt(y))]
    for n, sgm in enumerate(segs):
        last = (n == len(segs) - 1)
        if sgm.IsLinear(tol):
            if not (closed and last):          # "Z" closes it; avoids a doubled anchor
                x, y = page.xy(sgm.PointAtEnd)
                d.append("L%s %s" % (fmt(x), fmt(y)))
            continue
        ok, pl = sgm.TryGetPolyline()
        if ok:
            stop = pl.Count - 1 if (closed and last) else pl.Count
            for i in range(1, stop):
                x, y = page.xy(pl[i])
                d.append("L%s %s" % (fmt(x), fmt(y)))
            continue
        for b in (rg.BezierCurve.CreateCubicBeziers(sgm, tol, 0.017) or []):
            if b is None:
                continue
            pts = [page.xy(b.GetControlVertex3d(i)) for i in (1, 2, 3)]
            d.append("C" + " ".join("%s %s" % (fmt(a), fmt(c)) for a, c in pts))
    if closed:
        d.append("Z")
    return "".join(d)


def esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def write_svg(path, s, page, layers, hatches):
    tol = doc_tol()
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
                 'width="%sin" height="%sin" viewBox="0 0 %s %s">'
                 % (fmt(page.w_in), fmt(page.h_in), fmt(page.w), fmt(page.h)))
    lines.append("<title>%s</title>" % esc(s["name"]))
    for name in LAYER_NAMES:
        _, w, g, dash = PEN[name]
        body = []
        if name == "POCHE":
            for h in hatches:
                loops = list(h.Get3dCurves(True) or []) + list(h.Get3dCurves(False) or [])
                d = "".join(curve_d(c, page, tol) for c in loops)
                if d:
                    body.append('<path d="%s" fill="%s" fill-rule="evenodd" stroke="none"/>'
                                % (d, gray_hex(g)))
        elif name == "REG":
            cx, cy, r = page.w / 2.0, page.h / 2.0, 9.0
            body.append('<path d="M%s %sH%sM%s %sV%s" fill="none" stroke="#00aeef" stroke-width="0.25"/>'
                        % (fmt(cx - r), fmt(cy), fmt(cx + r), fmt(cx), fmt(cy - r), fmt(cy + r)))
            body.append('<circle cx="%s" cy="%s" r="%s" fill="none" stroke="#00aeef" stroke-width="0.25"/>'
                        % (fmt(cx), fmt(cy), fmt(r / 2)))
            info = "DWG|name=%s|ratio=%s|scale=%s|src=%s" % (
                s["name"], fmt(s["ratio"]), s["scale"], path.replace("\\", "/"))
            body.append('<text x="6" y="%s" font-family="Arial" font-size="3" fill="#00aeef">%s</text>'
                        % (fmt(page.h - 4), esc(info)))
        else:
            cap, join = ("butt", "miter") if name == "CUT" else ("round", "round")
            style = (' fill="none" stroke="%s" stroke-width="%s" stroke-linecap="%s" stroke-linejoin="%s"'
                     % (gray_hex(g), fmt(w), cap, join))
            if dash:
                style += ' stroke-dasharray="%s"' % " ".join(fmt(v) for v in dash)
            for c in layers.get(name, []):
                body.append('<path d="%s"%s/>' % (curve_d(c, page, tol), style))
        if not body and name not in ("CUT", "POCHE", "REG"):
            continue
        lines.append('<g id="%s" data-name="%s">' % (name, name))
        lines.extend(body)
        lines.append("</g>")
    lines.append("</svg>")
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"\n".join(lines))


# ----------------------------------------------------------------------------
# settings UI
# ----------------------------------------------------------------------------

OPTS = [
    ("poche",    "Poche: fill closed cut solids"),
    ("depth",    "Depth fade: lighter lines further behind the cut"),
    ("hidden",   "Hidden lines (dashed)"),
    ("tangent",  "Tangent edges (usually off for clean drawings)"),
    ("portrait", "Portrait page"),
    ("rhino",    "Also draw the result into Rhino (CleanMake2D layers)"),
]
DEFAULTS = {"poche": True, "depth": True, "hidden": False, "tangent": False,
            "portrait": False, "rhino": True}


def ask_settings(s):
    labels = [l for l, _ in SCALES]
    cur = s.get("scale", '1/8" = 1\'-0"')
    pick = rs.ListBox(labels, "Drawing scale", "CleanMake2D - " + s["name"], cur if cur in labels else labels[3])
    if pick is None:
        return False
    if pick != s.get("scale"):
        s.pop("paper_in", None)
    s["scale"] = pick
    s["ratio"] = dict(SCALES)[pick]

    plabels = [l for l, _ in PAPERS]
    cur = s.get("paper", "Fit to drawing")
    pick = rs.ListBox(plabels, "Page size (the SVG artboard)", "CleanMake2D - " + s["name"],
                      cur if cur in plabels else plabels[0])
    if pick is None:
        return False
    if pick != s.get("paper"):
        s.pop("paper_in", None)
    s["paper"] = pick

    items = [(label, bool(s.get(key, DEFAULTS[key]))) for key, label in OPTS]
    res = rs.CheckListBox(items, "Options", "CleanMake2D - " + s["name"])
    if res is None:
        return False
    for (key, _), (_, val) in zip(OPTS, res):
        if key == "portrait" and bool(val) != bool(s.get("portrait", False)):
            s.pop("paper_in", None)
        s[key] = bool(val)
    return True


def pick_objects():
    """[] = everything visible, None = cancelled."""
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt("Select objects to draw. Press Enter for everything visible")
    go.AcceptNothing(True)
    go.EnablePreSelect(True, True)
    go.GroupSelect = True
    go.SubObjectSelect = False
    go.DeselectAllBeforePostSelect = False
    res = go.GetMultiple(1, 0)
    if res == Rhino.Input.GetResult.Nothing:
        return []
    if res != Rhino.Input.GetResult.Object:
        return None
    return [go.Object(i).ObjectId for i in range(go.ObjectCount)]


NEW = "+ New drawing from the active view"
ACT_REDRAW = "Redraw from the model (same view, scale and page)"
ACT_EXPORT = "Re-export SVG from the Rhino linework only (keeps edits made in Rhino)"
ACT_EDIT = "Change scale / page / options, then redraw"
ACT_VIEW = "Re-capture camera + clipping planes from the active view, then redraw"
ACT_DELETE = "Delete this drawing (its Rhino layers + settings)"


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def build(s, ids):
    warn = []
    vpinfo = viewport_info(s)
    planes = clip_planes(s)
    look = vpinfo.CameraDirection
    look.Unitize()
    is_plan = look.Z < -0.7          # "Overhead" layers only mean something in plan views
    items = []
    for geo, xf, lp in collect_objects(ids):
        cat = category_for_layer(lp)
        if cat == "OVERHEAD" and not is_plan:
            cat = "BUILDING"
        if cat != "NODRAW":
            items.append((geo, xf, lp, cat))
    main_items = [it for it in items if it[3] != "OVERHEAD"]
    if not main_items:
        rs.MessageBox("Nothing to draw: no visible surfaces, solids, meshes or curves.", 0, "CleanMake2D")
        return None

    Rhino.RhinoApp.SetCommandPrompt("CleanMake2D: computing hidden lines for %d objects..." % len(main_items))
    hld = compute_hld(main_items, vpinfo, planes, s)
    if hld is None:
        rs.MessageBox("Hidden-line computation failed.", 0, "CleanMake2D")
        return None

    # first run of this drawing: fix the alignment anchor + page size
    w2h = hld.WorldToHiddenLine
    if "anchor" not in s:
        bb = rg.BoundingBox.Empty
        for it in main_items:
            bb.Union(it[0].GetBoundingBox(it[1]))
        s["anchor"] = [bb.Center.X, bb.Center.Y, bb.Center.Z]
    a = w2h * rg.Point3d(*s["anchor"])
    s["anchor2d"] = [a.X, a.Y]

    if s.get("parallel", True):
        k = unit_scale(Rhino.UnitSystem.Inches) * 72.0 / float(s["ratio"])
    else:
        k = None

    depth_fn = depth_function(hld, vpinfo, planes, s)
    layers, cut_src, stats = classify(hld, main_items, depth_fn, s)
    layers["OVERHEAD"] = overhead_lines(items, vpinfo, s)

    bbox = rg.BoundingBox.Empty
    for crvs in layers.values():
        for c in crvs:
            bbox.Union(c.GetBoundingBox(False))
    if not bbox.IsValid:
        rs.MessageBox("The drawing came out empty. Check the view direction and clipping planes.", 0, "CleanMake2D")
        return None

    if k is None:   # perspective: fit to page instead of a true scale
        pw, ph = (dict(PAPERS)[s["paper"]] or (17.0, 11.0))
        if s.get("portrait"):
            pw, ph = ph, pw
        ext_x = max(abs(bbox.Max.X - a.X), abs(bbox.Min.X - a.X)) * 2
        ext_y = max(abs(bbox.Max.Y - a.Y), abs(bbox.Min.Y - a.Y)) * 2
        k = min((pw - 2 * MARGIN_IN) * 72 / max(ext_x, 1e-9), (ph - 2 * MARGIN_IN) * 72 / max(ext_y, 1e-9))
        warn.append("Perspective view: no true scale, the drawing is fitted to the page.")
    s["k"] = k

    cut_lines, hatches = cut_regions(cut_src, k, warn, s.get("poche", True))
    if cut_lines:
        layers["CUT"] = cut_lines
    for name in list(layers.keys()):
        layers[name] = tidy(layers[name], k)

    if "paper_in" not in s:
        size = dict(PAPERS)[s["paper"]]
        if size is None:
            ext_x = max(abs(bbox.Max.X - a.X), abs(bbox.Min.X - a.X)) * 2 * k / 72.0
            ext_y = max(abs(bbox.Max.Y - a.Y), abs(bbox.Min.Y - a.Y)) * 2 * k / 72.0
            size = (math.ceil(ext_x + 2 * MARGIN_IN), math.ceil(ext_y + 2 * MARGIN_IN))
        elif s.get("portrait"):
            size = (size[1], size[0])
        s["paper_in"] = [float(size[0]), float(size[1])]

    page_w, page_h = s["paper_in"]
    over_x = (max(abs(bbox.Max.X - a.X), abs(bbox.Min.X - a.X)) * k / 72.0) - page_w / 2.0
    over_y = (max(abs(bbox.Max.Y - a.Y), abs(bbox.Min.Y - a.Y)) * k / 72.0) - page_h / 2.0
    if over_x > 0 or over_y > 0:
        warn.append("The drawing runs off the %s page by up to %.1f in. Pick a bigger page or "
                    "smaller scale (Change scale / page)." % (s["paper"], max(over_x, over_y)))

    if "place" not in s:      # where the Rhino copy goes: right of the model
        mb = rg.BoundingBox.Empty
        for it in main_items:
            mb.Union(it[0].GetBoundingBox(it[1]))
        gap = mb.Diagonal.Length * 0.15
        s["place"] = [mb.Max.X + gap + (a.X - bbox.Min.X), mb.Min.Y + (a.Y - bbox.Min.Y)]

    stats["poche"] = len(hatches)
    return layers, hatches, stats, warn


def reg_curves(s):
    ax, ay = s["anchor2d"]
    r = 9.0 / s["k"]
    return [rg.LineCurve(rg.Point3d(ax - r, ay, 0), rg.Point3d(ax + r, ay, 0)),
            rg.LineCurve(rg.Point3d(ax, ay - r, 0), rg.Point3d(ax, ay + r, 0)),
            rg.ArcCurve(rg.Circle(rg.Point3d(ax, ay, 0), r / 2))]


def export(s, layers, hatches):
    page = Page(s, s["k"])
    path = s.get("svg") or default_svg_path(s["name"])
    s["svg"] = path
    write_svg(path, s, page, layers, hatches)
    return path


def run(auto=None):
    """auto: optional dict for scripted use, e.g. {"name": "Plan L1", "scale": '1/8" = 1\'-0"',
    "paper": "ARCH D 36 x 24", "ids": None, "svg": r"C:\\...\\plan.svg"}"""
    drawings = load_drawings()
    ids = None

    if auto:
        s = drawings.get(auto["name"], {"name": auto["name"]})
        for key in DEFAULTS:
            s.setdefault(key, DEFAULTS[key])
        s.update(dict((k2, v) for k2, v in auto.items() if k2 not in ("ids", "action")))
        s["ratio"] = dict(SCALES)[s["scale"]]
        if "view" not in s and not capture_view(s):
            return None
        ids = auto.get("ids")
        action = ACT_EXPORT if auto.get("action") == "export" else ACT_REDRAW
    else:
        items = [NEW] + sorted(drawings.keys())
        last = sc.doc.Strings.GetValue(SECTION + "_last", "name")
        pick = rs.ListBox(items, "Pick a drawing to update, or start a new one from the active view.",
                          "CleanMake2D", last if last in items else items[0])
        if pick is None:
            return None
        if pick == NEW:
            view_name = sc.doc.Views.ActiveView.ActiveViewport.Name
            guess = {"Top": "Plan", "Front": "Elevation South", "Right": "Elevation East",
                     "Back": "Elevation North", "Left": "Elevation West"}.get(view_name, view_name)
            name = clean_name(rs.StringBox("Drawing name (letters, numbers, spaces)", guess, "CleanMake2D"))
            if not name:
                return None
            if name in drawings and rs.MessageBox("'%s' already exists. Replace its settings?" % name,
                                                  4 | 32, "CleanMake2D") != 6:
                return None
            s = {"name": name}
            for key in DEFAULTS:
                s[key] = DEFAULTS[key]
            if not capture_view(s) or not ask_settings(s):
                return None
            action = ACT_REDRAW
        else:
            s = drawings[pick]
            action = rs.ListBox([ACT_REDRAW, ACT_EXPORT, ACT_EDIT, ACT_VIEW, ACT_DELETE],
                                "What should happen to '%s'?" % pick, "CleanMake2D", ACT_REDRAW)
            if action is None:
                return None
            if action == ACT_DELETE:
                clear_drawing_layers(s)
                sc.doc.Strings.Delete(SECTION, pick)
                sc.doc.Views.Redraw()
                print("CleanMake2D: deleted '%s'." % pick)
                return None
            if action == ACT_EDIT and not ask_settings(s):
                return None
            if action == ACT_VIEW and not capture_view(s):
                return None
        sc.doc.Strings.SetString(SECTION + "_last", "name", s["name"])

        if action != ACT_EXPORT:
            ids = pick_objects()
            if ids is None:
                return None

    if action == ACT_EXPORT:
        if "anchor2d" not in s:
            rs.MessageBox("Draw it once first.", 0, "CleanMake2D")
            return None
        layers, hatches = read_from_rhino(s)
        path = export(s, layers, hatches)
        save_drawing(s)
        print("CleanMake2D: re-exported '%s' from Rhino linework -> %s" % (s["name"], path))
        return path

    result = build(s, ids)
    if result is None:
        return None
    layers, hatches, stats, warn = result
    if s.get("rhino", True):
        write_to_rhino(s, layers, hatches, reg_curves(s))
    else:
        clear_drawing_layers(s)
    path = export(s, layers, hatches)
    save_drawing(s)
    sc.doc.Views.Redraw()

    counts = ", ".join("%s %d" % (n, len(layers.get(n, []))) for n in LAYER_NAMES
                       if layers.get(n))
    print("CleanMake2D: '%s' at %s on %s -> %s" % (s["name"], s["scale"], s["paper"], path))
    print("  paths: %s, poche regions %d" % (counts, len(hatches)))
    for w in warn:
        print("  NOTE: " + w)
    if not auto and warn:
        rs.MessageBox("\n\n".join(warn), 48, "CleanMake2D")
    return path


if __name__ == "__main__":
    run()
