"""Windows 커서 파일(.cur / .ani) 쓰기와 읽기.

쓰기: Pillow RGBA 이미지 -> 32비트 BGRA DIB + 1비트 AND 마스크. 항목별로 PNG 압축본(CursorImage.png)도 가능.
읽기: check.py가 쓰는 자체 파서. 디렉터리·DIB·RIFF 구조는 바이트로 직접 검증하고, PNG 항목은 헤더를 직접 본 뒤 Pillow로 디코드한다.
"""
from __future__ import annotations

import hashlib
import io
import statistics
import struct
from dataclasses import dataclass, field
from typing import Sequence

from PIL import Image

CUR_TYPE = 2
AF_ICON = 1
ANIH_SIZE = 36
# Windows(11에서 확인)는 .ani 안의 'icon' 청크에서 이미지 오프셋이 65535를 넘는 항목이 있으면
# 파일 전체를 읽지 못한다. 같은 .cur 파일은 문제없다. 크기 5개(32~128)를 전부 DIB로 담으면
# 128px 항목 오프셋이 68982라서 이 한도를 넘는다. 큰 크기를 PNG 항목으로 두면 작아져서 들어간다.
MAX_ANI_ENTRY_OFFSET = 0xFFFF
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_ICONDIR = struct.Struct("<HHH")
_ICONDIRENTRY = struct.Struct("<BBBBHHII")  # w, h, colors, reserved, hotX, hotY, bytes, offset
_BMIH = struct.Struct("<IiiHHIIiiII")


class CurFormatError(ValueError):
    pass


@dataclass(frozen=True)
class CursorImage:
    """한 크기의 커서 이미지. image는 size x size RGBA(스트레이트 알파)."""

    size: int
    hotspot: tuple[int, int]
    image: Image.Image
    png: bool = False  # True면 DIB 대신 PNG 압축 항목으로 저장


@dataclass
class CurEntry:
    """파서가 읽은 .cur 디렉터리 항목 하나와 디코드한 이미지."""

    width: int
    height: int
    hotspot: tuple[int, int]
    byte_size: int
    offset: int
    bit_count: int
    image: Image.Image  # RGBA
    and_mask: list[list[int]] = field(repr=False)  # [y][x], 위에서 아래 순서, 1 = 투명 칸. PNG 항목은 빈 목록
    compressed: bool = False  # PNG 항목


@dataclass
class AniFile:
    anih: dict[str, int]
    rates: list[int] | None
    seq: list[int] | None
    frames: list[bytes]  # 'icon' 청크 내용(완전한 .cur)
    chunk_order: list[str]


# ---------------------------------------------------------------- 쓰기

def pixel_hash(img: Image.Image) -> str:
    """디코드한 RGBA 픽셀의 해시. 내보낼 때 매니페스트에 적고, check.py가 파일에서 다시 계산해 비교한다."""
    if img.mode != "RGBA":
        raise CurFormatError(f"pixel_hash needs RGBA, got {img.mode}")
    return hashlib.sha256(f"{img.width}x{img.height}:".encode() + img.tobytes()).hexdigest()


def _encode_png(img: Image.Image) -> bytes:
    if img.size[0] != img.size[1] or img.mode != "RGBA":
        raise CurFormatError(f"expected square RGBA image, got {img.size} {img.mode}")
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=9)
    return buf.getvalue()


def _encode_dib(img: Image.Image) -> bytes:
    n = img.width
    if img.size != (n, n) or img.mode != "RGBA":
        raise CurFormatError(f"expected square RGBA image, got {img.size} {img.mode}")
    # DIB는 아래에서 위로 저장한다.
    xor = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes("raw", "BGRA")
    alpha = img.getchannel("A").tobytes()
    row_bytes = (n + 7) // 8
    stride = (row_bytes + 3) // 4 * 4
    pad = bytes(stride - row_bytes)
    and_mask = bytearray()
    for y in range(n - 1, -1, -1):
        bits = 0
        for a in alpha[y * n:(y + 1) * n]:
            bits = (bits << 1) | (a == 0)
        bits <<= row_bytes * 8 - n
        and_mask += bits.to_bytes(row_bytes, "big") + pad
    header = _BMIH.pack(40, n, 2 * n, 1, 32, 0, len(xor) + len(and_mask), 0, 0, 0, 0)
    return header + xor + bytes(and_mask)


