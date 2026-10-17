"""Direction B "Material You pastel": the full 17-cursor Windows set, drawn headless.

Talks to a headless `photocraft-cli serve --port 7879` session (pcb.py), never to the
live app on 7878. Reproducible from an empty session:

    python draw_b.py [--out DIR] [--port N] [--token-file PATH]
    python draw_b.py spec       # print the spec that would be written (no drawing)

--out defaults to design/ and must lie under the server's --automation-write-root.

Every cursor lives on a 32-unit grid (1 unit = 1 px of the 32 px cursor). The
artwork of each role is drawn once at SCALE px/unit into its own group `cur/<Name>`,
animation frames into `anim/<Name>/<i>`, and the same geometry again at true
32 px and 48 px on dark and light panels (the board).

Outputs (--out, default design/):  b-pastel.pcraft, b-pastel.png (board, 1:1), b-pastel-large.png,
b-pastel-anim.png, b-pastel-overview.png, b-pastel-spec.json
"""
import json, math, pathlib, sys
import pcb
from pcb import connect, send

ROOT = pathlib.Path(__file__).resolve().parents[2].as_posix()
DESIGN = ROOT + "/design"                # output folder; --out replaces it
SCALE = 8                                # enlarged frames: px per grid unit
W = 1960
KR, LAT = "Malgun Gothic", "Inter"
MARK = "#3D7BFF"                         # hotspot marker (guides only)
PAGE, TILE = "#101317", "#181C22"
DARK, LIGHT = "#0B0F12", "#F3F4F6"

# ---------------------------------------------------------------- connection
_conn = {}
_n = [0]


def rpc(method, params=None):
    for attempt in range(2):
        if "f" not in _conn:
            _conn["s"], _conn["f"] = connect()
        _n[0] += 1
        try:
            r = send(_conn["f"], _n[0], method, params or {})
            break
        except (SystemExit, OSError):
            _conn.clear()
            if attempt:
                raise
    if not r.get("ok"):
        sys.exit(f"{method} failed: {r.get('error')}\nparams={json.dumps(params)[:800]}")
    return r.get("result")


def run(cmd, params=None):
    return rpc("engine.execute", {"command": cmd, "params": params or {}})


# ---------------------------------------------------------------- layer bookkeeping
_bucket = []


def _keep(layer):
    _bucket.append(layer)
    return layer


def group(name, fn):
    """Run fn() (which creates layers), then wrap everything it made into a top-level group."""
    start = len(_bucket)
    fn()
    made = _bucket[start:]
    del _bucket[start:]
    gid = run("layer.new.group", {"name": name})["layer"]
    if made:
        run("layer.moveTo", {"layers": made, "target": gid, "position": "into"})
    run("layer.select", {"layer": gid})          # next layers go above the group, at top level
    return gid


def style(layer, *effects):
    run("layer.layerStyle.replace",
        {"layer": layer, "effects": [{"kind": k, "params": p} for k, p in effects]})


class Grid:
    """Maps 32-grid units to document pixels for one cursor at scale s."""
    def __init__(self, ox, oy, s=SCALE):
        self.ox, self.oy, self.s = ox, oy, s

    def pt(self, x, y):
        return [round(self.ox + x * self.s, 3), round(self.oy + y * self.s, 3)]

    def rect(self, x, y, w, h):
        return [self.ox + x * self.s, self.oy + y * self.s, w * self.s, h * self.s]

    def px(self, units):
        return units * self.s


def shape(name, fill, stroke=None, **kw):
    p = {"name": name, "fill": fill, "stroke": stroke}
    p.update(kw)
    return _keep(run("shape.create", p)["layer"])


def text(x, y, t, size, color, name, font=KR, **kw):
    p = {"x": x, "y": y, "text": t, "size": size, "color": color, "name": name, "font": font}
    p.update(kw)
    return _keep(run("type.create", p)["layer"])


# ---------------------------------------------------------------- geometry helpers (grid units)
def rounded(pts, cuts, steps=7):
    """Polygon with corner i replaced by a sampled quadratic curve that starts
    cuts[i] units from the vertex (0 = keep the corner)."""
    out, n = [], len(pts)
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        d = cuts[i]
        if d <= 0:
            out.append(p1)
            continue

        def toward(a, b, d):
            l = math.dist(a, b)
            t = min(d, l * 0.45) / l
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        a, b = toward(p1, p0, d), toward(p1, p2, d)
        for k in range(steps + 1):
            t = k / steps
            out.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * p1[0] + t * t * b[0],
                        (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * p1[1] + t * t * b[1]))
    return out


