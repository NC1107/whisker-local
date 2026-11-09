#!/usr/bin/env python3
"""Draws the app icon and writes every size iOS, Android and the web build need."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

APP = Path(__file__).resolve().parent.parent / "app"

CANVAS = 1024
# Drawn large and scaled down because ImageDraw does not antialias.
SUPERSAMPLE = 4

PINE = "#1F6F5F"
MIST = "#F1F4F2"
INK = "#17332C"
MINT = "#7CC4AC"
FOAM = "#DDEEE8"

ANDROID_DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
ANDROID_ICON_DP = 48
ANDROID_FOREGROUND_DP = 108


def _rounded_polygon(
    draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], radius: int, fill: str
) -> None:
    draw.polygon(points, fill=fill)
    draw.line([*points, points[0]], fill=fill, width=radius * 2)
    for x, y in points:
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def draw_icon(background: str | None) -> Image.Image:
    """A cat looking over the rim of a litter box, inside the adaptive icon safe zone."""
    size = CANVAS * SUPERSAMPLE
    image = Image.new("RGBA", (size, size), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def at(*values: int) -> tuple[int, ...]:
        return tuple(value * SUPERSAMPLE for value in values)

    ear_radius = 22 * SUPERSAMPLE
    for ear in (
        [(348, 352), (318, 212), (470, 276)],
        [(676, 352), (706, 212), (554, 276)],
    ):
        _rounded_polygon(
            draw,
            [(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in ear],
            ear_radius,
            MIST,
        )
    draw.ellipse(at(302, 260, 722, 680), fill=MIST)
    draw.ellipse(at(400, 430, 464, 510), fill=INK)
    draw.ellipse(at(560, 430, 624, 510), fill=INK)

    draw.rounded_rectangle(at(212, 560, 812, 810), radius=56 * SUPERSAMPLE, fill=MINT)
    draw.rounded_rectangle(at(172, 530, 852, 620), radius=45 * SUPERSAMPLE, fill=FOAM)
    return image.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)


def _save(image: Image.Image, path: Path, pixels: int, *, alpha: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scaled = image.resize((pixels, pixels), Image.Resampling.LANCZOS)
    # App Store Connect rejects an icon that has an alpha channel.
    (scaled if alpha else scaled.convert("RGB")).save(path, optimize=True)


def main() -> None:
    icon = draw_icon(PINE)
    foreground = draw_icon(None)

    ios = APP / "ios/Runner/Assets.xcassets/AppIcon.appiconset"
    for entry in json.loads((ios / "Contents.json").read_text())["images"]:
        points = float(entry["size"].split("x")[0])
        _save(icon, ios / entry["filename"], round(points * int(entry["scale"][0])))

    res = APP / "android/app/src/main/res"
    for density, scale in ANDROID_DENSITIES.items():
        _save(
            icon,
            res / f"mipmap-{density}/ic_launcher.png",
            round(ANDROID_ICON_DP * scale),
        )
        _save(
            foreground,
            res / f"mipmap-{density}/ic_launcher_foreground.png",
            round(ANDROID_FOREGROUND_DP * scale),
            alpha=True,
        )
    adaptive = res / "mipmap-anydpi-v26/ic_launcher.xml"
    adaptive.parent.mkdir(parents=True, exist_ok=True)
    adaptive.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background"/>\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
        "</adaptive-icon>\n"
    )
    (res / "values/ic_launcher_background.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<resources>\n"
        f'    <color name="ic_launcher_background">{PINE}</color>\n'
        "</resources>\n"
    )

    web = APP / "web"
    for name, pixels in (
        ("icons/Icon-192.png", 192),
        ("icons/Icon-512.png", 512),
        ("icons/Icon-maskable-192.png", 192),
        ("icons/Icon-maskable-512.png", 512),
        ("favicon.png", 64),
    ):
        _save(icon, web / name, pixels)


if __name__ == "__main__":
    main()
