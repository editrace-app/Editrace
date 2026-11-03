# Editrace brand guidelines

Version 1. Logo: the Frame mark. System: Scan layout and type.
Tagline: **Rebuild the edit in minutes.**

---

## Brand concept

Editrace reverse-engineers short-form video. The mark is a 9:16 vertical frame cut by a single scan line: a video being read, layer by layer. The brand is precise, calm and engineered. Frame's ink-and-red palette carries the tone, and the monospaced Scan system gives it a measured, technical voice, like reading an x-ray of an edit. It should feel native to creators who live in CapCut, TikTok and Reels, and credible to the agencies paying for it. Confident, not loud: the videos are the loud part, and Editrace is the tool that shows how they were made. Avoid generic "AI startup" cues: no glows, sparkles or purple gradients.

---

## Colour

### Palette

| Name | Hex | RGB | Role |
|---|---|---|---|
| **Leader Red** | `#D8432E` | 216, 67, 46 | Primary brand colour. The scan line in the logo on light backgrounds, playheads, highlights and graphics. Not for small text or for text-bearing buttons (see UI Red). |
| **Leader Red · on dark** | `#E5553F` | 229, 85, 63 | The same red, lifted for dark backgrounds: the scan line in dark logos, and playheads and accents on Ink. |
| **Leader Red · UI** | `#C23A26` | 194, 58, 38 | Red for buttons with white text, links and small red text on light backgrounds. |
| **Brass** | `#A88A55` | 168, 138, 85 | Accent. Keyframes, secondary markers, small decorative details. Use sparingly, and never for body text on light backgrounds. |
| **Ink** | `#141412` | 20, 20, 18 | Dark background and primary text colour. The logo frame on light backgrounds. |
| **Paper** | `#EFEBE3` | 239, 235, 227 | Main light background. The logo frame and wordmark on dark backgrounds. |
| **Bone** | `#FBF9F5` | 251, 249, 245 | Light surface: cards and panels on Paper, and app screens. |
| **Stone** | `#6E6A62` | 110, 106, 98 | Muted text, labels and captions on light backgrounds. |
| Rule | `#D3CDC0` | 211, 205, 192 | Hairlines, borders and dividers on light backgrounds. |
| Muted on dark | `#A8A399` | 168, 163, 153 | Muted text and labels on Ink. |

### Approved combinations and contrast (WCAG 2.x)

AA requires 4.5:1 for normal text and 3:1 for large text (24px+, or 18.5px+ bold) and for UI graphics.

**On light backgrounds (Paper `#EFEBE3` / Bone `#FBF9F5`)**

| Foreground | On Paper | On Bone | Use |
|---|---|---|---|
| Ink `#141412` | 15.51:1 | 17.54:1 | Headings, body text, logo frame and wordmark. |
| Stone `#6E6A62` | 4.53:1 | 5.12:1 | Muted text and labels (AA). |
| UI Red `#C23A26` | 4.497:1 | 5.09:1 | Links and small red text on Bone (AA). On Paper it falls just short of AA, so use it there only for large text (24px+). |
| Leader Red `#D8432E` | 3.71:1 | — | Graphics, the logo scan line and large display text only. Not for small text. |
| Brass `#A88A55` | 2.75:1 | — | Decorative only (keyframes, markers). Never for text. |

**On dark backgrounds (Ink `#141412`)**

| Foreground | On Ink | Use |
|---|---|---|
| Paper `#EFEBE3` | 15.51:1 | Headings, body text, logo frame and wordmark. |
| `#D6D1C6` | 12.12:1 | Secondary body text. |
| Muted `#A8A399` | 7.35:1 | Labels, timecodes and captions. |
| Red on dark `#E5553F` | 5.01:1 | Logo scan line, playheads, red accent text. |
| Brass `#A88A55` | 5.65:1 | Keyframes and accent labels. |

**Buttons and filled red areas**

| Combination | Contrast | Use |
|---|---|---|
| White on UI Red `#C23A26` | 5.35:1 | Primary red buttons (e.g. "Export CapCut template"). |
| Paper on Ink | 15.51:1 | Default primary buttons ("Follow"). |
| White on Leader Red `#D8432E` | 4.41:1 | Large text only (24px+). Use UI Red for button labels. |
| Paper on Leader Red `#D8432E` | 3.71:1 | Logo and graphics only (e.g. the red circle avatar), never text. |

---

## Typography

Both fonts are free for commercial use under the SIL Open Font License, via Google Fonts.

