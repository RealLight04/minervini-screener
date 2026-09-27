---
name: Minervini Screener
description: 운항 관제실. 건메탈 스트립 랙 위에 인쇄 비행 스트립을 꽂아 오늘의 매수 판정을 읽는 다크 전용 스크리너
colors:
  rack-bg: "#13171b"
  rack-surface: "#1b2025"
  rack-inset: "#242a30"
  rack-border: "#343c43"
  rack-line: "#283038"
  rack-rail: "#465059"
  header-black: "#0b0c0d"
  text: "#e3e6e8"
  heading: "#f6f7f7"
  muted: "#a2aab1"
  faint: "#8a939a"
  strip-paper: "#e3e6e5"
  strip-paper-2: "#d5d9d9"
  strip-ink: "#121518"
  strip-ink-2: "#464d54"
  strip-rule: "#b9bfc2"
  sign-yellow: "#f2c200"
  sign-yellow-hover: "#ffd53d"
  sign-ink: "#0c0d0e"
  holder-go: "#f2c200"
  holder-wait: "#b6bec4"
  holder-warn: "#e5483d"
  holder-hold: "#8c959c"
  regime-vfr: "#3fb46a"
  regime-mvfr: "#4f95e6"
  regime-ifr: "#e5483d"
  paper-neg: "#b3261e"
  paper-pos: "#146c37"
  chart-up: "#52c47a"
  chart-down: "#ef5a4f"
  chart-ma200: "#b98ad0"
typography:
  verdict:
    fontFamily: "'Black Han Sans', 'Pretendard', sans-serif"
    fontSize: "clamp(3rem, 8.4vw, 5.6rem)"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "'Pretendard', -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "1.55rem"
    fontWeight: 800
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  title:
    fontFamily: "'Pretendard', -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "1.12rem"
    fontWeight: 800
    lineHeight: 1.4
    letterSpacing: "-0.01em"
  body:
    fontFamily: "'Pretendard', -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.55
  body-sm:
    fontFamily: "'Pretendard', -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "0.86rem"
    fontWeight: 500
    lineHeight: 1.7
  sign:
    fontFamily: "'Overpass', 'Pretendard', 'Segoe UI', sans-serif"
    fontSize: "0.86rem"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "0.02em"
  sign-code:
    fontFamily: "'Overpass', 'Pretendard', 'Segoe UI', sans-serif"
    fontSize: "0.76rem"
    fontWeight: 800
    lineHeight: 1.25
    letterSpacing: "0.1em"
  measure:
    fontFamily: "'Overpass Mono', 'D2Coding', 'Cascadia Code', Consolas, monospace"
    fontSize: "0.98rem"
    fontWeight: 600
    lineHeight: 1.35
    fontFeature: "tnum"
  callsign:
    fontFamily: "'Overpass Mono', 'D2Coding', 'Cascadia Code', Consolas, monospace"
    fontSize: "1.16rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "0.01em"
  label:
    fontFamily: "'Pretendard', -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"
    fontSize: "0.76rem"
    fontWeight: 700
    lineHeight: 1.3
rounded:
  square: "0px"
  sm: "2px"
  md: "3px"
spacing:
  strip-gap: "7px"
  gap: "14px"
  gutter: "18px"
  gutter-mobile: "14px"
  bay: "38px"
  section: "44px"
  container: "1180px"
components:
  button-primary:
    backgroundColor: "{colors.sign-yellow}"
    textColor: "{colors.sign-ink}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.sign-yellow-hover}"
    textColor: "{colors.sign-ink}"
  button-secondary:
    backgroundColor: "{colors.rack-inset}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "44px"
  sign-direction:
    backgroundColor: "{colors.sign-yellow}"
    textColor: "{colors.sign-ink}"
    typography: "{typography.sign}"
    rounded: "{rounded.square}"
    padding: "0 11px"
    height: "36px"
  sign-location:
    backgroundColor: "{colors.sign-ink}"
    textColor: "{colors.sign-yellow}"
    typography: "{typography.sign}"
    rounded: "{rounded.square}"
    padding: "6px 11px 5px"
  input-search:
    backgroundColor: "{colors.rack-inset}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0 40px 0 12px"
    height: "36px"
  flight-strip:
    backgroundColor: "{colors.strip-paper}"
    textColor: "{colors.strip-ink}"
    rounded: "{rounded.square}"
  strip-holder-go:
    backgroundColor: "{colors.holder-go}"
    textColor: "{colors.sign-ink}"
    width: "7.2rem"
  strip-holder-wait:
    backgroundColor: "{colors.holder-wait}"
    textColor: "{colors.sign-ink}"
    width: "7.2rem"
  strip-holder-warn:
    backgroundColor: "{colors.holder-warn}"
    textColor: "{colors.sign-ink}"
    width: "7.2rem"
  strip-holder-hold:
    backgroundColor: "{colors.holder-hold}"
    textColor: "{colors.sign-ink}"
    width: "7.2rem"
  strip-pull:
    backgroundColor: "{colors.strip-paper-2}"
    textColor: "{colors.strip-ink}"
    width: "6.8rem"
    height: "44px"
  flight-plan-sheet:
    backgroundColor: "{colors.strip-paper-2}"
    textColor: "{colors.strip-ink}"
    padding: "14px 16px 16px"
  regime-board:
    backgroundColor: "{colors.rack-surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.square}"
    padding: "22px 24px 20px"
  badge:
    rounded: "{rounded.sm}"
    padding: "2px 8px"
