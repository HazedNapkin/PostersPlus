"""
Landscape (16:9) poster rendering.

Deliberately a separate renderer rather than a mode inside ``build_poster``.
Almost every anchor in the portrait layout is keyed to *width* — the diagonal
sash, the badge row, the rating bar, the logo box — and on a canvas that is
twice as wide and 40% shorter every one of them lands wrong.  The portrait
vocabulary does not survive the aspect change, so this file owns its own.

Layout, all fractions of the canvas:

    +--------------------------------------------------+
    |  [badge]                              [badge]    |   top_left / top_right
    |                                                  |
    |                                                  |
    |......................vignette....................|   band, 0.40 h
    |  LOGO  (or title)              Genre | Yr | 87   |
    +--------------------------------------------------+

Three rules govern the whole thing:

  * **Sizes key off height, positions off both.**  A width-derived font on a
    1000x563 canvas is nearly three times its optical size on 500x750.
  * **Both top corners stay clear of anything load-bearing.**  Stremio draws its
    watched check and hover-dismiss top-left, Nuvio draws its watched badge
    top-right; each takes roughly 11% of width by 20% of height.  The badge is
    placed inside that zone only because the user asked for it — it is a glass
    pill, so a small circle overlapping its leading corner stays readable.
  * **Baselines sit above 0.85 h**, clearing Stremio's continue-watching
    progress bar.

The tinted vignette is the one part of the portrait system that transfers
unchanged, and improves: its colour ramp already runs left-to-right across the
band, so twice the width gives it twice the runway.  Its helpers live in
main.py and are imported at call time — the same late-import idiom tvdb.py uses
for tmdb internals — to keep this module free of a circular import.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from i18n import translate_genre, translate_sash

_FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# --- Layout constants (fractions of the canvas) ------------------------------

_BAND_RATIO      = 0.40   # bottom vignette height
_BAND_ALPHA      = 212    # peak alpha at the very bottom row
_BAND_CURVE      = 1.5    # easing exponent, shared with the portrait band

# Absolute cell counts the tint sampler works in.  The portrait defaults (64/24)
# describe a 500px-wide band; at 1000px each cell would cover twice the content,
# so the local-colour end of the blur slider would go coarse exactly where it
# wants to be sharper.
_TINT_COLUMNS    = 96
_RAMP_COLUMNS    = 36

_SIDE_PAD        = 0.055  # left inset for the logo / badge
_RIGHT_PAD       = 0.045  # right inset for the info strip
# Shared bottom baseline for the logo and the info strip — the logo's ink
# bottom and the text baseline, which is where the two align optically.
#
# Anchored low on purpose.  The band's alpha ramps to full at the very bottom
# row, so anything sitting high in it is being asked to read against the weakest
# part of the only thing put there to support it.  This leaves a ~6% margin
# below the text, which is about where the ink stops once descenders are drawn.
_BASELINE        = 0.925
_BAND_CLEAR      = 0.02   # keep the logo this far inside the band's top edge

_LOGO_MAX_W      = 0.42   # keeps the logo out of the info strip's half
_LOGO_MAX_H      = 0.30

_BADGE_TOP       = 0.075
_BADGE_FONT      = 0.042
_BADGE_PAD_X     = 22
_BADGE_PAD_Y     = 11

_INFO_FONT       = 0.072  # "Genre | Year | Score" strip
_TITLE_FONT      = 0.085  # fallback when no logo is available

_MUTED           = (255, 255, 255, 170)
_SEPARATOR       = (255, 255, 255, 90)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(_FONTS_DIR, name), max(1, size))


def _draw_vignette(image: Image.Image, art: Image.Image, cfg) -> None:
    """Paint the bottom band, tinted from the art when the user asked for it.

    ``art`` is the pre-vignette snapshot: sampling ``image`` would just return
    the darkness a previous pass painted.
    """
    from main import (
        _vignette_dominant_rgb, _vignette_secondary_rgb, _vignette_tint_band,
        _vignette_frost_band, _vignette_level_band, _VIGNETTE_SAT_FULL,
    )

    width, height = image.size
    band_h = max(1, int(height * _BAND_RATIO))
    band_y = height - band_h

    t = np.linspace(0, 1, band_h, dtype=np.float32)
    eased = ((1 - (1 - t) ** _BAND_CURVE) * _BAND_ALPHA).astype(np.uint8)
    ramp = Image.fromarray(
        np.broadcast_to(eased[:, np.newaxis], (band_h, width)).copy(), mode="L"
    )

    box = (0, band_y, width, height)
    tinted = None
    if cfg.vignette_poster_color_bottom:
        _strict, tint, conf = _vignette_dominant_rgb(art)
        if tint is not None:
            second = (
                _vignette_secondary_rgb(art, tint)
                if cfg.vignette_color_ramp and conf > 0 else None
            )
            # Same derivation the portrait bands use: levelling follows
            # whichever of saturation / blur is asking for more of it.
            slider = min(1.0, max(0.0, cfg.vignette_color_saturation) / _VIGNETTE_SAT_FULL)
            level = max(slider, min(1.0, max(0.0, cfg.vignette_color_blur)))
            _vignette_frost_band(image, box, ramp, cfg.vignette_color_blur)
            _vignette_level_band(image, box, ramp, level)
            tinted = _vignette_tint_band(
                art, box, tint, conf,
                cfg.vignette_color_saturation, cfg.vignette_color_blur,
                second, cfg.vignette_color_lightness,
            ).convert("RGBA")

    if tinted is None:
        tinted = Image.new("RGBA", (width, band_h), (0, 0, 0, 0))
    tinted.putalpha(ramp)
    image.paste(tinted, (0, band_y), mask=tinted)


def _glass_pill(image: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Frosted rounded rect, sampled from whatever it is sitting on.

    Adapts to its backing: a bright region gets a darker glass so the label
    keeps contrast without needing a second visible gradient behind it.  That
    is what lets the badge survive a top corner we do not control.
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return

    region = image.crop(box).convert("RGB").filter(ImageFilter.GaussianBlur(9))
    arr = np.asarray(region, dtype=np.float32)
    # Rec. 709 luma of the backing, 0-255.
    luma = float((arr[:, :, 0] * .2126 + arr[:, :, 1] * .7152 + arr[:, :, 2] * .0722).mean())
    # Bright backing -> pull down hard; dark backing -> lift slightly so the
    # pill reads as glass rather than as a hole.
    gain, lift = (0.55, 8.0) if luma > 120 else (0.85, 26.0)
    arr = np.clip(arr * gain + lift, 0, 255)

    glass = Image.fromarray(arr.astype(np.uint8)).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], h // 2, fill=255)
    glass.putalpha(mask)
    image.alpha_composite(glass, (x0, y0))
    ImageDraw.Draw(image).rounded_rectangle(box, h // 2, outline=(255, 255, 255, 56), width=2)


def _draw_badge(image: Image.Image, text: str, position: str,
                logo_height: int = 0) -> None:
    width, height = image.size
    font = _font("Inter-Bold.ttf", int(height * _BADGE_FONT))
    draw = ImageDraw.Draw(image)
    tw = draw.textlength(text, font=font)
    th = int(height * _BADGE_FONT)
    bw, bh = int(tw + _BADGE_PAD_X * 2), int(th + _BADGE_PAD_Y * 2)

    if position == "top_right":
        x, y = width - int(width * _RIGHT_PAD) - bw, int(height * _BADGE_TOP)
    elif position == "logo":
        # Stacked above the logo, sharing its left edge.  With no logo drawn —
        # original art, which carries its own title treatment — there is nothing
        # to stack on, so the badge takes the bottom-left slot itself.
        x = int(width * _SIDE_PAD)
        y = int(height * _BASELINE) - bh
        if logo_height:
            y -= logo_height + int(height * 0.045)
    else:  # top_left
        x, y = int(width * _SIDE_PAD), int(height * _BADGE_TOP)

    _glass_pill(image, (x, y, x + bw, y + bh))
    draw.text((x + _BADGE_PAD_X, y + _BADGE_PAD_Y - 2), text, font=font,
              fill=(255, 255, 255, 242))


def _draw_logo(image: Image.Image, logo: Image.Image) -> int:
    """Left-aligned, bottom-anchored. Returns the drawn height."""
    width, height = image.size

    alpha = logo.getchannel("A")
    bbox = alpha.point(lambda a: 255 if a > 32 else 0).getbbox() or alpha.getbbox()
    if bbox:
        logo = logo.crop(bbox)
    if logo.width <= 0 or logo.height <= 0:
        return 0

    # Height is capped by the ratio AND by the band itself: a tall logo scaled
    # only by _LOGO_MAX_H can top out above the vignette, leaving its upper half
    # sitting on bare art with nothing behind it.
    band_top = height * (1 - _BAND_RATIO) + height * _BAND_CLEAR
    max_h = min(int(height * _LOGO_MAX_H), int(height * _BASELINE - band_top))
    scale = min(int(width * _LOGO_MAX_W) / logo.width, max(1, max_h) / logo.height)
    drawn = logo.resize((max(1, round(logo.width * scale)),
                         max(1, round(logo.height * scale))), Image.Resampling.LANCZOS)

    x = int(width * _SIDE_PAD)
    y = int(height * _BASELINE) - drawn.height

    # Soft drop shadow so a white wordmark survives a light patch in the band.
    shadow = Image.new("RGBA", drawn.size, (0, 0, 0, 0))
    shadow.putalpha(drawn.getchannel("A"))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(7)), (x + 3, y + 5))
    image.alpha_composite(drawn, (x, y))
    return drawn.height


def _draw_title(image: Image.Image, title: str) -> None:
    width, height = image.size
    font = _font("Inter-Bold.ttf", int(height * _TITLE_FONT))
    draw = ImageDraw.Draw(image)
    max_w = int(width * _LOGO_MAX_W)
    text = title
    while draw.textlength(text, font=font) > max_w and len(text) > 1:
        text = text[:-1]
    if text != title:
        text = text.rstrip() + "…"
    draw.text((int(width * _SIDE_PAD), int(height * _BASELINE)), text,
              font=font, fill=(255, 255, 255, 245), anchor="ls")


def _draw_info_strip(image: Image.Image, cfg, genre_label: str,
                     release_year: str | None, score) -> None:
    """`Genre | Year | 87`, right-aligned on the shared baseline.

    Drawn right-to-left so the score stays pinned to the right edge whatever the
    genre string does, and the whole strip is measured before anything is drawn
    so a long genre can be dropped rather than colliding with the logo.
    """
    from ratings import score_color_for_mode

    width, height = image.size
    font = _font("Inter-Bold.ttf", int(height * _INFO_FONT))
    draw = ImageDraw.Draw(image)

    if isinstance(score, str):
        score_text = score if score.isdigit() else "—"
    else:
        score_text = str(score)

    if score_text.isdigit():
        score_rgb = score_color_for_mode(
            int(score_text), cfg.score_color_mode, cfg.score_custom_palette
        )[0]
        score_fill = (*score_rgb, 255)
    else:
        score_fill = (255, 255, 255, 220)

    parts: list[tuple[str, tuple[int, int, int, int]]] = []
    if genre_label:
        parts.append((genre_label, _MUTED))
    if release_year:
        parts.append((str(release_year), _MUTED))
    parts.append((score_text, score_fill))

    sep = "  |  "
    sep_w = draw.textlength(sep, font=font)

    def total(items) -> float:
        return (sum(draw.textlength(t, font=font) for t, _ in items)
                + sep_w * max(0, len(items) - 1))

    # Everything left of the info strip belongs to the logo; if the two would
    # meet, shed the genre first, then the year, before shrinking any type.
    limit = width * (1 - _RIGHT_PAD) - width * (_SIDE_PAD + _LOGO_MAX_W) - width * 0.03
    while len(parts) > 1 and total(parts) > limit:
        parts.pop(0)

    x = width - int(width * _RIGHT_PAD)
    baseline = int(height * _BASELINE)
    for i, (text, fill) in enumerate(reversed(parts)):
        tw = draw.textlength(text, font=font)
        draw.text((x - tw, baseline), text, font=font, fill=fill, anchor="ls")
        x -= tw
        if i < len(parts) - 1:
            x -= sep_w
            draw.text((x, baseline), sep, font=font, fill=_SEPARATOR, anchor="ls")


def build_landscape(
    image: Image.Image,
    score: int | str,
    genre: str,
    cfg,
    logo: Image.Image | None = None,
    fallback_title: str | None = None,
    discovery_meta=None,
    release_year: str | None = None,
    **_ignored,
) -> Image.Image:
    """Render the landscape poster.  Mirrors ``build_poster``'s call shape so the
    request pipeline can swap one for the other; extra kwargs it does not use
    (quality tokens, age rating) are accepted and dropped."""
    from main import pick_sash

    image = image.convert("RGBA")
    art = image.copy()          # pre-vignette snapshot for tint sampling

    _draw_vignette(image, art, cfg)

    # The upstream logo gate already passes logo=None for original art, but the
    # text fallback is guarded here too: printing our own title over art that
    # carries its own is the one failure this mode exists to avoid.
    logo_height = 0
    if getattr(cfg, "landscape_art", "textless") != "original":
        if logo is not None:
            logo_height = _draw_logo(image, logo)
        elif fallback_title:
            _draw_title(image, fallback_title)

    _draw_info_strip(image, cfg,
                     "" if cfg.hide_genre else (translate_genre(genre, cfg.logo_language) or genre),
                     release_year, score)

    if cfg.sash_mode != "hidden" and discovery_meta is not None:
        sash_result = pick_sash(discovery_meta, cfg.sash_priority)
        if sash_result is not None:
            label, _sash_type = sash_result
            _draw_badge(image, translate_sash(label, cfg.logo_language).upper(),
                        getattr(cfg, "landscape_badge_pos", "top_left"),
                        logo_height=logo_height)

    return image
