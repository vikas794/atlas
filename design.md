# Atlas – Frontend Design Guide

Reference for any agent or developer building the Atlas UI. Follow this file instead of making visual choices ad hoc. Pairs with `Atlas_PRD.md` (Sections 6, 7, 8, 15, 16).

## 1. Design Principles

1. **Clean and functional first.** A work tool, not a marketing site. No decoration that does not carry information.
2. **Information-dense but calm.** Show a lot (stage, counts, failure reasons) using tight spacing, small type and muted neutrals, with color reserved for status and the main action.
3. **Status is always visible.** The user should never have to ask "what is happening, what failed, why". Every video row shows its stage and, if failed, the reason.
4. **One accent color.** Everything else is neutral gray. Color means something.
5. **Never rely on color alone.** Every status has an icon and a text label in addition to color (accessibility and clarity).
6. **Predictable layout.** Same structure on every screen: top bar, optional left context, main content. Primary action is always top-right of its section.
7. **Safe by default.** Destructive actions (delete playlist, reprocess, regenerate, bulk approve) always ask for confirmation and say what will happen.

## 2. Visual Style

**Design language: flat design** (functional minimalism) — solid colors, thin 1px borders instead of shadows, no fake depth or translucency. This is the same family of style used by dashboard tools like GitHub, Linear and Stripe's dashboard, chosen over neumorphism (low-contrast, embossed look — hurts fast scanning) and glassmorphism/"liquid glass" (translucent blur — adds visual noise behind content) because Atlas is read for hours at a time and readability beats visual flair.

- Light theme by default, dark theme supported (follows system setting, with a manual toggle). Dark mode is tuned, not inverted: a dark navy background (not black) with soft off-white text (not pure white) avoids the halation/vibration effect pure black-and-white causes at small sizes; status colors are brightened slightly in dark mode since they otherwise look washed out on a dark surface.
- Flat surfaces, 1px borders, almost no shadows (only on popovers and modals).
- Rounded corners, small: 6px for controls and cards, 10px for modals.
- Generous alignment, tight spacing: aligned columns and consistent row heights matter more than ornament.

## 3. Color System

Define as CSS variables on `:root` and override under `[data-theme="dark"]` (or `prefers-color-scheme: dark`). Use only these tokens in components; never hardcode hex values in components.

### 3.1 Neutrals and accent

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F8FAFC` | `#0F172A` | App background |
| `--surface` | `#FFFFFF` | `#1E293B` | Cards, tables, panels |
| `--surface-muted` | `#F1F5F9` | `#273449` | Table header, code block background, hover rows |
| `--border` | `#E2E8F0` | `#334155` | Dividers, outlines |
| `--text` | `#0F172A` | `#E2E8F0` | Primary text |
| `--text-muted` | `#64748B` | `#94A3B8` | Secondary text, labels, timestamps |
| `--accent` | `#4F46E5` | `#818CF8` | Primary buttons, links, focus ring, selected state |
| `--accent-soft` | `#EEF2FF` | `#2A2F5C` | Selected row, subtle highlights |
| `--on-accent` | `#FFFFFF` | `#0F172A` | Text on accent buttons |

### 3.2 Status colors (semantic)

Each status has a text/icon color and a soft background for badges.

| Meaning | Token | Light fg / bg | Dark fg / bg |
|---|---|---|---|
| Neutral / waiting | `--st-neutral` | `#475569` / `#F1F5F9` | `#CBD5E1` / `#334155` |
| In progress | `--st-progress` | `#2563EB` / `#EFF6FF` | `#60A5FA` / `#1E3A5F` |
| Needs your action | `--st-review` | `#B45309` / `#FFFBEB` | `#FBBF24` / `#4A3A12` |
| Done / approved | `--st-success` | `#15803D` / `#F0FDF4` | `#4ADE80` / `#14392A` |
| Failed (permanent) | `--st-error` | `#B91C1C` / `#FEF2F2` | `#F87171` / `#4A1D1D` |
| Failed (transient, retrying) | `--st-warn` | `#C2410C` / `#FFF7ED` | `#FB923C` / `#4A2A12` |

