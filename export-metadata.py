import os
import sys
import glob
import csv
import sqlite3
from pmtiles.reader import Reader, MmapSource

# Mapping from PMTiles enum name to readable format string
TILE_TYPE_MAP = {
    "UNKNOWN": "unknown",
    "MVT":     "pbf",
    "PNG":     "png",
    "JPEG":    "jpeg",
    "WEBP":    "webp",
    "AVIF":    "avif",
}

# All standard MBTiles metadata fields we want to capture.
# These will appear as columns even if empty, in this exact order.
STANDARD_FIELDS = [
    "name",
    "description",
    "legend",
    "attribution",
    "type",
    "version",
    "format",
    "format_arguments",
    "minzoom",
    "maxzoom",
    "bounds",
    "scale",
    "profile",
    "scheme",
    "generator",
]

def get_tile_format(tile_type):
    """Resolve TileType enum or integer to a format string."""
    key = tile_type.name if hasattr(tile_type, "name") else str(tile_type)
    return TILE_TYPE_MAP.get(key, "unknown")


def read_mbtiles_metadata(path):
    """
    Extract metadata from an MBTiles (SQLite) file.
    All standard fields are included (empty string if not present).
    """
    # Start with all standard fields defaulting to empty string
    meta = {
        "source_file":  os.path.basename(path),
        "source_type":  "mbtiles",
        "file_size_mb": round(os.path.getsize(path) / 1024 / 1024, 3),
    }

    # Pre-populate every standard field so columns always exist in the CSV
    for field in STANDARD_FIELDS:
        meta[field] = ""

    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row

    # Read all key/value pairs from the metadata table.
    # Any standard field found will overwrite the empty default.
    # Any non-standard field will be appended as an extra column.
    try:
        for row in con.execute("SELECT name, value FROM metadata"):
            meta[row["name"]] = row["value"]
    except sqlite3.OperationalError:
        meta["metadata_error"] = "No metadata table found"

    # Total tile count
    try:
        count = con.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        meta["tile_count"] = count
    except sqlite3.OperationalError:
        meta["tile_count"] = "No tiles table found"

    # Per-zoom tile counts as a compact summary string e.g. "z0:1 | z1:4 | z2:16"
    try:
        zoom_counts = con.execute(
            "SELECT zoom_level, COUNT(*) as cnt FROM tiles "
            "GROUP BY zoom_level ORDER BY zoom_level"
        ).fetchall()
        meta["tiles_per_zoom"] = "  |  ".join(
            f"z{row[0]}:{row[1]}" for row in zoom_counts
        )
    except sqlite3.OperationalError:
        meta["tiles_per_zoom"] = ""

    con.close()
    return meta


def read_pmtiles_metadata(path):
    """
    Extract metadata from a PMTiles file.
    Maps PMTiles header fields onto the same standard field names
    used by MBTiles so the CSV columns align between both formats.
    """
    # Pre-populate every standard field so columns always exist in the CSV
    meta = {
        "source_file":  os.path.basename(path),
        "source_type":  "pmtiles",
        "file_size_mb": round(os.path.getsize(path) / 1024 / 1024, 3),
    }
    for field in STANDARD_FIELDS:
        meta[field] = ""

    with open(path, "rb") as f:
        source = MmapSource(f)
        reader = Reader(source)
        header = reader.header()

        # --- Standard fields (mapped from PMTiles header) ---
        meta["name"]        = header.get("name", "")
        meta["description"] = header.get("description", "")
        meta["attribution"] = header.get("attribution", "")
        meta["type"]        = header.get("type", "")
        meta["version"]     = str(header.get("version", ""))

        # Tile format resolved from the TileType enum
        tile_type_id   = header.get("tile_type", 0)
        meta["format"] = get_tile_format(tile_type_id)

        # Zoom levels
        meta["minzoom"] = str(header.get("min_zoom", ""))
        meta["maxzoom"] = str(header.get("max_zoom", ""))

        # Bounds — stored as fixed-point integers (× 1e7) in PMTiles
        min_lon = header.get("min_lon_e7", 0) / 1e7
        min_lat = header.get("min_lat_e7", 0) / 1e7
        max_lon = header.get("max_lon_e7", 0) / 1e7
        max_lat = header.get("max_lat_e7", 0) / 1e7
        meta["bounds"] = f"{min_lon},{min_lat},{max_lon},{max_lat}"

        # PMTiles uses "scheme" implicitly (always xyz), make it explicit
        meta["scheme"] = "xyz"

        # --- PMTiles-specific fields (no MBTiles equivalent) ---
        center_lon  = header.get("center_lon_e7", 0) / 1e7
        center_lat  = header.get("center_lat_e7", 0) / 1e7
        center_zoom = header.get("center_zoom", "")
        meta["center"] = f"{center_lon},{center_lat},{center_zoom}"

        meta["tile_count"]          = header.get("addressed_tiles_count", "")
        meta["tile_entries_count"]  = header.get("tile_entries_count", "")
        meta["tile_contents_count"] = header.get("tile_contents_count", "")

        internal_comp = header.get("internal_compression")
        tile_comp     = header.get("tile_compression")
        meta["internal_compression"] = (
            internal_comp.name if hasattr(internal_comp, "name") else str(internal_comp)
        )
        meta["tile_compression"] = (
            tile_comp.name if hasattr(tile_comp, "name") else str(tile_comp)
        )

        meta["spec_version"] = str(header.get("spec_version", ""))

    return meta