---

# Design System: Minervini Screener

## Overview

**Creative North Star: "운항 관제실 (The Strip Rack)"**

The site is an air traffic control room at night. The page itself is a gunmetal strip rack; every tradeable stock is a printed flight strip on cool off-white paper slotted into a bay; navigation and market tabs are airfield taxiway signs. The visitor first reads the weather (the market regime, stated as an aviation flight category), then reads only the strips that were cleared, then pulls a strip to unfold its flight plan (entry, stop, target, size), then goes down to the full preflight checklist on the detail page. It is dark only: there is no light theme, and `color-scheme: dark` makes native controls follow.

Density is operational, not decorative. Measurements sit in labelled cells separated by printed rules, the way a real strip carries callsign, altitude and route. Colour is almost absent until it means something: the rack is neutral gunmetal, paper is neutral, and colour appears only in holders (the state of a strip), regime chips and gauge fills (the state of the market), and the yellow of signs and focus (where you can go). The system explicitly rejects the card-grid AI dashboard and the HTS wall of tables.

The visual system is mirrored in Figma (file `t8mXuZka6OS3qbZv4MipwW`: 26 colour variables in collection "Tokens", mode "Dark"; 8 text styles; `Strip` component set Type=Go/Wait/Warn with 16 text properties; `Sign` component set Type=Location/Direction; a Home desktop screen). Figma uses **Noto Sans KR** in place of Pretendard because Pretendard is not available there; the site is the source of truth for type.

**Key Characteristics:**
- Dark only: gunmetal rack, cool paper strips, black-and-yellow airfield signs.
- Colour is state, never decoration.
- Measurements always in Overpass Mono; Korean prose in Pretendard; sign text in Overpass; one verdict word in Black Han Sans.
- Sharp geometry: square strips and signs, 2 to 3px only on controls and badges.
- Sections separated by rack rails, not boxed cards.
- Motion is mechanical and one-shot: strips slot in, gauges fill, a strip pulls out.
- Copy is terse Korean with noun endings.

## Colors

A neutral gunmetal-and-paper world where hue is reserved for state and for the one interaction colour.

### Primary
- **Taxiway Sign Yellow** (sign-yellow): the airfield sign colour. Direction signs (nav links, market tabs), the primary button, focus rings, selection, active sort arrows, link-hover underlines, and the command palette's active edge. Hover lifts to Sign Yellow Hover. On the rack its partner is Sign Ink.
- **Sign Ink** (sign-ink): the near-black of the sign face. Text on every yellow or coloured holder, and the ground of location signs (brand, active tab, current nav item) where the yellow becomes text plus a 2px inset yellow frame.

### Secondary: strip holder states
The coloured block at the head of each strip. One state, one colour, everywhere (home strips, detail strip, VCP log holder cells).
- **Cleared Yellow** (holder-go): 적극 매수 (`CLEARED`). Same pigment as the sign: on a strip rack yellow means "go".
- **Standby Gray** (holder-wait): 대기 states, 돌파 대기 (`HOLD SHORT`) and 눌림목 대기 (`EXTENDED`). Waiting is gray by contract.
- **Divert Red** (holder-warn): 매도 경고 (`DIVERT`). Carries Sign Ink, not white (4.9:1).
- **Hold Gray** (holder-hold): 관심 (`STANDBY`), 회피 (`NO GO`), 데이터 점검 (`CHECK`), and finished log rows.

