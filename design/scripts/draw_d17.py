"""Direction D "Capsule Tail": the full 17-cursor Windows set, drawn live in the Photocraft app.

Talks to the running app (pc.py: port 7878 and the token file unless --port / --token-file
or the PHOTOCRAFT_* variables say otherwise). Same file structure as B (draw_b.py /
design/b-pastel-spec.json): one group `cur/<Name>` per role in its own 256x256 frame
(32 units x 8 px), animation frames in `anim/<Name>/<i>`, guides / board / hotspots /
previews in other groups.

    python draw_d17.py          # new document d-capsule, draw all, save .pcraft/.png/spec
    python draw_d17.py redraw   # active document must be d-capsule: clear it and redraw
    python draw_d17.py finish   # only read back, save and export (after a manual tweak)
    python draw_d17.py spec     # print the spec that would be written (no drawing)
    --out DIR                   # output folder (default design), sent to the app as is: keep it
                                # relative to the app's --automation-write-root (the project)

Grammar (two parts only):
  HEAD    = white pointing part, ink outline 1.4 units
  CAPSULE = lime state part, ink outline 1.4 (small parts 1.2)
  ink marks only ever sit on lime; the tail slot (arrow axis, 2.6-unit gap) carries the state.
"""
import json, math, pathlib, sys
import pc
from pc import connect, send

ROOT = pathlib.Path(__file__).resolve().parents[2].as_posix()
OUT = "design"                           # output folder as the app sees it; --out replaces it
SCALE = 8
W = 1960
KR, LAT = "Malgun Gothic", "Inter"
MARK = "#3D7BFF"
PAGE, TILE = "#101317", "#181C22"
DARK, LIGHT = "#0B0F12", "#F3F4F6"
DOC_NAME = "d-capsule"

INK, WHITE, LIME = "#0F1411", "#FFFFFF", "#C4F23A"
EDGE, SMALL_EDGE, RIM = 1.4, 1.2, 1.0

# ---------------------------------------------------------------- connection
_conn, _n = {}, [0]


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
    start = len(_bucket)
    fn()
    made = _bucket[start:]
    del _bucket[start:]
    gid = run("layer.new.group", {"name": name})["layer"]
    if made:
        run("layer.moveTo", {"layers": made, "target": gid, "position": "into"})
    run("layer.select", {"layer": gid})          # next layers land above the group, top level
    return gid


class Grid:
    def __init__(self, ox, oy, s=SCALE):
        self.ox, self.oy, self.s = ox, oy, s

    def pt(self, x, y):
        return [round(self.ox + x * self.s, 3), round(self.oy + y * self.s, 3)]

    def rect(self, x, y, w, h):
        return [self.ox + x * self.s, self.oy + y * self.s, w * self.s, h * self.s]

    def px(self, u):
        return u * self.s


def shape(name, fill, stroke=None, **kw):
    p = {"name": name, "fill": fill, "stroke": stroke}
    p.update(kw)
    return _keep(run("shape.create", p)["layer"])


def text(x, y, t, size, color, name, font=KR, **kw):
    p = {"x": x, "y": y, "text": t, "size": size, "color": color, "name": name, "font": font}
    p.update(kw)
    return _keep(run("type.create", p)["layer"])


def path(g, *polys, closed=True):
    return {"subpaths": [{"closed": closed, "knots": [g.pt(*q) for q in poly]} for poly in polys]}


def edge(g, w=EDGE):
    return {"width": g.px(w), "color": INK, "align": "center", "join": "round"}


def rim(g, w=RIM):
    """Precision cursors: ink rim outside a pixel-aligned core (crisp at 32 px)."""
    return {"width": g.px(w), "color": INK, "align": "outside", "join": "round"}


def ink_line(g, w, cap="round"):
    return {"width": g.px(w), "color": INK, "cap": cap, "join": "round"}