def rot(pts, deg, c=(16, 16)):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(c[0] + (x - c[0]) * ca - (y - c[1]) * sa, c[1] + (x - c[0]) * sa + (y - c[1]) * ca)
            for x, y in pts]


def path(g, *polys, closed=True):
    return {"subpaths": [{"closed": closed, "knots": [g.pt(*q) for q in poly]} for poly in polys]}


def union(g, name, fill, stroke, parts):
    """Unioned rounded rects (x, y, w, h, radius|[tl,tr,br,bl]) -> one outline, one stroke."""
    first = None
    for x, y, w, h, r in parts:
        rad = [v * g.s for v in r] if isinstance(r, list) else r * g.s
        p = {"kind": "roundedRect", "rect": g.rect(x, y, w, h), "radii": rad}
        if first is None:
            first = shape(name, fill, stroke, **p)
        else:
            p.update({"addTo": first, "op": "combine"})
            run("shape.create", p)
    run("layer.combineShapes.mergeShapeComponents", {"layer": first})
    return first


def lines(g, name, segs, width, color, opacity=100):
    return shape(name, None, {"width": g.px(width), "color": color, "opacity": opacity, "cap": "round"},
                 kind="path", path=path(g, *segs, closed=False))


def circle_rect(g, r, c=(16, 16)):
    return g.rect(c[0] - r, c[1] - r, 2 * r, 2 * r)


def donut(g, name, r_out, r_in, fill, stroke, c=(16, 16)):
    l = shape(name, fill, stroke, kind="ellipse", rect=circle_rect(g, r_out, c))
    run("shape.create", {"kind": "ellipse", "rect": circle_rect(g, r_in, c), "addTo": l, "op": "subtract"})
    run("layer.combineShapes.mergeShapeComponents", {"layer": l})
    return l


def arc(g, name, r, width, color, frac, start=0.0, c=(16, 16)):
    """Round-capped arc as a FILLED outline (no dashed stroke: Photocraft renders dashed
    strokes too thin at 1-1.5 px/unit, so the 32/48 px sizes lost the indicator).
    Visible length incl. caps = `frac` of the circle, starting `start` (0..1) clockwise from 12."""
    hw = width / 2
    a0 = -math.pi / 2 + 2 * math.pi * (start % 1.0) + hw / r       # centre-line ends (caps add hw)
    sweep = max(2 * math.pi * frac - 2 * hw / r, 0.01)
    a1 = a0 + sweep
    n = max(6, int(math.degrees(sweep) / 3))
    on = lambda rad, t: (c[0] + rad * math.cos(t), c[1] + rad * math.sin(t))
    pts = [on(r + hw, a0 + sweep * k / n) for k in range(n + 1)]
    e1 = on(r, a1)
    pts += [(e1[0] + hw * math.cos(a1 + math.pi * k / 10), e1[1] + hw * math.sin(a1 + math.pi * k / 10))
            for k in range(1, 10)]
    pts += [on(r - hw, a1 - sweep * k / n) for k in range(n + 1)]
    e0 = on(r, a0)
    pts += [(e0[0] + hw * math.cos(a0 + math.pi + math.pi * k / 10), e0[1] + hw * math.sin(a0 + math.pi + math.pi * k / 10))
            for k in range(1, 10)]
    return shape(name, color, None, kind="path", path=path(g, pts))


def shadow(color, opacity, dist, size):
    return lambda g: ("dropShadow", {"color": color, "opacity": opacity, "blend": "multiply",
                                     "angle": 90, "distance": max(1, round(g.px(dist))),
                                     "spread": 0, "size": max(1, round(g.px(size)))})


def drop_outline(tip, axis_deg, d, r, n=40):
    """Convex hull of a sharp tip and a circle (a map-pin drop)."""
    a = math.radians(axis_deg)
    c = (tip[0] + d * math.cos(a), tip[1] + d * math.sin(a))
    half = math.asin(r / d)
    L = math.sqrt(d * d - r * r)
    pl = (tip[0] + L * math.cos(a + half), tip[1] + L * math.sin(a + half))
    pr = (tip[0] + L * math.cos(a - half), tip[1] + L * math.sin(a - half))
    al = math.atan2(pl[1] - c[1], pl[0] - c[0])
    ar = math.atan2(pr[1] - c[1], pr[0] - c[0])
    span = (ar - al) % (2 * math.pi)
    if span < math.pi:
        span -= 2 * math.pi
    pts = [tip, pl]
    for k in range(1, n):
        t = al + span * k / n
        pts.append((c[0] + r * math.cos(t), c[1] + r * math.sin(t)))
    pts.append(pr)
    return pts, c


