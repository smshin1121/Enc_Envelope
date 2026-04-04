# Digital Evidence Sealing System — Design System
# Based on Stripe Design System, adapted for forensic/governmental use

## Color Palette

### Primary Brand
- Primary Purple: `#533afd` — Primary CTAs, interactive highlights
- Deep Navy: `#061b31` — Headings, authority color (warm vs pure black)
- Pure White: `#ffffff` — Backgrounds, card surfaces

### Dark & Neutral
- Brand Dark: `#1c1e54` — Dark section backgrounds
- Slate: `#64748d` — Body text, secondary content
- Dark Slate: `#273951` — Form labels, tertiary text

### Semantic
- Seal Blue: `#2c3e50` — Sealing process accent
- Unseal Navy: `#1a5276` — Unsealing process accent
- Reseal Green: `#1e6e3e` — Resealing process accent
- Danger Ruby: `#ea2261` — Alerts, errors, warnings
- Success Green: `#15be53` — Status indicators, success
- Warning Amber: `#f39c12` — Caution states
- Info Blue: `#3498db` — Informational highlights

### Interactive States
- Purple Hover: `#4434d4`
- Purple Light: `#b9b9f9` — Subdued states
- Purple Deep: `#2e2b8c` — Icon hover

### Borders & Surfaces
- Border Default: `#e5edf5` — Card/divider borders
- Border Active: `#b9b9f9` — Selected state
- Card Background: `#ffffff`
- Page Background: `#f8f9fb`

### Shadow System (Chromatic Depth)
- Shadow Primary: `rgba(50,50,93,0.25)` — Blue-tinted signature shadow
- Shadow Secondary: `rgba(0,0,0,0.08)` — Neutral depth reinforcement
- Shadow Ambient: `rgba(23,23,23,0.06)` — Subtle elevation

## Typography

### Font Family
- Primary: `맑은 고딕` (Korean), `Segoe UI` (English fallback)
- Monospace: `Consolas`, `SFMono-Regular`

### Hierarchy

| Purpose | Size | Weight | Letter Spacing |
|---------|------|--------|----------------|
| Page Title | 24px | 300 | -0.5px |
| Section Heading | 18px | 300 | -0.3px |
| Sub-heading | 14px | 500 | normal |
| Body | 12px | 400 | normal |
| Button | 12px | 500 | normal |
| Caption | 11px | 400 | normal |
| Badge | 10px | 500 | 0.3px |

### Core Principle
Light weight (300) for headings creates authority through restraint.
Weight 500 for interactive elements, weight 400 for body text.

## Component Styles

### Buttons
- Primary: bg `#533afd`, text white, radius 6px, padding 8px 20px
- Hover: bg `#4434d4`
- Ghost: bg transparent, border `1px solid #b9b9f9`, text `#533afd`
- Danger: bg `#ea2261`, text white
- Disabled: opacity 0.5, cursor not-allowed

### Cards
- Background: white
- Border: `1px solid #e5edf5`
- Radius: 8px
- Shadow: `0 2px 8px rgba(50,50,93,0.12), 0 1px 3px rgba(0,0,0,0.06)`
- Hover shadow: `0 8px 24px rgba(50,50,93,0.15), 0 3px 10px rgba(0,0,0,0.08)`
- Padding: 20px

### Stat Cards (Dashboard)
- Large number: 32px weight 300, color `#061b31`
- Label: 12px weight 500, color `#64748d`, uppercase, letter-spacing 0.5px
- Divider: 2px solid process-color (seal/unseal/reseal)

### Step Indicator
- Circle: 28px diameter, 1.5px border
- Active: filled `#533afd`, white number
- Completed: filled `#533afd`, white checkmark
- Pending: border `#dee2e6`, text `#64748d`
- Connector line: 2px, completed `#533afd`, pending `#dee2e6`
- Label: 11px weight 400

### Forms
- Input border: `1px solid #e5edf5`, radius 6px
- Focus: border `#533afd` + `0 0 0 3px rgba(83,58,253,0.12)` ring
- Label: 12px weight 500 `#273951`
- Error: border `#ea2261`, message `#ea2261` 11px
- Success indicator: `#15be53` checkmark

### Navigation Header
- Background: white with `backdrop-filter: blur(8px)`
- Border bottom: `1px solid #e5edf5`
- Height: 52px
- Title: 16px weight 300 `#061b31`

### Toast Notifications
- Radius: 8px
- Shadow: elevated (multi-layer blue-tinted)
- Success: left border 3px `#15be53`
- Error: left border 3px `#ea2261`
- Info: left border 3px `#3498db`
- Warning: left border 3px `#f39c12`

## Layout & Spacing

### Base Unit: 8px
- xs: 4px
- sm: 8px
- md: 16px
- lg: 24px
- xl: 32px
- 2xl: 48px

### Window
- Default: 1000x700
- Minimum: 800x600

### Border Radius
- Small: 4px (badges, tags)
- Standard: 6px (buttons, inputs)
- Medium: 8px (cards, panels)
- Large: 12px (modals, featured)

## Elevation

| Level | Shadow | Use |
|-------|--------|-----|
| 0 | none | Flat surfaces |
| 1 | `0 1px 3px rgba(50,50,93,0.1)` | Subtle lift |
| 2 | `0 2px 8px rgba(50,50,93,0.12), 0 1px 3px rgba(0,0,0,0.06)` | Cards |
| 3 | `0 8px 24px rgba(50,50,93,0.15), 0 3px 10px rgba(0,0,0,0.08)` | Hover cards, dropdowns |
| 4 | `0 16px 48px rgba(50,50,93,0.2), 0 6px 16px rgba(0,0,0,0.1)` | Modals |

## Design Principles

### Do
- Use weight 300 for headings (authority through restraint)
- Use blue-tinted shadows for chromatic depth
- Use deep navy `#061b31` for headings
- Keep border-radius 6-8px (conservative, institutional)
- Layer shadows: blue-tinted far + neutral close
- Use uppercase + letter-spacing for category labels
- Apply page background `#f8f9fb` for depth separation

### Don't
- Use weights 600-700 for headings
- Use pill shapes or radius > 12px
- Use neutral gray shadows (always blue-tint)
- Use pure black for headings
- Clutter with decorative elements
- Use bright colors for backgrounds
