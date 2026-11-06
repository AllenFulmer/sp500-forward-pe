# sp500-forward-pe

Static site: https://allenfulmer.github.io/sp500-forward-pe/
(Valuation & Macro, Charts, Momentum, Monthly DCA, Gauges).

## Gauges tab

`gauges.html` reads `data/gauges.json`, built by:

```bash
python3 scripts/build_gauges.py
# on the shared box (xlrd via the Playwright venv):
/workspace/.pwvenv/bin/python scripts/build_gauges.py
```

A weekday GitHub Action (`.github/workflows/gauges-daily.yml`) rebuilds and commits the JSON after the US close. The full rebuild is also intended for the monthly data-refresh routine (16th). Both paths run `scripts/build_gauges.py`, which scores 13 models at valuation 50% / recession 25% / sentiment 25%. Mean Reversion (`meanrev`) is retired: it is not built, not restored from an older `data/gauges.json`, and not included in category or composite scores.

## Adding a chart to the Charts tab

1. Copy the image into `assets/charts/` (e.g. `assets/charts/my-chart.png`). For big files, also save a smaller display copy, e.g.
   `python3 -c "from PIL import Image; im=Image.open('assets/charts/my-chart.png'); im.thumbnail((1600,1600)); im.save('assets/charts/my-chart.webp', quality=85)"`
2. Add an entry to `data/charts.json` under `"charts"`:
   ```json
   {"file": "assets/charts/my-chart.png", "display": "assets/charts/my-chart.webp", "width": 4096, "height": 2207,
    "title": "Title", "caption": "Short neutral description.", "credit": "Author / tool, date", "source": "https://optional-link", "added": "2026-10-12"}
   ```
   `display`, `width`/`height`, `source`, `chart_date` (tie-break, newest first) and `tags` (e.g. `["Bitcoin"]`, adds filter buttons) are optional. Cards are sorted by `added`, newest first.
3. Commit and push; GitHub Pages redeploys.
