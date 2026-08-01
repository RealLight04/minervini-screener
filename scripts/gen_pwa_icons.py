"""PWA 앱 아이콘 생성 — 인앱 브랜드 마크(VCP 축소→돌파 글리프)를 그대로 사용.

런타임 의존성 아님(생성물 PNG만 커밋). 재생성:
    venv/Scripts/python.exe scripts/gen_pwa_icons.py
색·글리프는 templates/base.html 의 .brand-mark SVG 및 CSS 토큰과 일치시킨다.
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "static", "icons")
os.makedirs(OUT, exist_ok=True)

BG = (19, 23, 34)      # #131722  앱 다크 배경 토큰(--bg dark)
GLYPH = (41, 98, 255)  # #2962ff  브랜드 액센트 토큰(--accent)
SS = 4                 # 슈퍼샘플 배율(부드러운 안티에일리어싱)

VB_W, VB_H = 28, 20    # base.html .brand-mark 뷰박스


def quad(p0, c, p1, n=48):
    """2차 베지어를 점열로 샘플링 (base.html의 Q 커맨드와 동일)."""
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * c[0] + t * t * p1[0],
                    u * u * p0[1] + 2 * u * t * c[1] + t * t * p1[1]))
    return out


def seg(p0, p1, n=48):
    return [(p0[0] + (p1[0] - p0[0]) * i / n, p0[1] + (p1[1] - p0[1]) * i / n)
            for i in range(n + 1)]


# 브랜드 마크: 변동성 축소(파동이 점점 작아짐) → 우상향 돌파
MAIN = (quad((1, 11), (4, 2), (7, 11)) + quad((7, 11), (10, 4), (13, 11))
        + quad((13, 11), (15, 7), (17, 11)) + seg((17, 11), (25, 3)))
ARROW = seg((19, 3), (25, 3)) + seg((25, 3), (25, 9))
ALL = MAIN + ARROW

# 실제 잉크 바운딩박스(뷰박스가 아니라 그려지는 영역)로 중앙 정렬
minx = min(p[0] for p in ALL); maxx = max(p[0] for p in ALL)
miny = min(p[1] for p in ALL); maxy = max(p[1] for p in ALL)
CW, CH = maxx - minx, maxy - miny


def render(size, rounded, glyph_frac):
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if rounded:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=BG)
    else:  # 마스커블: 풀블리드(코너까지 배경)
        d.rectangle([0, 0, S, S], fill=BG)

    # 잉크 bbox를 종횡비 유지하며 glyph_frac 크기의 중앙 박스에 맞춤
    box = S * glyph_frac
    scale = min(box / CW, box / CH)
    gw, gh = CW * scale, CH * scale
    ox, oy = (S - gw) / 2, (S - gh) / 2

    def tf(p):
        return (ox + (p[0] - minx) * scale, oy + (p[1] - miny) * scale)

    rad = max(2, S * 0.052) / 2  # 라운드 캡/조인용 스탬프 반경

    def stroke(points):
        for p in points:
            x, y = tf(p)
            d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=GLYPH)

    stroke(MAIN)
    stroke(ARROW)
    return img.resize((size, size), Image.LANCZOS)


SPECS = [
    ("icon-192.png", 192, True, 0.62),
    ("icon-512.png", 512, True, 0.62),
    ("icon-512-maskable.png", 512, False, 0.50),
    ("apple-touch-icon.png", 180, True, 0.62),
    ("favicon-32.png", 32, True, 0.66),
]

if __name__ == "__main__":
    for name, size, rounded, frac in SPECS:
        render(size, rounded, frac).save(os.path.join(OUT, name))
        print("wrote", os.path.join("static", "icons", name))
