#!/usr/bin/env python3
"""
mbtiles-to-s3.py
────────────────
Streams tiles from an MBTiles file directly to S3 (or Wasabi) without
writing anything to disk. Updates index.json and uploads index.html.

Usage:
    python mbtiles-to-s3.py mymap.mbtiles
    python mbtiles-to-s3.py mymap.mbtiles --config my-config.ini
    python mbtiles-to-s3.py mymap.mbtiles --dry-run

First run:
    python mbtiles-to-s3.py --init        ← creates config.ini, fill it in
    python mbtiles-to-s3.py mymap.mbtiles ← then run for real

Requirements:
    pip install boto3
"""

import argparse
import configparser
import json
import math
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Check boto3 is installed ──────────────────────────────────────
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:
    print("ERROR: boto3 is not installed. Run:  pip install boto3")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = """\
[s3]
# Wasabi endpoint — change region if needed
endpoint_url    = https://s3.eu-central-1.wasabisys.com
bucket          = your-bucket-name
region          = eu-central-1

# AWS/Wasabi credentials
access_key_id   = YOUR_ACCESS_KEY_ID
secret_access_key = YOUR_SECRET_ACCESS_KEY

[upload]
# Number of parallel upload threads (4–16 recommended)
workers         = 8

# Name of the tile index file in the bucket root
index_json      = index.json

# Path to your viewer HTML file (leave blank to skip)
viewer_html     = index.html

# MIME type for tiles (image/png  image/jpeg  image/webp)
# Leave blank to detect automatically from MBTiles metadata
tile_mime       =
"""

CONFIG_FILE = "config.ini"


def init_config():
    if os.path.exists(CONFIG_FILE):
        print(f"config.ini already exists — edit it directly.")
    else:
        with open(CONFIG_FILE, "w") as f:
            f.write(DEFAULT_CONFIG)
        print(f"Created {CONFIG_FILE} — fill in your bucket name and credentials, then run again.")
    sys.exit(0)


def load_config(path):
    if not os.path.exists(path):
        print(f"ERROR: Config file not found: {path}")
        print(f"Run:  python mbtiles-to-s3.py --init  to create one.")
        sys.exit(1)
    cfg = configparser.ConfigParser()
    cfg.read(path)
    return cfg


# ─────────────────────────────────────────────────────────────────
# MBTILES HELPERS
# ─────────────────────────────────────────────────────────────────

MIME_MAP = {
    "png":  "image/png",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "pbf":  "application/x-protobuf",
}


def read_mbtiles_metadata(path):
    """Return metadata dict and tile count from an MBTiles file."""
    con = sqlite3.connect(path)
    try:
        meta = dict(con.execute("SELECT name, value FROM metadata").fetchall())
    except sqlite3.OperationalError:
        meta = {}
    try:
        count = con.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    except sqlite3.OperationalError:
        count = 0
    con.close()
    return meta, count


def flip_y(z, y):
    """Convert TMS y (bottom-left origin) to XYZ y (top-left origin)."""
    return (2 ** z - 1) - y


def iter_tiles(path):
    """
    Yield (z, x, y_xyz, tile_data) for every tile in the MBTiles file.
    MBTiles stores tiles in TMS scheme (y flipped); we convert to XYZ.
    """
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        for row in con.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
        ):
            z = row["zoom_level"]
            x = row["tile_column"]
            y = flip_y(z, row["tile_row"])
            yield z, x, y, bytes(row["tile_data"])
    finally:
        con.close()