def build_cur(images: Sequence[CursorImage]) -> bytes:
    """크기별 이미지들로 .cur 하나를 만든다. 핫스팟은 디렉터리 항목의 planes/bitcount 칸(PNG 항목도 같다)."""
    if not images:
        raise CurFormatError("no images")
    dibs = [_encode_png(i.image) if i.png else _encode_dib(i.image) for i in images]
    offset = _ICONDIR.size + _ICONDIRENTRY.size * len(images)
    out = bytearray(_ICONDIR.pack(0, CUR_TYPE, len(images)))
    for img, dib in zip(images, dibs):
        n = img.size
        hx, hy = img.hotspot
        if n > 256 or not (0 <= hx < n and 0 <= hy < n):
            raise CurFormatError(f"bad size/hotspot: {n} {img.hotspot}")
        out += _ICONDIRENTRY.pack(n % 256, n % 256, 0, 0, hx, hy, len(dib), offset)
        offset += len(dib)
    for dib in dibs:
        out += dib
    return bytes(out)


def _chunk(tag: bytes, data: bytes) -> bytes:
    """RIFF 청크. 크기는 패딩 제외, 홀수 길이면 0바이트 하나를 덧붙인다."""
    pad = b"\0" if len(data) & 1 else b""
    return tag + struct.pack("<I", len(data)) + data + pad


def build_ani(frames: Sequence[bytes], jiffies: Sequence[int], jif_rate: int | None = None) -> bytes:
    """프레임(.cur 바이트)마다 한 스텝씩 순서대로 보여 주는 .ani. seq 청크는 쓰지 않는다."""
    if not frames or len(frames) != len(jiffies):
        raise CurFormatError("need one jiffy value per frame (no 'seq' chunk is written)")
    if any(j < 1 for j in jiffies):
        raise CurFormatError("jiffies must be >= 1")
    for i, frame in enumerate(frames):
        count = _ICONDIR.unpack_from(frame, 0)[2]
        offsets = [_ICONDIRENTRY.unpack_from(frame, _ICONDIR.size + _ICONDIRENTRY.size * k)[7] for k in range(count)]
        if max(offsets) > MAX_ANI_ENTRY_OFFSET:
            raise CurFormatError(f"frame {i}: image offset {max(offsets)} > {MAX_ANI_ENTRY_OFFSET}; Windows rejects such .ani")
    if jif_rate is None:
        jif_rate = statistics.mode(jiffies)
    n = len(frames)
    anih = struct.pack("<9I", ANIH_SIZE, n, n, 0, 0, 0, 0, jif_rate, AF_ICON)
    rate = struct.pack(f"<{n}I", *jiffies)
    fram = b"fram" + b"".join(_chunk(b"icon", f) for f in frames)
    body = b"ACON" + _chunk(b"anih", anih) + _chunk(b"rate", rate) + _chunk(b"LIST", fram)
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------------------------------------------------------------- 읽기

def _decode_dib(data: bytes, width: int, height: int) -> tuple[Image.Image, list[list[int]], int]:
    if len(data) < 40:
        raise CurFormatError("DIB shorter than BITMAPINFOHEADER")
    (size, w, h, planes, bits, comp, size_image, _xp, _yp, clr_used, _ci) = _BMIH.unpack_from(data, 0)
    if size != 40:
        raise CurFormatError(f"biSize {size} (unsupported DIB header)")
    if (w, h) != (width, height * 2):
        raise CurFormatError(f"DIB {w}x{h} does not match entry {width}x{height} (doubled height expected)")
    if planes != 1 or bits != 32 or comp != 0 or clr_used != 0:
        raise CurFormatError(f"expected 32-bit BI_RGB, got planes={planes} bits={bits} comp={comp}")
    xor_len = width * height * 4
    row_bytes = (width + 7) // 8
    stride = (row_bytes + 3) // 4 * 4
    and_len = stride * height
    if len(data) != 40 + xor_len + and_len:
        raise CurFormatError(f"DIB length {len(data)} != {40 + xor_len + and_len}")
    if size_image not in (0, xor_len + and_len):
        raise CurFormatError(f"biSizeImage {size_image} != {xor_len + and_len}")
    xor = data[40:40 + xor_len]
    img = Image.frombytes("RGBA", (width, height), xor, "raw", "BGRA", 0, -1)  # -1 = 아래에서 위로
    mask: list[list[int]] = []
    base = 40 + xor_len
    for y in range(height - 1, -1, -1):
        row = data[base + y * stride: base + y * stride + row_bytes]
        mask.append([(row[x >> 3] >> (7 - (x & 7))) & 1 for x in range(width)])
    return img, mask, bits


