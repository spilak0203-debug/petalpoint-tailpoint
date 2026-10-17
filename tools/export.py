#!/usr/bin/env python
"""Photocraft 디자인 파일 + 스펙 JSON -> 커서 팩(.cur/.ani, install.inf, uninstall.inf, preview.png, zip).

    python tools/export.py design/b-pastel-spec.json --name "Pastel Cursors" --slug b-pastel
    python tools/export.py design/b-pastel-spec.json --name "Pastel Cursors" --slug b-pastel --dev   # 문서·zip 없이

스펙만 보고 움직이므로 팩이 달라도 같은 스크립트를 쓴다. 순서:
  1. photocraft-cli serve(stdio)로 원본을 열고, 스펙에 적힌 cur/*·anim/* 그룹만 켠 채 한 장의 PNG로 내보낸다.
  2. 그 PNG에서 스펙의 origin대로 256px 프레임을 잘라 크기별로 줄인다(프리멀티플라이 알파).
  3. 임시 폴더에 .cur/.ani, install.inf, uninstall.inf, preview.png, 문서를 쓰고 zip으로 묶은 뒤,
     모두 성공했을 때만 build/<slug>/, dist/<slug>.zip, build/<slug>.manifest.json 으로 옮긴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from fractions import Fraction
from pathlib import Path

from PIL import Image

import curformat
import preview

ROOT = Path(__file__).resolve().parents[1]

# 레지스트리 Schemes 값의 순서(Windows가 정한 17개).
SCHEME_ORDER = [
    "Arrow", "Help", "AppStarting", "Wait", "Crosshair", "IBeam", "NWPen", "No",
    "SizeNS", "SizeWE", "SizeNWSE", "SizeNESW", "SizeAll", "UpArrow", "Hand", "Pin", "Person",
]
EXPORT_PREFIXES = ("cur/", "anim/")
# .ani 프레임에서 이 크기 이상은 PNG 압축 항목으로 저장한다. 전부 DIB면 오프셋 한도(curformat.MAX_ANI_ENTRY_OFFSET)를
# 넘어 Windows가 .ani를 못 읽는다. .cur 파일은 항상 전부 DIB.
ANI_PNG_FROM = 96
ALPHA_EPS = 3  # 이 값 미만의 알파는 "비어 있음"으로 본다(그림자 꼬리 무시)
BLEED_MARGIN_PX = 24  # 프레임 바깥 이만큼을 번짐 검사 구역으로 본다
EDGE_CUT_ALPHA_MAX = 48  # 상자 바깥 고리(BLEED_MARGIN_PX)에 남은 내용의 최대 알파가 이 값 이하면(약한 그림자) 경고, 넘으면 오류
BOUNDS_TOLERANCE_PX = 4  # 그룹 레이어 경계가 자기 256px 상자를 이만큼까지 벗어나는 것은 봐준다
LOOP_TOLERANCE = 0.02  # 지피 합계와 loopMs의 허용 차이
ZIP_DATE = (2026, 1, 1, 0, 0, 0)  # 다시 만들어도 같은 zip이 나오도록 고정
# 평면 PNG 내보내기마다 나오는 정상 안내(문장 전체가 같을 때만 허용)
FLATTEN_NOTICE = re.compile(r"\d+ layer\(s\) flattened; layers, masks and blend modes are not kept")
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt")
# uninstall.inf가 되돌려 놓는 Windows 기본 구성표. 이 PC의 HKU\.DEFAULT\Control Panel\Cursors 를 읽어 옮긴 값이다
# (역할은 REG_EXPAND_SZ, 빈 문자열 = 시스템 기본 모양, 기본값 REG_SZ "@main.cpl,-1020", Scheme Source 2).
# Pin/Person은 그 키에 없어서 같은 폴더의 aero_pin/aero_person 으로 채웠다.
WINDOWS_DEFAULT_NAME = "@main.cpl,-1020"
WINDOWS_DEFAULT_CURSORS = {
    "Arrow": "aero_arrow.cur", "Help": "aero_helpsel.cur", "AppStarting": "aero_working.ani", "Wait": "aero_busy.ani",
    "Crosshair": "", "IBeam": "", "NWPen": "aero_pen.cur", "No": "aero_unavail.cur",
    "SizeNS": "aero_ns.cur", "SizeWE": "aero_ew.cur", "SizeNWSE": "aero_nwse.cur", "SizeNESW": "aero_nesw.cur",
    "SizeAll": "aero_move.cur", "UpArrow": "aero_up.cur", "Hand": "aero_link.cur",
    "Pin": "aero_pin.cur", "Person": "aero_person.cur",
}

errors: list[str] = []  # 린트 오류: --allow-warnings로 경고로 낮출 수 있다
fatal: list[str] = []  # 구조 오류(스펙과 문서가 어긋남 등): 낮출 수 없다
warnings: list[str] = []


def error(msg: str) -> None:
    errors.append(msg)
    print(f"  ERROR {msg}")


def fail(msg: str) -> None:
    fatal.append(msg)
    print(f"  ERROR (structural) {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"  WARN {msg}")


class ExportError(RuntimeError):
    pass


def raise_fatal(stage: str) -> None:
    if fatal:
        raise ExportError(f"{len(fatal)} structural error(s) after {stage}; --allow-warnings does not cover these")


def settle_findings(stage: str, allow_warnings: bool) -> None:
    """구조 오류가 있으면 항상 멈춘다. 린트 오류는 --allow-warnings일 때만 경고로 낮추고 계속한다(매니페스트에 남는다)."""
    raise_fatal(stage)
    if not errors:
        return
    if not allow_warnings:
        raise ExportError(f"{len(errors)} lint error(s) after {stage}; fix them or pass --allow-warnings")
    print(f"  --allow-warnings: {len(errors)} lint error(s) after {stage} downgraded to warnings")
    warnings.extend(f"(was error) {e}" for e in errors)
    errors.clear()


# ---------------------------------------------------------------- Photocraft

class PhotocraftSession:
    """photocraft-cli serve (stdio JSON 줄) 클라이언트. 포트는 쓰지 않는다."""

    def __init__(self, root: Path, timeout: float = 300.0):
        exe = shutil.which("photocraft-cli")
        if not exe:
            raise ExportError("photocraft-cli not found on PATH")
        self._timeout = timeout
        self._stderr = tempfile.TemporaryFile()
        self._proc = subprocess.Popen(
            [exe, "serve", "--automation-read-root", str(root), "--automation-write-root", str(root)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._stderr, cwd=root)
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._next_id = 0

    def _pump(self) -> None:
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _server_stderr(self) -> str:
        self._stderr.seek(0)
        return self._stderr.read().decode("utf-8", "replace").strip()[-500:]

    def call(self, method: str, params: dict | None = None):
        self._next_id += 1
        req = {"id": self._next_id, "method": method, "params": params or {}}
        self._proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        self._proc.stdin.flush()
        try:
            line = self._lines.get(timeout=self._timeout)
        except queue.Empty:
            raise ExportError(f"{method}: no reply in {self._timeout:.0f}s") from None
        if line is None:
            raise ExportError(f"{method}: photocraft-cli exited. {self._server_stderr()}")
        reply = json.loads(line)
        if reply.get("id") != req["id"]:
            raise ExportError(f"{method}: reply id {reply.get('id')} != {req['id']}")
        if not reply.get("ok"):
            raise ExportError(f"{method}: {reply.get('error')}")
        return reply.get("result")

    def close(self) -> None:
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=15)
        except Exception:
            self._proc.kill()
        finally:
            self._stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _is_export(name: str) -> bool:
    return name.startswith(EXPORT_PREFIXES)


def _contains(layers: list[dict], names: set[str]) -> bool:
    return any(l["name"] in names or _contains(l.get("children") or [], names) for l in layers)


def _hidden_children(layers: list[dict]) -> list[str]:
    """꺼져 있는 레이어 이름(꺼진 그룹 안쪽까지는 내려가지 않는다)."""
    out: list[str] = []
    for l in layers:
        if not l["visible"]:
            out.append(l["name"])
        else:
            out += _hidden_children(l.get("children") or [])
    return out


def plan_visibility(layers: list[dict], wanted: set[str]) -> tuple[list[int], list[int], dict[str, int]]:
    """(숨길 id, 켤 id, 스펙 그룹별로 찾은 개수). 스펙에 적힌 그룹과 그 조상만 켜고 나머지는 모두 숨긴다.

    스펙에 없는 cur/·anim/ 그룹, 이름이 겹치는 그룹, 스펙 그룹 안에서 꺼진 자식은 오류로 보고한다.
    """
    hide: list[int] = []
    show: list[int] = []
    seen: dict[str, int] = {}

    def scan(ls: list[dict]) -> None:
        for l in ls:
            if l["name"] in wanted:
                seen[l["name"]] = seen.get(l["name"], 0) + 1
            elif _is_export(l["name"]):
                fail(f"group {l['name']!r} looks like an export group but is not in the spec")
            scan(l.get("children") or [])

    def walk(ls: list[dict]) -> None:
        for l in ls:
            kids = l.get("children") or []
            if l["name"] in wanted:
                if not l["visible"]:
                    show.append(l["id"])
                hidden = _hidden_children(kids)
                if hidden:
                    fail(f"{l['name']}: hidden layer(s) inside the export group: {hidden[:5]}")
            elif kids and _contains(kids, wanted):
                if not l["visible"]:
                    show.append(l["id"])
                walk(kids)
            elif l["visible"]:
                hide.append(l["id"])

    scan(layers)
    walk(layers)
    for name, n in seen.items():
        if n > 1:
            fail(f"group {name!r} appears {n} times in the document")
    return hide, show, seen


def leftover_visible(layers: list[dict], wanted: set[str]) -> tuple[list[str], list[str]]:
    """(스펙 그룹 밖에서 보이는 레이어, 꺼져 있는 스펙 그룹)."""
    left: list[str] = []
    off: list[str] = []

    def walk(ls: list[dict]) -> None:
        for l in ls:
            if l["name"] in wanted:
                if not l["visible"]:
                    off.append(l["name"])
                continue
            if not l["visible"]:
                continue
            kids = l.get("children")
            if kids and _contains(kids, wanted):
                walk(kids)
            else:
                left.append(l["name"])

    walk(layers)
    return left, off


def photocraft_warnings(method: str, result: dict | None, expected: re.Pattern | None = None) -> None:
    bad = [w for w in (result or {}).get("warnings") or [] if not (expected and expected.fullmatch(w))]
    if bad:
        raise ExportError(f"{method} returned warnings: {bad}")


def _leaf_bounds(layer: dict):
    """보이는 잎 레이어들의 경계 합집합 (x0, y0, x1, y1). 그룹의 bounds는 비어 있어 자식에서 구한다."""
    kids = layer.get("children")
    if kids is None:
        b = layer.get("bounds")
        return (b[0], b[1], b[0] + b[2], b[1] + b[3]) if layer["visible"] and b else None
    boxes = [r for k in kids if k["visible"] for r in [_leaf_bounds(k)] if r]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def check_group_boxes(layers: list[dict], boxes: dict[str, tuple[int, int]], fs: int) -> None:
    """스펙 그룹마다 레이어 경계가 자기 frameOrigin 상자 안에 있는지 본다(origin을 바꿔 쓴 스펙을 잡는다)."""
    by_name: dict[str, dict] = {}

    def index(ls: list[dict]) -> None:
        for l in ls:
            if l["name"] in boxes:
                by_name.setdefault(l["name"], l)
            index(l.get("children") or [])

    index(layers)
    for name, (ox, oy) in boxes.items():
        layer = by_name.get(name)
        if layer is None:
            continue  # 없는 그룹은 호출한 쪽이 따로 알린다
        b = _leaf_bounds(layer)
        if b is None:
            fail(f"{name}: no visible content layers")
            continue
        over = max(ox - b[0], oy - b[1], b[2] - (ox + fs), b[3] - (oy + fs))
        if over > BOUNDS_TOLERANCE_PX:
            fail(f"{name}: layer bounds {b} stick out of its frame box {(ox, oy, ox + fs, oy + fs)} by {over}px "
                 f"(wrong origin in the spec, or the group was moved?)")


def render_flat(spec: dict, source: Path, flat_png: Path, wanted: set[str]) -> Image.Image:
    """원본에서 스펙의 cur/*·anim/* 만 켠 전체 크기 PNG를 한 장 만들어 연다."""
    rel_source = source.relative_to(ROOT).as_posix()
    rel_png = flat_png.relative_to(ROOT).as_posix()
    flat_png.unlink(missing_ok=True)
    doc = spec["document"]
    with PhotocraftSession(ROOT) as pc:
        info = pc.call("doc.open", {"path": rel_source})
        photocraft_warnings("doc.open", info)
        if (info["width"], info["height"]) != (doc["width"], doc["height"]):
            raise ExportError(f"document is {info['width']}x{info['height']}, spec says {doc['width']}x{doc['height']}")
        layers = pc.call("doc.inspect")["layers"]
        hide, show, seen = plan_visibility(layers, wanted)
        missing = sorted(wanted - set(seen))
        if missing:
            raise ExportError(f"groups in spec but not in document: {', '.join(missing)}")
        check_group_boxes(layers, dict(frame_boxes(spec)), spec["grid"]["frameSizePx"])
        raise_fatal("reading the document")
        steps = [{"command": "layer.setProps", "params": {"layer": i, "visible": False}} for i in hide]
        steps += [{"command": "layer.setProps", "params": {"layer": i, "visible": True}} for i in show]
        for i in range(0, len(steps), 200):  # batch는 한 번에 256 스텝까지
            res = pc.call("batch", {"steps": steps[i:i + 200]})
            if res["failed"]:
                raise ExportError(f"layer.setProps failed: {res['results']}")
        left, off = leftover_visible(pc.call("doc.inspect")["layers"], wanted)
        if left:
            raise ExportError(f"layers still visible outside the spec's groups: {left[:5]}")
        if off:
            raise ExportError(f"spec groups still hidden: {off[:5]}")
        print(f"  hid {len(hide)} layer(s), showing {len(wanted)} export group(s)")
        saved = pc.call("doc.save", {"path": rel_png})  # 확장자로 평면 내보내기. 원본 .pcraft는 건드리지 않는다.
        photocraft_warnings("doc.save", saved, FLATTEN_NOTICE)
    flat = Image.open(flat_png)
    flat.load()
    if flat.size != (doc["width"], doc["height"]):
        raise ExportError(f"flat PNG is {flat.size}, expected {doc['width']}x{doc['height']}")
    if flat.mode != "RGBA":
        flat = flat.convert("RGBA")
    lo, hi = flat.getchannel("A").getextrema()
    if lo != 0 or hi == 0 or flat.getpixel((0, 0))[3] != 0:
        raise ExportError(f"flat PNG is not transparent as expected (alpha {lo}..{hi})")
    return flat


# ---------------------------------------------------------------- 스펙 검사

def grid_hotspot(role: dict, size: int, units: int) -> tuple[int, int]:
    """격자 칸 단위 핫스팟 -> size px 핫스팟: floor(h * size / units). 소수는 문자열로 읽어 정확히 계산한다."""
    return tuple(math.floor(Fraction(str(h)) * size / units) for h in role["hotspot"])


def inside_root(path: Path, what: str) -> Path:
    path = path.resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        raise ExportError(f"{what} must be inside the project root {ROOT}: {path}") from None
    return path


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text(encoding="utf-8"))
    roles = {r["registryName"]: r for r in spec["roles"]}
    if len(roles) != len(spec["roles"]) or set(roles) != set(SCHEME_ORDER):
        raise ExportError(f"roles must be exactly the 17 Windows roles; got {sorted(roles)}")
    grid = spec["grid"]
    fs, units = grid["frameSizePx"], grid["units"]
    if grid["pxPerUnit"] * units != fs:
        raise ExportError(f"grid: pxPerUnit {grid['pxPerUnit']} x units {units} != frameSizePx {fs}")
    for sz in spec["sizes"]:
        if not 1 <= sz <= 128:
            raise ExportError(f"size {sz} out of range 1..128")
    doc_w, doc_h = spec["document"]["width"], spec["document"]["height"]
    for r in spec["roles"]:
        name = r["registryName"]
        if r["frameSizePx"] != fs:
            raise ExportError(f"{name}: frameSizePx {r['frameSizePx']} != grid {fs}")
        if r["group"] != f"cur/{name}":
            raise ExportError(f"{name}: unexpected group {r['group']}")
        if r["animated"] != bool(r.get("animation")):
            raise ExportError(f"{name}: animated flag and animation block disagree")
        origins = [tuple(r["frameOrigin"])]
        a = r.get("animation")
        if a:
            if len(a["frames"]) != len(a["jiffies"]):
                raise ExportError(f"{name}: {len(a['frames'])} frames but {len(a['jiffies'])} jiffies")
            for i, f in enumerate(a["frames"]):
                if f["group"] != f"anim/{name}/{i}":
                    raise ExportError(f"{name}: frame {i} group is {f['group']}")
                origins.append(tuple(f["origin"]))
        for ox, oy in origins:
            if not (0 <= ox and 0 <= oy and ox + fs <= doc_w and oy + fs <= doc_h):
                raise ExportError(f"{name}: frame at {(ox, oy)} leaves the {doc_w}x{doc_h} document")
        # 핫스팟: hotspot(격자 칸)에서 다시 계산한 값과 스펙의 hotspotPx가 모든 크기에서 같아야 한다.
        for n in spec["sizes"]:
            want = grid_hotspot(r, n, units)
            got = tuple(r["hotspotPx"].get(str(n), ()))
            if got != want:
                raise ExportError(f"{name}: hotspotPx[{n}]={list(got)} but floor(hotspot*{n}/{units}) = {list(want)} "
                                  f"(hotspot {r['hotspot']})")
            if not all(0 <= v < n for v in want):
                raise ExportError(f"{name}: hotspot {r['hotspot']} is outside the {n}px image")
    return spec


def frame_boxes(spec: dict) -> list[tuple[str, tuple[int, int]]]:
    """(그룹 이름, origin) 전부. 정지 커서의 cur/<이름>과 애니메이션 프레임."""
    boxes = []
    for r in spec["roles"]:
        boxes.append((r["group"], tuple(r["frameOrigin"])))
        for f in (r.get("animation") or {}).get("frames", []):
            boxes.append((f["group"], tuple(f["origin"])))
    return boxes


def lint_frames(spec: dict, flat: Image.Image) -> None:
    """프레임이 상자를 벗어나거나 잘렸는지, 시간이 맞는지 본다. 고치지는 않고 error()/warn()으로 알린다."""
    fs = spec["grid"]["frameSizePx"]
    ppu = spec["grid"]["pxPerUnit"]
    alpha = flat.getchannel("A").point(lambda a: 255 if a >= ALPHA_EPS else 0)
    boxes = frame_boxes(spec)

    raw = flat.getchannel("A")
    ring_px = BLEED_MARGIN_PX

    def ring_of(ox: int, oy: int):
        x0, y0 = max(ox - ring_px, 0), max(oy - ring_px, 0)
        x1, y1 = min(ox + fs + ring_px, flat.width), min(oy + fs + ring_px, flat.height)
        return (x0, y0, x1, y1)

    # 프레임끼리 겹치면 안 된다
    for i, (ga, (ax, ay)) in enumerate(boxes):
        for gb, (bx, by) in boxes[i + 1:]:
            if abs(ax - bx) < fs and abs(ay - by) < fs:
                error(f"frame boxes overlap: {ga} and {gb}")

    # 어느 프레임(과 그 둘레 고리)에도 속하지 않는 내용
    stray = alpha.copy()
    for _, (ox, oy) in boxes:
        stray.paste(0, ring_of(ox, oy))
    if stray.getbbox():
        error(f"content far outside every frame box: bbox {stray.getbbox()}")

    # 역할 단위로 묶어서 알린다(애니메이션 프레임은 같은 말이 12번 나오므로)
    for r in spec["roles"]:
        frames = [(r["group"], tuple(r["frameOrigin"]))] + [
            (f["group"], tuple(f["origin"])) for f in (r.get("animation") or {}).get("frames", [])]
        over = {"left": 0, "top": 0, "right": 0, "bottom": 0}
        outside_alpha = clipped = 0
        for group, (ox, oy) in frames:
            if alpha.crop((ox, oy, ox + fs, oy + fs)).getbbox() is None:
                error(f"{group}: frame is empty")
                continue
            x0, y0, *_ = box = ring_of(ox, oy)
            ring = alpha.crop(box)
            ix, iy = ox - x0, oy - y0
            ring.paste(0, (ix, iy, ix + fs, iy + fs))
            bb = ring.getbbox()
            if not bb:
                continue
            clipped += 1
            over["left"] = max(over["left"], ix - bb[0])
            over["top"] = max(over["top"], iy - bb[1])
            over["right"] = max(over["right"], bb[2] - (ix + fs))
            over["bottom"] = max(over["bottom"], bb[3] - (iy + fs))
            outer = raw.crop(box)  # 상자 바깥 고리에서 가장 진한 픽셀(가장자리 한 줄이 아니라)
            outer.paste(0, (ix, iy, ix + fs, iy + fs))
            outside_alpha = max(outside_alpha, outer.getextrema()[1])
        if clipped:
            sides = ", ".join(f"{side} {px}px" for side, px in over.items() if px > 0)
            msg = (f"{r['registryName']}: content runs past its {fs}px box ({sides}) in {clipped}/{len(frames)} frame(s); "
                   f"cropped there, strongest clipped pixel alpha {outside_alpha}/255")
            if outside_alpha <= EDGE_CUT_ALPHA_MAX:
                warn(msg)
            else:
                error(msg + f" (> {EDGE_CUT_ALPHA_MAX})")

    # 핫스팟 근처에 불투명 픽셀이 있는지 (정지 커서 기준, 애니메이션은 0번 프레임). 알려 주기만 한다.
    for r in spec["roles"]:
        ox, oy = r["animation"]["frames"][0]["origin"] if r["animated"] else r["frameOrigin"]
        hx, hy = (round(v * ppu) for v in r["hotspot"])
        rad = 4 * ppu
        crop = raw.crop((ox + hx - rad, oy + hy - rad, ox + hx + rad + 1, oy + hy + rad + 1))
        px = crop.load()
        best = None
        for y in range(crop.height):
            for x in range(crop.width):
                if px[x, y] >= 128:
                    d = math.hypot(x - rad, y - rad)
                    best = d if best is None or d < best else best
        if best is None:
            print(f"  note {r['registryName']}: hotspot {r['hotspot']} has no opaque pixel within 4 units (ring centre?)")
        elif best > 1.5 * ppu:
            print(f"  note {r['registryName']}: hotspot {r['hotspot']} is {best / ppu:.1f} units from the nearest opaque pixel")

    # 지피 합계와 loopMs, 프레임 시간과 지피
    for r in spec["roles"]:
        a = r.get("animation")
        if not a:
            continue
        total_ms = sum(a["jiffies"]) * 1000 / 60
        if abs(total_ms - a["loopMs"]) > LOOP_TOLERANCE * a["loopMs"]:
            error(f"{r['registryName']}: jiffies total {sum(a['jiffies'])} = {total_ms:.0f} ms vs loopMs {a['loopMs']} "
                  f"({(total_ms / a['loopMs'] - 1) * 100:+.1f}%)")
        for i, (f, j) in enumerate(zip(a["frames"], a["jiffies"])):
            if abs(f["durationMs"] * 60 / 1000 - j) > 1:
                error(f"{r['registryName']}: frame {i} {f['durationMs']} ms vs {j} jiffies")


# ---------------------------------------------------------------- 줄이기·쓰기

def downscale(frame: Image.Image, size: int) -> Image.Image:
    """프리멀티플라이 알파로 줄인다(어두운 테두리 번짐 방지). 이미 RGBA 평면 PNG에서 자른 것."""
    if frame.width == size:
        return frame.copy()
    premul = frame.convert("RGBa")
    out = premul.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")
    # 필터 링잉으로 알파 0인 칸에 색이 남을 수 있어 비운다(AND 마스크와 일관되게).
    out.paste((0, 0, 0, 0), mask=out.getchannel("A").point(lambda a: 255 if a == 0 else 0))
    return out


def role_images(spec: dict, flat: Image.Image, role: dict, origin, sizes: list[int],
                png_from: int | None = None) -> list[curformat.CursorImage]:
    fs = spec["grid"]["frameSizePx"]
    units = spec["grid"]["units"]
    ox, oy = origin
    frame = flat.crop((ox, oy, ox + fs, oy + fs))
    return [curformat.CursorImage(n, grid_hotspot(role, n, units), downscale(frame, n),
                                  png=png_from is not None and n >= png_from)
            for n in sizes]


def write_cursors(spec: dict, flat: Image.Image, out_dir: Path) -> tuple[list[str], dict[str, list[dict[str, str]]]]:
    """.cur/.ani 17개를 쓰고 (파일 이름들, 파일 -> 프레임 -> 크기별 픽셀 해시)를 돌려준다."""
    sizes = sorted(spec["sizes"])
    names: list[str] = []
    pixels: dict[str, list[dict[str, str]]] = {}
    for r in spec["roles"]:
        name = r["registryName"]
        if r["animated"]:
            a = r["animation"]
            per_frame = [role_images(spec, flat, r, f["origin"], sizes, ANI_PNG_FROM) for f in a["frames"]]
            data = curformat.build_ani([curformat.build_cur(imgs) for imgs in per_frame], a["jiffies"])
            fname = f"{name}.ani"
        else:
            per_frame = [role_images(spec, flat, r, r["frameOrigin"], sizes)]
            data = curformat.build_cur(per_frame[0])
            fname = f"{name}.cur"
        (out_dir / fname).write_bytes(data)
        names.append(fname)
        pixels[fname] = [{str(i.size): curformat.pixel_hash(i.image) for i in imgs} for imgs in per_frame]
    return names, pixels


# ---------------------------------------------------------------- INF

def validate_names(name: str, slug: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.\-]{0,62}[A-Za-z0-9]", name):
        raise ExportError("--name must be ASCII letters/digits/space/_.- (it becomes a folder name and an INF string)")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", slug):
        raise ExportError("--slug must match [a-z0-9][a-z0-9_-]*")


def _strings(name: str) -> list[str]:
    return ["[Strings]", f'SCHEME_NAME = "{name}"', f'CUR_DIR     = "Cursors\\{name}"']


def build_inf(name: str, cursor_files: dict[str, str]) -> str:
    """HKCU 구성표 + Control Panel\\Cursors 값 + 파일 복사. 마우스 설정을 여는 명령은 넣지 않는다(README에서 안내)."""
    reg_path = lambda role: f"%10%\\%CUR_DIR%\\{cursor_files[role]}"
    lines = [
        "; Cursor scheme installer. Right-click this file and choose Install.",
        "[Version]",
        'Signature="$CHICAGO$"',
        "",
        "[DefaultInstall]",
        "CopyFiles = Scheme.Cur",
        "AddReg    = Scheme.Reg, Wreg",
        "",
        "[DestinationDirs]",
        'Scheme.Cur = 10,"%CUR_DIR%"',
        "",
        "[Scheme.Reg]",
        'HKCU,"Control Panel\\Cursors\\Schemes","%SCHEME_NAME%",,"'
        + ",".join(reg_path(r) for r in SCHEME_ORDER) + '"',
        "",
        "[Wreg]",
        'HKCU,"Control Panel\\Cursors",,,"%SCHEME_NAME%"',
        'HKCU,"Control Panel\\Cursors","Scheme Source",0x00010001,1',
        *(f'HKCU,"Control Panel\\Cursors","{r}",,"{reg_path(r)}"' for r in SCHEME_ORDER),
        "",
        "[Scheme.Cur]",
        *(cursor_files[r] for r in SCHEME_ORDER),
        "",
        *_strings(name),
    ]
    return "\r\n".join(lines) + "\r\n"


def build_uninf(name: str, cursor_files: dict[str, str]) -> str:
    """install.inf를 되돌린다: 파일 삭제, 구성표 값 삭제, Control Panel\\Cursors를 Windows 기본 구성표 상태로.

    기본값 "@main.cpl,-1020"(REG_SZ), Scheme Source 2, 역할은 REG_EXPAND_SZ(0x00020000)의 %SystemRoot%\\cursors\\aero_*.
    INF 값 안의 리터럴 %는 %%로 적는다.
    """
    cpl = "Control Panel\\Cursors"
    roles = []
    for r in SCHEME_ORDER:
        f = WINDOWS_DEFAULT_CURSORS[r]
        value = f"%%SystemRoot%%\\cursors\\{f}" if f else ""
        roles.append(f'HKCU,"{cpl}","{r}",0x00020000,"{value}"')
    lines = [
        "; Cursor scheme uninstaller. Right-click this file and choose Install.",
        "[Version]",
        'Signature="$CHICAGO$"',
        "",
        "[DefaultInstall]",
        "DelFiles = Uninst.Files",
        "DelReg   = Uninst.DelReg",
        "AddReg   = Uninst.Reset",
        "",
        "[DestinationDirs]",
        'Uninst.Files = 10,"%CUR_DIR%"',
        "",
        "[Uninst.DelReg]",
        f'HKCU,"{cpl}\\Schemes","%SCHEME_NAME%"',
        "",
        "[Uninst.Reset]",
        f'HKCU,"{cpl}",,,"{WINDOWS_DEFAULT_NAME}"',
        f'HKCU,"{cpl}","Scheme Source",0x00010001,2',
        *roles,
        "",
        "[Uninst.Files]",
        *(cursor_files[r] for r in SCHEME_ORDER),
        "",
        *_strings(name),
    ]
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------- 문서·zip

def collect_docs(docs_root: Path, dev: bool) -> dict[str, Path]:
    """팩 폴더(와 zip)에 넣을 문서: 팩 폴더 안 파일 이름 -> 원본. LICENSE와 README.txt(packaging/README-pack.txt)만 담는다.

    루트 README.md는 이미지 링크가 팩 안에서 깨지므로 담지 않지만, 배포용 빌드는 있는지 확인한다.
    없는 것이 있으면 --dev가 아닌 한 실패한다.
    """
    candidates = [
        ("LICENSE", [docs_root / n for n in LICENSE_NAMES], None),  # 원래 파일 이름 그대로
        ("packaging/README-pack.txt", [docs_root / "packaging" / "README-pack.txt"], "README.txt"),
    ]
    docs: dict[str, Path] = {}
    missing: list[str] = []
    for label, paths, dest in candidates:
        src = next((p for p in paths if p.is_file()), None)
        if src is None:
            missing.append(label)
        else:
            docs[dest or src.name] = src
    if not (docs_root / "README.md").is_file():
        missing.append("README.md")
    if missing:
        if not dev:
            raise ExportError(f"missing under {docs_root}: {', '.join(missing)} (build with --dev to skip documents and the zip)")
        print(f"  --dev: no {', '.join(missing)}")
    return docs


def make_zip(src_dir: Path, zip_path: Path, top: str) -> None:
    """src_dir의 파일을 모두 <top>/ 폴더 아래로 담는다. 항목 순서·시각을 고정해 같은 입력이면 같은 zip."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src_dir.iterdir()):
            if not p.is_file():
                continue
            info = zipfile.ZipInfo(f"{top}/{p.name}", ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, p.read_bytes())


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def doc_path_label(path: Path) -> str:
    """매니페스트에 적는 문서 원본 경로: 프로젝트 안이면 루트 기준 상대 경로, 밖이면 절대 경로(둘 다 / 구분)."""
    path = path.resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def replace_dir(new: Path, final: Path, backup: Path) -> None:
    """final을 new로 바꾼다. 새 폴더를 옮기다 실패하면 이전 폴더를 되돌린다."""
    try:
        if final.exists():
            final.rename(backup)
        try:
            new.rename(final)
        except OSError:
            if backup.exists() and not final.exists():
                backup.rename(final)
            raise
    except OSError as exc:
        raise ExportError(f"could not replace {final} ({exc}); close programs that use it and retry") from None


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spec", type=Path, help="design/<pack>-spec.json (inside the project)")
    ap.add_argument("--name", required=True, help='scheme name shown in Mouse settings, e.g. "Pastel Cursors"')
    ap.add_argument("--slug", required=True, help="folder/zip name, e.g. b-pastel")
    ap.add_argument("--dev", action="store_true",
                    help="development build: LICENSE/README.md/packaging/README-pack.txt may be missing, and no zip is made")
    ap.add_argument("--allow-warnings", action="store_true",
                    help="downgrade lint errors to warnings and continue (structural errors still fail; check.py rejects such non-dev builds)")
    ap.add_argument("--docs-root", type=Path, default=ROOT,
                    help="where LICENSE, README.md and packaging/README-pack.txt live (default: the project root)")
    ap.add_argument("--keep-temp", action="store_true", help="keep the full-size flat PNG as build/_tmp/<slug>-flat.png")
    args = ap.parse_args(argv)

    # --- 시작 전 점검: 여기서 실패하면 아무것도 지우지 않는다
    validate_names(args.name, args.slug)
    spec_path = inside_root(args.spec, "spec")
    spec = load_spec(spec_path)
    source = inside_root(ROOT / spec["source"], "spec source")
    if not source.is_file():
        raise ExportError(f"source not found: {source}")
    source_stamp = (source.stat().st_mtime_ns, source.stat().st_size)
    docs = collect_docs(args.docs_root.resolve(), args.dev)

    slug = args.slug
    build_dir = ROOT / "build" / slug
    work = ROOT / "build" / "_tmp" / slug  # 이 실행만 쓰는 임시 폴더(다른 팩의 실행과 겹치지 않게 slug별)
    pack_dir = work / slug
    zip_path = ROOT / "dist" / f"{slug}.zip"
    manifest_path = ROOT / "build" / f"{slug}.manifest.json"

    # 이전 산출물은 시작할 때 지운다: 실패한 실행 뒤에 오래된 zip·매니페스트가 남아 통과처럼 보이지 않게.
    zip_path.unlink(missing_ok=True)
    manifest_path.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        print(f"[1/4] render {spec['source']}")
        wanted = {r["group"] for r in spec["roles"]}
        wanted |= {f["group"] for r in spec["roles"] for f in (r.get("animation") or {}).get("frames", [])}
        flat_png = work / "flat.png"
        flat = render_flat(spec, source, flat_png, wanted)
        print(f"  flat PNG {flat.size[0]}x{flat.size[1]} RGBA")
        settle_findings("render", args.allow_warnings)

        print("[2/4] lint frames")
        lint_frames(spec, flat)
        settle_findings("lint", args.allow_warnings)

        print("[3/4] write pack")
        pack_dir.mkdir()
        files, pixels = write_cursors(spec, flat, pack_dir)
        cursor_files = {Path(f).stem: f for f in files}
        (pack_dir / "install.inf").write_bytes(build_inf(args.name, cursor_files).encode("ascii"))
        (pack_dir / "uninstall.inf").write_bytes(build_uninf(args.name, cursor_files).encode("ascii"))
        for dest, src in docs.items():
            shutil.copy2(src, pack_dir / dest)
        preview.make_preview(pack_dir, spec, cursor_files, pack_dir / "preview.png")

        print("[4/4] zip" if not args.dev else "[4/4] zip skipped (--dev)")
        zip_tmp = work / f"{slug}.zip"
        if not args.dev:
            make_zip(pack_dir, zip_tmp, slug)
        if (source.stat().st_mtime_ns, source.stat().st_size) != source_stamp:
            raise ExportError(f"source file changed during export: {source}")

        manifest = {
            "slug": slug, "name": args.name, "dev": args.dev,
            "spec": spec_path.relative_to(ROOT).as_posix(), "specSha256": file_sha256(spec_path),
            "source": spec["source"], "sourceSha256": file_sha256(source),
            "zip": None if args.dev else zip_path.relative_to(ROOT).as_posix(),
            "schemeOrder": SCHEME_ORDER, "aniPngFrom": ANI_PNG_FROM,
            "allowWarnings": args.allow_warnings, "warnings": list(warnings),
            "docs": sorted(docs),
            # 문서 원본 위치와 팩에 넣은 바이트의 해시: check.py가 --docs-root로 만든 빌드도 그 원본과 비교한다
            "docSources": {dest: {"path": doc_path_label(src), "sha256": file_sha256(pack_dir / dest)}
                           for dest, src in sorted(docs.items())},
            "pixels": pixels,
        }
        manifest_tmp = work / "manifest.json"
        manifest_tmp.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

        # --- 모두 만들어졌을 때만 제자리로
        replace_dir(pack_dir, build_dir, work / "old")
        if not args.dev:
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(zip_tmp, zip_path)
        os.replace(manifest_tmp, manifest_path)
        if args.keep_temp:
            shutil.copy2(flat_png, ROOT / "build" / "_tmp" / f"{slug}-flat.png")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            (ROOT / "build" / "_tmp").rmdir()  # 비어 있을 때만 지워진다
        except OSError:
            pass

    total = sum(p.stat().st_size for p in build_dir.iterdir())
    zip_note = "no zip (--dev)" if args.dev else f"{zip_path.relative_to(ROOT)} ({zip_path.stat().st_size / 1024:.0f} KiB)"
    print(f"done: {len(files)} cursors -> {build_dir.relative_to(ROOT)} ({total / 1024:.0f} KiB), {zip_note}, "
          f"{len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ExportError, curformat.CurFormatError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