def build_tilejson(meta, basename, bucket, endpoint_url):
    """
    Build a TileJSON 2.2 object from MBTiles metadata.
    Tile URLs point to the S3 bucket.
    """
    # Derive the public base URL from the endpoint
    # e.g. https://s3.eu-central-1.wasabisys.com/bucket/folder/{z}/{x}/{y}.png
    host = endpoint_url.rstrip("/")
    fmt  = meta.get("format", "png")
    ext  = "." + fmt

    # URL-encode spaces in the basename for the tile URL
    safe_name = basename.replace(" ", "%20")
    tile_url  = f"{host}/{bucket}/{safe_name}/{{z}}/{{x}}/{{y}}{ext}"

    bounds = [-180, -85.0511, 180, 85.0511]
    if "bounds" in meta:
        try:
            bounds = list(map(float, meta["bounds"].split(",")))
        except ValueError:
            pass

    center = [
        (bounds[0] + bounds[2]) / 2,
        (bounds[1] + bounds[3]) / 2,
        int(meta.get("minzoom", 0)),
    ]
    if "center" in meta:
        try:
            parts  = meta["center"].split(",")
            center = [float(parts[0]), float(parts[1]), int(parts[2])]
        except (ValueError, IndexError):
            pass

    return {
        "tilejson":    "2.2.0",
        "name":        meta.get("name", basename),
        "basename":    basename,
        "description": meta.get("description", ""),
        "attribution": meta.get("attribution", ""),
        "type":        meta.get("type", "overlay"),
        "version":     meta.get("version", "1"),
        "format":      fmt,
        "scheme":      "xyz",
        "minzoom":     int(meta.get("minzoom", 0)),
        "maxzoom":     int(meta.get("maxzoom", 18)),
        "bounds":      bounds,
        "center":      center,
        "tiles":       [tile_url],
    }


# ─────────────────────────────────────────────────────────────────
# S3 HELPERS
# ─────────────────────────────────────────────────────────────────

def make_s3_client(cfg):
    return boto3.client(
        "s3",
        endpoint_url          = cfg["s3"]["endpoint_url"],
        region_name           = cfg["s3"].get("region", "us-east-1"),
        aws_access_key_id     = cfg["s3"]["access_key_id"],
        aws_secret_access_key = cfg["s3"]["secret_access_key"],
    )


