import os
import sys
import csv
import sqlite3
import datetime

# Only these fields may be updated — all others in the CSV are ignored
UPDATABLE_FIELDS = [
    "name",
    "description",
    "legend",
    "attribution",
    "type",
    "version",
    "generator",
]

# Valid values for the "type" field
VALID_TYPES = {"overlay", "baselayer"}

# Today's date in YYYYMMDD format — written automatically on every update
TODAY = datetime.date.today().strftime("%Y%m%d")


def validate_row(row):
    """
    Validate the updatable fields in a CSV row.
    Returns (is_valid, list_of_errors).
    """
    errors = []

    # "type" must be overlay or baselayer if provided
    type_val = row.get("type", "").strip()
    if type_val and type_val not in VALID_TYPES:
        errors.append(f"  'type' must be 'overlay' or 'baselayer', got: '{type_val}'")

    # All fields except attribution must be plain strings (no HTML tags)
    html_indicator = "<"
    plain_fields = ["name", "description", "legend", "version", "generator", "type"]
    for field in plain_fields:
        val = row.get(field, "").strip()
        if val and html_indicator in val:
            errors.append(f"  '{field}' must be plain text (no HTML), got: '{val[:60]}...'")

    return len(errors) == 0, errors


def build_updates(row):
    """
    Extract only the updatable fields from a CSV row that have a non-empty value.
    Returns a dict of {field: value} ready to write.
    """
    updates = {}
    for field in UPDATABLE_FIELDS:
        val = row.get(field, "").strip()
        if val:
            updates[field] = val
    return updates


def update_mbtiles(path, updates):
    """
    Write the provided metadata updates into an MBTiles file.
    Also stamps the 'date' field with today's date (YYYYMMDD).
    Only UPDATABLE_FIELDS are ever touched — all other metadata is preserved.
    """
    con = sqlite3.connect(path)
    cur = con.cursor()

    # Ensure metadata table exists
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)")
    except sqlite3.OperationalError as e:
        con.close()
        raise RuntimeError(f"Cannot access metadata table: {e}")

    # Always update the date field with today's date
    updates_with_date = {**updates, "date": TODAY}

    changed = []
    for key, value in updates_with_date.items():
        # Check if key already exists
        existing = cur.execute(
            "SELECT value FROM metadata WHERE name = ?", (key,)
        ).fetchone()

        if existing is None:
            cur.execute("INSERT INTO metadata (name, value) VALUES (?, ?)", (key, value))
            changed.append(f"  + {key} = {value}")
        elif existing[0] != value:
            cur.execute("UPDATE metadata SET value = ? WHERE name = ?", (value, key))
            changed.append(f"  ~ {key}: '{existing[0]}' → '{value}'")
        else:
            # Value unchanged — skip
            pass

    con.commit()
    con.close()
    return changed


def update_pmtiles(path, updates):
    """
    PMTiles files are immutable binary archives — metadata cannot be
    written back in-place. We report what would have been updated so
    the user is aware, but no file is modified.
    """
    notice = [
        "  ! PMTiles files are read-only binary archives.",
        "  ! Metadata cannot be updated in-place.",
        "  ! Convert to MBTiles first, update there, then re-export if needed.",
    ]
    for field, value in updates.items():
        notice.append(f"  ? Would set: {field} = {value}")
    return notice


def process_csv(csv_path):
    """
    Read the metadata CSV and apply updates to each referenced tile file.
    The CSV must contain a 'source_file' column identifying each tile file.
    Tile files are expected to be in the same folder as the CSV.
    """
    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found: {csv_path}")
        sys.exit(1)

    csv_dir = os.path.dirname(os.path.abspath(csv_path))

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        if "source_file" not in (reader.fieldnames or []):
            print("Error: CSV must contain a 'source_file' column.")
            sys.exit(1)

        rows = list(reader)

    if not rows:
        print("CSV is empty — nothing to do.")
        return

    print(f"Processing {len(rows)} row(s) from: {csv_path}")
    print(f"Date stamp that will be applied: {TODAY}\n")
    print("=" * 60)

    total_ok      = 0
    total_skipped = 0
    total_errors  = 0

    for row in rows:
        source_file = row.get("source_file", "").strip()

        if not source_file:
            print("SKIP — row has no source_file value.")
            total_skipped += 1
            continue

        tile_path = os.path.join(csv_dir, source_file)
        ext       = os.path.splitext(source_file)[1].lower()

        print(f"File: {source_file}")

        # Check the file exists
        if not os.path.exists(tile_path):
            print(f"  ERROR: File not found at {tile_path}")
            total_errors += 1
            print()
            continue

        # Only process known tile formats
        if ext not in (".mbtiles", ".pmtiles"):
            print(f"  SKIP: Unrecognised file type '{ext}'")
            total_skipped += 1
            print()
            continue

        # Validate the updatable fields in this row
        is_valid, validation_errors = validate_row(row)
        if not is_valid:
            print("  VALIDATION FAILED — skipping this file:")
            for err in validation_errors:
                print(err)
            total_errors += 1
            print()
            continue

        # Extract only the fields we are allowed to update
        updates = build_updates(row)

        if not updates:
            print("  SKIP: No updatable fields have values in this row.")
            total_skipped += 1
            print()
            continue

        print(f"  Updating {len(updates)} field(s) + date stamp:")

        try:
            if ext == ".mbtiles":
                changed = update_mbtiles(tile_path, updates)
            elif ext == ".pmtiles":
                changed = update_pmtiles(tile_path, updates)

            if changed:
                for line in changed:
                    print(line)
            else:
                print("  No changes needed — all values already match.")

            total_ok += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            total_errors += 1

        print()

    # Summary
    print("=" * 60)
    print(f"Done.")
    print(f"  Updated : {total_ok}")
    print(f"  Skipped : {total_skipped}")
    print(f"  Errors  : {total_errors}")


# --- Entry point ---
# Usage:
#   python update_metadata.py                          ← looks for metadata_export.csv in current folder
#   python update_metadata.py metadata_export.csv      ← explicit CSV path
#   python update_metadata.py "C:\path\to\export.csv"  ← full path

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Default: look for metadata_export.csv in the current directory
        default_csv = os.path.join(".", "metadata_export.csv")
        process_csv(default_csv)

    elif len(sys.argv) == 2:
        process_csv(sys.argv[1])

    else:
        print("Usage: python update_metadata.py [path/to/metadata_export.csv]")
        sys.exit(1)