# ================================================================ B palette (approved)
B_T10, B_T40, B_T80, B_T90, B_T95 = "#21005D", "#6750A4", "#D0BCFF", "#EADDFF", "#F6EDFF"
B_HI = "#FBF7FF"                                            # body top / light glyph tone
# badges stand on the desktop itself: tone 50 sits halfway, ~4.2:1 against both dark and light screens
# (tone 40 is kept for accents that sit on the lavender body/track, where it has 5:1)
B_T50 = "#7F67BE"
B_SHADOW = shadow("#1D1036", 32, 0.7, 1.6)

# breathing: the body gradient deepens one tonal step and returns (geometry never moves)
BREATH_PEAK = ("#F1E8FF", "#D9C7FF")


def mix(a, b, t):
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(ca, cb))


def body(t=0.0):
    top, bot = mix(B_HI, BREATH_PEAK[0], t), mix(B_T90, BREATH_PEAK[1], t)
    return {"gradient": {"stops": [[0, top], [1, bot]], "angle": -60, "style": "linear"}}


def edge(g, w=1.4):
    return {"width": g.px(w), "color": B_T10, "align": "center", "join": "round"}


def rim(g, w=1.0):
    """Precision cursors: 1 px dark rim around a 1 px light core, pixel-aligned at 32 px."""
    return {"width": g.px(w), "color": B_T10, "align": "outside", "join": "round"}


BODY_W, BADGE_W = 1.4, 1.2

# ---------------------------------------------------------------- arrow family
B_DART = [(3, 2), (3, 24), (9.2, 18.8), (18.8, 18.2)]          # hotspot = tip (3, 2)
B_DART_CUTS = [1.1, 2.2, 1.6, 2.2]
SLOT = (24.3, 24.3)                                            # modifier badge slot centre
SLOT_R = 6.1


def arrow_body(g, tag, t=0.0):
    l = shape(f"{tag} arrow", body(t), edge(g), kind="path", path=path(g, rounded(B_DART, B_DART_CUTS)))
    style(l, B_SHADOW(g))
    return l


def b_arrow(g, tag, t=0.0):
    arrow_body(g, tag, t)


def badge_disc(g, tag):
    l = shape(f"{tag} badge", B_T50, edge(g, BADGE_W), kind="ellipse", rect=circle_rect(g, SLOT_R, SLOT))
    style(l, B_SHADOW(g))
    return l


def b_help(g, tag):
    arrow_body(g, tag)
    badge_disc(g, tag)
    cx, cy = SLOT
    hook_c, hook_r = (cx, cy - 1.3), 2.1
    pts = []
    for k in range(15):                                        # from 9 o'clock over the top to ~4 o'clock
        a = math.pi + (math.pi + 0.62) * k / 14
        pts.append((hook_c[0] + hook_r * math.cos(a), hook_c[1] + hook_r * math.sin(a)))
    pts += [(cx, cy + 1.3)]
    lines(g, f"{tag} help mark", [pts], 1.7, B_HI)
    shape(f"{tag} help dot", B_HI, None, kind="ellipse", rect=circle_rect(g, 1.05, (cx, cy + 3.6)))


def mini_spinner(g, tag, phase=0.0):
    # at 12 px a ring has no room for a margin around the arc, so the arc fills the whole
    # band (ring fill -> arc -> outline on top): it reads as a ring with a dark moving segment
    r_out, r_in = SLOT_R, 2.9
    l = donut(g, f"{tag} spinner track", r_out, r_in, B_T90, None, c=SLOT)
    style(l, B_SHADOW(g))
    frac, start = spin_state(phase)
    arc(g, f"{tag} spinner arc", (r_out + r_in) / 2, r_out - r_in, B_T40, frac, start, c=SLOT)
    donut(g, f"{tag} spinner rim", r_out, r_in, None, edge(g, BADGE_W), c=SLOT)


def b_appstarting(g, tag, phase=0.0):
    arrow_body(g, tag)
    mini_spinner(g, tag, phase)


def b_pin(g, tag):
    arrow_body(g, tag)
    cx = SLOT[0]
    pts, c = drop_outline((cx, 30.9), -90, 8.0, 5.0)
    l = shape(f"{tag} pin", B_T50, edge(g, BADGE_W), kind="path", path=path(g, rounded(pts, [0.6] + [0] * (len(pts) - 1))))
    style(l, B_SHADOW(g))
    shape(f"{tag} pin hole", B_HI, None, kind="ellipse", rect=circle_rect(g, 1.9, c))