def s3_get_json(s3, bucket, key):
    """Download and parse a JSON file from S3. Returns {} if not found."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {}
        raise


def s3_put_json(s3, bucket, key, data, dry_run=False):
    """Upload a Python dict as a public-read JSON file to S3."""
    body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    if dry_run:
        print(f"  [DRY RUN] Would upload {key} ({len(body):,} bytes)")
        return
    s3.put_object(
        Bucket      = bucket,
        Key         = key,
        Body        = body,
        ContentType = "application/json",
        ACL         = "public-read",
    )


def s3_key_exists(s3, bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def upload_tile(s3, bucket, key, data, mime):
    """Upload a single tile. Called from thread pool."""
    s3.put_object(
        Bucket      = bucket,
        Key         = key,
        Body        = data,
        ContentType = mime,
        ACL         = "public-read",
        CacheControl= "public, max-age=86400",
    )


# ─────────────────────────────────────────────────────────────────
# PROGRESS
# ─────────────────────────────────────────────────────────────────

class Progress:
    def __init__(self, total):
        self.total    = total
        self.done     = 0
        self.errors   = 0
        self.t0       = time.time()
        self._last    = 0

    def update(self, success=True):
        self.done += 1
        if not success:
            self.errors += 1
        # Throttle redraws to every 200ms
        now = time.time()
        if now - self._last > 0.2 or self.done == self.total:
            self._last = now
            self._draw()

    def _draw(self):
        elapsed   = time.time() - self.t0
        rate      = self.done / elapsed if elapsed > 0 else 0
        remaining = (self.total - self.done) / rate if rate > 0 else 0
        pct       = self.done / self.total * 100 if self.total else 0
        bar_len   = 28
        filled    = int(bar_len * pct / 100)
        bar       = "█" * filled + "░" * (bar_len - filled)
        err_str   = f"  {self.errors} errors" if self.errors else ""
        sys.stdout.write(
            f"\r  [{bar}] {pct:5.1f}%  "
            f"{self.done:,}/{self.total:,}  "
            f"{rate:,.0f} t/s  "
            f"ETA {int(remaining)}s"
            f"{err_str}"
            "     "
        )
        sys.stdout.flush()

    def finish(self):
        self._draw()
        elapsed = time.time() - self.t0
        rate    = self.done / elapsed if elapsed > 0 else 0
        print(f"\n  Uploaded {self.done:,} tiles in {elapsed:.1f}s  ({rate:,.0f} t/s)")
        if self.errors:
            print(f"  WARNING: {self.errors} tiles failed to upload")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Stream MBTiles → S3 and update index.json"
    )
    parser.add_argument("mbtiles", nargs="?",
                        help="Path to the .mbtiles file to upload")
    parser.add_argument("--config", default=CONFIG_FILE,
                        help=f"Path to config file (default: {CONFIG_FILE})")
    parser.add_argument("--init", action="store_true",
                        help="Create a template config.ini and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be uploaded without actually doing it")
    parser.add_argument("--force", action="store_true",
                        help="Re-upload tiles even if they already exist")
    args = parser.parse_args()

    if args.init:
        init_config()

    if not args.mbtiles:
        parser.print_help()
        sys.exit(1)

    mbtiles_path = args.mbtiles
    if not os.path.exists(mbtiles_path):
        print(f"ERROR: File not found: {mbtiles_path}")
        sys.exit(1)

    cfg         = load_config(args.config)
    bucket      = cfg["s3"]["bucket"]
    endpoint    = cfg["s3"]["endpoint_url"].rstrip("/")
    workers     = int(cfg["upload"].get("workers", 8))
    index_key   = cfg["upload"].get("index_json", "index.json")
    viewer_path = cfg["upload"].get("viewer_html", "index.html").strip()
    tile_mime   = cfg["upload"].get("tile_mime", "").strip()

    basename = Path(mbtiles_path).stem   # filename without extension

    print(f"{'='*60}")
    print(f"  Source   : {mbtiles_path}")
    print(f"  Bucket   : {bucket}")
    print(f"  Folder   : {basename}/")
    print(f"  Endpoint : {endpoint}")
    if args.dry_run:
        print(f"  MODE     : DRY RUN — nothing will be uploaded")
    print(f"{'='*60}\n")

    # ── Read MBTiles metadata ──────────────────────────────────────
    print("Reading MBTiles metadata...")
    meta, tile_count = read_mbtiles_metadata(mbtiles_path)

    fmt = meta.get("format", "png").lower()
    if not tile_mime:
        tile_mime = MIME_MAP.get(fmt, "image/png")

    print(f"  Name     : {meta.get('name', basename)}")
    print(f"  Format   : {fmt}  ({tile_mime})")
    print(f"  Zoom     : {meta.get('minzoom', '?')} → {meta.get('maxzoom', '?')}")
    print(f"  Bounds   : {meta.get('bounds', 'unknown')}")
    print(f"  Tiles    : {tile_count:,}")
    print()

    # ── Validate config hasn't been left at defaults ─────────────
    if bucket == "your-bucket-name":
        print("ERROR: You haven't set your bucket name in config.ini yet.")
        print("       Open config.ini and change 'bucket = your-bucket-name'")
        sys.exit(1)
    if "YOUR_ACCESS_KEY" in cfg["s3"]["access_key_id"]:
        print("ERROR: You haven't set your credentials in config.ini yet.")
        print("       Fill in access_key_id and secret_access_key")
        sys.exit(1)

    # ── Connect to S3 ─────────────────────────────────────────────
    print("Connecting to S3...")
    try:
        s3 = make_s3_client(cfg)
    except Exception as e:
        print(f"ERROR: Could not create S3 client: {e}")
        sys.exit(1)

    try:
        s3.head_bucket(Bucket=bucket)
        print(f"  Connected — bucket '{bucket}' is accessible\n")
    except NoCredentialsError:
        print("ERROR: Invalid credentials. Check access_key_id and secret_access_key in config.ini")
        sys.exit(1)
    except ClientError as e:
        code = e.response["Error"]["Code"]

        if code == "301":
            print(f"ERROR: Bucket '{bucket}' exists but in a different region.")
            print(f"       Check the 'region' setting in config.ini matches your bucket's region.")
            print(f"       Common Wasabi regions: us-east-1  us-east-2  us-west-1  eu-central-1  ap-northeast-1")
            sys.exit(1)

        if code == "403":
            print(f"ERROR: Access denied to bucket '{bucket}'.")
            print(f"       Check your access_key_id and secret_access_key are correct.")
            sys.exit(1)

        if code in ("404", "NoSuchBucket"):
            print(f"  Bucket '{bucket}' does not exist.")
            answer = input(f"  Create it now? [y/N]: ").strip().lower()
            if answer != "y":
                print("Aborted.")
                sys.exit(0)
            try:
                region = cfg["s3"].get("region", "us-east-1")
                if region == "us-east-1":
                    s3.create_bucket(Bucket=bucket)
                else:
                    s3.create_bucket(
                        Bucket=bucket,
                        CreateBucketConfiguration={"LocationConstraint": region},
                    )
                s3.put_bucket_acl(Bucket=bucket, ACL="public-read")
                print(f"  Created bucket '{bucket}' in {region} with public-read ACL\n")
            except ClientError as ce:
                print(f"ERROR: Could not create bucket: {ce}")
                sys.exit(1)
        else:
            print(f"ERROR: Cannot access bucket '{bucket}': {e}")
            print(f"       Error code: {code}")
            sys.exit(1)

    # ── Upload tiles ───────────────────────────────────────────────
    print(f"Uploading {tile_count:,} tiles with {workers} threads...")
    if args.dry_run:
        print("  [DRY RUN] Skipping tile upload")
    else:
        progress  = Progress(tile_count)
        skipped   = 0

        def upload_one(args_tuple):
            z, x, y, data = args_tuple
            key = f"{basename}/{z}/{x}/{y}.{fmt}"
            if not args.force and s3_key_exists(s3, bucket, key):
                return "skip"
            upload_tile(s3, bucket, key, data, tile_mime)
            return "ok"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(upload_one, tile): tile
                for tile in iter_tiles(mbtiles_path)
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result == "skip":
                        skipped += 1
                        progress.total -= 1   # adjust total since we're skipping
                    progress.update(success=True)
                except Exception as e:
                    progress.update(success=False)

        progress.finish()
        if skipped:
            print(f"  Skipped {skipped:,} tiles already in S3 (use --force to re-upload)")

    print()

    # ── Build TileJSON entry ───────────────────────────────────────
    print("Building TileJSON entry...")
    tilejson = build_tilejson(meta, basename, bucket, endpoint)
    print(f"  Tile URL : {tilejson['tiles'][0]}")

    # ── Update index.json ──────────────────────────────────────────
    print(f"\nUpdating {index_key}...")
    existing = s3_get_json(s3, bucket, index_key)

    # index.json can be a single object or an array
    if isinstance(existing, dict) and existing:
        entries = [existing]
    elif isinstance(existing, list):
        entries = existing
    else:
        entries = []

    # Replace existing entry for this basename or append
    replaced = False
    for i, entry in enumerate(entries):
        if entry.get("basename") == basename or entry.get("name") == basename:
            entries[i] = tilejson
            replaced   = True
            print(f"  Updated existing entry for: {basename}")
            break
    if not replaced:
        entries.append(tilejson)
        print(f"  Added new entry for: {basename}")
    print(f"  Total entries in index.json: {len(entries)}")

    s3_put_json(s3, bucket, index_key, entries, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"  Uploaded {index_key}")

    # ── Upload viewer HTML ─────────────────────────────────────────
    if viewer_path and os.path.exists(viewer_path):
        viewer_key = os.path.basename(viewer_path)
        if s3_key_exists(s3, bucket, viewer_key) and not args.force:
            print(f"\nSkipping {viewer_key} — already exists in bucket (use --force to overwrite)")
        else:
            print(f"\nUploading {viewer_path} → {viewer_key}...")
            if not args.dry_run:
                with open(viewer_path, "rb") as f:
                    s3.put_object(
                        Bucket      = bucket,
                        Key         = viewer_key,
                        Body        = f.read(),
                        ContentType = "text/html",
                        ACL         = "public-read",
                    )
                print(f"  Uploaded {viewer_key}")
            else:
                print(f"  [DRY RUN] Would upload {viewer_key}")
    elif viewer_path and not os.path.exists(viewer_path):
        print(f"\nWARNING: viewer_html = '{viewer_path}' not found — skipping")

    # ── Summary ────────────────────────────────────────────────────
    viewer_url = f"{endpoint}/{bucket}/index.html"
    print(f"\n{'='*60}")
    print(f"  Done!")
    print(f"  Viewer : {viewer_url}")
    print(f"  Direct : {endpoint}/{bucket}/{basename.replace(' ', '%20')}/{{z}}/{{x}}/{{y}}.{fmt}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
