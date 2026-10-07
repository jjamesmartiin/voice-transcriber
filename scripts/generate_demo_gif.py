#!/usr/bin/env python3
"""
Generate vibrant, high-framerate demo GIF for Voice Transcriber landing page / README.
"""
import os
from PIL import Image, ImageDraw, ImageFont

font_path = "/nix/store/5dg9348vl33vd5lwgc0anr6chwd3jrqw-dejavu-fonts-2.37/share/fonts/truetype/DejaVuSansMono.ttf"
font_bold_path = "/nix/store/5dg9348vl33vd5lwgc0anr6chwd3jrqw-dejavu-fonts-2.37/share/fonts/truetype/DejaVuSansMono-Bold.ttf"

font = ImageFont.truetype(font_path, 15)
font_bold = ImageFont.truetype(font_bold_path, 15)
font_sm = ImageFont.truetype(font_path, 13)
font_sm_bold = ImageFont.truetype(font_bold_path, 13)
font_title = ImageFont.truetype(font_bold_path, 13)

W, H = 920, 360

def create_base():
    im = Image.new("RGB", (W, H), "#0d1117")
    draw = ImageDraw.Draw(im)
    
    # Title bar
    draw.rectangle([(0, 0), (W, 36)], fill="#161b22")
    draw.ellipse([(14, 12), (26, 24)], fill="#ff5f56")
    draw.ellipse([(34, 12), (46, 24)], fill="#ffbd2e")
    draw.ellipse([(54, 12), (66, 24)], fill="#27c93f")
    draw.text((W//2, 18), "Voice Transcriber — Cohere ASR Dictation (16kHz)", font=font_title, fill="#7d8590", anchor="mm")
    
    # Header Banner
    draw.text((24, 50), "vt", font=font_bold, fill="#00d2ff")
    draw.text((46, 50), "❯", font=font_bold, fill="#00e676")
    draw.text((64, 50), "voice transcriber v1.3.0 active", font=font_bold, fill="#ffffff")
    draw.text((360, 50), "(model: COHERE │ mic: Default Microphone)", font=font, fill="#6e7681")
    
    return im

def draw_badge(draw, x, y, text, bg, fg, bold=True):
    f = font_sm_bold if bold else font_sm
    bbox = draw.textbbox((x, y), text, font=f)
    w = bbox[2] - bbox[0] + 12
    h = 22
    draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=4, fill=bg)
    draw.text((x + 6, y + 3), text, font=f, fill=fg)
    return x + w + 8

def draw_divider(draw, y, saved_txt, meta_txt):
    dash = "─" * 4
    draw.text((24, y), dash, font=font, fill="#484f58")
    x = 24 + 40
    draw.text((x, y), "⚡ " + saved_txt, font=font_bold, fill="#ffd700")
    x += draw.textlength("⚡ " + saved_txt, font=font_bold) + 8
    draw.text((x, y), "· " + meta_txt, font=font, fill="#7d8590")
    x += draw.textlength("· " + meta_txt, font=font) + 8
    draw.text((x, y), "─" * 28, font=font, fill="#484f58")

def render_frame(state, rec_sec="", vu="", show_t1=False, show_t2=False, cursor=True):
    im = create_base()
    draw = ImageDraw.Draw(im)
    
    y = 86
    if show_t1:
        draw.text((24, y), "›", font=font_bold, fill="#00e676")
        draw.text((42, y), "\"Remind me Wednesday. Also add a note to GitHub.\"", font=font_bold, fill="#e6edf3")
        y += 26
        draw_divider(draw, y, "saved: +12s (session: 12s · total: 14m 20s)", "0.68s (RTF 0.14x)")
        y += 34
        
    if show_t2:
        draw.text((24, y), "›", font=font_bold, fill="#00e676")
        draw.text((42, y), "\"Push the new Docker container to Gitea.\"", font=font_bold, fill="#e6edf3")
        y += 26
        draw_divider(draw, y, "saved: +8s (session: 20s · total: 14m 28s)", "0.54s (RTF 0.12x)")
        y += 34
        
    # Status bar
    bx = 24
    if state == "READY":
        bx = draw_badge(draw, bx, y, "READY", "#1b4728", "#3fb950")
        bx = draw_badge(draw, bx, y, "CLIPBOARD", "#0c3b5e", "#58a6ff")
        bx = draw_badge(draw, bx, y, "DEFAULT", "#39255c", "#bc8cff")
        bx = draw_badge(draw, bx, y, "AUTO NUM", "#3c2e15", "#d29922")
        bx = draw_badge(draw, bx, y, "EN", "#21262d", "#8b949e")
        draw.text((bx + 8, y + 3), "Hold Alt+Shift to talk...", font=font, fill="#6e7681")
        if cursor:
            draw.text((bx + 230, y + 2), "▋", font=font, fill="#00e676")
    elif state == "RECORDING":
        bx = draw_badge(draw, bx, y, "RECORDING", "#5a1e1e", "#ff7b72")
        bx = draw_badge(draw, bx, y, f"● REC ({rec_sec})", "#490202", "#ffa198")
        draw.text((bx + 8, y + 2), f"VU: {vu} (54%)", font=font_bold, fill="#00e676")
    elif state == "PROCESSING":
        bx = draw_badge(draw, bx, y, "PROCESSING", "#543005", "#d29922")
        draw.text((bx + 8, y + 2), "⚙ Transcribing + Microsecond Post-Processing...", font=font_bold, fill="#e3b341")
        
    return im

def main():
    frames = []
    durations = []

    # Sequence:
    # 1. Initial idle
    frames.append(render_frame("READY", cursor=True))
    durations.append(1000)
    frames.append(render_frame("READY", cursor=False))
    durations.append(500)

    # 2. Recording 1: "remind me tuesday no wait make that wednesday and add note to github"
    frames.append(render_frame("RECORDING", rec_sec="0.4s", vu="▃▅"))
    durations.append(400)
    frames.append(render_frame("RECORDING", rec_sec="0.9s", vu="▃▅█▆▄"))
    durations.append(400)
    frames.append(render_frame("RECORDING", rec_sec="1.4s", vu="▃▅▇█▇▅▃"))
    durations.append(500)
    frames.append(render_frame("RECORDING", rec_sec="1.8s", vu="▃▄▆▅▃"))
    durations.append(400)

    # 3. Processing 1
    frames.append(render_frame("PROCESSING"))
    durations.append(600)

    # 4. Output 1 displayed + Ready
    frames.append(render_frame("READY", show_t1=True, cursor=True))
    durations.append(1200)
    frames.append(render_frame("READY", show_t1=True, cursor=False))
    durations.append(600)

    # 5. Recording 2: "push the new docker container to get tea"
    frames.append(render_frame("RECORDING", rec_sec="0.3s", vu="▃▅", show_t1=True))
    durations.append(400)
    frames.append(render_frame("RECORDING", rec_sec="0.7s", vu="▃▅█▆▄", show_t1=True))
    durations.append(400)
    frames.append(render_frame("RECORDING", rec_sec="1.1s", vu="▃▅▇█▇▅▃", show_t1=True))
    durations.append(500)

    # 6. Processing 2
    frames.append(render_frame("PROCESSING", show_t1=True))
    durations.append(500)

    # 7. Output 2 displayed + Ready
    frames.append(render_frame("READY", show_t1=True, show_t2=True, cursor=True))
    durations.append(1500)
    frames.append(render_frame("READY", show_t1=True, show_t2=True, cursor=False))
    durations.append(1000)
    frames.append(render_frame("READY", show_t1=True, show_t2=True, cursor=True))
    durations.append(2500)

    out_path = "docs/assets/vt-demo.gif"
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True
    )
    sz = os.path.getsize(out_path)
    print(f"Generated {out_path} with {len(frames)} frames ({sz} bytes).")

if __name__ == "__main__":
    main()