### Tertiary: flight categories (market regime only)
- **VFR Green** (regime-vfr): 시계 양호, 매수 가능 (server regime BULL).
- **MVFR Blue** (regime-mvfr): 제한 시계, 선별 매수 (NEUTRAL). Also the 150-day moving-average line on the chart.
- **IFR Red** (regime-ifr): 시계 불량, 매수 중지 (BEAR). Also tints the buy-suspension gate note.
These colour the regime chip and the breadth gauge fills (200-day ≥60 VFR, <40 IFR, else MVFR; 50-day ≥50 VFR else MVFR; Stage 2 gauge always Hold Gray). Server-side, `compute_market_breadth` emits the same dark values (#3fb46a / #4f95e6 / #e5483d).

### Neutral
- **Rack Gunmetal** (rack-bg): page ground and scrollbar track.
- **Rack Panel** (rack-surface): the regime board, checklist, tables and chart cards.
- **Rack Inset** (rack-inset): inputs, secondary buttons, table header row, gauge track.
- **Rack Border** (rack-border) and **Rack Line** (rack-line): panel outlines and row dividers.
- **Rack Rail** (rack-rail): the structural rail. Section and bay tops, header bottom, dashed empty-slot outline, scrollbar thumb.
- **Header Black** (header-black): the sticky sign bar behind the header.
- **Text / Heading / Muted / Faint**: text on the rack, in descending emphasis. Faint is the lowest tier allowed and still passes AA (5.3:1 on panel).
- **Strip Paper / Strip Paper 2** (strip-paper, strip-paper-2): the printed strip and its fold-out flight plan / pull tab.
- **Strip Ink / Strip Ink 2 / Strip Rule** (strip-ink, strip-ink-2, strip-rule): print ink, secondary print, and printed cell rules.
- **Paper Negative / Paper Positive** (paper-neg, paper-pos): stop-loss and gain figures printed on paper (5.2:1 each).

### Chart
- **Chart Up / Chart Down / MA200 Violet** (chart-up, chart-down, chart-ma200): candle bodies and the 200-day line. The 50-day line uses Sign Yellow and the 150-day uses MVFR Blue. These are the only greens, reds and violet that should appear on the rack outside the states above.

### Named Rules
**The Color Is State Rule.** Hue appears only to encode a state (holder, regime, gain/loss, pass/fail) or the single interaction colour. Normal and neutral are gray. Never add colour for emphasis or decoration.

**The One Meaning Per Colour Rule.** Yellow means go / you can act here. Red means warn / stop. Blue appears only as MVFR (and its chart line). Green appears only as VFR, gains and passed checks. Two opposite meanings never share a hue.

**The Paper Remap Rule.** A rack component placed on paper does not get new colours; the `.paper` container redefines the rack tokens (surface, text, muted, border, green, red, badge backgrounds) to their print equivalents, so inline styles and heat chips follow automatically.

## Typography

**Body Font:** Pretendard (with Apple SD Gothic Neo, Malgun Gothic, system sans)
**Sign/Label Font:** Overpass (with Pretendard)
**Measurement Font:** Overpass Mono (with D2Coding, Cascadia Code, Consolas)
**Verdict Font:** Black Han Sans (with Pretendard)

**Character:** Pretendard carries Korean prose with `word-break: keep-all`; Overpass, the typeface drawn from highway signage, gives signs and holder codes their stencilled airfield voice; Overpass Mono prints every number in tabular figures like a strip printer; Black Han Sans is the one loud word on the board.

### Hierarchy
- **Verdict** (400, clamp(3rem, 8.4vw, 5.6rem), 1; 3.2rem under 480px): the single regime word on the board (매수 가능 / 선별 매수 / 매수 중지). Nothing else.
- **Headline** (800, 1.55rem, 1.3; 1.3rem mobile): page titles (VCP 일지, 이메일 알림 구독).
- **Title** (800, 1.12 to 1.14rem): bay heads and section titles, sitting on a rail.
- **Callsign** (Overpass Mono 700, 1.16rem on strips, 1.9rem on the detail strip): the ticker.
- **Measure** (Overpass Mono 600, 0.98rem in strip cells, up to 1.5rem for gauge values): every price, percent, count.
- **Body** (400, 1rem, 1.55; notes 0.86rem at 1.7, advice capped at 34ch, disclaimers 80ch).
- **Sign** (Overpass 800, 0.86 to 0.95rem, 0.02 to 0.06em tracking): nav, tabs, brand (uppercase).
- **Sign Code** (Overpass 800, 0.76rem, 0.1em, uppercase English): holder codes CLEARED / HOLD SHORT / EXTENDED / DIVERT / STANDBY / NO GO / CHECK, and the regime code VFR / MVFR / IFR.
- **Label** (Pretendard 700, 0.76rem): cell `dt` labels and table headers. 0.76rem is the floor; no text below 12px.

### Named Rules
**The One Loud Word Rule.** Black Han Sans is used for the verdict word only. Never for headings, numbers, buttons or badges.

**The Printer Rule.** Every numeric measurement is Overpass Mono with tabular figures. Korean labels around it stay in Pretendard.

## Layout

A single centred column (max 1180px, 18px gutters, 14px under 720px, 26px top and 72px bottom padding). The home page is a vertical stack: sign bar, market tab signs, the full-width regime board, then bays (이륙 허가 · 적극 매수, 대기 · 돌파 대기, 눌림목 대기 · 연장, 매도 경고, 주도 섹터, 데이터 점검) 38px apart (22px on small phones). Each bay opens with a 2px rail, its title, a mono count, and a one-line rule; strips stack beneath with 7px gaps.

The regime board is a two-column grid (verdict left, breadth gauges right, 44px column gap) that becomes one column under 900px. A strip is an 8-column grid on desktop (7.2rem holder, id, five measurement cells, 6.8rem pull tab, remark row beneath); under 760px it reflows to three columns with the holder as a full-width header row and the pull tab as a full-width footer. Tables become stacked label-value cards under 720px (VCP log cards become paper strips). The sticky header wraps nav signs onto their own full-width row on mobile.

### Named Rules
**The Rail Not Box Rule.** Sections are separated by a rack rail (1px on section titles, 2px on bays) and whitespace, not by wrapping each section in a bordered card.

## Elevation & Depth

Flat rack, lifted paper. The rack surfaces are flat and tonally layered (bg, surface, inset). Only paper casts a shadow: strips, the flight plan, the detail strip, paper tables and the hover tooltip use one shadow, a hard 1px contact line plus a short soft drop, so paper reads as physically slotted into the rack. The command palette overlay uses a larger ambient shadow over a 60% black scrim.

### Shadow Vocabulary
- **Paper in rack** (`box-shadow: 0 1px 0 rgba(0,0,0,.55), 0 3px 10px rgba(0,0,0,.32)`): every paper strip and sheet.
- **Palette lift** (`box-shadow: 0 12px 40px rgba(0,0,0,.5)`): the ⌘K modal only.
- **Sign frame** (`box-shadow: inset 0 0 0 2px var(--sign)`): location-sign state for the active tab or nav item (a border, not depth).

### Named Rules
**The Only Paper Lifts Rule.** Rack panels never get shadows. If it casts a shadow, it is paper.

## Shapes

Sharp and physical. Strips, signs, holders, the regime board, gauges and the flight plan are square (0). Controls you type into or press (buttons, inputs) take 3px; badges and heat chips take 2px. The only round shapes are the 7px VCP contraction dot, 22px ticker avatars, and the scrollbar thumb. Borders are 1px hairlines; the rack rail is the one 2px structural line. Empty bays are shown as a dashed-rail slot (빈 슬롯), never as a boxed empty card with an illustration.

## Components

### Buttons
Taxiway-sign plain: a flat block, no gradient, no shadow.
- **Shape:** 3px corners, 44px minimum height, 0 16px padding, Pretendard 700 0.9rem.
- **Primary (fill):** Sign Yellow ground, Sign Ink text; hover to Sign Yellow Hover.
- **Secondary:** Rack Inset ground, Rack Border outline, Text; hover turns the outline Sign Yellow.
- **Focus:** global 2px Sign Yellow outline at 2px offset.

### Signs (navigation and market tabs)
- **Direction sign:** yellow ground, black Overpass 800 text, square, 36px (nav) or 40px (tabs) tall.
- **Location sign:** black ground, yellow text, 2px yellow frame. Used for the brand mark and for the current page / active market (`aria-current`).
- Mobile: nav signs split the full width equally at 40px.

### Inputs / Fields
- **Style:** Rack Inset ground, 1px Rack Border, 3px (search) or 2px (alert form) radius, Faint placeholder.
- **Focus:** border turns Sign Yellow. The header search opens the ⌘K palette instead of taking input.

### Regime Board (signature)
One panel carrying a METAR-style mono observation line (market, close date, screen date, universe; a stale-price notice in Sign Yellow), the flight-category chip (code in Overpass plus Korean sky condition, on the regime colour with Sign Ink text), the verdict word, one line of advice, and three breadth gauges. Gauges are 12px inset tracks with a regime-coloured fill and white threshold ticks labelled in mono (40 약세, 60 강세, 50 강세). A buy-suspension gate note appears under IFR, tinted with IFR Red.

### Flight Strip (signature)
Paper strip: holder block (Korean state word in Pretendard 800 plus English sign code), callsign and company name, labelled mono measurement cells split by printed rules (피벗, 현재가, 손절 in Paper Negative, RS, 형태), a remark row with the Korean signal reason, and a pull tab. The warn strip is a compact single-row variant whose tab links straight to the checklist. The detail page opens with a larger version of the same strip.

### Strip Pull and Flight Plan (signature interaction)
The pull tab is a real `<button>` with `aria-expanded` / `aria-controls`. Pulling slides the strip 10px to the right (desktop only), rotates the chevron 180°, and unfolds the flight-plan sheet beneath it by animating `grid-template-rows` 0fr to 1fr. The sheet is Strip Paper 2 behind a dashed printed tear line, indented to align with the strip body, and holds four mono figures (진입가, 손절가, 1차 익절, 손익비), position sizing, numbered steps, and a location-sign style link to the full checklist.

### Preflight Checklist
A rack panel with a mono pass count, then one row per trend-template condition: item, dotted leader, and an Overpass mark (통과 green, 미통과 red with the item in bold heading colour, faint for not applicable).

### VCP Log Holder Cells
Tables on the rack whose first column is a 44px holder chip in the strip-holder colours (go for 돌파 확인 / 돌파권 / 임박, wait gray for 형성, hold gray for finished 돌파). On mobile each row becomes a paper strip with the holder as its header band.

### Badges and Heat Chips
2px radius, 0.78rem 700. Green, red and gray variants built from 14 to 15% tints with a 40% outline; heat chips carry server-computed backgrounds for RS and returns.

### Motion
- **Slot in:** each strip enters once from translateX(-12px) over .5s, staggered 45ms per strip, capped at 10.
- **Gauge fill:** fills scale from 0 once over .9s.
- **Pull:** transform .28s; plan unfold .32s.
- All use `cubic-bezier(.16, 1, .3, 1)`. Hover transitions are .15s colour changes. Under `prefers-reduced-motion: reduce` every animation and transition collapses to .01ms.

## Do's and Don'ts

### Do:
- **Do** use the `:root` tokens for every colour; charts read them the same way the UI does.
- **Do** give every new strip-like state one of the four holder colours (go, wait, warn, hold) and put Sign Ink text on it.
- **Do** express market regime only with VFR Green / MVFR Blue / IFR Red.
- **Do** print every number in Overpass Mono with tabular figures.
- **Do** separate sections with a rack rail (1px section title, 2px bay head) and whitespace.
- **Do** wrap paper-coloured sub-areas in the `.paper` token remap instead of hard-coding print colours.
- **Do** keep text on rack at or above Faint (5.3:1 on panel), strip labels at Strip Ink 2 (6.0:1 or better), and interactive targets at 44px.
- **Do** write Korean copy as short noun-ending phrases (e.g. "피벗 위 5%까지가 매수 구간, 손절은 진입가 −8%.").
- **Do** show an empty bay as a dashed-rail 빈 슬롯 with one line saying what to look at instead.

### Don't:
- **Don't** add a light theme or light-mode token overrides; this world is dark only.
- **Don't** use colour for emphasis or decoration, and don't give opposite meanings (opportunity and risk) the same hue.
- **Don't** use yellow for anything that is not interaction, focus, or 적극 매수 / go.
- **Don't** use Black Han Sans for anything but the verdict word.
- **Don't** add coloured `border-left` accent stripes to cards, rows or callouts.
- **Don't** add eyebrows or kickers above headings.
- **Don't** use emoji or text glyphs as icons; use currentColor stroke SVGs (square caps, 2.2 to 2.6 stroke).
- **Don't** box each section in a card, and don't give rack panels shadows.
- **Don't** round strips or signs, and don't go above 3px radius anywhere.
- **Don't** put white text on the warn holder; it uses Sign Ink (4.9:1).
- **Don't** add looping or scroll-triggered motion; strips slot in once and gauges fill once.
- **Don't** end copy in ~습니다 / ~하세요, and don't chain tags with dashes or equals signs.
