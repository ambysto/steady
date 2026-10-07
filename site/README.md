# Landing page (steady.ambysto.com)

A static, English-only page for **Ambysto Steady**. It has no build step and needs no JavaScript to be readable.

| File | Purpose |
|---|---|
| `index.html` | The page: hero, connection path, problem, features, evidence, safety, FAQ, download |
| `assets/css/steady.css` | All styles, light and dark, and the motion |
| `assets/js/steady.js` | Mobile menu, scrolled nav style, scroll reveals and number count-ups |
| `assets/img/logo.svg`, `apple-touch-icon.png` | Ambysto logo and touch icon |

The page is self-contained: it needs no stylesheet from another site. The only third-party request is Google Fonts (DM Sans, JetBrains Mono); without it the system fonts are used.

## Motion

Each animation shows what the app does: the hero chart scrolls like a live graph, packets travel along the connection path and some drop at the failing hop, numbers count to the measured values. Everything is CSS except the scroll reveals and count-ups, which only start when the script runs; the final values are in the HTML. `prefers-reduced-motion: reduce` turns all motion off.

## Preview locally

```bash
python3 -m http.server 8799 --bind 127.0.0.1 --directory site
```

Then open `http://127.0.0.1:8799/`. The `/privacy` link only works on the deployed site.

## Content rules

- State only what the released app does (check `README.md` and `CHANGELOG.md`). No claim that releases are signed while they are not, and no prices.
- The page must stay honest about limits: Ambysto Steady cannot make a provider faster.
- The numbers in the "Real data" section come from the development machine (`docs/EXPERIMENT-LOG.md`). They carry no network identifiers and are labelled as a single case. Show them rounded (35%, 0.4%) so they are easy to remember; the exact values stay in the log.
- No personal names, SSIDs, MAC or IP addresses; use placeholders if an example needs one.
- Plain copy: no em dashes, round numbers where the exact value adds nothing.
- Load nothing from third-party origins except Google Fonts.