def export_metadata_to_csv(folder_path, output_csv="metadata_export.csv"):
    """
    Scan a folder for all .mbtiles and .pmtiles files, read their metadata,
    and write everything to a single CSV. Each file becomes one row.
    Standard fields always appear first in a fixed order; any extra keys
    discovered in individual files are appended as additional columns.
    """

    mbtiles_files = sorted(glob.glob(os.path.join(folder_path, "*.mbtiles")))
    pmtiles_files = sorted(glob.glob(os.path.join(folder_path, "*.pmtiles")))
    all_files     = mbtiles_files + pmtiles_files

    # Exclude the CSV output file itself if it somehow matches
    output_path = os.path.join(folder_path, output_csv)
    all_files   = [f for f in all_files if os.path.abspath(f) != os.path.abspath(output_path)]

    if not all_files:
        print(f"No .mbtiles or .pmtiles files found in: {folder_path}")
        return

    print(f"Found {len(mbtiles_files)} MBTiles and {len(pmtiles_files)} PMTiles file(s).\n")

    rows   = []
    errors = []

    for path in all_files:
        filename = os.path.basename(path)
        ext      = os.path.splitext(path)[1].lower()

        try:
            if ext == ".mbtiles":
                meta = read_mbtiles_metadata(path)
            elif ext == ".pmtiles":
                meta = read_pmtiles_metadata(path)
            else:
                continue

            rows.append(meta)
            print(f"  OK   {filename}")

        except Exception as e:
            print(f"  ERR  {filename}: {e}")
            errors.append({
                "source_file": filename,
                "source_type": ext,
                "metadata_error": str(e),
            })

    if not rows and not errors:
        print("Nothing to export.")
        return

    # --- Build column order ---
    # Fixed prefix columns, then all standard metadata fields in spec order,
    # then any extra keys found in individual files (e.g. custom MBTiles fields)
    prefix_cols = ["source_file", "source_type", "file_size_mb"]

    extra_cols = []
    for row in rows:
        for key in row:
            if key not in prefix_cols and key not in STANDARD_FIELDS and key not in extra_cols:
                extra_cols.append(key)

    all_cols = prefix_cols + STANDARD_FIELDS + extra_cols

    # --- Write CSV ---
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        if errors:
            for err in errors:
                writer.writerow(err)

    print(f"\nExported {len(rows)} file(s) to: {output_path}")
    if errors:
        print(f"  {len(errors)} file(s) failed — see metadata_error column in CSV.")


# --- Entry point ---
# Usage:
#   python export_metadata.py                        ← scans current folder
#   python export_metadata.py "C:\path\to\folder"   ← scans given folder
#   python export_metadata.py "C:\path\to\folder" out.csv

if __name__ == "__main__":
    if len(sys.argv) == 1:
        export_metadata_to_csv(".")

    elif len(sys.argv) == 2:
        target = sys.argv[1]
        if os.path.isdir(target):
            export_metadata_to_csv(target)
        else:
            print(f"Error: '{target}' is not a valid folder.")
            sys.exit(1)

    elif len(sys.argv) == 3:
        export_metadata_to_csv(sys.argv[1], sys.argv[2])

    else:
        print("Usage: python export_metadata.py [folder] [output.csv]")
        sys.exit(1)