def b_person(g, tag):
    arrow_body(g, tag)
    cx = SLOT[0]
    l = shape(f"{tag} person", B_T50, edge(g, BADGE_W), kind="ellipse", rect=circle_rect(g, 3.0, (cx, 20.5)))
    run("shape.create", {"kind": "roundedRect", "rect": g.rect(cx - 5.9, 24.6, 11.8, 6.0),
                         "radii": [5.6 * g.s, 5.6 * g.s, 1.7 * g.s, 1.7 * g.s], "addTo": l, "op": "combine"})
    run("layer.combineShapes.mergeShapeComponents", {"layer": l})
    style(l, B_SHADOW(g))


# ---------------------------------------------------------------- hand (approved)
B_HAND = [                                                     # hotspot = (11.7, 2)
    (9, 1.5, 5.4, 17, 2.7),                     # index finger
    (8, 12.5, 16.5, 16.5, [3, 5, 6.5, 6.5]),    # fist
    (13.4, 10.2, 4.6, 8, 2.3),                  # middle knuckle
    (17.4, 11.0, 4.4, 8, 2.2),                  # ring knuckle
    (20.9, 12.4, 3.6, 7, 1.8),                  # little knuckle
    (4.4, 16.2, 7.6, 6.6, 3.3),                 # thumb
]
B_HAND_LINES = [[(17.6, 13.4), (17.6, 16.2)], [(21.1, 14.6), (21.1, 17.0)]]


def b_hand(g, tag, t=0.0):
    l = union(g, f"{tag} hand", body(t), edge(g), B_HAND)
    style(l, B_SHADOW(g))
    lines(g, f"{tag} hand knuckles", B_HAND_LINES, 1.0, B_T10)


# ---------------------------------------------------------------- busy (approved) + motion
SPIN_FRAMES = 12


def spin_state(phase):
    """Indicator arc for a loop phase 0..1: one full turn per loop, length breathing 24..44 %
    (Material-style indeterminate arc), phase 0 = the approved static 34 % arc at 12 o'clock."""
    frac = 0.34 + 0.10 * math.sin(2 * math.pi * phase)
    return frac, phase


def b_wait(g, tag, phase=0.0):
    l = donut(g, f"{tag} busy track", 12.6, 7.4, B_T90, edge(g, 1.3))
    style(l, B_SHADOW(g))
    frac, start = spin_state(phase)
    arc(g, f"{tag} busy indicator", 10, 2.6, B_T40, frac, start)