### 3.3 Pipeline stage to badge mapping

| Stage / state | Badge label | Color token | Icon |
|---|---|---|---|
| `queued` | Queued | neutral | clock |
| `fetching_transcript` | Fetching transcript | progress | spinner |
| `transcript_ready` | Transcript ready | neutral | file-text |
| `generating_notes` | Generating notes | progress | spinner |
| `pending_review` | Needs review | review | eye |
| `approved` | Approved | success | check |
| `published` | Published | success | cloud-check (Drive) |
| `failed` + `transient` | Retrying / Failed – temporary | warn | refresh |
| `failed` + `permanent` | Failed | error | x-circle |
| Removed upstream flag | No longer in playlist | neutral | archive |

Rule: **amber = "this needs you"**. Pending review is the one stage that asks the user to act, so it gets the attention color.

## 4. Typography

Three fonts, each with one job — never mix them for the same purpose.

- **UI font (chrome, tables, buttons, labels):** `Inter`, fallback `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`. Designed for screens and small sizes, which suits a dense video table. (System fonts alone are acceptable if avoiding a web font.)
- **Notes content font (Review screen reading pane only):** `Lexend`, fallback `Inter, system-ui, sans-serif`. Lexend's spacing is built from reading-speed research (reduces visual crowding), which fits the paragraphs of generated notes you'll be skimming. Kept as a sans-serif deliberately, so it stays visually consistent with the Inter UI around it rather than switching families mid-screen.
- **Monospace (code blocks, IDs):** `"JetBrains Mono"`, fallback `ui-monospace, "SF Mono", Menlo, Consolas, monospace`.

| Role | Font | Size / line height | Weight |
|---|---|---|---|
| Page title | Inter | 22px / 28px | 600 |
| Section title | Inter | 16px / 24px | 600 |
| Body, table cells | Inter | 14px / 20px | 400 |
| Labels, badges, captions | Inter | 12px / 16px | 500 |
| Notes content (Review screen reader/editor) | Lexend | 15px / 26px | 400 |

Use sentence case everywhere ("Approve all", not "APPROVE ALL"). Numbers in tables use tabular figures (`font-variant-numeric: tabular-nums`).

## 5. Spacing, Layout, Sizing

- Spacing scale (4px base): `4, 8, 12, 16, 24, 32`. Use only these.
- Page max width: 1280px, centered; side padding 24px.
- Table row height: 40px (compact). Control height: 32px (buttons, inputs). Modal width: 480px.
- Top bar: 56px, with app name "Atlas" on the left, theme toggle on the right.

## 6. Screens

### 6.1 Playlists (home)

- Top: single input "Paste a YouTube playlist link" with a primary **Add playlist** button (FR1). Inline validation message for bad URLs.
- Below: table of playlists. Columns: Title, Videos (count), Progress (segmented bar), Needs review (count, amber), Failed (count, red), Last synced, row actions menu (Open, Re-sync new videos, Reprocess, Delete).
- Empty state: one line of explanation and the input focused.

### 6.2 Playlist detail (main working screen)

Layout, top to bottom:

1. **Header:** playlist title, source link, primary actions on the right: **Process** (start/resume), **Approve all** (shows count, e.g. "Approve all (42)"), **Publish approved to Drive**, and an overflow menu (Re-sync, Reprocess, Clean transcripts, Delete).
2. **Summary strip:** one card per stage with a count, clickable as a filter (Queued, In progress, Needs review, Approved, Published, Failed). Beside it, a **segmented progress bar** of the whole playlist using the status colors. Show "N of M published".
3. **Videos table** (dense, sortable by order, filterable by stage via the strip and a search box on title):
   - Columns: `#` (zero-padded order), Title, Stage badge, Detail (failure reason, retry count, or "edited" tag), Updated (relative time), Actions.
   - Failed rows show the **reason in plain text** in the Detail column (for example "Transcripts disabled by uploader"), and a **Retry** button. Transient failures show "Retrying in 2m (attempt 3/5)".
   - Row click opens the Review screen. Rows needing review have a subtle amber left border.
   - Virtualize or paginate (50 per page) so 250 rows stay fast.
