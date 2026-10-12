# sp500-forward-pe

Static site: https://allenfulmer.github.io/sp500-forward-pe/ (Valuation & Macro, Momentum, Crypto Momentum, Charts).

## Adding a chart to the Charts tab

1. Copy the image into `assets/charts/` (e.g. `assets/charts/my-chart.png`). For big files, also save a smaller display copy, e.g.
   `python3 -c "from PIL import Image; im=Image.open('assets/charts/my-chart.png'); im.thumbnail((1600,1600)); im.save('assets/charts/my-chart.webp', quality=85)"`
2. Add an entry to `data/charts.json` under `"charts"`:
   ```json
   {"file": "assets/charts/my-chart.png", "display": "assets/charts/my-chart.webp", "width": 4096, "height": 2207,
    "title": "Title", "caption": "Short neutral description.", "credit": "Author / tool, date", "source": "https://optional-link", "added": "2026-10-12"}
   ```
   `display`, `width`/`height` and `source` are optional. Cards are sorted by `added`, newest first.
3. Commit and push; GitHub Pages redeploys.
