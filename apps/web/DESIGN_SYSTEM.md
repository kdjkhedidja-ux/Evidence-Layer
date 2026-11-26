# Web design system

The web app uses the visual language of the **Verdara dashboard** template
(HorizonX, v1.0.1), adapted to Evidence Layer. Verdara's colour values,
component anatomy and interface icon paths are used under the owner's HorizonX
membership licence. Its placeholder brand (Logoipsum symbol, "Verdara"
wordmark), profile photo and sample data are not used. Fonts are system fonts
(Arial/Helvetica stack); no font files are shipped.

All styling lives in `app/globals.css`:

1. **Design tokens** (top of file): the only place colours, radii, shadows,
   control sizes and font stacks are defined.
2. **Component rules**: the existing page and feature styles, which reference
   tokens only.
3. **Verdara layer** (end of file): the app shell, sidebar, type scale, cards,
   controls, tables, status messages and page frames.

When you add UI, use the tokens. Do not introduce raw hex values outside the
token block.

## Tokens

| Token | Light | Use |
| --- | --- | --- |
| `--bg-primary` | `#f0f6f5` (mint) | Content panel background |
| `--bg-secondary` | `#fff` | Cards, inputs |
| `--text-main` / `--text-strong` | `#152b2c` (ink) | Body and headings |
| `--text-muted` | `#536b6b` | Labels, captions, eyebrows |
| `--accent-color` | `#006b61` | Links, focus ring, active text |
| `--primary-bg` / `--primary-ink` | `#173b3d` / `#fff` | Primary actions |
| `--alert-color` | `#b4434b` | Errors and destructive states |
| `--success-color` | `#256b35` | Positive deltas |
| `--border-color` | `#dfe9e7` | Dividers |
| `--border-control-color` | `#6d8672` | Input borders |
| `--bg-hover` / `--bg-selected` | `#eaf2eb` / `#d2e7e1` (sage) | Hover and selected rows |
| `--shell-*` | white (light) / forest gradient `#15383a → #102629` (dark) | Sidebar and top bar |
| `--btn-*` | white surface, `#c9d8d3` border | Secondary buttons |
| `--icon-btn-ink` | `#006b61` / `#7fd4c1` | Icon-only buttons (theme, menu) |
| `--radius-card` / `--radius-control` | `18px` / `10px` | Corners |
| `--button-height-primary` | `44px` | Minimum hit target |

**No mixing:** the light theme is light on every surface (sidebar, top bar,
cards, buttons). The dark theme is dark on every surface. Do not hard-code a
dark surface in light mode or the reverse; use a token. The only exception is
the MFA QR code, which stays on white so it scans reliably.

`:root[data-theme="dark"]` redefines only the semantic tokens. The dark
palette is derived from Verdara's forest surfaces, because the template itself
ships light-only. The theme toggle and its `evidence-layer-theme` storage key
are unchanged.

## Layout

- **Desktop (above 1050px):** a fixed sidebar (`--sidebar-width`, 256 to
  288px), white in light mode and forest in dark mode. The page root (`.workspace-page`, `.auth-page`, `.public-site`,
  `.live-shell`) becomes the mint panel with a 23px rounded leading edge.
- **Tablet and phone (1050px and below):** the sidebar collapses into a top bar
  with the theme icon and a **Menu** icon button
  (lines when closed, circled X when open). The mint panel starts below it with rounded top
  corners. Navigation uses two columns, then one column at 480px and below.
- Page frames are capped at 1180px wide, with 18px gaps between cards.
- **Scrolling:** page content scrolls between the top (the mobile bar, or the
  top of the panel on desktop) and a fixed footer.
  - The desktop sidebar is fixed and scrolls on its own if it is taller than
    the window.
  - The mobile bar is sticky.
- **Mobile menu:** an overlay below the bar that never pushes content. It
  scrolls on its own, and while it is open it locks only the root scroller.
  Locking `body` as well would make body a scroll container and unstick the
  bar. It closes on a link tap, the close icon, or Escape.
- **Footer:** the legal links are fixed at the bottom centre of the content
  area, with safe-area padding. Page roots reserve `--footer-height` at the
  bottom so the last content is never hidden.
- Hover backgrounds on icon buttons only apply to devices with a mouse
  (`@media (hover: hover)`), so a tap doesn't leave a stuck highlight.

## Components

- **Sidebar** (`components/site-header.tsx`): brand mark and theme icon,
  grouped navigation (Navigation, Resources, User account) with 32px icon
  tiles, and the session (avatar initial, email, sign out). Only the most
  specific matching destination is marked current. The nav labels and order are a
  tested contract.
- **Icons** (`components/nav-icon.tsx`): 24px grid with a 1.6 stroke. The
  Verdara icon paths are reused; chart, link, plug, gauge and coins are drawn
  to match.
- **Cards:** flat white, 18px radius, no shadow.
- **Metric cards** (`.telemetry-card`): a muted label over a large tabular
  value. The first card is highlighted with `--bg-feature` (sage in
  light mode, deep green in dark mode).
- **Buttons** (single definition at the end of `globals.css`):
  - Primary: `--primary-bg`, which is dark teal in light mode and pale green in
    dark mode. Every form `type="submit"` button is primary.
  - Secondary: `--btn-*` (surface, quiet border, sage hover).
  - Icon buttons (theme, menu): a bare SVG in `--icon-btn-ink`, with a 44px
    hit area and an accessible name from `aria-label`.
  - All buttons use a 10px radius.
- **Inputs:** 9px radius, `--border-control-color` border, and a 3px focus
  outline.
- **Eyebrows:** 0.75rem uppercase muted labels above section titles.

## Accessibility

- Keep visible `:focus-visible` outlines. The sidebar ring is `--shell-focus`
  (teal in light mode, light green in dark mode).
- Keep 44px minimum targets for primary controls.
- `prefers-reduced-motion` disables all transitions and animations.