# ---------------------------------------------------------------- precision: crosshair, I-beam
def rect_pts(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def b_crosshair(g, tag):
    # light 1 px core on pixel column/row 16 (centre 16.5), 1 px dark rim outside
    arms = [rect_pts(16, 5, 17, 12), rect_pts(16, 21, 17, 28),
            rect_pts(5, 16, 12, 17), rect_pts(21, 16, 28, 17),
            rect_pts(16, 16, 17, 17)]
    l = shape(f"{tag} crosshair", B_HI, rim(g), kind="path", path=path(g, *arms))
    style(l, B_SHADOW(g))


def b_ibeam(g, tag):
    pts = [(13, 6), (20, 6), (20, 7), (17, 7), (17, 25), (20, 25), (20, 26), (13, 26),
           (13, 25), (16, 25), (16, 7), (13, 7)]
    l = shape(f"{tag} ibeam", B_HI, rim(g), kind="path", path=path(g, pts))
    style(l, B_SHADOW(g))


# ---------------------------------------------------------------- handwriting pen
PEN_TIP, PEN_LEN, PEN_HW, NIB = (3, 3), 27, 3.3, 7.0


def pen_pts(local):
    a = math.radians(45)
    return [(PEN_TIP[0] + u * math.cos(a) - v * math.sin(a), PEN_TIP[1] + u * math.sin(a) + v * math.cos(a))
            for u, v in local]


def b_nwpen(g, tag):
    outline = [(0, 0), (NIB, -PEN_HW), (PEN_LEN, -PEN_HW), (PEN_LEN, PEN_HW), (NIB, PEN_HW)]
    geom = path(g, rounded(pen_pts(outline), [0.5, 0.6, 2.4, 2.4, 0.6]))
    b = shape(f"{tag} pen body", body(), None, kind="path", path=geom)
    style(b, B_SHADOW(g))
    shape(f"{tag} pen nib", B_T80, None, kind="path",
          path=path(g, pen_pts([(0, 0), (NIB, -PEN_HW), (NIB, PEN_HW)])))
    shape(f"{tag} pen point", B_T10, None, kind="path",
          path=path(g, pen_pts([(0, 0), (2.6, -PEN_HW * 2.6 / NIB), (2.6, PEN_HW * 2.6 / NIB)])))
    shape(f"{tag} pen band", B_T40, None, kind="path",
          path=path(g, pen_pts(rect_pts(19.6, -PEN_HW, 22.2, PEN_HW))))
    shape(f"{tag} pen rim", None, edge(g), kind="path", path=geom)
    lines(g, f"{tag} pen nib line", [pen_pts([(NIB, -PEN_HW), (NIB, PEN_HW)])], 1.0, B_T10)


# ---------------------------------------------------------------- unavailable
def b_no(g, tag):
    l = shape(f"{tag} no", body(), edge(g), kind="ellipse", rect=circle_rect(g, 11.6))
    run("shape.create", {"kind": "ellipse", "rect": circle_rect(g, 7.7), "addTo": l, "op": "subtract"})
    bar = rot(rect_pts(16 - 9.0, 16 - 1.9, 16 + 9.0, 16 + 1.9), 45)
    run("shape.create", {"kind": "path", "path": path(g, bar), "addTo": l, "op": "combine"})
    run("layer.combineShapes.mergeShapeComponents", {"layer": l})
    style(l, B_SHADOW(g))


# ---------------------------------------------------------------- directional arrows
# one shared arrowhead: tip, swept-back barbs (echo of the dart's notch), 3.6-unit shaft
HEAD = dict(len=6.5, bw=6.0, sweep=0.5, hw=1.8)
CUT_TIP, CUT_BARB, CUT_JOIN, CUT_IN = 1.1, 1.4, 0.6, 0.5


def arm(c, L, head, inner=True):
    """Points (clockwise) of one arm pointing up from centre c, tip at distance L."""
    cx, cy = c
    tip_y = cy - L
    barb_y = tip_y + head["len"] + head["sweep"]
    join_y = tip_y + head["len"]
    hw, bw = head["hw"], head["bw"]
    pts = [(cx - hw, join_y), (cx - bw, barb_y), (cx, tip_y), (cx + bw, barb_y), (cx + hw, join_y)]
    cuts = [CUT_JOIN, CUT_BARB, CUT_TIP, CUT_BARB, CUT_JOIN]
    if inner:
        pts = [(cx - hw, cy - hw)] + pts
        cuts = [CUT_IN] + cuts
    return pts, cuts


def double_arrow(L=12.5, deg=0, c=(16, 16)):
    up, uc = arm(c, L, HEAD, inner=False)
    down = rot(up, 180, c)
    return rot(rounded(up + down, uc + uc), deg, c)


def b_resize(deg, L=12.5):
    def draw(g, tag):
        l = shape(f"{tag} resize", body(), edge(g), kind="path", path=path(g, double_arrow(L, deg)))
        style(l, B_SHADOW(g))
    return draw


def b_sizeall(g, tag):
    head = dict(len=5.4, bw=4.9, sweep=0.4, hw=1.8)
    a, ac = arm((16, 16), 12.5, head)
    pts, cuts = [], []
    for k in range(4):
        pts += rot(a, 90 * k)
        cuts += ac
    l = shape(f"{tag} move", body(), edge(g), kind="path", path=path(g, rounded(pts, cuts)))
    style(l, B_SHADOW(g))


def b_uparrow(g, tag):
    up, uc = arm((16, 16), 14, dict(len=6.6, bw=6.3, sweep=0.5, hw=1.9), inner=False)
    pts = up + [(17.9, 27.5), (14.1, 27.5)]
    l = shape(f"{tag} up", body(), edge(g), kind="path", path=path(g, rounded(pts, uc + [1.9, 1.9])))
    style(l, B_SHADOW(g))


# ================================================================ the 17 roles
# (registry name, Windows Korean label, short board label, draw fn, hotspot in grid units, anim)
ROLES = [
    ("Arrow", "일반 선택", "일반 선택", b_arrow, (3, 2), "breath"),
    ("Help", "도움말 선택", "도움말 선택", b_help, (3, 2), None),
    ("AppStarting", "백그라운드 작업", "백그라운드 작업", b_appstarting, (3, 2), "spin"),
    ("Wait", "사용 중", "사용 중", b_wait, (16, 16), "spin"),
    ("Crosshair", "정밀도 선택", "정밀도 선택", b_crosshair, (16.5, 16.5), None),
    ("IBeam", "텍스트 선택", "텍스트 선택", b_ibeam, (16.5, 16), None),
    ("NWPen", "필기", "필기", b_nwpen, (3, 3), None),
    ("No", "사용할 수 없음", "사용할 수 없음", b_no, (16, 16), None),
    ("SizeNS", "수직 크기 조정", "수직 크기 조정", b_resize(0), (16, 16), None),
    ("SizeWE", "수평 크기 조정", "수평 크기 조정", b_resize(90), (16, 16), None),
    ("SizeNWSE", "대각선 방향 크기 조정 1", "대각선 크기 조정 1", b_resize(-45, 11.8), (16, 16), None),
    ("SizeNESW", "대각선 방향 크기 조정 2", "대각선 크기 조정 2", b_resize(45, 11.8), (16, 16), None),
    ("SizeAll", "이동", "이동", b_sizeall, (16, 16), None),
    ("UpArrow", "대체 선택", "대체 선택", b_uparrow, (16, 2), None),
    ("Hand", "연결 선택", "연결 선택", b_hand, (11.7, 2), "breath"),
    ("Pin", "위치 선택", "위치 선택", b_pin, (3, 2), None),
    ("Person", "사용자 선택", "사용자 선택", b_person, (3, 2), None),
]

SPIN_MS = 100                                      # 12 x 100 ms = 1.2 s per turn (6 jiffies each, exact)
BREATH_T = [0.0, 1 / 3, 2 / 3, 1.0, 2 / 3, 1 / 3]  # tonal depth per frame
BREATH_MS = [1200, 160, 160, 560, 160, 160]        # 2.4 s per breath, long rest


def frames_of(kind):
    if kind == "spin":
        return [{"phase": i / SPIN_FRAMES} for i in range(SPIN_FRAMES)], [SPIN_MS] * SPIN_FRAMES
    if kind == "breath":
        return [{"t": t} for t in BREATH_T], list(BREATH_MS)
    return [], []


# ================================================================ layout
BOARD_Y = 0
PANEL_H = 330
BOARD_H = 90 + 2 * PANEL_H + 20                    # title + two panels
LARGE_Y = BOARD_H + 30
COLS, PITCH_X, X0 = 6, 310, 64
FRAME = 32 * SCALE
LARGE_TOP = LARGE_Y + 110
LARGE_PITCH_Y = FRAME + 84
ANIM_Y = LARGE_TOP + 3 * LARGE_PITCH_Y + 10
ANIM_PITCH_Y = FRAME + 44


def large_origin(i):
    return X0 + (i % COLS) * PITCH_X, LARGE_TOP + (i // COLS) * LARGE_PITCH_Y


def anim_layout():
    """[(role index, frame index, origin)] plus title rows."""
    out, titles, y = [], [], ANIM_Y + 90
    for ri, r in enumerate(ROLES):
        if not r[5]:
            continue
        params, _ = frames_of(r[5])
        titles.append((ri, y))
        y += 40
        for k in range(len(params)):
            if k and k % COLS == 0:
                y += ANIM_PITCH_Y
            out.append((ri, k, (X0 + (k % COLS) * PITCH_X, y)))
        y += ANIM_PITCH_Y + 30
    return out, titles, y


ANIM, ANIM_TITLES, H = anim_layout()
H += 20


def grid_guides(ox, oy, light=False):
    col = "#9AA3AB" if light else "#5A6670"
    g = Grid(ox, oy)
    shape("tile", "#EEF0F3" if light else TILE, kind="rect", rect=g.rect(0, 0, 32, 32))
    segs = [[(0, k), (32, k)] for k in range(4, 32, 4)] + [[(k, 0), (k, 32)] for k in range(4, 32, 4)]
    shape("grid lines", None, {"width": 1, "color": col, "opacity": 18, "cap": "butt"},
          kind="path", path=path(g, *segs, closed=False))
    shape("grid frame", None, {"width": 1, "color": col, "opacity": 50, "align": "inside"},
          kind="rect", rect=g.rect(0, 0, 32, 32))


def hotspot_mark(ox, oy, hx, hy):
    c = Grid(ox, oy).pt(hx, hy)
    shape("hotspot", None, {"width": 2, "color": MARK, "opacity": 90},
          kind="ellipse", rect=[c[0] - 5, c[1] - 5, 10, 10])
    shape("hotspot dot", MARK, None, kind="ellipse", rect=[c[0] - 1.5, c[1] - 1.5, 3, 3])


def fmt(v):
    return f"{v:g}"


# ================================================================ drawing passes
def draw_page():
    shape("page", PAGE, kind="rect", rect=[0, 0, W, H])


def draw_board():
    text(64, 52, "B  머티리얼 유 파스텔 — 17종 실제 크기", 26, "#E6EBEF", "board title", weight=700)
    text(640, 50, "각 칸: 왼쪽 32px · 오른쪽 48px (확대 없이 1:1)", 15, "#7F8A93", "board sub")
    for pi, dark in enumerate([True, False]):
        py = 80 + pi * PANEL_H
        bg = DARK if dark else LIGHT
        ink, mute = ("#E6EBEF", "#7F8A93") if dark else ("#1B2126", "#6A737B")
        shape("board panel", bg, kind="rect", rect=[0, py, W, PANEL_H])
        text(64, py + 36, "어두운 화면" if dark else "밝은 화면", 17, mute, "panel label")
        tag = "dark" if dark else "light"
        for i, r in enumerate(ROLES):
            col, row = i % 9, i // 9
            x = 64 + col * 206
            y = py + 62 + row * 132
            r[3](Grid(x, y + 16, 1), f"{tag} {r[0]} 32")
            r[3](Grid(x + 44, y, 1.5), f"{tag} {r[0]} 48")
            text(x, y + 76, r[2], 13, ink, "board label")
            text(x, y + 94, r[0], 11, mute, "board reg", font=LAT)


def draw_large_guides():
    text(64, LARGE_Y + 52, "확대 원본 — 32 격자, 1단위 = 8px", 24, "#E6EBEF", "large title", weight=700)
    text(64, LARGE_Y + 82, "그룹 cur/<이름>이 내보내기 원본입니다. 파란 원 = 클릭 지점(핫스팟). 격자선 4단위 간격.",
         14, "#7F8A93", "large sub")
    for i, r in enumerate(ROLES):
        ox, oy = large_origin(i)
        grid_guides(ox, oy)
        text(ox, oy + FRAME + 28, r[1], 15, "#E6EBEF", "caption")
        anim = {"spin": " · 회전 애니메이션", "breath": " · 숨쉬기 애니메이션"}.get(r[5], "")
        text(ox, oy + FRAME + 50, f"{r[0]} · 핫스팟 ({fmt(r[4][0])}, {fmt(r[4][1])}){anim}", 12,
             "#7F8A93", "caption reg", font=LAT if not anim else KR)


def draw_anim_guides():
    text(64, ANIM_Y + 52, "움직임 — 프레임 순서대로 (그룹 anim/<이름>/<번호>)", 24, "#E6EBEF", "anim title", weight=700)
    for ri, y in ANIM_TITLES:
        r = ROLES[ri]
        params, ms = frames_of(r[5])
        desc = {"spin": f"{len(params)}프레임 × {ms[0]}ms = 한 바퀴 {sum(ms) / 1000:g}초 · 호 길이 24↔44% 늘었다 줄며 회전",
                "breath": f"{len(params)}프레임 · 한 번 숨쉬기 {sum(ms) / 1000:g}초 · 몸체 톤만 한 단계 짙어졌다 돌아옴 (모양·핫스팟 고정)"}[r[5]]
        text(64, y + 14, f"{r[1]}  ({r[0]})", 17, "#E6EBEF", "anim role")
        text(330, y + 14, desc, 13, "#7F8A93", "anim desc")
    for ri, k, (ox, oy) in ANIM:
        r = ROLES[ri]
        _, ms = frames_of(r[5])
        grid_guides(ox, oy)
        text(ox, oy + FRAME + 22, f"{k}  ·  {ms[k]}ms", 12, "#7F8A93", "frame label", font=LAT)


def draw_large():
    for i, r in enumerate(ROLES):
        ox, oy = large_origin(i)
        group(f"cur/{r[0]}", lambda r=r, ox=ox, oy=oy: r[3](Grid(ox, oy), r[0]))


def draw_anim():
    for ri, k, (ox, oy) in ANIM:
        r = ROLES[ri]
        params, _ = frames_of(r[5])
        group(f"anim/{r[0]}/{k}", lambda r=r, ox=ox, oy=oy, p=params[k]: r[3](Grid(ox, oy), f"{r[0]} f{k}", **p))
    # a 32 px strip of every animation, next to its title, to judge the motion at true size
    def strips():
        for ri, y in ANIM_TITLES:
            r = ROLES[ri]
            params, _ = frames_of(r[5])
            for k, p in enumerate(params):
                r[3](Grid(1320 + k * 50, y - 12, 1), f"{r[0]} strip {k}", **p)
    group("preview/anim 32px", strips)          # preview only, not an export source


def draw_hotspots():
    for i, r in enumerate(ROLES):
        ox, oy = large_origin(i)
        hotspot_mark(ox, oy, *r[4])
    for ri, k, (ox, oy) in ANIM:
        hotspot_mark(ox, oy, *ROLES[ri][4])


# ================================================================ spec
def spec():
    roles = []
    for i, r in enumerate(ROLES):
        ox, oy = large_origin(i)
        params, ms = frames_of(r[5])
        frames = [{"group": f"anim/{r[0]}/{k}", "origin": list(o), "durationMs": ms[k]}
                  for ri, k, o in ANIM if ri == i]
        hx, hy = r[4]
        roles.append({
            "registryName": r[0],
            "labelKo": r[1],
            "group": f"cur/{r[0]}",
            "frameOrigin": [ox, oy],
            "frameSizePx": FRAME,
            "pxPerUnit": SCALE,
            "hotspot": [hx, hy],
            "hotspotPx": {str(sz): [min(sz - 1, math.floor(hx * sz / 32)), min(sz - 1, math.floor(hy * sz / 32))]
                          for sz in (32, 48, 64, 96, 128)},
            "animated": bool(r[5]),
            "animation": None if not r[5] else {
                "kind": {"spin": "rotate", "breath": "breathe"}[r[5]],
                "frames": frames,
                "loopMs": sum(ms),
                "jiffies": [max(1, round(m * 60 / 1000)) for m in ms],
            },
        })
    return {
        "pack": "B Material You pastel",
        "source": "design/b-pastel.pcraft",
        "document": {"width": W, "height": H},
        "grid": {"units": 32, "pxPerUnit": SCALE, "frameSizePx": FRAME,
                 "note": "Render a group's layers alone on transparency, crop [origin, origin + frameSizePx], "
                         "scale to the target size. 1 grid unit = 1 px at 32 px."},
        "hotspotRule": "hotspot is in grid units (continuous, origin = frame top-left); "
                       "pixel hotspot at size N = floor(h * N / 32), listed in hotspotPx",
        "guides": "Groups other than cur/* and anim/* (page, board, guides, preview/*, hotspots) are not export sources.",
        "sizes": [32, 48, 64, 96, 128],
        "palette": {"outline": B_T10, "primary": B_T40, "primaryContainer": B_T80, "track": B_T90,
                    "bodyGradient": [B_HI, B_T90], "badge": B_T50, "breathPeakGradient": list(BREATH_PEAK),
                    "shadow": "#1D1036 32% multiply, distance 0.7u, size 1.6u, angle 90"},
        "roles": roles,
    }


# ================================================================ main
def main():
    rpc("doc.new", {"width": W, "height": H, "background": PAGE, "name": "b-pastel"})
    group("page", draw_page)
    group("board", draw_board)
    group("guides", lambda: (draw_large_guides(), draw_anim_guides()))
    draw_large()
    draw_anim()
    group("hotspots", draw_hotspots)
    rpc("doc.save", {"path": DESIGN + "/b-pastel.pcraft"})
    rpc("doc.render", {"path": DESIGN + "/b-pastel-overview.png", "maxSide": 2048})
    for name, rect in [("b-pastel.png", [0, 0, W, BOARD_H]),
                       ("b-pastel-large.png", [0, LARGE_Y, W, ANIM_Y - LARGE_Y]),
                       ("b-pastel-anim.png", [0, ANIM_Y, W, H - ANIM_Y])]:
        run("image.duplicate", {"name": "crop"})
        run("image.crop", {"x": rect[0], "y": rect[1], "width": rect[2], "height": rect[3]})
        rpc("doc.render", {"path": f"{DESIGN}/{name}", "maxSide": 0 if max(rect[2:]) <= 2048 else 2048})
        rpc("doc.close", {})
    pathlib.Path(DESIGN, "b-pastel-spec.json").write_text(
        json.dumps(spec(), ensure_ascii=False, indent=2), encoding="utf-8")
    print("done,", _n[0], "requests; doc", W, "x", H)


if __name__ == "__main__":
    args = pcb.take_args(sys.argv[1:], pcb.CONFIG)
    if "--out" in args:
        i = args.index("--out")
        DESIGN = pathlib.Path(args[i + 1]).resolve().as_posix()
        del args[i:i + 2]
    if args and args[0] == "spec":
        print(json.dumps(spec(), ensure_ascii=False, indent=1)[:3000])
    else:
        main()
