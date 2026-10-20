#!/usr/bin/env python3
"""프로필 인포그래픽(SVG) 생성기.

왼쪽 판(profile): 3D 기여 지형(아이소메트릭) · 기여 통계(요일별 평균, 7일 이동평균 ±1σ) · 언어 분포
오른쪽 판(twin):  디지털 트윈 · 3D 시연 카드 · 현장 데이터 규모 · 성과

사용법
  python metrics/generate.py           metrics/calendar.json 의 기여 달력으로 그린다
  python metrics/generate.py --fetch   GITHUB_TOKEN 으로 GitHub GraphQL 에서 기여 달력을 새로 받아
                                       calendar.json 에 저장한 뒤 그린다 (GitHub Actions 에서 매일 실행)

결과: assets/metrics/profile-light.svg · profile-dark.svg · twin-light.svg · twin-dark.svg
표준 라이브러리만 쓴다 (Python 3.9 이상).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import math
import os
import statistics
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "assets" / "metrics"
W = 480                      # 판 너비 (README 에서 두 판을 나란히 둠)
H = 670                      # 판 높이 (두 판 같게)

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,"
        "'Apple SD Gothic Neo','Malgun Gothic','Noto Sans CJK KR',sans-serif")

THEMES = {
    "light": {
        "bg": "#ffffff", "fg": "#1f2328", "muted": "#59636e", "accent": "#0969da", "border": "#d1d9e0",
        "card": "#f6f8fa", "chip": "#ddf4ff", "chip_fg": "#0969da",
        "cal": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "bar": "#40c463", "bar_hi": "#216e39", "line": "#0969da",
    },
    "dark": {
        "bg": "#0d1117", "fg": "#e6edf3", "muted": "#9198a1", "accent": "#4493f8", "border": "#3d444d",
        "card": "#151b23", "chip": "#121d2f", "chip_fg": "#4493f8",
        "cal": ["#1f2630", "#0e4429", "#006d32", "#26a641", "#39d353"],
        "bar": "#26a641", "bar_hi": "#39d353", "line": "#4493f8",
    },
}

# GitHub linguist 색
LANG_COLORS = {"TypeScript": "#3178c6", "C#": "#178600", "JavaScript": "#f1e05a", "SQL": "#e38c00",
               "CSS": "#663399", "HTML": "#e34c26", "Python": "#3572a5", "기타": "#8b949e"}

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def n(v: float) -> str:
    """좌표 글자: 소수 한 자리, .0 은 뺌"""
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def shade(hex_color: str, k: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c * k))) for c in (r, g, b)))


def text_width(s: str, size: float) -> float:
    """대략의 글자 폭 (한글 1em, 그 밖 0.6em) — 칩 크기 계산용"""
    return sum(size if ord(ch) > 0x2E80 else size * 0.6 for ch in s)


def data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


class Svg:
    def __init__(self, theme: str, icons: dict):
        self.t = THEMES[theme]
        self.icons = icons
        self.defs: list[str] = []
        self.body: list[str] = []
        self._id = 0

    def uid(self, prefix: str) -> str:
        self._id += 1
        return f"{prefix}{self._id}"

    def add(self, s: str) -> None:
        self.body.append(s)

    def c(self, color: str) -> str:
        return self.t.get(color, color)

    def text(self, x, y, s, size=12, color="fg", weight=None, anchor=None, tspans=(), halo=False):
        attrs = f'x="{n(x)}" y="{n(y)}" font-size="{size}" fill="{self.c(color)}"'
        if halo:                       # 선 위에 놓인 글자가 잘 보이게 바탕색 테두리
            attrs += f' stroke="{self.c("bg")}" stroke-width="3" stroke-linejoin="round" paint-order="stroke"'
        if weight:
            attrs += f' font-weight="{weight}"'
        if anchor:
            attrs += f' text-anchor="{anchor}"'
        inner = escape(s) + "".join(
            f'<tspan fill="{self.c(col)}" font-size="{sz}">{escape(t)}</tspan>' for t, col, sz in tspans)
        self.add(f"<text {attrs}>{inner}</text>")

    def icon(self, name, x, y, color="accent", size=16):
        k = size / 16
        for d in self.icons[name].split("|"):
            self.add(f'<path transform="translate({n(x)} {n(y)}) scale({k:g})" d="{d}" fill="{self.c(color)}"/>')

    def rect(self, x, y, w, h, fill, rx=0, stroke=None, opacity=None):
        a = f'x="{n(x)}" y="{n(y)}" width="{n(w)}" height="{n(h)}" fill="{self.c(fill)}"'
        if rx:
            a += f' rx="{rx}"'
        if stroke:
            a += f' stroke="{self.c(stroke)}"'
        if opacity is not None:
            a += f' fill-opacity="{opacity}"'
        self.add(f"<rect {a}/>")

    def title(self, x, y, icon, s, note=None):
        self.icon(icon, x, y - 13, "accent", 16)
        self.text(x + 22, y, s, 14, "accent", tspans=[(f"  {note}", "muted", 11)] if note else ())

    def render(self, height: int) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
                f'viewBox="0 0 {W} {height}" font-family="{FONT}">'
                f'<defs>{"".join(self.defs)}</defs>{"".join(self.body)}</svg>\n')


# ───────────────────────── 기여 달력 · 통계 ─────────────────────────

def fetch_calendar(user: str, token: str) -> list[list]:
    query = ('query($u:String!){user(login:$u){contributionsCollection{contributionCalendar'
             '{weeks{contributionDays{date contributionCount}}}}}}')
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": {"u": user}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-metrics"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    weeks = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [[d["date"], d["contributionCount"]] for w in weeks for d in w["contributionDays"]]


def levels(counts: list[int]) -> list[int]:
    """GitHub 잔디처럼 0 과 4단계 (기여가 있는 날의 사분위수 기준)"""
    nz = sorted(c for c in counts if c > 0)
    if not nz:
        return [0] * len(counts)

    def q(p):
        return nz[min(len(nz) - 1, int(p * (len(nz) - 1)))]

    t1, t2, t3 = q(0.25), q(0.5), q(0.75)
    return [0 if c == 0 else 1 if c <= t1 else 2 if c <= t2 else 3 if c <= t3 else 4 for c in counts]


def stats(days: list[list]) -> dict:
    counts = [c for _, c in days]
    active = [c for c in counts if c > 0]
    mean = sum(counts) / len(counts)
    sd = statistics.pstdev(counts)
    i = len(counts) - 1
    if counts[i] == 0:                 # 오늘 기여가 없으면 어제부터 셈 (GitHub 와 같음)
        i -= 1
    cur = 0
    while i >= 0 and counts[i] > 0:
        cur, i = cur + 1, i - 1
    best = run = 0
    for c in counts:
        run = run + 1 if c > 0 else 0
        best = max(best, run)
    by = [[] for _ in range(7)]
    for d, c in days:
        by[dt.date.fromisoformat(d).weekday()].append(c)
    wmean = [sum(v) / len(v) if v else 0 for v in by]
    return {
        "total": sum(counts), "days": len(counts), "active": len(active), "mean": mean, "sd": sd,
        "cv": sd / mean if mean else 0, "median_active": statistics.median(active) if active else 0,
        "max": max(counts), "cur": cur, "best": best, "wmean": wmean,
        "top_wd": max(range(7), key=lambda k: wmean[k]),
    }


def iso_calendar(svg: Svg, days: list[list], lv: list[int], x0: float, y0: float) -> None:
    """아이소메트릭 3D 잔디: 주 = 오른쪽 아래, 요일 = 왼쪽 아래, 높이 = √(기여 수 / 최대)"""
    t = svg.t
    offset = (dt.date.fromisoformat(days[0][0]).weekday() + 1) % 7      # 일요일 = 0
    ux, uy, vx, vy, hmax = 4.6, 2.3, -4.6, 2.3, 54
    ox, oy = x0 + 7 * 4.6 + 2, y0 + hmax + 2
    mx = max(c for _, c in days) or 1
    cells = []
    for i, (_, c) in enumerate(days):
        w, wd = divmod(i + offset, 7)
        cells.append((w, wd, c, lv[i]))
    cells.sort(key=lambda q: (q[0] + q[1], q[0]))                       # 뒤에서 앞으로 그림
    out = []
    for w, wd, c, l in cells:
        ax, ay = ox + w * ux + wd * vx, oy + w * uy + wd * vy
        bx, by = ax + ux, ay + uy
        cx, cy = bx + vx, by + vy
        dx, dy = ax + vx, ay + vy
        h = 0 if c == 0 else 2 + math.sqrt(c / mx) * hmax
        base = t["cal"][l]
        if h:
            out.append(f'<path d="M{n(dx)} {n(dy)}L{n(cx)} {n(cy)}L{n(cx)} {n(cy - h)}L{n(dx)} {n(dy - h)}Z" '
                       f'fill="{shade(base, 0.8)}"/>')
            out.append(f'<path d="M{n(cx)} {n(cy)}L{n(bx)} {n(by)}L{n(bx)} {n(by - h)}L{n(cx)} {n(cy - h)}Z" '
                       f'fill="{shade(base, 0.62)}"/>')
        out.append(f'<path d="M{n(ax)} {n(ay - h)}L{n(bx)} {n(by - h)}L{n(cx)} {n(cy - h)}L{n(dx)} {n(dy - h)}Z" '
                   f'fill="{base}"/>')
    svg.add("<g>" + "".join(out) + "</g>")


def chart_weekday(svg: Svg, x, y, w, h, st: dict) -> None:
    svg.text(x, y, "요일별 평균 기여", 11, "muted")
    top, base = y + 18, y + h - 14
    wm, mx = st["wmean"], max(st["wmean"]) or 1
    span = base - top - 12
    bw = w / 7
    my = base - st["mean"] / mx * span
    svg.add(f'<line x1="{n(x)}" x2="{n(x + w)}" y1="{n(my)}" y2="{n(my)}" stroke="{svg.c("line")}" '
            f'stroke-width="1" stroke-dasharray="3 3"/>')
    for i, v in enumerate(wm):
        bh = v / mx * span
        bx = x + i * bw + bw * 0.2
        svg.rect(bx, base - bh, bw * 0.6, max(bh, 0.5), "bar_hi" if i == st["top_wd"] else "bar", rx=2)
        svg.text(bx + bw * 0.3, base - bh - 3, f"{v:.1f}", 9.5, "muted", anchor="middle", halo=True)
        svg.text(bx + bw * 0.3, base + 12, WEEKDAYS[i], 10, "muted", anchor="middle")
    svg.text(x + w, my - 3, f"μ {st['mean']:.1f}", 9.5, "line", anchor="end", halo=True)
    svg.add(f'<line x1="{n(x)}" x2="{n(x + w)}" y1="{n(base)}" y2="{n(base)}" stroke="{svg.c("border")}"/>')


def chart_trend(svg: Svg, x, y, w, h, days: list[list]) -> None:
    svg.text(x, y, "최근 12주 · 7일 이동평균 ±1σ", 11, "muted")
    full = [c for _, c in days]
    k = min(84, len(full) - 6)
    off = len(full) - k
    ma, sd = [], []
    for i in range(k):
        win = full[off + i - 6: off + i + 1]
        ma.append(sum(win) / 7)
        sd.append(statistics.pstdev(win))
    vals = full[off:]
    top, base = y + 18, y + h - 14
    vmax = max(max(vals), max(m + s for m, s in zip(ma, sd))) or 1
    sx = w / k

    def Y(v):
        return base - min(v, vmax) / vmax * (base - top)

    band = [(x + (i + 0.5) * sx, Y(ma[i] + sd[i])) for i in range(k)]
    band += [(x + (i + 0.5) * sx, Y(max(0.0, ma[i] - sd[i]))) for i in reversed(range(k))]
    svg.add(f'<path d="M{"L".join(f"{n(a)} {n(b)}" for a, b in band)}Z" fill="{svg.c("line")}" fill-opacity="0.16"/>')
    for i, v in enumerate(vals):
        if v:
            svg.rect(x + i * sx + 0.3, Y(v), max(sx - 0.6, 0.6), base - Y(v), "bar", opacity=0.6)
    line = "L".join(f"{n(x + (i + 0.5) * sx)} {n(Y(ma[i]))}" for i in range(k))
    svg.add(f'<path d="M{line}" fill="none" stroke="{svg.c("line")}" stroke-width="1.6" stroke-linejoin="round"/>')
    for i, (d, _) in enumerate(days[off:]):
        day = dt.date.fromisoformat(d)
        if day.day == 1:
            tx = x + i * sx
            svg.add(f'<line x1="{n(tx)}" x2="{n(tx)}" y1="{n(base)}" y2="{n(base + 3)}" stroke="{svg.c("border")}"/>')
            svg.text(tx + 2, base + 12, f"{day.month}월", 10, "muted")
    svg.add(f'<line x1="{n(x)}" x2="{n(x + w)}" y1="{n(base)}" y2="{n(base)}" stroke="{svg.c("border")}"/>')


# ───────────────────────── 왼쪽 판: 3D · 통계 · 언어 ─────────────────────────

def profile_panel(theme: str, data: dict, days: list[list], icons: dict) -> str:
    svg = Svg(theme, icons)
    st = stats(days)
    lv = levels([c for _, c in days])

    # 머리: 사진 · 이름 · 역할 · 최근 14일
    clip = svg.uid("av")
    svg.defs.append(f'<clipPath id="{clip}"><circle cx="40" cy="40" r="28"/></clipPath>')
    svg.add(f'<image x="12" y="12" width="56" height="56" clip-path="url(#{clip})" '
            f'href="{data_uri(HERE / "thumbs" / "avatar.png")}"/>')
    svg.add(f'<circle cx="40" cy="40" r="28" fill="none" stroke="{svg.c("border")}"/>')
    svg.text(80, 34, data["name"], 18, "fg", weight="600")
    svg.text(80, 54, data["role"], 11.5, "muted")
    for i, l in enumerate(lv[-14:]):
        svg.rect(W - 12 - (14 - i) * 11 + 2, 14, 9, 9, svg.t["cal"][l], rx=2)
    svg.text(W - 12, 36, "최근 14일", 9.5, "muted", anchor="end")

    joined = data["joined"].replace("-", ".")
    rows = [("clock", f"{joined}부터 GitHub"), ("repo", f"{data['contributed_to']}개 저장소에 기여"),
            ("calendar", f"최근 1년 기여 {st['total']:,}회"), ("flame", f"하루 최고 {st['max']}회")]
    for i, (ic, s) in enumerate(rows):
        x, y = (12 if i % 2 == 0 else 246), (88 if i < 2 else 108)
        svg.icon(ic, x, y - 11, "muted", 14)
        svg.text(x + 20, y, s, 12, "fg")

    # 3D 기여 지형
    svg.title(12, 146, "package", "3D 기여 지형", "최근 1년 · 높이 = 하루 기여 수")
    iso_calendar(svg, days, lv, 12, 156)
    side = [("flame", "연속 기록", f"현재 {st['cur']}일 · 최고 {st['best']}일"),
            ("graph", "하루 평균", f"{st['mean']:.1f}회 · 최고 {st['max']}회"),
            ("calendar", "활동한 날", f"{st['active']}일 · {st['active'] / st['days'] * 100:.1f}%"),
            ("git-commit", "가장 활발한 요일", f"{WEEKDAYS[st['top_wd']]}요일")]
    for i, (ic, label, value) in enumerate(side):
        y = 182 + i * 42
        svg.icon(ic, 318, y - 11, "muted", 14)
        svg.text(338, y, label, 11, "muted")
        svg.text(318, y + 19, value, 13, "fg", weight="600")

    # 통계
    svg.title(12, 376, "pulse", "통계 · 기여 분포", "최근 1년 일별 기여 수")
    chart_weekday(svg, 12, 398, 210, 118, st)
    chart_trend(svg, 246, 398, 222, 118, days)
    svg.text(12, 538, f"평균 μ {st['mean']:.1f} · 표준편차 σ {st['sd']:.1f} · 변동계수 {st['cv']:.2f} · "
                      f"활동한 날 중앙값 {st['median_active']:.0f}", 11, "muted")

    # 언어 분포
    lang = data["languages"]
    items = [tuple(v) for v in lang["items"]]
    total = sum(v for _, v in items)
    shown = items[:6]
    rest = total - sum(v for _, v in shown)
    if rest:
        shown.append(("기타", rest))
    svg.title(12, 570, "code", "언어 분포", f"{total / 10000:.1f}만 줄 · {lang['repos']}개 저장소 기준 추정 (비공개 포함)")
    clip = svg.uid("lb")
    svg.defs.append(f'<clipPath id="{clip}"><rect x="12" y="582" width="456" height="8" rx="4"/></clipPath>')
    x = 12.0
    segs = []
    for name, v in shown:
        w = v / total * 456
        segs.append(f'<rect x="{n(x)}" y="582" width="{n(w + 0.5)}" height="8" fill="{LANG_COLORS.get(name, "#8b949e")}"/>')
        x += w
    svg.add(f'<g clip-path="url(#{clip})">{"".join(segs)}</g>')
    for i, (name, v) in enumerate(shown):
        cx, cy = (12 if i % 2 == 0 else 246), 610 + (i // 2) * 18
        svg.add(f'<circle cx="{cx + 5}" cy="{cy - 4}" r="4.5" fill="{LANG_COLORS.get(name, "#8b949e")}"/>')
        svg.text(cx + 16, cy, name, 12, "fg")
        lines = f"{v / 10000:.1f}만 줄" if v >= 10000 else f"{v:,}줄"
        svg.text(cx + 168, cy, lines, 11, "muted", anchor="end")
        svg.text(cx + 222, cy, f"{v / total * 100:.1f}%", 12, "fg", anchor="end")
    return svg.render(H)


# ───────────────────────── 오른쪽 판: 디지털 트윈 · 데이터 · 성과 ─────────────────────────

def twin_panel(theme: str, data: dict, icons: dict) -> str:
    svg = Svg(theme, icons)
    svg.icon("package", 12, 11, "accent", 18)
    svg.text(38, 26, "디지털 트윈 · 3D", 16, "accent", weight="600")
    svg.text(12, 48, "공정 · 설비를 3D로 옮겨 관제하는 시연 — Three.js · WebGL · Supabase 실시간", 11.5, "muted")

    for i, card in enumerate(data["twins"]):
        y = 62 + i * 110
        svg.rect(12, y, 456, 100, "card", rx=6, stroke="border")
        clip = svg.uid("th")
        svg.defs.append(f'<clipPath id="{clip}"><rect x="22" y="{y + 11}" width="140" height="78" rx="4"/></clipPath>')
        svg.add(f'<image x="22" y="{y + 11}" width="140" height="78" preserveAspectRatio="xMidYMid slice" '
                f'clip-path="url(#{clip})" href="{data_uri(HERE / "thumbs" / card["thumb"])}"/>')
        svg.text(176, y + 26, card["title"], 13, "fg", weight="600")
        for j, line in enumerate(card["lines"]):
            svg.text(176, y + 45 + j * 16, line, 11.5, "muted")
        cx = 176.0
        for tag in card["tags"]:
            tw = text_width(tag, 10.5) + 14
            svg.rect(cx, y + 74, tw, 17, "chip", rx=8.5)
            svg.text(cx + tw / 2, y + 86.5, tag, 10.5, "chip_fg", anchor="middle")
            cx += tw + 6

    svg.title(12, 418, "database", "현장 데이터 · 통계", "울산 상수도 · 비공개 과제")
    for i, row in enumerate(data["field"]):
        y = 442 + i * 40
        svg.icon(row["icon"], 12, y - 11, "muted", 14)
        svg.text(32, y, row["label"], 12, "fg", weight="600")
        svg.text(32, y + 17, row["text"], 11, "muted")

    svg.title(12, 576, "trophy", "성과")
    for i, a in enumerate(data["achievements"]):
        x = 12 + i * 116
        svg.rect(x, 588, 108, 70, "card", rx=6, stroke="border")
        svg.text(x + 54, 618, a["value"], 20, "fg", weight="700", anchor="middle")
        svg.text(x + 54, 636, a["label"], 11, "muted", anchor="middle")
        svg.text(x + 54, 650, a["sub"], 9.5, "muted", anchor="middle")
    return svg.render(H)


def main() -> None:
    data = json.loads((HERE / "data.json").read_text(encoding="utf-8"))
    icons = json.loads((HERE / "icons.json").read_text(encoding="utf-8"))
    cal_path = HERE / "calendar.json"
    cal = json.loads(cal_path.read_text(encoding="utf-8"))
    if "--fetch" in sys.argv:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("GITHUB_TOKEN 이 없습니다")
        days = fetch_calendar(data["user"], token)
        cal = {"source": "GitHub GraphQL contributionCalendar", "fetched": dt.date.today().isoformat(), "days": days}
        cal_path.write_text(json.dumps(cal, ensure_ascii=False), encoding="utf-8")
    days = cal["days"]
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (OUT / f"profile-{theme}.svg").write_text(profile_panel(theme, data, days, icons), encoding="utf-8")
        (OUT / f"twin-{theme}.svg").write_text(twin_panel(theme, data, icons), encoding="utf-8")
    st = stats(days)
    print(f"기여 {st['total']:,}회 · 활동 {st['active']}일 · 연속 현재 {st['cur']} / 최고 {st['best']}일 → {OUT}")


if __name__ == "__main__":
    main()
