# mbtiles-utilities

A collection of Python utilities for managing, merging, and editing metadata in MBTiles and PMTiles map tile archives.

---

## Tools

| Script | Description |
|---|---|
| [`merge-mbtiles.py`](#merge-mbtilespy) | Merge multiple MBTiles files into one |
| [`export-metadata.py`](#export-metadatapy) | Export metadata from MBTiles/PMTiles files to CSV |
| [`update-metadata.py`](#update-metadatapy) | Write metadata changes from a CSV back into MBTiles files |
| [`mbtiles-to-s3.py`](#mbtiles-to-s3py) | Stream tiles from MBTiles directly to S3 and update index.json |
| [`mbtiles-S3-viewer.html`](#mbtiles-s3-viewerhtml) | Self-contained tile viewer for S3-hosted XYZ archives |

---

## Requirements

- Python 3.8+
- [pmtiles](https://pypi.org/project/pmtiles/) — required for PMTiles support
- [boto3](https://pypi.org/project/boto3/) — required for S3 upload

```bash
pip install pmtiles boto3
```

---

## merge-mbtiles.py

**[View script](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/merge-mbtiles.py)**

Merges multiple MBTiles files into a single output file. Metadata bounds and zoom ranges are automatically expanded to cover all inputs. Where tiles overlap, later files take precedence.

### Usage

```bash
# Merge all MBTiles in the current folder → merged.mbtiles
python merge-mbtiles.py

# Merge all MBTiles in a specific folder
python merge-mbtiles.py "C:\path\to\folder"

# Merge explicit files into a named output
python merge-mbtiles.py file1.mbtiles file2.mbtiles file3.mbtiles output.mbtiles
```

### Features

- Merges any number of MBTiles files in a single pass
- Automatically computes merged bounds and zoom range across all inputs
- Copies metadata from the first valid file as the base
- `INSERT OR REPLACE` tile strategy — later files win on overlap
- Commits every 1000 tiles so partial output is usable if interrupted
- Gracefully skips files missing a `metadata` or `tiles` table
- Progress bar
- Prints per-file tile counts and a final summary

### Example output

```
Found 4 MBTiles file(s) to merge in: .

Reading metadata from input files...
  Merged bounds:  80.947266,27.683528,88.264160,30.505484
  Merged zoom:    0 → 14

  Merged   8705 tiles  ←  TPC-part0000.mbtiles
  Merged  12340 tiles  ←  TPC-part0001.mbtiles
  Merged   9871 tiles  ←  TPC-part0002.mbtiles
  Merged  11203 tiles  ←  TPC-part0003.mbtiles

Done — 42119 total tiles written to merged.mbtiles
```

---

## export-metadata.py

**[View script](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/export-metadata.py)**

Scans a folder for MBTiles and PMTiles files and exports their metadata into a single CSV file. Each file becomes one row. Standard fields always appear as columns in a fixed order, even if empty.

### Usage

```bash
# Scan current folder → metadata_export.csv
python export-metadata.py

# Scan a specific folder
python export-metadata.py "C:\path\to\folder"

# Specify a custom output filename
python export-metadata.py "C:\path\to\folder" my_output.csv
```

### Standard fields captured

| Field | Description |
|---|---|
| `name` | Display name of the tileset |
| `description` | Human-readable description |
| `legend` | Legend content (may be HTML) |
| `attribution` | Source attribution (may be HTML) |
| `type` | `baselayer` or `overlay` |
| `version` | Version of the tileset |
| `format` | Tile image format (`png`, `jpeg`, `webp`, `avif`, `pbf`) |
| `format_arguments` | Additional format parameters |
| `minzoom` | Minimum zoom level |
| `maxzoom` | Maximum zoom level |
| `bounds` | Bounding box: `min_lon,min_lat,max_lon,max_lat` |
| `scale` | Display scale |
| `profile` | Tile profile (e.g. `mercator`) |
| `scheme` | Tile scheme (`tms` or `xyz`) |
| `generator` | Tool used to generate the tileset |

Additional file-level columns are also included:

| Column | Description |
|---|---|
| `source_file` | Filename of the tile archive |
| `source_type` | `mbtiles` or `pmtiles` |
| `file_size_mb` | File size in megabytes |
| `tile_count` | Total number of tiles |
| `tiles_per_zoom` | Tile count broken down per zoom level |

Any non-standard metadata keys found in individual files are appended as extra columns at the end.

### Example output

```
Found 2 MBTiles and 1 PMTiles file(s).

  OK   TPC-part0001.mbtiles
  OK   TPC-part0002.mbtiles
  OK   TPC-part0003.pmtiles

Exported 3 file(s) to: metadata_export.csv
```

---

## update-metadata.py

**[View script](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/update-metadata.py)**

Reads a CSV produced by `export-metadata.py` and writes selected metadata changes back into the corresponding MBTiles files. Only the permitted fields are ever modified — all other columns in the CSV are silently ignored.

### Recommended workflow

```
1. python export-metadata.py     →  metadata_export.csv
2. Edit metadata_export.csv      →  fill in / correct fields
3. python update-metadata.py     →  changes written back to .mbtiles files
```

### Usage

```bash
# Use metadata_export.csv in the current folder
python update-metadata.py

# Specify a CSV path explicitly
python update-metadata.py metadata_export.csv

# Full path
python update-metadata.py "C:\path\to\metadata_export.csv"
```

### Updatable fields

Only these fields are written back — all others in the CSV are ignored:

| Field | Rules |
|---|---|
| `name` | Plain text only |
| `description` | Plain text only |
| `legend` | Plain text only |
| `attribution` | HTML allowed (e.g. `<a href="...">Source</a>`) |
| `type` | Must be `overlay` or `baselayer` — rejected otherwise |
| `version` | Plain text only |
| `generator` | Plain text only |

### Automatic fields

| Field | Behaviour |
|---|---|
| `date` | Always stamped with today's date in `YYYYMMDD` format — never read from the CSV |

### Validation

- `type` values other than `overlay` or `baselayer` are rejected and the file is skipped
- HTML tags in any plain-text field (`name`, `description`, `legend`, `version`, `generator`) are rejected
- PMTiles files are reported but not modified — they are read-only binary archives; convert to MBTiles first

### Example output

```
Processing 3 row(s) from: metadata_export.csv
Date stamp that will be applied: 20260314

============================================================
File: TPC-part0001.mbtiles
  Updating 3 field(s) + date stamp:
  + legend = Elevation contours at 40m intervals
  ~ attribution: '' → '<a href="https://example.com">Maps</a>'
  ~ name: 'maps' → 'Maps 1979'
  ~ date: '20260101' → '20260314'

File: TPC-part0001.pmtiles
  ! PMTiles files are read-only binary archives.
  ! Metadata cannot be updated in-place.
  ! Convert to MBTiles first, update there, then re-export if needed.

File: TPC-part0001.mbtiles
  No changes needed — all values already match.

============================================================
Done.
  Updated : 1
  Skipped : 1
  Errors  : 0
```

---

## mbtiles-to-s3.py

**[View script](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-to-s3.py)** · **[Full documentation](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-to-s3-README.md)**

Streams tiles from an MBTiles file directly to S3-compatible storage (Wasabi, AWS, Cloudflare R2, MinIO) without writing anything to disk. Automatically converts TMS → XYZ tile coordinates, updates `index.json` with a TileJSON entry, and optionally uploads the viewer HTML.

### Usage

```bash
# First run — create config.ini
python mbtiles-to-s3.py --init

# Test without uploading
python mbtiles-to-s3.py mymap.mbtiles --dry-run

# Upload
python mbtiles-to-s3.py mymap.mbtiles
```

### Features

- Streams tiles direct to S3 — no temporary files or folder expansion on disk
- Parallel uploads with configurable thread count (default 8 workers)
- Live progress bar with upload rate and ETA
- Converts TMS → XYZ tile coordinates automatically
- Creates or updates `index.json` with a TileJSON 2.2 entry
- Skips tiles already in S3 — safe to resume after interruption
- Offers to create the bucket if it doesn't exist
- Friendly error messages for wrong region (301), bad credentials (403), missing bucket (404)
- Windows drag-and-drop support via a simple `.bat` launcher

See [`mbtiles-to-s3-README.md`](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-to-s3-README.md) for full configuration, provider endpoints, and troubleshooting.

---

## mbtiles-S3-viewer.html

**[View file](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-S3-viewer.html)** · **[Full documentation](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-S3-viewer-README.md)**

A single-file tile viewer for browsing XYZ tile archives hosted on S3 or any static file host. Drop it into the same bucket as your tiles and it works immediately — no server, no build step, no API keys.

The viewer reads `index.json` (standard TileJSON) from the same folder and presents all tilesets in a searchable sidebar with live interactive previews.

### Deployment

```
your-bucket/
├── mbtiles-S3-viewer.html   ← rename to index.html
├── index.json               ← TileJSON array describing your tilesets
├── Map Sheet A/{z}/{x}/{y}.png
└── Map Sheet B/{z}/{x}/{y}.png
```

Upload `mbtiles-S3-viewer.html` (rename to `index.html`) alongside your `index.json` and tile folders. Ensure the bucket has public read access and a CORS policy allowing `GET` requests.

### Features

- **Five live map frameworks** — Leaflet, OpenLayers, MapLibre GL JS, Esri 2D MapView, Esri 3D SceneView
- **Download** — generates a clean standalone HTML viewer for any framework
- **Opacity slider**, full-screen button, one-click XYZ URL copy
- **Dark / light theme** toggle with localStorage persistence
- Zero external dependencies at runtime — all libraries loaded from public CDNs

See [`mbtiles-S3-viewer-README.md`](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-S3-viewer-README.md) for full configuration, theming, and deployment details.

---

## Typical full workflow

```bash
# 1. Convert any PMTiles sources to MBTiles
python pmtiles2mbtiles.py "D:\path\to\folder"

# 2. Merge all MBTiles into one archive
python merge-mbtiles.py "D:\path\to\folder"

# 3. Export metadata to CSV for review and editing
python export-metadata.py "D:\path\to\folder"

# 4. Edit metadata_export.csv in Excel or a text editor

# 5. Write changes back to the MBTiles files
python update-metadata.py "D:\path\to\folder\metadata_export.csv"

# 6. Upload to S3 and update the viewer index
python mbtiles-to-s3.py "D:\path\to\folder\merged.mbtiles"
```

---

## Notes

- All scripts expect tile files and the CSV to be in the **same folder**
- MBTiles files are standard SQLite databases and can be inspected with any SQLite browser such as [DB Browser for SQLite](https://sqlitebrowser.org/)
- PMTiles metadata is read-only — use the [go-pmtiles](https://github.com/protomaps/go-pmtiles) CLI or convert to MBTiles before editing
- Tested against MBTiles spec 1.3 and PMTiles spec version 3

---

## Related tools

- [go-pmtiles](https://github.com/protomaps/go-pmtiles) — official PMTiles CLI for merging and inspecting PMTiles
- [pmtiles Python library](https://pypi.org/project/pmtiles/) — PMTiles reader used by these scripts
- [GDAL](https://gdal.org/) — geospatial format conversion including MBTiles → GeoTIFF
- [DB Browser for SQLite](https://sqlitebrowser.org/) — inspect MBTiles files directly as SQLite databases
- [MARTIN](https://maplibre.org/martin/mbtiles/) — another MBTiles tool
- [MBTiles spec](https://github.com/mapbox/mbtiles-spec/blob/master/1.3/spec.md) — MBTiles spec

---

## Licence

MIT

