#!/usr/bin/env python
"""내보낸 커서 팩 검증.

    python tools/check.py build/b-pastel

build/<slug>/ 의 .cur/.ani/*.inf 를 자체 파서(curformat)로 읽어 스펙과 맞는지 확인한다.
  - 파일마다·프레임마다·크기마다 디코드한 픽셀 해시를 build/<slug>.manifest.json 의 값과 비교한다
    (파일이 바뀌거나 프레임 순서가 뒤집혀도 잡힌다). 스펙·원본이 내보낸 뒤 바뀌었는지도 해시로 본다.
  - 핫스팟은 스펙의 hotspot(격자 칸)에서 floor(h*N/32)로 다시 계산해 비교한다.
  - Pillow와 Windows(user32 LoadImage, 읽기만 함)로 한 번 더 열어 본다.
  - install.inf/uninstall.inf, preview.png(다시 만들어 비교), dist/<slug>.zip(폴더와 목록·바이트 비교)을 본다.
하나라도 어긋나면 종료 코드 1.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import io
import json
import math
import sys
import tempfile
import zipfile
from fractions import Fraction
from pathlib import Path

from PIL import Image

import curformat
import export
import preview

ROOT = Path(__file__).resolve().parents[1]

# 일부러 export.py와 따로 적은 값: 쓰는 쪽과 검사하는 쪽이 같은 실수를 하지 않도록. 아래에서 export의 상수와 맞는지 확인한다.
# .ani 프레임에서 PNG 항목이어야 하는 최소 크기(.cur은 전부 DIB여야 한다).
ANI_PNG_FROM = 96
GRID_UNITS = 32
EXPECTED_ORDER = [
    "Arrow", "Help", "AppStarting", "Wait", "Crosshair", "IBeam", "NWPen", "No",
    "SizeNS", "SizeWE", "SizeNWSE", "SizeNESW", "SizeAll", "UpArrow", "Hand", "Pin", "Person",
]
EXTRA_FILES = {"install.inf", "uninstall.inf", "preview.png"}
# uninstall.inf가 되돌려 놓아야 하는 Windows 기본 구성표(이 PC의 HKU\.DEFAULT\Control Panel\Cursors를 읽은 값). 빈 값 = 시스템 기본 모양.
WINDOWS_DEFAULT_NAME = "@main.cpl,-1020"
WINDOWS_DEFAULT_CURSORS = {
    "Arrow": "aero_arrow.cur", "Help": "aero_helpsel.cur", "AppStarting": "aero_working.ani", "Wait": "aero_busy.ani",
    "Crosshair": "", "IBeam": "", "NWPen": "aero_pen.cur", "No": "aero_unavail.cur",
    "SizeNS": "aero_ns.cur", "SizeWE": "aero_ew.cur", "SizeNWSE": "aero_nwse.cur", "SizeNESW": "aero_nesw.cur",
    "SizeAll": "aero_move.cur", "UpArrow": "aero_up.cur", "Hand": "aero_link.cur",
    "Pin": "aero_pin.cur", "Person": "aero_person.cur",
}


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.sections: list[tuple[str, int]] = []
        self._name = ""
        self._count = 0

    def section(self, name: str) -> None:
        self._flush()
        self._name, self._count = name, 0

    def _flush(self) -> None:
        if self._name:
            self.sections.append((self._name, self._count))

    def check(self, cond: object, msg: str) -> bool:
        self._count += 1
        if not cond:
            self.failures.append(f"[{self._name}] {msg}")
            print(f"  FAIL {msg}")
        return bool(cond)

    def note(self, msg: str) -> None:
        print(f"  note {msg}")

    def finish(self) -> bool:
        self._flush()
        for name, n in self.sections:
            print(f"{name}: {n} checks")
        total = sum(n for _, n in self.sections)
        print(f"{'PASS' if not self.failures else 'FAIL'}: {total} checks, {len(self.failures)} failure(s)")
        return not self.failures


# ---------------------------------------------------------------- 공통

def expected_files(spec: dict) -> dict[str, str]:
    return {r["registryName"]: f"{r['registryName']}{'.ani' if r['animated'] else '.cur'}" for r in spec["roles"]}


def expected_hotspot(role: dict, size: int) -> tuple[int, int]:
    """스펙의 hotspot(격자 칸)에서 직접 계산: floor(h * N / 32). hotspotPx는 쓰지 않는다."""
    return tuple(math.floor(Fraction(str(h)) * size / GRID_UNITS) for h in role["hotspot"])


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_cur_entries(ck: Checker, label: str, entries: list[curformat.CurEntry], sizes_want: list[int], role: dict,
                      want_hashes: dict[str, str] | None, png_from: int | None = None) -> None:
    """png_from=None: 전부 DIB여야 함(.cur). 숫자: 그 크기 이상은 PNG, 나머지는 DIB(.ani 프레임)."""
    sizes = [e.width for e in entries]
    ck.check(sizes == sizes_want, f"{label}: sizes {sizes} != {sizes_want}")
    if want_hashes is None:
        ck.check(False, f"{label}: no pixel hashes in the manifest")
    else:
        got = {str(e.width): curformat.pixel_hash(e.image) for e in entries}
        ck.check(set(got) == set(want_hashes), f"{label}: sizes {sorted(got)} vs manifest {sorted(want_hashes)}")
        for n, h in want_hashes.items():
            ck.check(got.get(n) == h, f"{label}: {n}px pixels differ from what export wrote (manifest hash)")
    for e in entries:
        want = expected_hotspot(role, e.width)
        ck.check(e.width == e.height, f"{label}: {e.width}x{e.height} not square")
        ck.check(e.hotspot == want, f"{label}: {e.width}px hotspot {e.hotspot} != floor(hotspot*{e.width}/32) = {want}")
        ck.check(e.bit_count == 32, f"{label}: {e.width}px is {e.bit_count}-bit")
        want_png = png_from is not None and e.width >= png_from
        ck.check(e.compressed == want_png, f"{label}: {e.width}px is {'PNG' if e.compressed else 'DIB'}, want {'PNG' if want_png else 'DIB'}")
        px = e.image.load()
        if not e.compressed:
            mask_ok = all(
                (e.and_mask[y][x] == 1) == (px[x, y][3] == 0)
                for y in range(e.height) for x in range(e.width))
            ck.check(mask_ok, f"{label}: {e.width}px AND mask disagrees with alpha")
        ck.check(e.image.getchannel("A").getbbox() is not None, f"{label}: {e.width}px image is empty")
        black_ok = all(px[x, y][:3] == (0, 0, 0) for y in range(e.height) for x in range(e.width) if px[x, y][3] == 0)
        ck.check(black_ok, f"{label}: {e.width}px transparent pixels carry colour")


# ---------------------------------------------------------------- Pillow / Windows

def pillow_opinion(ck: Checker, label: str, data: bytes, entries: list[curformat.CurEntry], cur_plugin: bool = True) -> None:
    """Pillow가 .cur을 열 수 있는지, ICO 디코더로 바꿔 읽어도 크기별 색·모양이 같은지.

    Pillow는 32비트 알파 대신 AND 마스크로 투명도를 정하므로(반투명 칸도 불투명으로 읽음),
    색은 "우리 알파 > 0"인 칸에서, 알파는 "있음/없음"만 비교한다. PNG 항목은 Pillow도 실제 알파로 읽는다.
    CUR 디코더는 가장 큰 항목을 DIB로만 읽으므로 .ani 프레임(큰 항목이 PNG)에는 cur_plugin=False.
    """
    if cur_plugin:
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
            biggest = max(entries, key=lambda e: e.width)
            ck.check(im.format == "CUR" and im.size == (biggest.width, biggest.height),
                     f"{label}: Pillow opened as {im.format} {im.size}")
            ours, theirs = biggest.image.load(), im.convert("RGB").load()
            bad = sum(1 for y in range(im.height) for x in range(im.width)
                      if ours[x, y][3] == 255 and theirs[x, y] != ours[x, y][:3])
            ck.check(bad == 0, f"{label}: Pillow CUR decode differs from ours at {bad} opaque pixel(s)")
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 보고
            ck.check(False, f"{label}: Pillow could not open ({exc})")
            return
    ico_bytes = bytearray(data)
    ico_bytes[2:4] = (1).to_bytes(2, "little")  # type 2 -> 1: ICO 디코더로 크기별 디코드
    try:
        ico = Image.open(io.BytesIO(bytes(ico_bytes)))
        for e in entries:
            theirs = ico.ico.getimage((e.width, e.height)).convert("RGBA").load()
            ours = e.image.load()
            bad = sum(1 for y in range(e.height) for x in range(e.width)
                      if (theirs[x, y][3] > 0) != (ours[x, y][3] > 0)
                      or (ours[x, y][3] > 0 and theirs[x, y][:3] != ours[x, y][:3]))
            ck.check(bad == 0, f"{label}: Pillow ICO decode of {e.width}px differs from ours at {bad} pixel(s)")
    except Exception as exc:  # noqa: BLE001
        ck.check(False, f"{label}: Pillow ICO decode failed ({exc})")


def windows_opinion(ck: Checker, files: dict[str, Path], spec: dict) -> bool:
    """user32 LoadImage로 파일을 읽어 크기·핫스팟을 확인한다. 레지스트리·시스템 폴더는 건드리지 않는다."""
    if sys.platform != "win32":
        ck.note("not Windows: skipping LoadImage check")
        return False
    import ctypes
    from ctypes import wintypes as w

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    class ICONINFO(ctypes.Structure):
        _fields_ = [("fIcon", w.BOOL), ("xHotspot", w.DWORD), ("yHotspot", w.DWORD),
                    ("hbmMask", w.HBITMAP), ("hbmColor", w.HBITMAP)]

    class BITMAP(ctypes.Structure):
        _fields_ = [("bmType", w.LONG), ("bmWidth", w.LONG), ("bmHeight", w.LONG), ("bmWidthBytes", w.LONG),
                    ("bmPlanes", w.WORD), ("bmBitsPixel", w.WORD), ("bmBits", w.LPVOID)]

    user32.LoadImageW.restype = w.HANDLE
    user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]
    user32.GetIconInfo.argtypes = [w.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.DestroyCursor.argtypes = [w.HANDLE]
    gdi32.GetObjectW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [w.HANDLE]

    for role in spec["roles"]:
        name = role["registryName"]
        path = files[name]
        for n in spec["sizes"]:
            h = user32.LoadImageW(None, str(path), 2, n, n, 0x10)  # IMAGE_CURSOR, LR_LOADFROMFILE
            if not ck.check(h, f"{path.name}: LoadImage({n}px) failed (error {ctypes.get_last_error()})"):
                continue
            info = ICONINFO()
            ok = user32.GetIconInfo(h, ctypes.byref(info))
            if ck.check(ok, f"{path.name}: GetIconInfo({n}px) failed"):
                bm = BITMAP()
                bmp = info.hbmColor or info.hbmMask
                gdi32.GetObjectW(bmp, ctypes.sizeof(bm), ctypes.byref(bm))
                height = bm.bmHeight if info.hbmColor else bm.bmHeight // 2
                ck.check((bm.bmWidth, height) == (n, n), f"{path.name}: Windows loaded {bm.bmWidth}x{height}, asked {n}")
                got = (info.xHotspot, info.yHotspot)
                want = expected_hotspot(role, n)
                ck.check(got == want, f"{path.name}: Windows hotspot {got} at {n}px, want {want}")
                for b in (info.hbmColor, info.hbmMask):
                    if b:
                        gdi32.DeleteObject(b)
            user32.DestroyCursor(h)
    return True


# ---------------------------------------------------------------- INF

def parse_inf(text: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    sections: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for raw in text.split("\r\n"):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = sections.setdefault(line[1:-1], [])
        elif cur is not None:
            cur.append(line)
    strings = {}
    for line in sections.get("Strings", []):
        key, _, val = line.partition("=")
        strings[key.strip()] = val.strip().strip('"')
    return sections, strings


class Inf:
    """읽은 INF 한 개. ASCII/CRLF 검사, [Strings] 치환, 레지스트리 줄 파싱."""

    def __init__(self, ck: Checker, path: Path, generated: str) -> None:
        self.label = path.name
        raw = path.read_bytes()
        ck.check(raw.isascii(), f"{self.label} is not pure ASCII")
        ck.check(raw.count(b"\n") == raw.count(b"\r\n") and raw.endswith(b"\r\n"), f"{self.label}: line endings are not CRLF only")
        self.text = raw.decode("ascii", "replace")
        # 화이트리스트: 섹션, [DefaultInstall]의 키, 모든 레지스트리 줄(플래그 포함)이 export가 만드는 텍스트와 정확히 같아야 한다.
        if not ck.check(self.text == generated, f"{self.label} differs from what export.py generates"):
            diff = [l for l in difflib.unified_diff(generated.split("\r\n"), self.text.split("\r\n"), "expected", "found", n=0, lineterm="")
                    if not l.startswith(("---", "+++", "@@"))]
            for line in diff[:6]:
                print(f"       {line}")
        self.sections, self.strings = parse_inf(self.text)
        self.name = self.strings.get("SCHEME_NAME")
        self.cur_dir = self.strings.get("CUR_DIR")
        ck.check(self.name and self.cur_dir == f"Cursors\\{self.name}",
                 f"{self.label}: [Strings] SCHEME_NAME/CUR_DIR odd: {self.name!r} {self.cur_dir!r}")
        self.default = {k.strip(): v.strip() for k, _, v in (ln.partition("=") for ln in self.sections.get("DefaultInstall", []))}

    def expand(self, s: str) -> str:
        for k, v in self.strings.items():
            s = s.replace(f"%{k}%", v)
        return s.replace("%10%", "<WINDIR>")

    def rows(self, section: str) -> list[list[str]]:
        return [next(csv.reader([self.expand(ln)], skipinitialspace=True)) for ln in self.sections.get(section, [])]

    def dest_dir(self, section: str) -> str | None:
        hit = [ln for ln in self.sections.get("DestinationDirs", []) if ln.startswith(section + " ")]
        return self.expand(hit[0].partition("=")[2].strip()) if hit else None


def check_inf_shape(ck: Checker, inf: Inf, keys: set[str]) -> None:
    """export.build_inf/build_uninf와 따로 보는 INF 뼈대: [DefaultInstall] 키, 다른 설치 섹션, 명령 실행 지시어."""
    got = [ln.partition("=")[0].strip() for ln in inf.sections.get("DefaultInstall", [])]
    ck.check(sorted(got) == sorted(keys), f"{inf.label}: [DefaultInstall] keys are {got}, want exactly {sorted(keys)}")
    others = [s for s in inf.sections if s.lower().startswith("defaultinstall") and s != "DefaultInstall"]
    ck.check(not others, f"{inf.label}: extra install sections {others} (only [DefaultInstall] may exist)")
    low = inf.text.lower()
    ck.check("runpostsetupcommands" not in low and "runpresetupcommands" not in low,
             f"{inf.label}: has RunPostSetupCommands/RunPreSetupCommands")


def check_infs(ck: Checker, build_dir: Path, files: dict[str, str], scheme_name: str) -> None:
    tail = [files[r] for r in EXPECTED_ORDER]
    want_dir = f'10,"Cursors\\{scheme_name}"'
    cpl = "Control Panel\\Cursors"

    # --- install.inf
    inst = Inf(ck, build_dir / "install.inf", export.build_inf(scheme_name, files))
    ck.check(inst.name == scheme_name, f"install.inf scheme name {inst.name!r} != manifest {scheme_name!r}")
    check_inf_shape(ck, inst, {"CopyFiles", "AddReg"})
    ck.check(inst.default.get("CopyFiles") == "Scheme.Cur", "install.inf: [DefaultInstall] CopyFiles != Scheme.Cur")
    add_sections = [s.strip() for s in inst.default.get("AddReg", "").split(",")]
    for sec in add_sections:
        ck.check(sec in inst.sections, f"install.inf: AddReg section [{sec}] missing")
        ck.check(all(r[0] == "HKCU" for r in inst.rows(sec)), f"install.inf: [{sec}] writes outside HKCU")
        # 레지스트리 줄 = root,key,name,flags,value. Scheme Source만 REG_DWORD 1, 나머지는 모두 플래그 없는 REG_SZ
        # (MULTI_SZ·NOCLOBBER·APPEND 같은 플래그가 붙으면 구성표가 안 맞게 써지거나 기존 값을 못 덮는다).
        for r in inst.rows(sec):
            if not ck.check(len(r) == 5, f"install.inf: [{sec}] line has {len(r)} fields, want 5: {r}"):
                continue
            if r[2] == "Scheme Source":
                ck.check(r[3:] == ["0x00010001", "1"], f"install.inf: Scheme Source is flags/value {r[3:]}, want 0x00010001/1")
            else:
                ck.check(r[3] == "", f"install.inf: {r[1]}\\{r[2] or '(default)'} has flags {r[3]!r}, want empty (REG_SZ)")
    ck.check(inst.dest_dir("Scheme.Cur") == want_dir, f"install.inf: Scheme.Cur goes to {inst.dest_dir('Scheme.Cur')}, want {want_dir}")
    low = inst.text.lower()
    ck.check("hklm" not in low and "runonce" not in low and "rundll32" not in low,
             "install.inf touches HKLM/RunOnce or runs a command")

    scheme_rows = [r for r in inst.rows("Scheme.Reg") if r[0] == "HKCU" and r[1] == f"{cpl}\\Schemes"]
    if ck.check(len(scheme_rows) == 1 and scheme_rows[0][2] == scheme_name, "install.inf: Schemes value missing or not named after the scheme"):
        ck.check(len(scheme_rows[0]) == 5 and scheme_rows[0][3] == "",
                 f"install.inf: Schemes value flags {scheme_rows[0][3:4]}, want empty (plain REG_SZ, not MULTI_SZ/NOCLOBBER)")
        paths = scheme_rows[0][4].split(",")
        ck.check(len(paths) == 17, f"install.inf: Schemes lists {len(paths)} paths, want 17")
        want = [f"<WINDIR>\\Cursors\\{scheme_name}\\{f}" for f in tail]
        ck.check(paths == want, f"install.inf: Schemes paths/order differ: {paths}")

    reg = {r[2]: r for r in inst.rows("Wreg") if r[0] == "HKCU" and r[1] == cpl}
    ck.check("" in reg and reg[""][4] == scheme_name, "install.inf: default value of Control Panel\\Cursors is not the scheme name")
    ck.check("Scheme Source" in reg and reg["Scheme Source"][4] == "1", "install.inf: Scheme Source value missing or not 1")
    for role, fname in zip(EXPECTED_ORDER, tail):
        got = reg[role][4] if role in reg else None
        ck.check(got == f"<WINDIR>\\Cursors\\{scheme_name}\\{fname}", f"install.inf: {cpl}\\{role} = {got}")
    ck.check(inst.sections.get("Scheme.Cur") == tail, f"install.inf: [Scheme.Cur] list differs: {inst.sections.get('Scheme.Cur')}")
    ck.check(all((build_dir / f).is_file() for f in tail), "a file listed in install.inf is missing from the folder")

    # --- uninstall.inf
    un = Inf(ck, build_dir / "uninstall.inf", export.build_uninf(scheme_name, files))
    ck.check(un.name == scheme_name and un.cur_dir == inst.cur_dir,
             f"uninstall.inf scheme/folder {un.name!r}/{un.cur_dir!r} != install.inf {inst.name!r}/{inst.cur_dir!r}")
    check_inf_shape(ck, un, {"DelFiles", "DelReg", "AddReg"})
    ck.check(un.default.get("DelFiles") == "Uninst.Files" and un.default.get("DelReg") == "Uninst.DelReg"
             and un.default.get("AddReg") == "Uninst.Reset", f"uninstall.inf: [DefaultInstall] is {un.default}")
    ck.check(un.dest_dir("Uninst.Files") == want_dir, f"uninstall.inf: Uninst.Files goes to {un.dest_dir('Uninst.Files')}, want {want_dir}")
    ck.check(un.sections.get("Uninst.Files") == tail, f"uninstall.inf: DelFiles list is {un.sections.get('Uninst.Files')}")
    ck.check(un.rows("Uninst.DelReg") == [["HKCU", f"{cpl}\\Schemes", scheme_name]],
             f"uninstall.inf: DelReg is {un.rows('Uninst.DelReg')}")
    reset = un.rows("Uninst.Reset")
    ck.check(all(r[0] == "HKCU" and r[1] == cpl for r in reset), "uninstall.inf: reset writes outside HKCU Control Panel\\Cursors")
    by_name = {r[2]: r for r in reset}
    ck.check(len(reset) == 19 and len(by_name) == 19, f"uninstall.inf: {len(reset)} reset lines, want 19 (default, Scheme Source, 17 roles)")
    ck.check("" in by_name and by_name[""][3:] == ["", WINDOWS_DEFAULT_NAME], f"uninstall.inf: default value row is {by_name.get('')}")
    ck.check("Scheme Source" in by_name and by_name["Scheme Source"][3:] == ["0x00010001", "2"],
             f"uninstall.inf: Scheme Source row is {by_name.get('Scheme Source')}")
    ck.check([r[2] for r in reset if r[2] in EXPECTED_ORDER] == EXPECTED_ORDER, f"uninstall.inf: role order is {[r[2] for r in reset]}")
    for role in EXPECTED_ORDER:
        f = WINDOWS_DEFAULT_CURSORS[role]
        want = ["0x00020000", f"%%SystemRoot%%\\cursors\\{f}" if f else ""]  # REG_EXPAND_SZ, INF에서는 %를 %%로 적는다
        ck.check(role in by_name and by_name[role][3:] == want, f"uninstall.inf: {role} row is {by_name.get(role)}, want {want}")
    ul = un.text.lower()
    ck.check("hklm" not in ul and "runonce" not in ul and "rundll32" not in ul, "uninstall.inf touches HKLM/RunOnce or runs a command")


# ---------------------------------------------------------------- zip

def check_zip(ck: Checker, build_dir: Path, manifest: dict) -> None:
    slug, dev = manifest["slug"], manifest["dev"]
    if dev:
        stale = ROOT / "dist" / f"{slug}.zip"
        ck.check(manifest["zip"] is None, "dev manifest lists a zip")
        ck.check(not stale.exists(), f"stale {stale.relative_to(ROOT)} exists but the build was --dev (no zip)")
        ck.note("dev build: zip not checked")
        return
    if not ck.check(manifest["zip"], "non-dev manifest has no zip path"):
        return
    zip_path = ROOT / manifest["zip"]
    if not ck.check(zip_path.is_file(), f"missing {manifest['zip']}"):
        return
    with zipfile.ZipFile(zip_path) as zf:
        ck.check(zf.testzip() is None, "zip has a corrupt member")
        want = {f"{slug}/{p.name}": p for p in sorted(build_dir.iterdir()) if p.is_file()}
        names = zf.namelist()
        ck.check(sorted(names) == sorted(want), f"zip listing differs from {build_dir.name}/: "
                 f"extra {sorted(set(names) - set(want))}, missing {sorted(set(want) - set(names))}")
        ck.check(len(names) == len(set(names)), "zip has duplicate entries")
        for name, path in want.items():
            if name in names:
                ck.check(zf.read(name) == path.read_bytes(), f"zip member {name} differs from the build folder (stale zip?)")


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("build_dir", type=Path, help="e.g. build/b-pastel")
    ap.add_argument("--spec", type=Path, help="spec JSON to check against (default: the one recorded in build/<slug>.manifest.json)")
    args = ap.parse_args(argv)

    build_dir = args.build_dir.resolve()
    manifest_path = build_dir.with_name(build_dir.name + ".manifest.json")
    if not manifest_path.is_file():
        print(f"error: {manifest_path.name} not found; run export.py first (a failed export removes it)", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec_path = args.spec.resolve() if args.spec else ROOT / manifest["spec"]
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    files = expected_files(spec)
    ck = Checker()
    if manifest["dev"]:
        print("DEV BUILD: exported with --dev (no zip, documents optional). Not a release; run export.py without --dev before shipping.")
    if manifest.get("warnings"):
        print(f"export warnings recorded in the manifest ({len(manifest['warnings'])}):")
        for w in manifest["warnings"]:
            print(f"  note {w}")

    ck.section("files")
    ck.check(EXPECTED_ORDER == export.SCHEME_ORDER, "check.py role order differs from export.py SCHEME_ORDER")
    ck.check(ANI_PNG_FROM == export.ANI_PNG_FROM, f"check.py ANI_PNG_FROM {ANI_PNG_FROM} != export.py {export.ANI_PNG_FROM}")
    ck.check(manifest["schemeOrder"] == EXPECTED_ORDER and manifest["aniPngFrom"] == ANI_PNG_FROM,
             "manifest schemeOrder/aniPngFrom differ from check.py's")
    ck.check(manifest["slug"] == build_dir.name, f"manifest slug {manifest['slug']!r} != folder {build_dir.name!r}")
    ck.check(list(files) == EXPECTED_ORDER, f"spec roles are not the 17 Windows roles in Schemes order: {list(files)}")
    ck.check(spec["grid"]["units"] == GRID_UNITS, f"spec grid units {spec['grid']['units']} != {GRID_UNITS}")
    ck.check(file_sha256(spec_path) == manifest["specSha256"], f"{spec_path.name} differs from the spec this build was exported from")
    source = ROOT / manifest["source"]
    ck.check(source.is_file() and file_sha256(source) == manifest["sourceSha256"],
             f"{manifest['source']} changed since this build was exported (stale build)")
    ck.check(spec["source"] == manifest["source"], f"spec source {spec['source']} != manifest {manifest['source']}")
    for role in spec["roles"]:  # 스펙의 hotspotPx도 hotspot과 일관돼야 한다
        for n in spec["sizes"]:
            ck.check(tuple(role["hotspotPx"].get(str(n), ())) == expected_hotspot(role, n),
                     f"spec {role['registryName']}: hotspotPx[{n}]={role['hotspotPx'].get(str(n))} != floor(hotspot*{n}/32) = {list(expected_hotspot(role, n))}")
    for fname in files.values():
        ck.check((build_dir / fname).is_file(), f"missing {fname}")
    present = {p.name for p in build_dir.glob("*.cur")} | {p.name for p in build_dir.glob("*.ani")}
    ck.check(present == set(files.values()), f"unexpected cursor files: {sorted(present - set(files.values()))}")
    docs = set(manifest["docs"])
    for fname in sorted(EXTRA_FILES | docs):
        ck.check((build_dir / fname).is_file(), f"missing {fname}")
    ck.check({p.name for p in build_dir.iterdir()} == set(files.values()) | EXTRA_FILES | docs,
             f"files in the folder differ from the expected set: {sorted({p.name for p in build_dir.iterdir()} ^ (set(files.values()) | EXTRA_FILES | docs))}")
    ck.check(all(d == "README.txt" or d.startswith("LICENSE") for d in docs), f"unexpected documents in the pack: {sorted(docs)}")
    ck.check(not (build_dir / "README.md").exists(), "README.md must not ship in the pack (its image links break there)")
    if not manifest["dev"]:
        ck.check("README.txt" in docs and any(d.startswith("LICENSE") for d in docs),
                 f"release build lacks LICENSE/README.txt: {sorted(docs)}")
        ck.check(manifest.get("allowWarnings") is False,
                 "release build was exported with --allow-warnings (lint errors were downgraded); rebuild without it")
        # 팩 안의 문서는 export가 매니페스트에 적은 원본(--docs-root 포함)과 같아야 한다(원본을 고친 뒤 다시 내보내지 않았다면 낡은 빌드)
        sources = manifest.get("docSources")
        if ck.check(isinstance(sources, dict) and set(sources) == docs,
                    f"manifest docSources {sorted(sources) if isinstance(sources, dict) else sources} != docs {sorted(docs)} (re-export)"):
            for name in sorted(docs):
                rec = sources[name]
                src = ROOT / rec["path"]  # 절대 경로면 그대로
                want_tail = "packaging/README-pack.txt" if name == "README.txt" else name
                ck.check(rec["path"] == want_tail or rec["path"].endswith("/" + want_tail),
                         f"{name} came from {rec['path']}, want a file named {want_tail}")
                packed = build_dir / name
                ck.check(packed.is_file() and file_sha256(packed) == rec["sha256"],
                         f"{name} in the pack differs from what export copied (manifest hash)")
                ck.check(src.is_file() and file_sha256(src) == rec["sha256"],
                         f"{name} in the pack differs from {rec['path']} (missing or edited since the export?)")
    if not all((build_dir / f).is_file() for f in files.values()):
        ck.finish()
        return 1

    ck.section("cur")
    for role in spec["roles"]:
        fname = files[role["registryName"]]
        if role["animated"]:
            continue
        data = (build_dir / fname).read_bytes()
        try:
            entries = curformat.parse_cur(data)
        except curformat.CurFormatError as exc:
            ck.check(False, f"{fname}: {exc}")
            continue
        frames_want = manifest["pixels"].get(fname)
        ck.check(frames_want is not None and len(frames_want) == 1, f"{fname}: manifest frame list is {None if frames_want is None else len(frames_want)} long, want 1")
        check_cur_entries(ck, fname, entries, spec["sizes"], role, frames_want[0] if frames_want else None)

    ck.section("ani")
    ani_frames: dict[str, list[tuple[bytes, list[curformat.CurEntry]]]] = {}
    for role in spec["roles"]:
        if not role["animated"]:
            continue
        fname = files[role["registryName"]]
        a = role["animation"]
        try:
            ani = curformat.parse_ani((build_dir / fname).read_bytes())
        except curformat.CurFormatError as exc:
            ck.check(False, f"{fname}: {exc}")
            continue
        n = len(a["frames"])
        h = ani.anih
        ck.check(h["cbSize"] == 36 and h["nFrames"] == n and h["nSteps"] == n, f"{fname}: anih {h}, want {n} frames/steps")
        ck.check((h["cx"], h["cy"], h["cBitCount"], h["cPlanes"]) == (0, 0, 0, 0) and h["flags"] == 1, f"{fname}: anih cx/cy/bits/planes/flags {h}")
        ck.check(h["JifRate"] in set(a["jiffies"]), f"{fname}: JifRate {h['JifRate']} is not one of the rates")
        ck.check(ani.rates == a["jiffies"], f"{fname}: rate chunk {ani.rates} != {a['jiffies']}")
        ck.check(ani.seq is None and ani.chunk_order == ["anih", "rate", "LIST"], f"{fname}: chunk order {ani.chunk_order}")
        ck.check(len(ani.frames) == n, f"{fname}: {len(ani.frames)} icon chunks, want {n}")
        frames_want = manifest["pixels"].get(fname)
        ck.check(frames_want is not None and len(frames_want) == n, f"{fname}: manifest has {None if frames_want is None else len(frames_want)} frames, want {n}")
        rendered = set()
        for i, frame in enumerate(ani.frames):
            try:
                entries = curformat.parse_cur(frame)
            except curformat.CurFormatError as exc:
                ck.check(False, f"{fname} frame {i}: {exc}")
                continue
            hashes = frames_want[i] if frames_want and i < len(frames_want) else None
            check_cur_entries(ck, f"{fname}[{i}]", entries, spec["sizes"], role, hashes, ANI_PNG_FROM)
            ck.check(max(e.offset for e in entries) <= curformat.MAX_ANI_ENTRY_OFFSET,
                     f"{fname}[{i}]: image offset {max(e.offset for e in entries)} > {curformat.MAX_ANI_ENTRY_OFFSET} (Windows cannot load it)")
            ani_frames.setdefault(fname, []).append((frame, entries))
            rendered.add(entries[-1].image.tobytes())
        ck.check(len(rendered) > 1, f"{fname}: all frames are identical")

    ck.section("pillow")
    for role in spec["roles"]:
        fname = files[role["registryName"]]
        if role["animated"]:
            for i, (frame, entries) in enumerate(ani_frames.get(fname, [])):
                pillow_opinion(ck, f"{fname}[{i}]", frame, entries, cur_plugin=False)  # .ani 프레임은 그 자체가 .cur
        else:
            data = (build_dir / fname).read_bytes()
            pillow_opinion(ck, fname, data, curformat.parse_cur(data))

    ck.section("windows")
    windows_opinion(ck, {r["registryName"]: build_dir / files[r["registryName"]] for r in spec["roles"]}, spec)

    ck.section("inf")
    check_infs(ck, build_dir, files, manifest["name"])

    ck.section("preview")
    with tempfile.TemporaryDirectory() as tmp:
        again = Path(tmp) / "preview.png"
        preview.make_preview(build_dir, spec, files, again)
        ck.check((build_dir / "preview.png").read_bytes() == again.read_bytes(),
                 "preview.png is not what the cursor files in this folder render to")

    ck.section("zip")
    check_zip(ck, build_dir, manifest)

    ok = ck.finish()
    if manifest["dev"]:
        print("DEV BUILD: this pass does not cover the release zip.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
