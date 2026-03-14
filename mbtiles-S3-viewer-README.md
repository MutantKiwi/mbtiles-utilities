# mutant.kiwi — Map Sheet Viewer

A self-contained, zero-dependency tile viewer for hosting map tile archives on S3-compatible storage. Drop a single HTML file into any bucket alongside your tiles and it works — no server, no build step, no MapTiler account required.

Works with any XYZ tile archive.

**Live demo:** [s3.eu-central-1.wasabisys.com/UbiqueVir.battle.guide/index.html](https://s3.eu-central-1.wasabisys.com/UbiqueVir.battle.guide/index.html)

---

## Features

- **Single file** — everything is inline in `index.html`, no external assets required
- **Auto-discovers maps** from `index.json` in the same folder
- **Five map frameworks** — live interactive preview for each:
  - Leaflet 1.9.4
  - OpenLayers 10.3
  - MapLibre GL JS 4.7
  - Esri Maps SDK 4.31 (2D MapView)
  - Esri Scene SDK 4.31 (3D SceneView with terrain)
- **Download button** — generates a clean standalone HTML file for each framework, ready to embed in any webpage
- **Opacity slider** — overlay transparency control, bottom-left of map
- **Full screen button** — opens the current map in a new tab
- **XYZ tile URL** bar with one-click copy
- **Searchable sidebar** — filter maps by name
- **Bookmarkable URLs** — hash navigation (`#MapName/leaflet`)
- **Dark / Light theme toggle** — preference saved in localStorage
- **Carto Voyager** base layer for Leaflet, OpenLayers, and MapLibre
- **Esri light-gray** basemap for 2D and 3D Esri views
- **No analytics, no tracking, no external dependencies** at runtime

---

## Deployment

### 1. Bucket layout

```
your-bucket/
├── index.html          ← the viewer (this file)
├── index.json          ← tile metadata (TileJSON array)
├── Map Sheet Name A/
│   ├── {z}/{x}/{y}.png
│   └── ...
├── Map Sheet Name B/
│   └── ...
```

### 2. Upload

Drag `index.html` into your bucket via the Wasabi (or S3) web console. No other files are needed.

Set `Content-Type: text/html` on the file if your bucket doesn't detect it automatically.

### 3. CORS

Make sure your bucket has a CORS policy that allows `GET` requests from browser origins, otherwise the XHR request to `index.json` will be blocked:

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET"],
    "AllowedHeaders": ["*"]
  }
]
```

### 4. Public access

Tiles, `index.json`, and `index.html` all need to be publicly readable.

---

## Configuration

Edit the `CONFIG` block at the top of `index.html` — it is the only section you need to touch:

```js
var CONFIG = {
  title:       'mutant.kiwi',          // browser tab title
  headerTitle: 'mutant.kiwi',          // header bar left text
  headerSub:   'MAP SHEET VIEWER',     // header bar right text
  indexFile:   'index.json',           // path to tile index file
  defaultTab:  'leaflet',              // opening tab: leaflet | ol | maplibre | esri2d | esri3d
};
```

---

## index.json format

Standard [TileJSON](https://github.com/mapbox/tilejson-spec) — either a single object or an array of objects:

```json
[
  {
    "name": "84 C Paletwa ed-3 (1944)",
    "basename": "84 C Paletwa ed-3 (1944)",
    "format": "png",
    "minzoom": 7,
    "maxzoom": 13,
    "bounds": [91.91, 20.64, 93.12, 22.13],
    "center": [92.52, 21.39, 10],
    "tiles": ["84 C Paletwa ed-3 (1944)/{z}/{x}/{y}.png"],
    "attribution": "Historical Map Archive"
  }
]
```

If `tiles` is omitted, the viewer constructs the URL as `{basename}/{z}/{x}/{y}.{format}` relative to the page.

---

## Downloaded standalone files

The **Download** button (top-right of the map) generates a self-contained HTML file for the active framework. Each file:

- Has a fully-resolved absolute tile URL (spaces encoded)
- Includes an opacity slider (bottom-left)
- Uses Carto Voyager as the base layer (Leaflet / OL / MapLibre)
- Requires no server — open directly in a browser or host anywhere

| Tab | Downloaded file |
|-----|----------------|
| Leaflet | `MapName-leaflet.html` |
| OpenLayers | `MapName-openlayers.html` |
| MapLibre GL JS | `MapName-maplibre.html` |
| Esri 2D | `MapName-esri-2d.html` |
| Esri 3D | `MapName-esri-3d.html` |

---

## Esri tabs

The Esri 2D and 3D tabs load via a self-referencing iframe (`index.html?esri=1&tab=esri2d&map=…`) rather than a sandboxed `srcdoc`. This is necessary because the ArcGIS JS SDK makes internal XHR requests that browsers block inside sandboxed iframes (CORB). No Esri API key is required for the base grey basemap or for WebTileLayer overlays.

---

## Theming

The viewer uses CSS custom properties. Both themes are defined at the top of the `<style>` block:

```css
/* Dark (default) — mutant.kiwi palette */
:root {
  --bg:      #0a0c0f;
  --accent:  #e8ff47;   /* yellow-green */
  --font:    'Syne', system-ui, sans-serif;
  ...
}

/* Light */
:root.light {
  --bg:      #f5f5f0;
  --accent:  #7a8a00;
  ...
}
```

The toggle button (top-right of header) switches between themes and saves the preference in `localStorage`.

---

## Browser support

Any modern browser. Tested in Chrome, Firefox, and Safari. Requires:

- `fetch` / `XMLHttpRequest`
- `Blob` + `URL.createObjectURL`
- `navigator.clipboard` (for copy buttons — degrades silently if unavailable)
- `localStorage` (for theme preference — degrades silently)

---

## Tile generation

Tiles can be produced with any standard tool:

- [GDAL2Tiles](https://gdal.org/programs/gdal2tiles.html) — `gdal2tiles.py input.tif output_dir/`
- [mbutil](https://github.com/mapbox/mbutil) — to unpack `.mbtiles` into XYZ directories
- [MapTiler Desktop](https://www.maptiler.com/desktop/)


The viewer expects XYZ scheme (top-left origin, same as Google Maps / OpenStreetMap). TMS scheme (bottom-left origin) is not currently supported.

---

## Acknowledgements

Base layer tiles by [CARTO](https://carto.com/attributions) (Voyager style) via their free tile CDN.  
Esri basemaps via the [ArcGIS Maps SDK for JavaScript](https://developers.arcgis.com/javascript/).  
Map libraries: [Leaflet](https://leafletjs.com), [OpenLayers](https://openlayers.org), [MapLibre GL JS](https://maplibre.org).  
Typography: [Syne](https://fonts.google.com/specimen/Syne) + [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono) via Google Fonts.

---

## Licence

MIT
