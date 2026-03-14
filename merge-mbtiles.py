import os
import sqlite3
import sys
import time
import glob


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

    # SPEED: journal_mode=OFF + synchronous=OFF is fastest for bulk insert.
    # Index is created AFTER all tiles are inserted — building the index
    # incrementally (row by row into an indexed table) is ~2x slower.
    cur.executescript("""
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous  = OFF;
        PRAGMA cache_size   = -65536;
        CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT);
        CREATE TABLE IF NOT EXISTS tiles (
            zoom_level  INTEGER,
            tile_column INTEGER,
            tile_row    INTEGER,
            tile_data   BLOB
        );
    """)

    # --- Collect metadata and tile counts ---
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
            print(f"  {os.path.basename(path):50s}  {count:>9,} tiles")
        except sqlite3.OperationalError:
            tile_counts[path] = 0
            print(f"  {os.path.basename(path):50s}  (no tiles table)")

        src.close()

    total_tiles = sum(tile_counts.values())
    print(f"\n  Total tiles to copy: {total_tiles:,}\n")

    # --- Write merged metadata ---
    if first_metadata:
        if bounds_found:
            merged_bounds = f"{all_min_lon},{all_min_lat},{all_max_lon},{all_max_lat}"
            first_metadata["bounds"] = merged_bounds
            print(f"  Merged bounds:  {merged_bounds}")
            first_metadata["center"] = (
                f"{(all_min_lon+all_max_lon)/2},"
                f"{(all_min_lat+all_max_lat)/2},"
                f"{global_min_zoom if global_min_zoom < 99 else 0}"
            )

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

    # --- Copy tiles ---
    # SPEED: executemany() with batches is significantly faster than
    # individual execute() calls. Single BEGIN...COMMIT avoids the overhead
    # of committing every N rows. Index is built after all data is loaded.
    BATCH_SIZE = 10_000
    grand_done = 0
    t_start    = time.time()
    last_print = time.time()

    def show_progress(done, total, rate, eta):
        pct    = done / total * 100 if total else 0
        filled = int(28 * pct / 100)
        bar    = '█' * filled + '░' * (28 - filled)
        sys.stdout.write(
            f"\r  [{bar}] {pct:5.1f}%  {done:,}/{total:,}  "
            f"{rate:,.0f} t/s  ETA {int(eta)}s     "
        )
        sys.stdout.flush()

    cur.execute("BEGIN")

    for path in inputs:
        file_total = tile_counts.get(path, 0)
        file_done  = 0
        t_file     = time.time()
        print(f"Copying: {os.path.basename(path)}  ({file_total:,} tiles)")

        try:
            src = sqlite3.connect(path)
        except sqlite3.OperationalError as e:
            print(f"  WARNING: Could not open: {e}")
            continue

        try:
            batch = []
            for row in src.execute(
                "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
            ):
                batch.append(tuple(row))
                file_done  += 1
                grand_done += 1

                if len(batch) >= BATCH_SIZE:
                    cur.executemany(
                        "INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", batch
                    )
                    batch = []

                    # Throttle progress to once per second — flush is slow on Windows
                    now = time.time()
                    if now - last_print >= 1.0:
                        last_print = now
                        elapsed = now - t_start
                        rate    = grand_done / elapsed if elapsed > 0 else 0
                        eta     = (total_tiles - grand_done) / rate if rate > 0 else 0
                        show_progress(grand_done, total_tiles, rate, eta)

            # flush remaining batch
            if batch:
                cur.executemany(
                    "INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", batch
                )

        except sqlite3.OperationalError:
            print(f"\n  WARNING: No tiles table in {os.path.basename(path)}")
        finally:
            src.close()

        elapsed_file = time.time() - t_file
        rate_file    = file_done / elapsed_file if elapsed_file > 0 else 0
        elapsed_all  = time.time() - t_start
        rate_all     = grand_done / elapsed_all if elapsed_all > 0 else 0
        eta          = (total_tiles - grand_done) / rate_all if rate_all > 0 else 0
        show_progress(grand_done, total_tiles, rate_all, eta)
        print(f"\n  Done: {file_done:,} tiles in {elapsed_file:.1f}s  ({rate_file:,.0f} t/s)\n")

    # Commit all tiles then build the index once — much faster than
    # maintaining the index incrementally during insert
    print("Committing...", end=' ', flush=True)
    con.execute("COMMIT")
    print("done")

    print("Building tile index...", end=' ', flush=True)
    con.executescript(
        "CREATE UNIQUE INDEX IF NOT EXISTS tile_index "
        "ON tiles (zoom_level, tile_column, tile_row);"
    )
    print("done")

    con.close()

    elapsed = time.time() - t_start
    rate    = grand_done / elapsed if elapsed > 0 else 0
    print(f"\n{'='*60}")
    print(f"  Merge complete")
    print(f"  Tiles   : {grand_done:,}")
    print(f"  Time    : {elapsed:.1f}s  ({rate:,.0f} t/s avg)")
    print(f"  Output  : {output}")
    print(f"{'='*60}")


# ── Entry point ───────────────────────────────────────────────────
if __name__ == "__main__":
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
