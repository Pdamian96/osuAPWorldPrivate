#!/usr/bin/env python3
import json, sys, os

def collect_ids(obj, out):
    """
    Recursively walk any nested structure and collect values under 'id'
    for items that look like beatmapsets (dicts that have 'beatmaps' or
    'artist'/'title' keys). This makes us robust to odd shapes.
    """
    if isinstance(obj, dict):
        # If this dict looks like a beatmapset, take its 'id'
        if "id" in obj and (
            "beatmaps" in obj or
            ("artist" in obj and "title" in obj) or
            ("status" in obj and "ranked_date" in obj)
        ):
            try:
                out.add(int(obj["id"]))
            except Exception:
                pass
        # If it has a 'beatmapsets' key, scan that
        if "beatmapsets" in obj and isinstance(obj["beatmapsets"], (list, tuple)):
            for x in obj["beatmapsets"]:
                collect_ids(x, out)
        # Recurse all values
        for v in obj.values():
            collect_ids(v, out)

    elif isinstance(obj, (list, tuple)):
        for x in obj:
            collect_ids(x, out)

def main():
    if len(sys.argv) < 3:
        print("Usage: python extract_allowlist.py OsuSongData.json allowed_sets.txt", file=sys.stderr)
        sys.exit(2)

    src = sys.argv[1]
    dst = sys.argv[2]

    if not os.path.isfile(src):
        print(f"ERROR: file not found: {src}", file=sys.stderr)
        sys.exit(2)

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    ids = set()
    collect_ids(data, ids)
    if not ids:
        print("WARNING: no IDs found. Check the JSON format.", file=sys.stderr)

    with open(dst, "w", encoding="utf-8") as f:
        for i in sorted(ids):
            f.write(f"{i}\n")

    print(f"Wrote {len(ids)} IDs to {dst}")

if __name__ == "__main__":
    main()
