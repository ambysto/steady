# Landing page (steady.ambysto.com)

A static, English-only page for **Ambysto Steady**. It has no build step and needs no JavaScript to be readable.

| File | Purpose |
|---|---|
| `index.html` | The page: hero, problem, features, evidence, safety, FAQ, download |
| `assets/css/steady.css` | Styles specific to this page |
| `assets/js/steady.js` | Mobile menu and the scrolled nav style only |

## Shared assets are not in this repository

`index.html` expects the host site to provide `/assets/css/style.css` (design tokens, navigation, hero, cards, FAQ, footer), `/assets/img/logo.svg` and `/assets/img/apple-touch-icon.png`. The privacy page already served at `https://steady.ambysto.com/privacy` uses the same files, so the pages look alike.

## Preview locally

Copy the shared assets from the live site next to this folder's files, then serve the result:

```powershell
$p = Join-Path $env:TEMP 'steady-preview'
New-Item -ItemType Directory -Force "$p\assets\css", "$p\assets\img", "$p\assets\js" | Out-Null
Copy-Item site\index.html $p
Copy-Item site\assets\css\steady.css "$p\assets\css"
Copy-Item site\assets\js\steady.js "$p\assets\js"
Invoke-WebRequest https://steady.ambysto.com/assets/css/style.css -OutFile "$p\assets\css\style.css"
Invoke-WebRequest https://steady.ambysto.com/assets/img/logo.svg -OutFile "$p\assets\img\logo.svg"
python -m http.server 8799 --bind 127.0.0.1 --directory $p
```

Then open `http://127.0.0.1:8799/`.

## Content rules

- State only what the released app does (check `README.md` and `CHANGELOG.md`). No claim that releases are signed while they are not, and no prices.
- The page must stay honest about limits: Ambysto Steady cannot make a provider faster.
- The numbers in the "Real data" section come from the development machine (`docs/EXPERIMENT-LOG.md`). They carry no network identifiers and are labelled as a single case.
- No personal names, SSIDs, MAC or IP addresses; use placeholders if an example needs one.
- Load nothing from third-party origins except what the host site already loads (fonts).
