"""미리보기 시트(preview.png). export.py가 팩 폴더에 만들고, check.py가 같은 파일인지 다시 만들어 비교한다."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import curformat


def _font(size: int):
    for cand in ("malgun.ttf", "NotoSansKR-Regular.ttf"):
        for base in (Path("C:/Windows/Fonts"),):
            if (base / cand).is_file():
                return ImageFont.truetype(str(base / cand), size), True
    return ImageFont.load_default(size), False


def make_preview(build_dir: Path, spec: dict, files: dict[str, str], out: Path) -> None:
    """디코드한 파일만으로 역할별 32/48/64px(어두운·밝은 줄), 애니메이션 프레임(32px)을 한 장에 모은다."""
    font, has_ko = _font(14)
    dark, light, page, ink = (0x20, 0x21, 0x24, 255), (0xF4, 0xF4, 0xF6, 255), (0x8C, 0x8C, 0x92, 255), (0x10, 0x10, 0x10, 255)
    sizes = [32, 48, 64]
    pad, row_h, label_w = 12, 76, 270
    strip_w = pad + sum(s + pad for s in sizes)
    anim_roles = [r for r in spec["roles"] if r["animated"]]
    max_frames = max((len(r["animation"]["frames"]) for r in anim_roles), default=0)
    anim_w = pad + max_frames * (32 + 8)
    width = max(label_w + 2 * strip_w + 3 * 8 + 16, label_w + anim_w + 16)
    head_h = 30
    height = head_h + len(spec["roles"]) * row_h + head_h + len(anim_roles) * (2 * 44 + 8) + 16
    sheet = Image.new("RGBA", (width, height), page)
    d = ImageDraw.Draw(sheet)

    def label(xy, role):
        text = role["registryName"] + (f"  {role['labelKo']}" if has_ko else "")
        d.text(xy, text, fill=ink, font=font)

    def first_frame(role):
        data = (build_dir / files[role["registryName"]]).read_bytes()
        if role["animated"]:
            return curformat.parse_cur(curformat.parse_ani(data).frames[0])
        return curformat.parse_cur(data)

    d.text((8, 6), "static / first frame  -  32 48 64 px on dark | light", fill=ink, font=font)
    y = head_h
    x_dark, x_light = label_w + 8, label_w + 8 + strip_w + 8
    for role in spec["roles"]:
        by_size = {e.width: e.image for e in first_frame(role)}
        label((8, y + row_h // 2 - 8), role)
        for x0, bg in ((x_dark, dark), (x_light, light)):
            d.rectangle((x0, y, x0 + strip_w, y + row_h - 4), fill=bg)
            x = x0 + pad
            for s in sizes:
                sheet.alpha_composite(by_size[s], (x, y + (row_h - 4 - s) // 2))
                x += s + pad
        y += row_h
    d.text((8, y + 6), "animated roles  -  every frame at 32 px", fill=ink, font=font)
    y += head_h
    for role in anim_roles:
        ani = curformat.parse_ani((build_dir / files[role["registryName"]]).read_bytes())
        frames = [{e.width: e.image for e in curformat.parse_cur(f)}[32] for f in ani.frames]
        label((8, y + 36), role)
        for bg in (dark, light):
            d.rectangle((label_w + 8, y, label_w + 8 + pad + len(frames) * 40, y + 40), fill=bg)
            for i, fr in enumerate(frames):
                sheet.alpha_composite(fr, (label_w + 8 + pad + i * 40, y + 4))
            y += 44
        y += 8
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(out)
