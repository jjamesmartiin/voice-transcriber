#!/usr/bin/env python3
"""Pins for the Linux AppImage packaging: desktop entry, icons, .DirIcon.

AppImage/appdir-lint.sh (the checker the AppImage catalogue runs) refuses an
AppImage whose AppDir has no ``.DirIcon``, no ``.desktop`` file, or a ``.desktop``
file that ``desktop-file-validate`` rejects. nix-appimage only produces those
files when the derivation ships a ``.desktop`` entry whose ``Exec=`` basename
matches the bundled program and whose ``Icon=`` names an icon under
``share/icons/hicolor/<size>/apps/`` - otherwise its extra-files.sh prints "no
.desktop found; giving up" and the AppDir gets none of them.

Nothing else exercises this: it surfaced only when the release AppImage was
submitted to the catalogue, which reported "FATAL: .DirIcon is missing in
/tmp/.mount_..." (https://github.com/AppImage/appimage.github.io/pull/8908).
"""
from __future__ import annotations

import struct
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING = REPO_ROOT / "packaging" / "linux"
DESKTOP = PACKAGING / "vt.desktop"
APPSTREAM = PACKAGING / "io.github.jjamesmartiin.voice-transcriber.appdata.xml"
HICOLOR = PACKAGING / "icons" / "hicolor"
FLAKE = REPO_ROOT / "flake.nix"

# flake.nix meta.mainProgram; nix bundle makes this the AppImage's entrypoint,
# and extra-files.sh matches the desktop entry's Exec= basename against it.
MAIN_PROGRAM = "vt"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _desktop_entries() -> list[str]:
    """The [Desktop Entry] section as raw "Key=Value" lines."""
    lines = []
    in_section = False
    for raw in _read(DESKTOP).splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_section = line == "[Desktop Entry]"
            continue
        if in_section and line and not line.startswith("#"):
            lines.append(line)
    return lines


def _value(key: str) -> str:
    values = [line.split("=", 1)[1] for line in _desktop_entries()
              if line.startswith(f"{key}=")]
    assert len(values) == 1, f"{key}= must appear exactly once, found {values}"
    return values[0]


def _png_size(path: Path) -> tuple[int, int]:
    """Width/height straight out of the IHDR chunk (no image library needed)."""
    data = path.read_bytes()
    assert data.startswith(PNG_MAGIC), f"{path} is not a PNG"
    return struct.unpack(">II", data[16:24])


