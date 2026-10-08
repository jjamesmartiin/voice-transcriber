#!/usr/bin/env python3
"""Generate the Voice Transcriber hero GIF.

The animation faithfully renders the real ratatui front-end:

* chrome, palette and block layout mirror ``tui-rs/src/ui.rs`` and the
  authoritative reference render ``docs/assets/vt-tui-demo.svg``;
* the frame order mirrors the scripted demo in ``tui-rs/src/demo.rs``.

PIL only, no extra dependencies.
"""
import glob
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# Canvas / geometry
# --------------------------------------------------------------------------
W, H = 1190, 400   # matches docs/assets/vt-tui-demo.svg, widened for no clipping
FONT_SIZE = 14     # narrow enough that even the longest header fits
LH = 19            # terminal row height
Y0 = 44            # first text row
MARGIN = 20        # left/right text margin
TOTAL_ROWS = (H - Y0 - 8) // LH   # 18 usable rows

# --------------------------------------------------------------------------
# Palette (from docs/assets/vt-tui-demo.svg)
# --------------------------------------------------------------------------
BG = "#292929"
ACCENT = "#68a0b3"      # theme accent / ANSI cyan
CYAN_THEME = "#56d4dd"  # accent after "THEME SWITCHED" -> cyan
WHITE_BOLD = "#fdfdc5"
DIM = "#868887"
DEFAULT = "#c5c8c6"
YELLOW = "#d0b344"
GREEN = "#98a84b"       # ANSI green (status "Copied & Typed", sound on)
LIGHTCYAN = "#398280"   # ANSI light-cyan ("ready:" timing)
RED = "#8a4346"         # ANSI red (muted, RECORDING badge, VU peak)
MAGENTA = "#b48ead"     # Color::Magenta (REFINING GRAMMAR)
BG_RGB = (0x29, 0x29, 0x29)

TITLE = "Voice Transcriber — Terminal UI (Ratatui)"
MIC = "HyperX QuadCast S"

# Braille spinners (U+2800 block) are not present in the available monospace
# fonts, so use the covered quarter-circle rotation instead of a tofu box.
SPINNER = ["◐", "◓", "◑", "◒"]


def hx(s):
    return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))


def dim_color(c):
    # Soft blend (was 0.55): keeps dimmed red "sound: off" legible on #292929.
    r, g, b = hx(c)
    return tuple(int(v + (BG_RGB[i] - v) * 0.35) for i, v in enumerate((r, g, b)))


# --------------------------------------------------------------------------
# Font discovery (candidate list, then globs, then PIL default)
# --------------------------------------------------------------------------
def find_font(bold):
    name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    direct = [
        "/usr/share/fonts/truetype/dejavu/" + name,
        "/usr/local/share/fonts/dejavu/" + name,
        "/run/current-system/sw/share/fonts/truetype/dejavu/" + name,
        os.path.expanduser("~/.local/share/fonts/" + name),
    ]
    cands = list(direct)
    for pat in (
        "/nix/store/*dejavu*/share/fonts/truetype/" + name,
        "/usr/share/fonts/**/" + name,
        "/usr/local/share/fonts/**/" + name,
    ):
        cands.extend(sorted(glob.glob(pat, recursive=True)))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def load_font(size, bold=False):
    path = find_font(bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def find_emoji():
    for pat in (
        "/nix/store/*noto-fonts-color-emoji*/share/fonts/noto/NotoColorEmoji.ttf",
        "/usr/share/fonts/**/NotoColorEmoji.ttf",
    ):
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0]
    return None


