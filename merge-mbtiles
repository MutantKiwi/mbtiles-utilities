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

    # Exclude output from inputs if it somehow appears in the list
    inputs = [p for p in inputs if os.path.abspath(p) != os.path.abspath(output)]

    # Remove existing output so we start clean
    if os.path.exists(output):
        os.remove(output)
        print(f"Removed existing: {output}")

    con = sqlite3.connect(output)
    cur = con.cursor()

    cur.executescript("""
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

    # --- Collect metadata from all inputs to compute merged bounds/zoom ---
    all_min_lon, all_min_lat, all_max_lon, all_max_lat = 180, 90, -180, -90
    global_min_zoom, global_max_zoom = 99, 0
    first_metadata = {}

    print("Reading metadata from input files...")
    for path in inputs:
        src = sqlite3.connect(path)
        src.row_factory = sqlite3.Row

        # Read metadata — gracefully skip files with no metadata table
        try:
            meta = {row["name"]: row["value"]
                    for row in src.execute("SELECT name, value FROM metadata")}
        except sqlite3.OperationalError:
            print(f"  WARNING: No metadata table in {os.path.basename(path)} — skipping metadata.")
            meta = {}

        # Keep metadata from the first file that has it as the base
        if not first_metadata and meta:
            first_metadata = meta

        # Expand bounds to cover all input files
        if "bounds" in meta:
            try:
                min_lon, min_lat, max_lon, max_lat = map(float, meta["bounds"].split(","))
                all_min_lon = min(all_min_lon, min_lon)
                all_min_lat = min(all_min_lat, min_lat)
                all_max_lon = max(all_max_lon, max_lon)
                all_max_lat = max(all_max_lat, max_lat)
            except ValueError:
                pass

        # Expand zoom range
        if "minzoom" in meta:
            global_min_zoom = min(global_min_zoom, int(meta["minzoom"]))
        if "maxzoom" in meta:
            global_max_zoom = max(global_max_zoom, int(meta["maxzoom"]))

        src.close()

    # --- Write merged metadata to output ---
    if first_metadata:
        first_metadata["bounds"]  = f"{all_min_lon},{all_min_lat},{all_max_lon},{all_max_lat}"
        first_metadata["minzoom"] = str(global_min_zoom)
        first_metadata["maxzoom"] = str(global_max_zoom)
        first_metadata["name"]    = first_metadata.get("name", os.path.splitext(os.path.basename(output))[0])
        cur.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", first_metadata.items())
        con.commit()
        print(f"  Merged bounds:  {all_min_lon},{all_min_lat},{all_max_lon},{all_max_lat}")
        print(f"  Merged zoom:    {global_min_zoom} → {global_max_zoom}")
    else:
        print("  WARNING: No metadata found in any input file.")
    print()

    # --- Copy tiles from each input file ---
    total = 0
    for path in inputs:
        src   = sqlite3.connect(path)
        count = 0

        try:
            for row in src.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"):
                cur.execute("INSERT OR REPLACE INTO tiles VALUES (?,?,?,?)", tuple(row))
                count += 1
                total += 1
                if total % 1000 == 0:
                    con.commit()
        except sqlite3.OperationalError:
            print(f"  WARNING: No tiles table in {os.path.basename(path)} — skipping.")

        src.close()
        print(f"  Merged {count:>6} tiles  ←  {os.path.basename(path)}")

    con.commit()
    con.close()
    print(f"\nDone — {total} total tiles written to {output}")