def _png_rgba_rows(path: Path) -> tuple[int, list[bytearray]]:
    """Decode an 8-bit RGBA, non-interlaced PNG into unfiltered rows.

    Deliberately stdlib-only (the test environment is not guaranteed to have an
    image library), and only used on the small icons we ship.
    """
    data = path.read_bytes()
    assert data.startswith(PNG_MAGIC), f"{path} is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    depth, colour_type, _, _, interlace = data[24:29]
    assert (depth, colour_type, interlace) == (8, 6, 0), (depth, colour_type, interlace)

    idat = bytearray()
    pos = 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        if data[pos + 4:pos + 8] == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length  # length + type + data + CRC

    raw = zlib.decompress(bytes(idat))
    stride, bpp = width * 4, 4
    rows: list[bytearray] = []
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filter_type = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            up_left = previous[i - bpp] if i >= bpp else 0
            if filter_type == 1:      # Sub
                row[i] = (row[i] + left) & 0xFF
            elif filter_type == 2:    # Up
                row[i] = (row[i] + up) & 0xFF
            elif filter_type == 3:    # Average
                row[i] = (row[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:    # Paeth
                p = left + up - up_left
                candidates = (left, up, up_left)
                best = min(candidates, key=lambda c: abs(p - c))
                row[i] = (row[i] + best) & 0xFF
            elif filter_type != 0:
                raise AssertionError(f"unknown PNG filter {filter_type} in {path}")
        rows.append(row)
        previous = row
    return width, rows


class TestDesktopEntry:
    def test_has_the_keys_the_linter_requires(self):
        # appdir-lint.sh: desktop-file-validate must pass, and Icon= and
        # Categories= must each appear exactly once in [Desktop Entry].
        assert DESKTOP.is_file(), DESKTOP
        assert _value("Type") == "Application"
        assert _value("Icon"), "Icon= (what .DirIcon is derived from) is empty"
        assert "Utility" in _value("Categories").split(";")
        # A TUI app: this is also what makes the catalogue run it inside a
        # terminal (worker.sh greps the AppDir's .desktop files for Terminal=true).
        assert _value("Terminal") == "true"

    def test_exec_matches_the_bundled_program(self):
        # extra-files.sh skips a desktop file whose Exec= basename is not the
        # program nix bundle bundles, and then there is no .desktop at all.
        assert Path(_value("Exec").split()[0]).name == MAIN_PROGRAM
        assert f'mainProgram = "{MAIN_PROGRAM}"' in _read(FLAKE)


class TestIcons:
    def test_icon_named_by_the_desktop_entry_exists_per_size(self):
        icon = _value("Icon")
        for size in ("256x256", "128x128", "64x64", "48x48"):
            path = HICOLOR / size / "apps" / f"{icon}.png"
            assert path.is_file(), path
            assert _png_size(path) == tuple(int(n) for n in size.split("x")), path

    def test_icons_have_transparent_corners_and_a_visible_glyph(self):
        # The icons come from icon.ico, whose frames are an opaque rounded tile
        # painted on white: white corners look broken as a catalogue thumbnail on
        # dark backgrounds, so packaging/linux keeps the outside transparent.
        # A regression here (re-deriving from icon.ico without the flood fill) is
        # invisible to every other test.
        for path in sorted(HICOLOR.glob("*x*/apps/*.png")):
            width, rows = _png_rgba_rows(path)
            corner_alpha = rows[0][3]          # top-left pixel, RGBA
            centre = rows[len(rows) // 2][(width // 2) * 4 + 3]
            assert corner_alpha == 0, f"{path}: corner is not transparent"
            assert centre == 255, f"{path}: centre is not opaque (art missing?)"


class TestFlakeInstallsThem:
    def test_flake_installs_desktop_entry_and_icons(self):
        text = _read(FLAKE)
        # extra-files.sh reads $drv/share/applications and $drv/share/icons
        assert "packaging/linux/vt.desktop $out/share/applications/vt.desktop" in text
        assert "cp -r packaging/linux/icons/hicolor $out/share/icons/" in text

    def test_flake_installs_the_appstream_metadata(self):
        assert "cp packaging/linux/*.appdata.xml $out/share/metainfo/" in _read(FLAKE)


class TestAppStreamMetadata:
    """AppStream data: the catalogue's description and licence come from here."""

    def test_is_valid_shaped_metadata_named_after_its_id(self):
        assert APPSTREAM.is_file(), APPSTREAM
        root = ET.parse(APPSTREAM).getroot()
        assert root.tag == "component"
        assert root.get("type") == "desktop-application"
        # appdir-lint.sh globs *appdata.xml and appstream requires <id> == file name
        component_id = root.findtext("id")
        assert component_id, "missing <id>"
        assert APPSTREAM.name == f"{component_id}.appdata.xml"
        # reverse-DNS, or appstreamcli warns cid-desktopapp-is-not-rdns
        assert component_id.count(".") >= 2, component_id

    def test_launchable_matches_the_desktop_entry(self):
        root = ET.parse(APPSTREAM).getroot()
        launchable = root.find('launchable[@type="desktop-id"]')
        assert launchable is not None and launchable.text == DESKTOP.name

    def test_licence_matches_the_licence_file(self):
        # The repo once claimed Apache-2.0 in README.md while LICENSE said MIT.
        root = ET.parse(APPSTREAM).getroot()
        assert root.findtext("project_license") == "MIT"
        assert _read(REPO_ROOT / "LICENSE").startswith("MIT License")