def build_icons(size=15):
    """Pre-render colour emoji that the mono font lacks (🤖)."""
    icons = {}
    path = find_emoji()
    if not path:
        return icons
    for px in (109, 128, 136):
        try:
            emoji = ImageFont.truetype(path, px)
            im = Image.new("RGBA", (px + 40, px + 40), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((10, 10), "🤖", font=emoji, embedded_color=True)
            bb = im.getbbox()
            if bb:
                icons["🤖"] = im.crop(bb).resize((size, size), Image.LANCZOS)
                break
        except Exception:
            continue
    return icons


# --------------------------------------------------------------------------
# Tiny styled-span rendering
# --------------------------------------------------------------------------
def draw_text(im, draw, x, y, text, font, color, icons):
    """Draw a run, pasting pre-rendered icons where needed."""
    buf = []
    for ch in text:
        if ch in icons:
            if buf:
                run = "".join(buf)
                draw.text((x, y), run, font=font, fill=color)
                x += font.getlength(run)
                buf = []
            icon = icons[ch]
            im.paste(icon, (int(x), int(y + (LH - icon.height) // 2 + 1)), icon)
            x += font.getlength(ch)
        else:
            buf.append(ch)
    if buf:
        run = "".join(buf)
        draw.text((x, y), run, font=font, fill=color)
        x += font.getlength(run)
    return x


def line_width(line, fl, fb):
    total = 0.0
    for text, _color, bold, _dim, _bg in line:
        total += (fb if bold else fl).getlength(text.replace("\ufe0f", ""))
    return total


def draw_line(im, draw, x, y, spans, fl, fb, icons):
    for text, color, bold, dim, bg in spans:
        font = fb if bold else fl
        col = dim_color(color) if dim else hx(color)
        text = text.replace("\ufe0f", "")
        if bg:
            width = font.getlength(text)
            draw.rectangle([x - 1, y + 1, x + width + 1, y + LH - 3], fill=hx(bg))
        x = draw_text(im, draw, x, y, text, font, col, icons)


def render(lines, fl, fb, ft, icons):
    im = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(im)
    # window chrome
    draw.rounded_rectangle([(0, 0), (W - 1, H - 1)], radius=8,
                           outline=(112, 112, 112), width=1)
    for cx, color in zip((26, 48, 70), ("#ff5f57", "#febc2e", "#28c840"),
                         strict=False):
        draw.ellipse([cx - 7, 22 - 7, cx + 7, 22 + 7], fill=color)
    draw.text((W / 2, 22), TITLE, font=ft, fill=DEFAULT, anchor="mm")
    for i, line in enumerate(lines):
        draw_line(im, draw, MARGIN, Y0 + i * LH, line, fl, fb, icons)
    return im


# --------------------------------------------------------------------------
# Content builders (mirror ui.rs)
# --------------------------------------------------------------------------
def time_str(seconds):
    seconds %= 86400
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def trunc(s, maxn):
    if len(s) > maxn:
        return s[:maxn - 3] + "..."
    return s


def wrap_body(text, width):
    lines = textwrap.wrap(text, width=width, break_long_words=False,
                          break_on_hyphens=False) or [""]
    lines[0] = "  " + lines[0]
    return [[(ln, DEFAULT, False, False, None)] for ln in lines]


def header_line(mic):
    return [
        ("vt ", ACCENT, True, False, None),
        ("❯ ", ACCENT, True, False, None),
        ("voice transcriber v1.3.1 active ", WHITE_BOLD, True, False, None),
        (f"(model: COHERE │ mic: {mic})", DIM, False, False, None),
    ]


def event_lines(title, message, ts, level, accent, width):
    title_disp = title
    if level == "warning":
        title_disp = "⚠️ " + title
    elif level == "error":
        title_disp = "❌ " + title
    mid = f" ⚙️ {title_disp} "
    ts_part = f" [{ts}] "
    visible = len(mid.replace("\ufe0f", ""))
    right = max(2, width - 4 - visible - len(ts_part))
    top = [
        ("────", accent, True, False, None),
        (mid, accent, True, False, None),
        (ts_part, DIM, False, False, None),
        ("─" * right, accent, True, False, None),
    ]
    bottom = [("─" * width, accent, False, False, None)]
    return [top, [("  " + message, DEFAULT, False, False, None)], bottom]


def transcription_lines(n, text, rec, proc, ready, status, saved, session,
                        total, ts, accent, width):
    spans = [
        ("────", accent, True, False, None),
        (f" ❯ #{n} ", accent, True, False, None),
        (f" {ts} ", DIM, False, False, None),
    ]
    if rec > 0:
        spans += [("│ ", DIM, False, False, None),
                  (f"rec: {rec:.2f}s ", ACCENT, False, False, None)]
    spans += [("│ ", DIM, False, False, None),
              (f"proc: {proc:.2f}s ", YELLOW, False, False, None)]
    if proc > 0:
        spans += [("│ ", DIM, False, False, None),
                  (f"ready: {ready:.2f}s ", LIGHTCYAN, False, False, None)]
    if saved:
        parts = []
        if session:
            parts.append(f"session: {session}")
        if total:
            parts.append(f"total: {total}")
        totals = "(" + " · ".join(parts) + ")" if parts else ""
        spans += [("│ ", DIM, False, False, None),
                  (f"⚡ saved: +{saved} {totals} ", GREEN, True, False, None)]
    if status == "typed":
        status_txt, status_col = "Copied & Typed", GREEN
    elif status == "copied":
        status_txt, status_col = "Copied to Clipboard", ACCENT
    else:
        status_txt, status_col = "Clipboard Error", RED
    spans += [("│ ", DIM, False, False, None),
              (status_txt, status_col, False, False, None)]

    return [spans] + wrap_body(text, width - 2) + \
        [[("─" * width, accent, False, False, None)]]


def vu_spans(level):
    bar_len = 16
    filled = min(int(min(level, 1.0) * 3.5 * bar_len), bar_len)
    chars = "█" * filled + "░" * (bar_len - filled)
    if filled > 12:
        segs = [(0, 9, GREEN), (9, 13, YELLOW), (13, bar_len, RED)]
    elif filled > 7:
        segs = [(0, 7, GREEN), (7, bar_len, YELLOW)]
    else:
        segs = [(0, bar_len, GREEN)]
    return [(chars[a:b], c, False, False, None) for a, b, c in segs]


def status_lines(state, elapsed, vu, sub, spinner, muted, theme, mic):
    if state == "ready":
        l1 = [
            ("❯ ", theme, True, False, None),
            ("ready ", theme, True, False, None),
            ("│ ", DIM, False, False, None),
            (f"mic: {trunc(mic, 18)} ", ACCENT, False, False, None),
            ("│ ", DIM, False, False, None),
            ("model: cohere ", ACCENT, False, False, None),
            ("│ ", DIM, False, False, None),
        ]
        if muted:
            l1.append(("sound: off ", RED, False, True, None))
        else:
            l1.append(("sound: on ", GREEN, False, False, None))
        l1 += [
            ("│ ", DIM, False, False, None),
            ("clipboard ", ACCENT, False, False, None),
            ("│ ", DIM, False, False, None),
            ("preset: default ", ACCENT, False, False, None),
            ("│ ", DIM, False, False, None),
            ("num: auto ", ACCENT, False, False, None),
            ("│ ", DIM, False, False, None),
            ("mouse: off ", DIM, False, False, None),
        ]
        l2 = [("  [Space] Rec (tap again = stop, hands-free)  "
               "[s/S/,] Settings  [r] Reset  [q] Quit", DIM, False, False, None)]
        return [l1, l2]

    if state == "recording":
        return [[
            ("❯ ", theme, True, False, None),
            ("RECORDING ", "#ffffff", True, False, RED),
            (" ", DEFAULT, False, False, None),
            (f"[{elapsed:04.1f}s] ", YELLOW, True, False, None),
            ("│ ", DIM, False, False, None),
        ] + vu_spans(vu) + [
            (f" ({int(vu * 100)}%) ", ACCENT, False, True, None),
            ("│ ", DIM, False, False, None),
            (f"mic: {trunc(mic, 18)} ", ACCENT, False, False, None),
        ]]

    if state == "rewriting":
        sp = SPINNER[spinner % len(SPINNER)]
        return [[
            ("❯ ", theme, True, False, None),
            (f"🤖 {sp} REFINING GRAMMAR [{elapsed:04.1f}s] ", MAGENTA, True, False, None),
            ("│ ", DIM, False, False, None),
            ("SLM polishing text...", DEFAULT, False, False, None),
        ]]

    # processing
    sp = SPINNER[spinner % len(SPINNER)]
    msg = sub if sub else "Transcribing stream with Cohere..."
    return [[
        ("❯ ", theme, True, False, None),
        (f"{sp} PROCESSING AUDIO [{elapsed:04.1f}s] ", YELLOW, True, False, None),
        ("│ ", DIM, False, False, None),
        (msg, DEFAULT, False, False, None),
    ]]


# --------------------------------------------------------------------------
# Demo script (order from demo.rs)
# --------------------------------------------------------------------------
TEXT1 = ("This is the ratatui front-end of the voice transcriber status line, "
         "streaming cleanly into scrollback.")
TEXT2 = "Remind me to ship the ratatui migration next Tuesday at three thirty."
TEXT3 = "No wait, make that Wednesday."

DURATIONS = [1200, 700, 900, 800, 1600, 700, 600, 900, 1500, 1400, 700,
             600, 700, 600, 1400, 1500, 2400]


def main():
    fl = load_font(FONT_SIZE)
    fb = load_font(FONT_SIZE, bold=True)
    ft = load_font(13, bold=True)
    icons = build_icons()
    width = int((W - 2 * MARGIN) / fl.getlength("M"))
    content_w = W - 2 * MARGIN

    frames = []
    scroll_blocks = []
    max_width = 0.0
    theme = ACCENT
    muted = False
    counter = 0
    clock = 17 * 3600 + 2 * 60 + 25

    def tick(step=6):
        nonlocal clock
        clock += step
        return time_str(clock)

    def add_block(block):
        scroll_blocks.append(block)

    def snapshot(state, elapsed=0.0, vu=0.0, sub="", spinner=0):
        nonlocal max_width
        status = status_lines(state, elapsed, vu, sub, spinner, muted, theme, MIC)
        avail = TOTAL_ROWS - 2 - len(status)
        # Evict whole blocks only (newest first); never a partial block, so a
        # body can never appear orphaned from its header/rules.
        chosen, used = [], 0
        for block in reversed(scroll_blocks):
            if used + len(block) <= avail:
                chosen.append(block)
                used += len(block)
            else:
                break
        chosen.reverse()
        mid = [ln for block in chosen for ln in block]
        lines = ([header_line(MIC), []] + mid +
                 [[] for _ in range(avail - len(mid))] + status)
        for ln in lines:
            max_width = max(max_width, line_width(ln, fl, fb))
        frames.append(render(lines, fl, fb, ft, icons))

    # 1. warmup event
    add_block(event_lines("SYSTEM READY",
                          "Warming up model weights and audio device.",
                          tick(), "info", theme, width))
    snapshot("ready")
    # recording / processing / first transcription
    # VU levels stay in a realistic low-speech range so the bar fills
    # progressively; the `level * 3.5` multiplier in vu_spans is untouched.
    snapshot("recording", 1.4, 0.05)
    snapshot("recording", 3.2, 0.12)
    snapshot("processing", 4.6, 0.0, "Transcribing stream with Cohere...", 0)
    counter += 1
    add_block(transcription_lines(counter, TEXT1, 3.20, 1.35, 0.62, "typed",
                                  "12s", "1m 45s", "14h 20m", tick(), theme, width))
    snapshot("ready")
    # second take: processing then grammar refine
    snapshot("recording", 0.9, 0.08)
    snapshot("processing", 2.1, 0.0, "Transcribing stream with Cohere...", 1)
    snapshot("rewriting", 0.7, 0.0, "", 2)
    counter += 1
    add_block(transcription_lines(counter, TEXT2, 2.10, 1.05, 0.48, "typed",
                                  "8s", "1m 53s", "14h 20m", tick(), theme, width))
    snapshot("ready")
    # theme switch (accent -> cyan)
    add_block(event_lines("THEME SWITCHED",
                          "UI Color Theme set to 'cyan' (Active: CYAN)",
                          tick(), "info", theme, width))
    theme = CYAN_THEME
    snapshot("ready")
    # mute, third take (clipboard)
    muted = True
    snapshot("ready")
    snapshot("recording", 0.6, 0.06)
    snapshot("recording", 2.0, 0.18)
    snapshot("processing", 1.0, 0.0, "Transcribing stream with Cohere...", 3)
    counter += 1
    add_block(transcription_lines(counter, TEXT3, 2.00, 0.92, 0.41, "copied",
                                  "6s", "1m 59s", "14h 20m", tick(), theme, width))
    snapshot("ready")
    # dictionary/numbers tuning warning
    add_block(event_lines("TUNING",
                          "Dictionary hit: 'ratatui' → 'Ratatui'. "
                          "Numbers conversion enabled.",
                          tick(), "warning", theme, width))
    snapshot("ready")
    # unmute, final idle
    muted = False
    snapshot("ready")

    assert len(frames) == len(DURATIONS), (len(frames), len(DURATIONS))
    assert max_width <= content_w, (max_width, content_w)
    print(f"max line width: {max_width:.1f}px <= content {content_w}px")

    out_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "docs", "assets", "vt-demo.gif")
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=DURATIONS, loop=0, optimize=True, disposal=2)
    size = os.path.getsize(out_path)
    print(f"Generated {out_path}: {len(frames)} frames, "
          f"{sum(DURATIONS) / 1000.0:.1f}s, {size} bytes, {W}x{H}")


if __name__ == "__main__":
    main()