# ---------------------------------------------------------------- geometry (grid units)
def rounded(pts, cuts, steps=8):
    out, n = [], len(pts)
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        d = cuts[i]
        if d <= 0:
            out.append(p1)
            continue

        def toward(a, b):
            l = math.dist(a, b)
            t = min(d, l * 0.45) / l
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        a, b = toward(p1, p0), toward(p1, p2)
        for k in range(steps + 1):
            t = k / steps
            out.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * p1[0] + t * t * b[0],
                        (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * p1[1] + t * t * b[1]))
    return out


def capsule(p0, p1, r, n=18):
    """Stadium between centre points p0 and p1, radius r (p0 == p1 gives a circle)."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    a = math.atan2(dy, dx) if (dx or dy) else -math.pi / 2
    pts = []
    for c, start in ((p1, a - math.pi / 2), (p0, a + math.pi / 2)):
        for k in range(n + 1):
            t = start + math.pi * k / n
            pts.append((c[0] + r * math.cos(t), c[1] + r * math.sin(t)))
    return pts


def circle(c, r, n=40):
    return [(c[0] + r * math.cos(2 * math.pi * k / n), c[1] + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def add(p, v, k=1.0):
    return (p[0] + v[0] * k, p[1] + v[1] * k)


def unit(deg):
    return (math.cos(math.radians(deg)), math.sin(math.radians(deg)))


def rot(pts, deg, c=(16, 16)):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(c[0] + (x - c[0]) * ca - (y - c[1]) * sa, c[1] + (x - c[0]) * sa + (y - c[1]) * ca)
            for x, y in pts]


def rect_pts(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ================================================================ the HEAD (approved)
HEAD_T, HEAD_L, HEAD_R = (0, 0), (0, 17), (12.5, 12.5)
HEAD_CUTS = [0.45, 1.1, 1.1]
AXIS_DEG = 67.5                                  # tip-angle bisector
AXIS = unit(AXIS_DEG)
TAIL_GAP = 2.6                                   # head base -> state part, along the axis


def _axis_hits_base():
    (lx, ly), (rx, ry) = HEAD_L, HEAD_R
    ex, ey = rx - lx, ry - ly
    det = AXIS[0] * (-ey) - AXIS[1] * (-ex)
    return (lx * (-ey) - ly * (-ex)) / det        # distance tip -> base along the axis


BASE_D = _axis_hits_base()
ARROW_TIP = (3, 2)


def head_poly(tip):
    return rounded([add(tip, p) for p in (HEAD_T, HEAD_L, HEAD_R)], HEAD_CUTS)


def on_axis(d, tip=ARROW_TIP):
    return add(tip, AXIS, d)


def slot_centre(r, tip=ARROW_TIP, gap=TAIL_GAP):
    """Centre of a round state part of radius r sitting in the tail slot."""
    return on_axis(BASE_D + gap + r, tip)


def draw_head(g, tag, tip=ARROW_TIP, fill=WHITE):
    return shape(f"{tag} head", fill, edge(g), kind="path", path=path(g, head_poly(tip)))


# ---------------------------------------------------------------- Arrow: head + detached tail
TAIL_R, TAIL_LEN = 2.4, 5.0
TAIL_DRIFT = 1.4                                  # breathing: tail moves this far out at peak


def d_arrow(g, tag, t=0.0):
    gap = TAIL_GAP + TAIL_DRIFT * t
    p0 = on_axis(BASE_D + gap + TAIL_R)
    p1 = add(p0, AXIS, TAIL_LEN)
    shape(f"{tag} tail", LIME, edge(g), kind="path", path=path(g, capsule(p0, p1, TAIL_R)))
    draw_head(g, tag)


# ---------------------------------------------------------------- AppStarting: tail with a travelling ink bead
WORK_R, WORK_LEN, WORK_BEAD = 2.9, 5.4, 1.25


def d_appstarting(g, tag, phase=0.0):
    p0 = on_axis(BASE_D + TAIL_GAP + WORK_R)
    p1 = add(p0, AXIS, WORK_LEN)
    shape(f"{tag} tail", LIME, edge(g), kind="path", path=path(g, capsule(p0, p1, WORK_R)))
    s = (1 - math.cos(2 * math.pi * phase)) / 2      # ping-pong, eased; 0 = next to the head
    c = add(p0, AXIS, WORK_LEN * s)
    shape(f"{tag} bead", INK, None, kind="path", path=path(g, circle(c, WORK_BEAD)))
    draw_head(g, tag)


# ---------------------------------------------------------------- Help: tail grows into a lime disc with an ink "?"
BADGE_R = 5.5


def d_help(g, tag):
    c = slot_centre(BADGE_R)
    shape(f"{tag} badge", LIME, edge(g), kind="path", path=path(g, circle(c, BADGE_R, 48)))
    cx, cy = c
    hc, hr = (cx, cy - 1.45), 1.85
    pts = [(hc[0] + hr * math.cos(a), hc[1] + hr * math.sin(a))
           for a in [math.pi * (1.0 + 1.25 * k / 14) for k in range(15)]]          # 9 o'clock over the top to ~4:30
    pts += [(cx, cy + 0.35), (cx, cy + 1.05)]
    shape(f"{tag} help hook", None, ink_line(g, 1.75), kind="path", path=path(g, pts, closed=False))
    shape(f"{tag} help dot", INK, None, kind="path", path=path(g, circle((cx, cy + 3.15), 1.05)))
    draw_head(g, tag)


# ---------------------------------------------------------------- Pin: tail becomes a map pin
def drop_outline(c, r, tip_dy, n=40):
    """Map-pin drop: circle (c, r) and a point straight below at distance tip_dy."""
    tip = (c[0], c[1] + tip_dy)
    half = math.asin(r / tip_dy)
    al = math.pi / 2 + (math.pi / 2 - half)        # tangent point angles on the circle
    ar = math.pi / 2 - (math.pi / 2 - half)
    pts = [tip]
    span = 2 * math.pi - (al - ar)
    for k in range(n + 1):
        t = al + span * k / n
        pts.append((c[0] + r * math.cos(t), c[1] + r * math.sin(t)))
    return pts


PIN_R, PIN_TIP = 4.1, 7.0


def d_pin(g, tag):
    c = slot_centre(PIN_R)
    pts = drop_outline(c, PIN_R, PIN_TIP)
    shape(f"{tag} pin", LIME, edge(g), kind="path", path=path(g, rounded(pts, [0.7] + [0] * (len(pts) - 1), 6)))
    shape(f"{tag} pin hole", INK, None, kind="path", path=path(g, circle(c, 1.55)))
    draw_head(g, tag)


# ---------------------------------------------------------------- Person: tail becomes a person (head bead + shoulders)
def d_person(g, tag):
    hr = 2.55
    hc = slot_centre(hr)
    shape(f"{tag} person head", LIME, edge(g, SMALL_EDGE), kind="path", path=path(g, circle(hc, hr)))
    cx = hc[0]
    top, bot, hw = hc[1] + hr + 1.15, 30.2, 5.0
    body = rounded(rect_pts(cx - hw, top, cx + hw, bot), [4.0, 4.0, 1.2, 1.2])
    shape(f"{tag} person body", LIME, edge(g, SMALL_EDGE), kind="path", path=path(g, body))
    draw_head(g, tag)


# ---------------------------------------------------------------- Hand (link): lime head + three click marks
LINK_TIP = (12, 12)
MARK_ANGLES = (-170, -110, -50)                   # 60 deg apart, centred on the side away from the head
MARK_IN, MARK_OUT, MARK_R = 5.6, 8.4, 1.6
MARK_SPREAD = (1.3, 0.6)                          # breathing: inner / outer end move out at peak


def d_hand(g, tag, t=0.0):
    rin, rout = MARK_IN + MARK_SPREAD[0] * t, MARK_OUT + MARK_SPREAD[1] * t
    marks = [capsule(add(LINK_TIP, unit(a), rin), add(LINK_TIP, unit(a), rout), MARK_R, 12) for a in MARK_ANGLES]
    shape(f"{tag} click marks", LIME, edge(g, SMALL_EDGE), kind="path", path=path(g, *marks))
    draw_head(g, tag, LINK_TIP, LIME)


# ---------------------------------------------------------------- Wait: white capsule, three lime beads stretching in turn
PILL = (2, 9.5, 28, 13)
BEAD_X, BEAD_R, BEAD_STRETCH = (8.7, 16, 23.3), 2.4, 2.0
WAVE_FRAMES = 12
WAVE_PEAKS = (0, 3, 6)                            # frame at which bead i is tallest; 8-10 rest


def wave(i, phase):
    k = phase * WAVE_FRAMES
    d = abs(((k - WAVE_PEAKS[i]) + WAVE_FRAMES / 2) % WAVE_FRAMES - WAVE_FRAMES / 2)
    return max(0.0, math.cos(math.pi / 2 * d / 1.5)) if d < 1.5 else 0.0


def d_wait(g, tag, phase=0.0):
    x, y, w, h = PILL
    shape(f"{tag} pill", WHITE, edge(g), kind="roundedRect", rect=g.rect(x, y, w, h), radii=g.px(h / 2))
    cy = y + h / 2
    beads = []
    for i, bx in enumerate(BEAD_X):
        s = BEAD_STRETCH * wave(i, phase)
        beads.append(capsule((bx, cy + s), (bx, cy - s), BEAD_R))
    shape(f"{tag} beads", LIME, edge(g, SMALL_EDGE), kind="path", path=path(g, *beads))


# ---------------------------------------------------------------- precision: Crosshair, IBeam
def d_crosshair(g, tag):
    c = 16.5                                     # pixel column/row 16
    arms = [capsule((c, 5.5), (c, 10.5), 1.5), capsule((c, 22.5), (c, 27.5), 1.5),
            capsule((5.5, c), (10.5, c), 1.5), capsule((22.5, c), (27.5, c), 1.5)]
    shape(f"{tag} arms", LIME, rim(g), kind="path", path=path(g, *arms))
    shape(f"{tag} centre", WHITE, rim(g), kind="path", path=path(g, rect_pts(16, 16, 17, 17)))


def d_ibeam(g, tag):
    shape(f"{tag} stem", WHITE, rim(g), kind="path", path=path(g, rect_pts(16, 7, 17, 25)))
    serifs = [capsule((14, 6), (19, 6), 1.0), capsule((14, 26), (19, 26), 1.0)]
    shape(f"{tag} serifs", LIME, rim(g), kind="path", path=path(g, *serifs))


# ---------------------------------------------------------------- NWPen: white nib + long lime capsule
PEN_TIP = (3, 3)
NIB_L, NIB_W, PEN_GAP, PEN_R, PEN_END = 7.4, 3.3, 1.5, 2.7, 27.2


def pen_pts(local):
    a = math.radians(45)
    return [(PEN_TIP[0] + u * math.cos(a) - v * math.sin(a), PEN_TIP[1] + u * math.sin(a) + v * math.cos(a))
            for u, v in local]


def d_nwpen(g, tag):
    u0 = NIB_L + PEN_GAP + PEN_R
    body = pen_pts(capsule((u0, 0), (PEN_END - PEN_R, 0), PEN_R))
    shape(f"{tag} pen body", LIME, edge(g), kind="path", path=path(g, body))
    nib = pen_pts(rounded([(0, 0), (NIB_L, -NIB_W), (NIB_L, NIB_W)], [0.35, 0.8, 0.8]))
    shape(f"{tag} pen nib", WHITE, edge(g), kind="path", path=path(g, nib))


# ---------------------------------------------------------------- No: lime ring + detached white bar
def d_no(g, tag):
    ro, ri = 12.2, 7.8
    # lime ring: a white ring between two ink edges read as a target (concentric circles) on light screens
    l = shape(f"{tag} ring", LIME, edge(g), kind="path",
              path={"subpaths": [{"closed": True, "knots": [g.pt(*q) for q in circle((16, 16), ro, 64)]},
                                 {"closed": True, "op": "subtract",
                                  "knots": [g.pt(*q) for q in circle((16, 16), ri, 64)]}],
                    "fillRule": "evenodd"})
    br = 2.1
    half = ri - EDGE / 2 - 1.0 - SMALL_EDGE / 2 - br  # 1 unit of background between bar and ring
    u = unit(45)
    shape(f"{tag} bar", WHITE, edge(g, SMALL_EDGE), kind="path",
          path=path(g, capsule(add((16, 16), u, -half), add((16, 16), u, half), br)))


# ---------------------------------------------------------------- directional: point - capsule - point
PT_L, PT_W, PT_CUTS = 7.2, 5.4, [0.5, 0.8, 0.8]
TIP_D, BAR_GAP, BAR_R = 13.8, 1.9, 1.9


def point_poly(tip, deg, L=PT_L, w=PT_W):
    """Symmetric arrowhead with its tip at `tip`, pointing towards `deg`."""
    d = unit(deg)
    n = (-d[1], d[0])
    base = add(tip, d, -L)
    return rounded([tip, add(base, n, w), add(base, n, -w)], PT_CUTS)


def d_resize(deg):
    def draw(g, tag):
        c = (16, 16)
        u = unit(deg)
        half = TIP_D - PT_L - BAR_GAP - BAR_R
        shape(f"{tag} bar", LIME, edge(g), kind="path",
              path=path(g, capsule(add(c, u, -half), add(c, u, half), BAR_R)))
        heads = [point_poly(add(c, u, TIP_D), deg), point_poly(add(c, u, -TIP_D), deg + 180)]
        shape(f"{tag} heads", WHITE, edge(g), kind="path", path=path(g, *heads))
    return draw


def d_sizeall(g, tag):
    c = (16, 16)
    heads = [point_poly(add(c, unit(a), 13.6), a, 6.6, 5.2) for a in (-90, 0, 90, 180)]
    shape(f"{tag} dot", LIME, edge(g), kind="path", path=path(g, circle(c, 3.1)))
    shape(f"{tag} heads", WHITE, edge(g), kind="path", path=path(g, *heads))


def d_uparrow(g, tag):
    tip, L = (16, 2), 8.6
    r = 2.3
    y0 = tip[1] + L + BAR_GAP + r
    shape(f"{tag} shaft", LIME, edge(g), kind="path", path=path(g, capsule((16, y0), (16, 29.6 - r), r)))
    shape(f"{tag} head", WHITE, edge(g), kind="path", path=path(g, point_poly(tip, -90, L, 6.4)))


# ================================================================ the 17 roles
ROLES = [
    ("Arrow", "일반 선택", "일반 선택", d_arrow, (3, 2), "breath"),
    ("Help", "도움말 선택", "도움말 선택", d_help, (3, 2), None),
    ("AppStarting", "백그라운드 작업", "백그라운드 작업", d_appstarting, (3, 2), "travel"),
    ("Wait", "사용 중", "사용 중", d_wait, (16, 16), "wave"),
    ("Crosshair", "정밀도 선택", "정밀도 선택", d_crosshair, (16.5, 16.5), None),
    ("IBeam", "텍스트 선택", "텍스트 선택", d_ibeam, (16.5, 16), None),
    ("NWPen", "필기", "필기", d_nwpen, (3, 3), None),
    ("No", "사용할 수 없음", "사용할 수 없음", d_no, (16, 16), None),
    ("SizeNS", "수직 크기 조정", "수직 크기 조정", d_resize(-90), (16, 16), None),
    ("SizeWE", "수평 크기 조정", "수평 크기 조정", d_resize(0), (16, 16), None),
    ("SizeNWSE", "대각선 방향 크기 조정 1", "대각선 크기 조정 1", d_resize(45), (16, 16), None),
    ("SizeNESW", "대각선 방향 크기 조정 2", "대각선 크기 조정 2", d_resize(-45), (16, 16), None),
    ("SizeAll", "이동", "이동", d_sizeall, (16, 16), None),
    ("UpArrow", "대체 선택", "대체 선택", d_uparrow, (16, 2), None),
    ("Hand", "연결 선택", "연결 선택", d_hand, LINK_TIP, "breath"),
    ("Pin", "위치 선택", "위치 선택", d_pin, (3, 2), None),
    ("Person", "사용자 선택", "사용자 선택", d_person, (3, 2), None),
]

LOOP_MS = 100                                      # 12 x 100 ms = 1.2 s (6 jiffies each, exact)
BREATH_T = [0.0, 1 / 3, 2 / 3, 1.0, 2 / 3, 1 / 3]
BREATH_MS = [1200, 160, 160, 560, 160, 160]        # 2.4 s, same rhythm as B
KIND = {"breath": "breathe", "travel": "travel", "wave": "wave"}


def frames_of(kind):
    if kind in ("travel", "wave"):
        return [{"phase": i / 12} for i in range(12)], [LOOP_MS] * 12
    if kind == "breath":
        return [{"t": t} for t in BREATH_T], list(BREATH_MS)
    return [], []


MOTION_KO = {
    "Arrow": "꼬리 캡슐이 1.4칸 멀어졌다 돌아옴",
    "Hand": "클릭 표시 세 개가 바깥으로 퍼졌다 돌아옴",
    "Wait": "세 점이 차례로 세로 캡슐로 늘었다 돌아옴, 3프레임 쉼",
    "AppStarting": "꼬리 속 잉크 점이 끝까지 갔다 돌아옴",
}

# ================================================================ layout (identical to B)
PANEL_H = 330
BOARD_H = 90 + 2 * PANEL_H + 20
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


def grid_guides(ox, oy, name, light=False):
    col = "#9AA3AB" if light else "#5A6670"
    g = Grid(ox, oy)
    shape(f"tile {name}", "#EEF0F3" if light else TILE, kind="rect", rect=g.rect(0, 0, 32, 32))
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
    shape("board background", PAGE, kind="rect", rect=[0, 0, W, BOARD_H])
    text(64, 52, "D  캡슐 테일 — 17종 실제 크기", 26, "#E6EBEF", "board title", weight=700)
    text(560, 50, "각 칸: 왼쪽 32px · 오른쪽 48px (확대 없이 1:1) · 흰 화살촉 = 가리키는 곳, 라임 캡슐 = 상태",
         15, "#7F8A93", "board sub")
    for j, sw in enumerate([INK, WHITE, LIME]):
        shape("swatch", sw, {"width": 1, "color": "#808890", "opacity": 60, "align": "inside"},
              kind="ellipse", rect=[W - 64 - 22 - (2 - j) * 30, 34, 22, 22])
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
    text(64, LARGE_Y + 82, "그룹 cur/<이름>이 내보내기 원본입니다. 파란 원 = 클릭 지점(핫스팟). 격자선 4단위 간격. "
         "상태는 늘 꼬리 자리(화살 축, 2.6칸 틈)에서 바뀝니다.", 14, "#7F8A93", "large sub")
    for i, r in enumerate(ROLES):
        ox, oy = large_origin(i)
        grid_guides(ox, oy, f"cur/{r[0]}")
        text(ox, oy + FRAME + 28, r[1], 15, "#E6EBEF", "caption")
        anim = {"breath": " · 숨쉬기", "travel": " · 꼬리 속 점 이동", "wave": " · 점 물결"}.get(r[5], "")
        text(ox, oy + FRAME + 50, f"{r[0]} · 핫스팟 ({fmt(r[4][0])}, {fmt(r[4][1])}){anim}", 12,
             "#7F8A93", "caption reg", font=LAT if not anim else KR)


def draw_anim_guides():
    text(64, ANIM_Y + 52, "움직임 — 프레임 순서대로 (그룹 anim/<이름>/<번호>, 0번 = 정지 커서)", 24, "#E6EBEF",
         "anim title", weight=700)
    for ri, y in ANIM_TITLES:
        r = ROLES[ri]
        params, ms = frames_of(r[5])
        desc = f"{len(params)}프레임 · 한 번 {sum(ms) / 1000:g}초 · {MOTION_KO[r[0]]} (핫스팟 고정)"
        text(64, y + 14, f"{r[1]}  ({r[0]})", 17, "#E6EBEF", "anim role")
        text(330, y + 14, desc, 13, "#7F8A93", "anim desc")
    for ri, k, (ox, oy) in ANIM:
        r = ROLES[ri]
        _, ms = frames_of(r[5])
        grid_guides(ox, oy, f"anim/{r[0]}/{k}")
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

    def strips():
        for ri, y in ANIM_TITLES:
            r = ROLES[ri]
            params, _ = frames_of(r[5])
            for k, p in enumerate(params):
                r[3](Grid(1320 + k * 50, y - 12, 1), f"{r[0]} strip {k}", **p)
    group("preview/anim 32px", strips)


def draw_hotspots():
    for i, r in enumerate(ROLES):
        ox, oy = large_origin(i)
        hotspot_mark(ox, oy, *r[4])
    for ri, k, (ox, oy) in ANIM:
        hotspot_mark(ox, oy, *ROLES[ri][4])


# ================================================================ read-back + spec
def walk(layers):
    for l in layers:
        yield l
        yield from walk(l.get("children") or [])


def readback():
    """Frame origins from the guide tiles' real bounds; checks every cur/anim group stays in its box."""
    doc = run("document.inspect")
    tops = {l["name"]: l for l in doc["layers"]}
    tiles = {l["name"][5:]: l["bounds"] for l in walk(doc["layers"]) if l.get("name", "").startswith("tile ")}
    origins, problems = {}, []
    for name, b in tiles.items():
        origins[name] = [b[0], b[1]]
        if b[2] != FRAME or b[3] != FRAME:
            problems.append(f"{name}: tile {b}")
    for name, lay in tops.items():
        if not (name.startswith("cur/") or name.startswith("anim/")):
            continue
        ox, oy = origins[name]
        for c in walk(lay.get("children") or []):
            b = c.get("bounds")
            if b and (b[0] < ox or b[1] < oy or b[0] + b[2] > ox + FRAME or b[1] + b[3] > oy + FRAME):
                problems.append(f"{name}/{c['name']}: {b} outside {ox},{oy}")
    want = {f"cur/{r[0]}" for r in ROLES} | {f"anim/{ROLES[ri][0]}/{k}" for ri, k, _ in ANIM}
    missing = want - set(tops)
    if missing:
        problems.append(f"missing groups {sorted(missing)}")
    return origins, problems, doc