| Role | Font | Weights | Links |
|---|---|---|---|
| Display, headings, wordmark | **Space Grotesk** | 600 (headings, wordmark), 500 (subheads), 400 (body) | [Specimen](https://fonts.google.com/specimen/Space+Grotesk) |
| UI labels, timecodes, data | **JetBrains Mono** | 400, 500, 700 | [Specimen](https://fonts.google.com/specimen/JetBrains+Mono) |

Embed both:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
```

**When to use each**

- **Headings and display:** Space Grotesk 600, with tight tracking (letter-spacing −0.04em at large sizes, −0.02em around 24px) and line height 0.95–1.1. Sentence case. A Leader Red full stop on a hero headline ("Frame.") is an approved accent.
- **Body:** Space Grotesk 400 at 16–21px, line height 1.5. Use Ink on light and Paper / `#D6D1C6` on dark.
- **UI chrome, labels, timecodes, data, small caps-style tags:** JetBrains Mono 400/500 at 10–15px, usually in Stone (light) or `#A8A399` (dark). Timecodes always use mono, e.g. `00:06.12`.
- **Buttons:** Space Grotesk 600 at 15px.
- **Wordmark:** always use the supplied logo files. Never retype "Editrace" in a font, even Space Grotesk.

---

## Logo

### Versions and where to use them

| File | Use |
|---|---|
| `logo-horizontal-light` ([SVG](logo/svg/logo-horizontal-light.svg), [PNG](logo/png/logo-horizontal-light-2000w.png)) | Default lockup on Paper, Bone, white or other light backgrounds: website header, decks, documents. |
| `logo-horizontal-dark` ([SVG](logo/svg/logo-horizontal-dark.svg), [PNG](logo/png/logo-horizontal-dark-2000w.png)) | The lockup on Ink or other dark backgrounds: dark-mode site, video end cards, banners. |
| `wordmark-light` / `wordmark-dark` ([SVG](logo/svg/wordmark-light.svg) / [SVG](logo/svg/wordmark-dark.svg), PNGs in [logo/png](logo/png/)) | Where the icon already appears nearby, or where space is very wide and short (footers, credits). |
| `icon-light` / `icon-dark` ([SVG](logo/svg/icon-light.svg) / [SVG](logo/svg/icon-dark.svg), PNGs at 256/512/1024 in [logo/png](logo/png/)) | Standalone mark on light or dark backgrounds: app UI, watermarks, small placements. |
| `icon-mono-black` / `icon-mono-white` ([SVG](logo/svg/icon-mono-black.svg) / [SVG](logo/svg/icon-mono-white.svg), PNGs in [logo/png](logo/png/)) | Single-colour use: print, embossing, stamps, one-colour merch, overlays on photography or video. |
| [`avatar-800`](social/avatars/avatar-800.png) (ink square, centred icon) | The profile picture for every platform (TikTok, Instagram, X, LinkedIn). 800×800; each platform scales it down. Built to sit fully inside the circular crop. |
| [`favicon-32`](logo/favicon/favicon-32.png) / [`apple-touch-icon-180`](logo/favicon/apple-touch-icon-180.png) | Browser tab and iOS home screen. The 32px file is a pixel-snapped version (see below). |

Logo on Leader Red (e.g. the red circle avatar variant): use the mono version in Bone/white only.

### Clear space

- **Lockup and wordmark:** keep clear space on every side equal to **half the height of the "E"** in the wordmark (0.5 × cap height). No text, edges or other graphics may enter this zone.
- **Icon on its own:** keep clear space on every side equal to **the scan-line overhang**, which is the length the red line extends past the side of the frame (about 19% of the icon's width).

### Minimum size

| Asset | Digital | Print |
|---|---|---|
| Horizontal lockup | 120px wide | 30mm wide |
| Wordmark | 90px wide | 22mm wide |
| Icon | 24px (16px minimum using the pixel-snapped favicon artwork) | 6mm |

Below 32px, use the pixel-snapped icon (`favicon-32`). Its frame sides are whole pixels, so it stays sharp.

### Don'ts

1. **Don't stretch, squash or skew** the logo. Always scale proportionally.
2. **Don't recolour** it outside the approved versions: no new colours, gradients, or a red frame with an ink line.
3. **Don't rotate or tilt** the logo or the icon. The frame is always upright 9:16, and the scan line is always horizontal.
4. **Don't add effects:** no shadows, glows, outlines, bevels, blur or transparency.
5. **Don't place it on busy backgrounds** (photos, video frames, patterns) without a solid Ink or Paper plate behind it. Use the mono versions over imagery only when contrast is clear.
6. **Don't rebuild the wordmark** in another font or retype it. Don't change its letter-spacing or casing ("Editrace" with a capital E), and don't rearrange the icon and wordmark into a new lockup.

---

## Social assets notes

- **Avatars:** Ink background with the dark icon, sized to 63% of the canvas so it sits well inside every platform's circle crop.
- **Banners** ([X](social/banners/x-header.png), [LinkedIn](social/banners/linkedin-banner.png)): Ink background, the dark lockup, the tagline with a red full stop, and the timeline breakdown motif (clip track, Brass keyframes, red playhead with a mono timecode).
  - X keeps the bottom-left clear for the profile picture.
  - LinkedIn keeps the left side clear for the logo overlap.
