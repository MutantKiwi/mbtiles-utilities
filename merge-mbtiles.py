import os
import sqlite3
import sys
import time


def merge_mbtiles(inputs, output):
    """
    Merge multiple MBTiles files into a single output MBTiles file.
    Where tiles overlap, later files in the list take precedence.
    Metadata is taken from the first valid file and bounds/zoom are
    expanded to cover all input files. Files missing tables are skipped.
    """

    if not inputs:
        print("No input files provided.")
        return

    inputs = [p for p in inputs if os.path.abspath(p) != os.path.abspath(output)]
    if not inputs:
        print("No input files remaining after excluding the output path.")
        return

    missing = [p for p in inputs if not os.path.exists(p)]
    if missing:
        for p in missing:
            print(f"  ERROR: File not found: {p}")
        return

    if os.path.exists(output):
        os.remove(output)
        print(f"Removed existing: {output}")

    con = sqlite3.connect(output)
    cur = con.cursor()

    cur.executescript("""
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous  = OFF;
        CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT);
        CREATE TABLE IF NOT EXISTS tiles (
            zoom_level  INTEGER,
            tile_column INTEGER,
            tile_row    INTEGER,
            tile_data   BLOB
        );
        CREATE UNIQUE INDEX IF NOT EXISTS tile_index
            ON tiles (zoom_level, tile_column, tile_row);
    """)

    # --- Collect metadata and tile counts for progress display ---
    all_min_lon, all_min_lat, all_max_lon, all_max_lat = 180, 90, -180, -90
    global_min_zoom, global_max_zoom = 99, 0
    first_metadata = {}
    bounds_found   = False
    tile_counts    = {}

    print("Reading metadata from input files...")
    for path in inputs:
        try:
            src = sqlite3.connect(path)
            src.row_factory = sqlite3.Row
        except sqlite3.OperationalError as e:
            print(f"  WARNING: Could not open {os.path.basename(path)}: {e}")
            continue

        try:
            meta = {row["name"]: row["value"]
                    for row in src.execute("SELECT name, value FROM metadata")}
        except sqlite3.OperationalError:
            print(f"  WARNING: No metadata table in {os.path.basename(path)}")
            meta = {}

        if not first_metadata and meta:
            first_metadata = dict(meta)

        if "bounds" in meta:
            try:
                mn, ms, mx, my = map(float, meta["bounds"].split(","))
                all_min_lon = min(all_min_lon, mn)
                all_min_lat = min(all_min_lat, ms)
                all_max_lon = max(all_max_lon, mx)
                all_max_lat = max(all_max_lat, my)
                bounds_found = True
            except ValueError:
                pass

        if "minzoom" in meta:
            global_min_zoom = min(global_min_zoom, int(meta["minzoom"]))
        if "maxzoom" in meta:
            global_max_zoom = max(global_max_zoom, int(meta["maxzoom"]))

        try:
            count = src.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
            tile_counts[path] = count
            print(f"  {os.path.basename(path):40s}  {count:>9,} tiles")
        except sqlite3.OperationalError:
            tile_counts[path] = 0
            print(f"  {os.path.basename(path):40s}  (no tiles table)")

        src.close()

    total_tiles = sum(tile_counts.values())
    print(f"\n  Total tiles to copy: {total_tiles:,}\n")

    # --- Write merged metadata ---
    if first_metadata:
        if bounds_found:
            merged_bounds = f"{all_min_lon},{all_min_lat},{all_max_lon},{all_max_lat}"
            first_metadata["bounds"] = merged_bounds
            print(f"  Merged bounds:  {merged_bounds}")
            center_lon  = (all_min_lon + all_max_lon) / 2
            center_lat  = (all_min_lat + all_max_lat) / 2
            center_zoom = global_min_zoom if global_min_zoom < 99 else 0
            first_metadata["center"] = f"{center_lon},{center_lat},{center_zoom}"

        if global_min_zoom < 99:
            first_metadata["minzoom"] = str(global_min_zoom)
            first_metadata["maxzoom"] = str(global_max_zoom)
            print(f"  Merged zoom:    {global_min_zoom} → {global_max_zoom}")

        first_metadata["name"] = first_metadata.get(
            "name", os.path.splitext(os.path.basename(output))[0]
        )
        cur.executemany(
            "INSERT OR REPLACE INTO metadata VALUES (?,?)", first_metadata.items()
        )
        con.commit()
    else:
        print("  WARNING: No metadata found in any input file.")
    print()

    # --- Copy tiles with live progress bar ---
    def show_progress(grand_done, total_tiles, file_done, file_total, path, elapsed):
        pct       = grand_done / total_tiles * 100 if total_tiles else 0
        rate      = file_done / elapsed if elapsed > 0 else 0
        remaining = (file_total - file_done) / rate if rate > 0 else 0
        bar_len   = 28
        filled    = int(bar_len * pct / 100)
        bar       = '█' * filled + '░' * (bar_len - filled)
        sys.stdout.write(
            f"\r  [{bar}] {pct:5.1f}%  "
            f"{grand_done:,}/{total_tiles:,}  "
            f"{rate:,.0f} t/s  "
            f"ETA {int(remaining)}s"
            "     "
        )
        sys.stdout.flush()

    grand_done = 0

    for path in inputs:
        file_total = tile_counts.get(path, 0)
        file_done  = 0
        t0         = time.time()

        print(f"Copying: {os.path.basename(path)}  ({file_total:,} tiles)")

        try:
            src = sqlite3.connect(path)
        except sqlite3.OperationalError as e:
            print(f"  WARNING: Could not open: {e}")
            continue

        try:
            for row in src.execute(
                "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
            ):
                cur.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", tuple(row))
                file_done  += 1
                grand_done += 1

                if file_done % 500 == 0:
                    show_progress(grand_done, total_tiles, file_done,
                                  file_total, path, time.time() - t0)
                if grand_done % 1000 == 0:
                    con.commit()

        except sqlite3.OperationalError:
            print(f"\n  WARNING: No tiles table in {os.path.basename(path)}")
        finally:
            src.close()
            con.commit()

        elapsed = time.time() - t0
        show_progress(grand_done, total_tiles, file_done,
                      file_total, path, elapsed if elapsed > 0 else 0.001)
        rate = file_done / elapsed if elapsed > 0 else 0
        print(f"\n  Done: {file_done:,} tiles in {elapsed:.1f}s  ({rate:,.0f} t/s)\n")

    con.close()
    print(f"{'='*60}")
    print(f"Merge complete — {grand_done:,} total tiles written to {output}")
    print(f"{'='*60}")


# ── Entry point ───────────────────────────────────────────────────
if __name__ == "__main__":
    import glob

    args = sys.argv[1:]

    if len(args) == 0:
        folder = '.'
        inputs = sorted(glob.glob(os.path.join(folder, '*.mbtiles')))
        output = os.path.join(folder, 'merged.mbtiles')

    elif len(args) == 1 and os.path.isdir(args[0]):
        folder = args[0]
        inputs = sorted(glob.glob(os.path.join(folder, '*.mbtiles')))
        output = os.path.join(folder, 'merged.mbtiles')

    else:
        inputs = args[:-1]
        output = args[-1]

    inputs = [p for p in inputs if os.path.abspath(p) != os.path.abspath(output)]

    if not inputs:
        print("No input files found.")
        sys.exit(1)

    print(f"Found {len(inputs)} MBTiles file(s) to merge\n")
    merge_mbtiles(inputs, output)