def spec(origins=None):
    roles = []
    for i, r in enumerate(ROLES):
        ox, oy = large_origin(i)
        name = f"cur/{r[0]}"
        if origins:
            ox, oy = origins[name]
        params, ms = frames_of(r[5])
        frames = []
        for ri, k, o in ANIM:
            if ri == i:
                g = f"anim/{r[0]}/{k}"
                frames.append({"group": g, "origin": list(origins[g]) if origins else list(o), "durationMs": ms[k]})
        hx, hy = r[4]
        roles.append({
            "registryName": r[0],
            "labelKo": r[1],
            "group": name,
            "frameOrigin": [ox, oy],
            "frameSizePx": FRAME,
            "pxPerUnit": SCALE,
            "hotspot": [hx, hy],
            "hotspotPx": {str(sz): [min(sz - 1, math.floor(hx * sz / 32)), min(sz - 1, math.floor(hy * sz / 32))]
                          for sz in (32, 48, 64, 96, 128)},
            "animated": bool(r[5]),
            "animation": None if not r[5] else {
                "kind": KIND[r[5]],
                "frames": frames,
                "loopMs": sum(ms),
                "jiffies": [max(1, round(m * 60 / 1000)) for m in ms],
            },
        })
    return {
        "pack": "D Capsule Tail",
        "source": "design/d-capsule.pcraft",
        "document": {"width": W, "height": H},
        "grid": {"units": 32, "pxPerUnit": SCALE, "frameSizePx": FRAME,
                 "note": "Render a group's layers alone on transparency, crop [origin, origin + frameSizePx], "
                         "scale to the target size. 1 grid unit = 1 px at 32 px."},
        "hotspotRule": "hotspot is in grid units (continuous, origin = frame top-left); "
                       "pixel hotspot at size N = floor(h * N / 32), listed in hotspotPx",
        "guides": "Groups other than cur/* and anim/* (page, board, guides, preview/*, hotspots) are not export sources.",
        "sizes": [32, 48, 64, 96, 128],
        "palette": {"outline": INK, "head": WHITE, "capsule": LIME,
                    "outlineWidthUnits": EDGE, "smallOutlineWidthUnits": SMALL_EDGE,
                    "precisionRimUnits": RIM,
                    "effects": "none (no shadow, glow or gradient)"},
        "roles": roles,
    }


