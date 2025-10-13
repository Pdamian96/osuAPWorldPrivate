#!/usr/bin/env python3

import argparse
import json
import os
import sys
import time
import signal
import requests
from typing import Dict, Any, Optional

BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"

# ---------------- OAuth ----------------

def get_token(client_id: str, client_secret: str) -> str:
    r = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "public",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]

# ---------------- I/O helpers ----------------

def atomic_write_json(path: str, obj: Any, minify: bool = False) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if minify:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)

def load_json(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ---------------- Search paging ----------------

def search_pages(sess: requests.Session, token: str, q: str, start_cursor: Optional[str],
                 sleep_s: float, verbose: bool):
    headers = {"Authorization": f"Bearer {token}"}
    cursor = start_cursor
    page_idx = 0
    while True:
        params = {}
        if q:
            params["q"] = q
        if cursor:
            params["cursor_string"] = cursor

        r = sess.get(f"{BASE}/beatmapsets/search", headers=headers, params=params, timeout=30)
        if r.status_code == 429:
            if verbose:
                print("[rate] 429 received; backing off 1.5s", file=sys.stderr)
            time.sleep(1.5)
            continue
        r.raise_for_status()
        j = r.json()
        sets = j.get("beatmapsets", []) or []
        new_cursor = j.get("cursor_string")

        page_idx += 1
        yield page_idx, sets, new_cursor

        if not new_cursor:
            break
        cursor = new_cursor
        time.sleep(sleep_s)

# ---------------- Normalization ----------------

def to_year(ranked_date) -> Optional[int]:
    if not ranked_date:
        return None
    try:
        return int(str(ranked_date)[:4])
    except Exception:
        return None

def normalize_mode(bm: dict) -> str:
    m = bm.get("mode")
    if m is not None:
        return m
    mi = bm.get("mode_int")
    return {0: "osu", 1: "taiko", 2: "fruits", 3: "mania"}.get(mi, "osu")

def normalize_set_osu_only(s: dict) -> Optional[dict]:
    """
    Keep only diffs where mode == 'osu'. If none remain, return None (drop set).
    Compute set 'length' as max seconds across remaining diffs.
    """
    diffs = []
    length = 0
    for bm in s.get("beatmaps", []) or []:
        mode = normalize_mode(bm)
        if mode != "osu":
            continue
        sec = bm.get("hit_length") or bm.get("total_length") or 0
        try:
            sec = int(sec)
        except Exception:
            sec = 0
        if sec > length:
            length = sec
        diffs.append({
            "mode": "osu",
            "sr": float(bm.get("difficulty_rating", 0.0)),
            "id": int(bm["id"]),
        })

    if not diffs:
        return None 

    return {
        "id": int(s["id"]),
        "nsfw": bool(s.get("nsfw", False)),
        "artist": s.get("artist", "") or "",
        "title": s.get("title", "") or "",
        "status": s.get("status", "") or "",
        "ranked_date": to_year(s.get("ranked_date")),
        "length": int(length),
        "beatmaps": diffs,
    }

# ---------------- Main builder ----------------

def main():
    ap = argparse.ArgumentParser(description="Build OsuSongData.json (Ranked+Loved, osu!standard only), resumable.")
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--out", default="OsuSongData.json", help="Final output JSON path")
    ap.add_argument("--checkpoint", default="pool_checkpoint.json", help="Checkpoint/resume file")
    ap.add_argument("--sleep", type=float, default=0.35, help="Seconds between page requests")
    ap.add_argument("--verbose", action="store_true", help="Print detailed progress")
    ap.add_argument("--minify", action="store_true", help="Minify output JSON (no spaces)")
    args = ap.parse_args()

    state = load_json(args.checkpoint) or {
        "done_statuses": [],
        "cursor_by_status": {},     
        "by_id": {},                
        "stats": {                  
            "pages": {"r": 0, "l": 0},
            "seen_sets": 0,
            "unique_sets": 0,
            "total_osu_diffs": 0,  
            "sets_without_osu": 0,  
        },
    }

    def save_checkpoint():
        atomic_write_json(args.checkpoint, state, minify=False)
        if args.verbose:
            print(f"[ckpt] saved -> {args.checkpoint}", file=sys.stderr)

    interrupted = {"flag": False}

    def handle_sigint(sig, frame):
        interrupted["flag"] = True
        print("\n[interrupt] Ctrl+C detected; finalizing partial data...", file=sys.stderr)

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        token = get_token(args.client_id, args.client_secret)
        sess = requests.Session()

        def process_status(code: str, label: str):
            if code in state["done_statuses"]:
                if args.verbose:
                    print(f"[{label}] already completed; skipping", file=sys.stderr)
                return

            start_cursor = state["cursor_by_status"].get(code)
            if args.verbose:
                print(f"[{label}] starting; cursor={start_cursor!r}", file=sys.stderr)

            for page_idx, sets, new_cursor in search_pages(
                sess, token, q=f"status={code}", start_cursor=start_cursor,
                sleep_s=args.sleep, verbose=args.verbose
            ):
                for s in sets:
                    sid = str(s["id"])
                    if sid not in state["by_id"]:
                        state["by_id"][sid] = s
                state["stats"]["pages"][code] += 1
                state["stats"]["seen_sets"] += len(sets)
                state["stats"]["unique_sets"] = len(state["by_id"])
                state["cursor_by_status"][code] = new_cursor
                if args.verbose:
                    print(f"[{label}] page={state['stats']['pages'][code]} "
                          f"seen+={len(sets)} total_seen={state['stats']['seen_sets']} "
                          f"unique_sets={state['stats']['unique_sets']} "
                          f"cursor={'<end>' if not new_cursor else '…'}", file=sys.stderr)

                if (state["stats"]["pages"][code] % 20 == 0) or interrupted["flag"]:
                    save_checkpoint()

                if interrupted["flag"]:
                    break

            if not interrupted["flag"]:
                if code not in state["done_statuses"]:
                    state["done_statuses"].append(code)
                save_checkpoint()
                if args.verbose:
                    print(f"[{label}] done. pages={state['stats']['pages'][code]} "
                          f"unique_sets={state['stats']['unique_sets']}", file=sys.stderr)

        process_status("r", "ranked")
        if not interrupted["flag"]:
            process_status("l", "loved")

        if args.verbose:
            print("[convert] filtering to osu!standard diffs and computing lengths…", file=sys.stderr)

        converted = []
        sets_without_osu = 0
        osu_diff_count = 0
        for s in state["by_id"].values():
            norm = normalize_set_osu_only(s)
            if norm is None:
                sets_without_osu += 1
                continue
            osu_diff_count += len(norm["beatmaps"])
            converted.append(norm)

        state["stats"]["total_osu_diffs"] = osu_diff_count
        state["stats"]["sets_without_osu"] = sets_without_osu

        if args.verbose:
            print(f"[convert] kept_sets={len(converted)} "
                  f"sets_without_osu={sets_without_osu} "
                  f"total_osu_diffs={osu_diff_count}", file=sys.stderr)

        out_obj = [{"beatmapsets": converted}]

        if interrupted["flag"]:
            partial = args.out + ".partial.json"
            atomic_write_json(partial, out_obj, minify=args.minify)
            save_checkpoint()
            print(f"[write] interrupted; wrote partial -> {partial}", file=sys.stderr)
        else:
            atomic_write_json(args.out, out_obj, minify=args.minify)
            save_checkpoint()
            print(f"[write] {args.out} with {len(converted)} beatmapsets", file=sys.stderr)

    except KeyboardInterrupt:
        partial = args.out + ".partial.json"
        out_obj = [{"beatmapsets": [
            x for x in (normalize_set_osu_only(s) for s in (state["by_id"] or {}).values())
            if x is not None
        ]}]
        atomic_write_json(partial, out_obj, minify=args.minify)
        save_checkpoint()
        print(f"\n[write] interrupted (KeyboardInterrupt); wrote partial -> {partial}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
