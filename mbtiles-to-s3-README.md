# mbtiles-to-s3.py

Streams tiles from an MBTiles file directly to S3-compatible storage (Wasabi, AWS, Cloudflare R2, MinIO, etc.) without writing anything to disk. Automatically updates `index.json` for use with [mbtiles-S3-viewer](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-S3-viewer.html) and optionally uploads the viewer HTML itself.

---

## Features

- **Streams direct to S3** — no temporary files on disk, no intermediate folder expansion
- **Parallel uploads** — configurable thread pool (default 8), typically 500–2,000 tiles/sec on a fast connection
- **Live progress bar** — shows percentage, tile count, upload rate, and ETA
- **TMS → XYZ conversion** — MBTiles stores tiles with a flipped Y axis (TMS); the script converts to standard XYZ automatically
- **Creates or updates `index.json`** — adds a [TileJSON 2.2](https://github.com/mapbox/tilejson-spec) entry for the uploaded tileset, or replaces it on re-upload
- **Uploads viewer HTML** — optionally pushes your `index.html` viewer to the bucket on first run
- **Resumable** — skips tiles that already exist in S3; safe to restart after interruption
- **Bucket creation** — offers to create the bucket if it doesn't exist
- **Dry run mode** — previews everything that would happen without touching S3
- **Friendly error messages** — explains 301 (wrong region), 403 (bad credentials), 404 (bucket missing) clearly

---

## Requirements

Python 3.8+ and one external library:

```bash
pip install boto3
```

---

## Quick Start

### 1. Create the config file

```bash
python mbtiles-to-s3.py --init
```

This creates `config.ini` in the current folder. Open it and fill in your bucket name and credentials:

```ini
[s3]
endpoint_url      = https://s3.eu-central-1.wasabisys.com
bucket            = your-bucket-name
region            = eu-central-1
access_key_id     = YOUR_ACCESS_KEY_ID
secret_access_key = YOUR_SECRET_ACCESS_KEY

[upload]
workers           = 8
index_json        = index.json
viewer_html       = index.html
tile_mime         =
```

### 2. Test without uploading

```bash
python mbtiles-to-s3.py mymap.mbtiles --dry-run
```

### 3. Upload

```bash
python mbtiles-to-s3.py mymap.mbtiles
```

---

## Usage

```
python mbtiles-to-s3.py [OPTIONS] mymap.mbtiles

Options:
  --init        Create a template config.ini and exit
  --config PATH Use a different config file (default: config.ini)
  --dry-run     Show what would happen without uploading anything
  --force       Re-upload tiles and viewer HTML even if they already exist in S3
```

---

## Example Output

```
============================================================
  Source   : Uluru_Kata_Tjuta.mbtiles
  Bucket   : my-map-bucket
  Folder   : Uluru_Kata_Tjuta/
  Endpoint : https://s3.eu-central-1.wasabisys.com
============================================================

Reading MBTiles metadata...
  Name     : Uluru_Kata_Tjuta
  Format   : png  (image/png)
  Zoom     : 8 → 14
  Bounds   : 130.576979,-25.602975,131.589041,-24.906196
  Tiles    : 2,380

Connecting to S3...
  Connected — bucket 'my-map-bucket' is accessible

Uploading 2,380 tiles with 8 threads...
  [████████████████████░░░░░░░░] 71.2%  1,695/2,380  1,243 t/s  ETA 6s
  Uploaded 2,380 tiles in 14.3s  (1,664 t/s)

Building TileJSON entry...
  Tile URL : https://s3.eu-central-1.wasabisys.com/my-map-bucket/Uluru_Kata_Tjuta/{z}/{x}/{y}.png

Updating index.json...
  Added new entry for: Uluru_Kata_Tjuta
  Total entries in index.json: 7

Uploading index.html → index.html...
  Uploaded index.html

============================================================
  Done!
  Viewer : https://s3.eu-central-1.wasabisys.com/my-map-bucket/index.html
  Direct : https://s3.eu-central-1.wasabisys.com/my-map-bucket/Uluru_Kata_Tjuta/{z}/{x}/{y}.png
============================================================
```

---

## config.ini Reference

### [s3] section

| Key | Description | Example |
|-----|-------------|---------|
| `endpoint_url` | S3-compatible endpoint URL | `https://s3.eu-central-1.wasabisys.com` |
| `bucket` | Bucket name | `my-map-tiles` |
| `region` | Bucket region | `eu-central-1` |
| `access_key_id` | Access key ID | `ABCDEF123456` |
| `secret_access_key` | Secret access key | `abc123xyz...` |

### [upload] section

| Key | Default | Description |
|-----|---------|-------------|
| `workers` | `8` | Parallel upload threads. Increase to 16–32 on fast connections, reduce if you see rate-limit errors |
| `index_json` | `index.json` | Filename of the tile index in the bucket root |
| `viewer_html` | `index.html` | Local path to your viewer HTML. Leave blank to skip viewer upload |
| `tile_mime` | _(auto)_ | Force a specific MIME type. Leave blank to detect from MBTiles format field |

### Endpoint URLs by provider

| Provider | Endpoint |
|----------|----------|
| Wasabi EU Central | `https://s3.eu-central-1.wasabisys.com` |
| Wasabi US East 1 | `https://s3.us-east-1.wasabisys.com` |
| Wasabi US East 2 | `https://s3.us-east-2.wasabisys.com` |
| Wasabi US West 1 | `https://s3.us-west-1.wasabisys.com` |
| Wasabi AP Northeast 1 | `https://s3.ap-northeast-1.wasabisys.com` |
| AWS S3 | `https://s3.amazonaws.com` |
| Cloudflare R2 | `https://<account-id>.r2.cloudflarestorage.com` |
| MinIO (local) | `http://localhost:9000` |

---

## How It Works

### Tile coordinate conversion

MBTiles stores tiles using the [TMS](https://wiki.openstreetmap.org/wiki/TMS) convention where Y=0 is at the bottom (south). Web mapping libraries (Leaflet, OpenLayers, MapLibre, Esri) expect [XYZ / Slippy Map](https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames) tiles where Y=0 is at the top (north). The script flips Y automatically:

```python
y_xyz = (2 ** zoom - 1) - y_tms
```

### S3 key structure

Each tile is uploaded to:

```
s3://bucket/MBTilesBasename/{zoom}/{x}/{y}.{format}
```

For example, `Uluru_Kata_Tjuta.mbtiles` zoom 10, tile 925/610:

```
s3://my-bucket/Uluru_Kata_Tjuta/10/925/610.png
```

Spaces in the filename become spaces in the S3 key (Wasabi handles them fine). The tile URLs in `index.json` have spaces encoded as `%20` for browser compatibility.

### index.json format

The script maintains `index.json` as a JSON array of [TileJSON 2.2](https://github.com/mapbox/tilejson-spec) objects. Each upload adds or replaces one entry identified by `basename`:

```json
[
  {
    "tilejson":    "2.2.0",
    "name":        "Uluru_Kata_Tjuta",
    "basename":    "Uluru_Kata_Tjuta",
    "description": "",
    "attribution": "",
    "type":        "overlay",
    "format":      "png",
    "scheme":      "xyz",
    "minzoom":     8,
    "maxzoom":     14,
    "bounds":      [130.576979, -25.602975, 131.589041, -24.906196],
    "center":      [131.083, -25.254, 8],
    "tiles":       ["https://s3.eu-central-1.wasabisys.com/my-bucket/Uluru_Kata_Tjuta/{z}/{x}/{y}.png"]
  }
]
```

---

## Bucket Setup

### CORS policy (required for browser access)

Your bucket needs a CORS policy so browsers can fetch tiles. In the Wasabi console:
**Bucket → Settings → CORS**

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 86400
  }
]
```

### Public access

All tiles and the `index.json` are uploaded with `public-read` ACL automatically. Ensure your bucket policy also allows public read, or the ACL alone may not be sufficient depending on your provider's settings.

---

## Supported Tile Formats

| Format | MIME type | Notes |
|--------|-----------|-------|
| `png` | `image/png` | Most common for raster tiles |
| `jpg` / `jpeg` | `image/jpeg` | Smaller files, lossy |
| `webp` | `image/webp` | Best compression, modern browsers only |
| `pbf` | `application/x-protobuf` | Vector tiles (Mapbox Vector Tiles) |

The format is read automatically from the MBTiles `metadata` table. Override with `tile_mime` in `config.ini` if needed.

---

## Windows Drag-and-Drop

Create a file called `upload.bat` in the same folder as `mbtiles-to-s3.py`:

```bat
@echo off
python "%~dp0mbtiles-to-s3.py" "%~1"
pause
```

Drag any `.mbtiles` file onto `upload.bat` to start uploading immediately.

---

## Re-uploading / Updating

By default the script **skips tiles that already exist** in S3, so re-running after an interruption is safe and fast — only missing tiles are uploaded.

To force a complete re-upload (e.g. after regenerating tiles):

```bash
python mbtiles-to-s3.py mymap.mbtiles --force
```

`index.json` is always updated regardless of `--force`.

---

## Multiple Config Files

If you upload to more than one bucket, keep a config file per bucket:

```bash
python mbtiles-to-s3.py mymap.mbtiles --config wasabi-eu.ini
python mbtiles-to-s3.py mymap.mbtiles --config aws-us.ini
```

---

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `bucket = your-bucket-name` | Config not filled in | Edit `config.ini`, set real bucket name |
| `301 Moved Permanently` | Wrong region for bucket | Fix `region` in `config.ini` to match where the bucket was created |
| `403 Access Denied` | Wrong credentials | Check `access_key_id` and `secret_access_key` |
| `404 NoSuchBucket` | Bucket doesn't exist | Script offers to create it — press `y` |
| `NoCredentialsError` | Credentials missing entirely | Fill in both keys in `config.ini` |
| Tiles load in viewer but upside-down | TMS/XYZ mismatch | This script converts automatically; check the source MBTiles wasn't already XYZ |
| Very slow upload | Low `workers` setting or spinning disk | Increase `workers` to 16 or 32 in `config.ini` |

---

## Related Tools

- [`merge-mbtiles.py`](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/merge-mbtiles.py) — merge multiple MBTiles files before uploading
- [`mbtiles-S3-viewer.html`](https://github.com/MutantKiwi/mbtiles-utilities/blob/main/mbtiles-S3-viewer.html) — the single-file tile viewer this script uploads
- [mbutil](https://github.com/mapbox/mbutil) — extract MBTiles to XYZ folders on disk (alternative approach)
- [MapTiler Desktop](https://www.maptiler.com/desktop/) — generate MBTiles from GeoTIFF and other sources

---

## Licence

MIT