4. Polls the status endpoint every 3 seconds while anything is in progress, otherwise every 15 seconds (no websockets in v1).

### 6.3 Review screen (view, edit, approve)

- Two-pane layout on wide screens: **left** narrow list of videos (number, title, stage dot) for fast navigation, **right** the notes.
- Right pane header: `#012 Video title`, stage badge, buttons: **Approve** (primary), **Regenerate** (secondary, confirm), **Next needing review** (secondary).
- Body toggles between **Preview** (rendered notes) and **Edit** (Markdown editor), with an "Unsaved changes" indicator and **Save**. Keyboard: `Ctrl/Cmd+S` save, `Ctrl/Cmd+Enter` approve and go to next.
- Show small metadata under the title: model used, prompt version, generated time. No video link or duration is shown in the published Doc, but it is fine to show the YouTube link here as a small "Open video" text link.
- Published videos that were edited show a banner: "Edited since publishing" with a **Re-publish** button.

### 6.4 Dialogs and feedback

- **Confirm dialogs** state the consequence and the count: "Delete playlist and all 187 stored videos, transcripts and notes? This does not delete Google Docs already in Drive." Destructive button uses the error color and is the right-hand button.
- **Toasts** (bottom-right, auto-dismiss 4s) for success; errors stay until dismissed.
- **Empty and loading states:** skeleton rows for tables, a plain sentence for empty states, never a blank area.

## 7. Components

- **Buttons:** primary (accent fill), secondary (surface with border), ghost (text only), danger (error fill). One primary button per section. Disabled state at 50% opacity with a tooltip explaining why.
- **Badges:** pill, 12px text, soft background plus colored text and icon (see 3.3).
- **Progress bar:** 8px tall, rounded, segmented by status color, with a text label of counts beside it.
- **Tables:** sticky header on `--surface-muted`, 1px row dividers, hover row on `--surface-muted`, selected row on `--accent-soft`.
- **Code blocks in notes:** `--surface-muted` background, 1px border, 6px radius, monospace 13px, horizontal scroll, language label in the top-right corner. This matches the "distinct code block" requirement in the published Google Docs (shaded, monospace).
- **Inputs:** 32px high, 1px border, accent focus ring (2px, offset 1px).
- **Icons:** one icon set only (Lucide recommended), 16px in tables, 20px elsewhere.

## 8. Interaction and Accessibility

- Visible focus ring on every interactive element; full keyboard navigation of tables and dialogs.
- Minimum contrast 4.5:1 for text (the palette above is chosen to meet this).
- Status uses icon plus label plus color, never color alone.
- Respect `prefers-reduced-motion` (spinners stay, other animation off). Keep transitions to 120ms.
- Relative times ("3 min ago") with the exact time in a tooltip.

## 9. Implementation Notes

- Keep whatever frontend framework the existing repo uses (Atlas PRD Section 13). Put the color, spacing and typography tokens above in a single global stylesheet (`tokens.css`) so every component reads from it.
- Map backend stage and failure values to badges in **one** helper (`stageToBadge(stage, failureType)`) so the table, summary strip and review screen can never disagree.
- Build order follows the PRD milestones: Playlists screen with the detail table in M1 to M2, status strip and review screen in M3, publish button and Drive states in M4, re-sync and management actions in M5.

## 10. Out of Scope for v1 UI

Multi-user or login screens, cross-playlist search, scheduling controls, mobile-optimized layout (desktop browser on the local machine is the target; it should still not break at 1024px width).