def _decode_png(data: bytes, width: int, height: int) -> Image.Image:
    # IHDR: 8바이트 시그니처 + 길이/타입 8 + width/height 8 + 비트 깊이 1 + 색 종류 1(6 = RGBA)
    if len(data) < 26 or data[:8] != PNG_MAGIC or data[12:16] != b"IHDR":
        raise CurFormatError("bad PNG signature/IHDR")
    w, h = struct.unpack_from(">II", data, 16)
    if (w, h) != (width, height):
        raise CurFormatError(f"PNG is {w}x{h}, entry says {width}x{height}")
    if (data[24], data[25]) != (8, 6):
        raise CurFormatError(f"PNG bit depth/colour type {data[24]}/{data[25]}, want 8/6 (RGBA)")
    img = Image.open(io.BytesIO(data))
    img.load()
    if img.format != "PNG" or img.mode != "RGBA" or img.size != (width, height):
        raise CurFormatError(f"PNG decoded as {img.format} {img.mode} {img.size}")
    return img


def parse_cur(data: bytes) -> list[CurEntry]:
    if len(data) < _ICONDIR.size:
        raise CurFormatError("file too short")
    reserved, kind, count = _ICONDIR.unpack_from(data, 0)
    if reserved != 0 or kind != CUR_TYPE:
        raise CurFormatError(f"bad ICONDIR header (reserved={reserved}, type={kind}, want type 2)")
    if count == 0 or len(data) < _ICONDIR.size + _ICONDIRENTRY.size * count:
        raise CurFormatError("bad entry count")
    entries: list[CurEntry] = []
    expected_offset = _ICONDIR.size + _ICONDIRENTRY.size * count
    for i in range(count):
        w, h, colors, res, hx, hy, nbytes, offset = _ICONDIRENTRY.unpack_from(
            data, _ICONDIR.size + _ICONDIRENTRY.size * i)
        w = w or 256
        h = h or 256
        if colors != 0 or res != 0:
            raise CurFormatError(f"entry {i}: colorCount/reserved not 0")
        if offset != expected_offset or offset + nbytes > len(data):
            raise CurFormatError(f"entry {i}: offset/length out of place ({offset}, {nbytes})")
        expected_offset += nbytes
        body = data[offset:offset + nbytes]
        if body[:8] == PNG_MAGIC:
            entries.append(CurEntry(w, h, (hx, hy), nbytes, offset, 32, _decode_png(body, w, h), [], True))
        else:
            img, mask, bits = _decode_dib(body, w, h)
            entries.append(CurEntry(w, h, (hx, hy), nbytes, offset, bits, img, mask))
    if expected_offset != len(data):
        raise CurFormatError(f"{len(data) - expected_offset} trailing bytes after last image")
    return entries


def _iter_chunks(data: bytes, start: int, end: int):
    pos = start
    while pos < end:
        if pos + 8 > end:
            raise CurFormatError("truncated chunk header")
        tag = data[pos:pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        body_start = pos + 8
        body_end = body_start + size
        if body_end > end:
            raise CurFormatError(f"chunk {tag!r} overruns its parent")
        pos = body_end + (size & 1)
        if pos > end:
            raise CurFormatError(f"chunk {tag!r} missing even padding")
        yield tag, body_start, body_end


def parse_ani(data: bytes) -> AniFile:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"ACON":
        raise CurFormatError("not a RIFF ACON file")
    (riff_size,) = struct.unpack_from("<I", data, 4)
    if riff_size != len(data) - 8:
        raise CurFormatError(f"RIFF size {riff_size} != file size - 8 ({len(data) - 8})")
    anih: dict[str, int] | None = None
    rates = seq = None
    frames: list[bytes] = []
    order: list[str] = []
    for tag, s, e in _iter_chunks(data, 12, len(data)):
        order.append(tag.decode("ascii", "replace"))
        if tag == b"anih":
            if e - s != ANIH_SIZE:
                raise CurFormatError(f"anih is {e - s} bytes, want {ANIH_SIZE}")
            names = ("cbSize", "nFrames", "nSteps", "cx", "cy", "cBitCount", "cPlanes", "JifRate", "flags")
            anih = dict(zip(names, struct.unpack_from("<9I", data, s)))
        elif tag in (b"rate", b"seq "):
            if (e - s) % 4:
                raise CurFormatError(f"{tag!r} length not a multiple of 4")
            values = list(struct.unpack_from(f"<{(e - s) // 4}I", data, s))
            if tag == b"rate":
                rates = values
            else:
                seq = values
        elif tag == b"LIST":
            if data[s:s + 4] != b"fram":
                continue  # INFO 등은 건너뜀
            for sub, ss, se in _iter_chunks(data, s + 4, e):
                if sub != b"icon":
                    raise CurFormatError(f"unexpected chunk {sub!r} in LIST fram")
                frames.append(data[ss:se])
    if anih is None:
        raise CurFormatError("missing anih")
    return AniFile(anih, rates, seq, frames, order)