# ================================================================ main
def clear_active():
    doc = run("document.inspect")
    if not str(doc.get("name", "")).startswith(DOC_NAME):
        sys.exit(f"active document is {doc.get('name')!r}, not {DOC_NAME}; refusing to clear it")
    for l in doc["layers"]:
        if l.get("name") != "Background":
            run("layer.delete", {"layer": l["id"]})


def draw_all():
    group("page", draw_page)
    group("board", draw_board)
    group("guides", lambda: (draw_large_guides(), draw_anim_guides()))
    draw_large()
    draw_anim()
    group("hotspots", draw_hotspots)


def export_board():
    """Flat render of the board: crop the document to the board, save PNG, undo the crop."""
    run("image.crop", {"x": 0, "y": 0, "width": W, "height": BOARD_H})
    rpc("app.save", {"path": f"{OUT}/d-capsule.png"})
    run("history.undo")
    doc = run("document.inspect")
    if (doc["width"], doc["height"]) != (W, H):
        sys.exit(f"undo of the board crop failed: {doc['width']}x{doc['height']}")


def finish():
    origins, problems, doc = readback()
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems[:40]))
    rpc("app.save", {"path": f"{OUT}/d-capsule.pcraft"})
    export_board()
    rpc("app.save", {"path": f"{OUT}/d-capsule.pcraft"})
    pathlib.Path(ROOT, OUT, "d-capsule-spec.json").write_text(
        json.dumps(spec(origins), ensure_ascii=False, indent=2), encoding="utf-8")
    rpc("ui.set", {"tool": "move"})
    rpc("ui.set", {"fit": True})
    print("done,", _n[0], "requests; doc", W, "x", H, "; problems:", len(problems))


if __name__ == "__main__":
    args = pc.take_args(sys.argv[1:], pc.CONFIG)
    if "--out" in args:
        i = args.index("--out")
        OUT = args[i + 1].replace("\\", "/").rstrip("/")
        del args[i:i + 2]
    mode = args[0] if args else "new"
    if mode == "spec":
        print(json.dumps(spec(), ensure_ascii=False, indent=1)[:3000])
        sys.exit()
    if mode == "new":
        run("file.new", {"width": W, "height": H, "background": PAGE, "name": DOC_NAME})
    elif mode == "redraw":
        clear_active()
    if mode != "finish":
        draw_all()
    finish()
