#!/usr/bin/env python3
"""
Build OsuSongData.json — Featured Artists only, ALL modes (osu/taiko/fruits/mania).
Single-pass (no final convert): normalize during paging.
Stores per-diff SR/AR/CS and hit/total length; separates mapper_tags vs user_tags.

Examples:
  python3 build_fa_pool.py --client-id XXX --client-secret YYY --out OsuSongData.json --checkpoint pool_ckpt.json --sleep 0.4 --verbose
  python3 build_fa_pool.py --client-id XXX --client-secret YYY --only-ranked --max-pages 5 --max-sets 500 --verbose
  python3 build_fa_pool.py --client-id XXX --client-secret YYY --user-tags --sleep 0.5 --verbose
"""

import argparse
import json
import os
import re
import sys
import time
import signal
from typing import Any, Optional, List, Dict

import requests

BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
FA_DIRECTORY_URL = "https://osu.ppy.sh/beatmaps/artists"


# ──────────────────────────────────────────────────────────────────────────────
# OAuth
# ──────────────────────────────────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────────────────────────────────
# I/O helpers
# ──────────────────────────────────────────────────────────────────────────────

def atomic_write_json(path: str, obj: Any, minify: bool = False) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if minify:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_json(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def to_year(dt) -> Optional[int]:
    if not dt:
        return None
    try:
        return int(str(dt)[:4])
    except Exception:
        return None


def normalize_mode(bm: dict) -> str:
    m = bm.get("mode")
    if m is not None:
        return m
    mi = bm.get("mode_int")
    return {0: "osu", 1: "taiko", 2: "fruits", 3: "mania"}.get(mi, "osu")


def split_mapper_tags(s: dict) -> List[str]:
    raw = (s.get("tags") or "").strip()
    if not raw:
        return []
    seen = set()
    out = []
    for t in raw.split():
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def extract_user_tags_from_payload(s: dict) -> List[str]:
    for key in ("user_tags", "community_tags", "lazer_user_tags"):
        tags = s.get(key)
        if isinstance(tags, list):
            return [t for t in tags if isinstance(t, str) and t]
    return []


def fetch_user_tags_detail(sess: requests.Session, token: str, set_id: int) -> List[str]:
    headers = {"Authorization": f"Bearer {token}"}
    r = sess.get(f"{BASE}/beatmapsets/{set_id}", headers=headers, timeout=30)
    if r.status_code == 429:
        time.sleep(1.5)
        r = sess.get(f"{BASE}/beatmapsets/{set_id}", headers=headers, timeout=30)
    r.raise_for_status()
    return extract_user_tags_from_payload(r.json())


# ──────────────────────────────────────────────────────────────────────────────
# Featured Artist directory scraping (robust local allowlist)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_fa_artist_names(sess: requests.Session, verbose: bool = False) -> set:
    r = sess.get(FA_DIRECTORY_URL, timeout=30)
    r.raise_for_status()
    html = r.text
    text = re.sub(r"<[^>]+>", " ", html)
    tokens = [t.strip() for t in re.split(r"\s+", text) if t.strip()]
    blacklist = {
        "Featured", "Artists", "artist", "artists", "osu!", "osu", "beatmap", "beatmaps",
        "news", "store", "help", "community", "rankings", "supporter", "download",
        "login", "register", "search", "team", "copyright", "policy", "terms", "privacy"
    }
    candidates = {t for t in tokens if (1 < len(t) <= 60 and t not in blacklist)}
    if verbose:
        print(f"[fa] scraped {len(candidates)} candidate tokens from directory", file=sys.stderr)
    return candidates


def is_fa_set(artist_name: str, fa_names_lower: set) -> bool:
    return (artist_name or "").strip().lower() in fa_names_lower


# ──────────────────────────────────────────────────────────────────────────────
# Paging over search (single-pass normalization)
# ──────────────────────────────────────────────────────────────────────────────

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


def normalize_set_all_modes(s: dict) -> Optional[dict]:
    """
    Normalize a beatmapset WITHOUT filtering modes.
    Store per-diff: id, mode (string), sr, ar, cs, hit_length, total_length.
    """
    diffs = []
    length_max = 0

    for bm in s.get("beatmaps", []) or []:
        mode = normalize_mode(bm)

        # lengths
        hit_len = bm.get("hit_length") or 0
        tot_len = bm.get("total_length") or 0
        try:
            hit_len = int(hit_len)
        except Exception:
            hit_len = 0
        try:
            tot_len = int(tot_len)
        except Exception:
            tot_len = 0
        if hit_len > length_max:
            length_max = hit_len

        # per-diff values
        try:
            diff_id = int(bm["id"])
        except Exception:
            continue

        sr = float(bm.get("difficulty_rating", 0.0) or 0.0)
        ar = float(bm.get("ar", 0.0) or 0.0)
        cs = float(bm.get("cs", 0.0) or 0.0)

        diffs.append({
            "id": diff_id,
            "mode": mode,           # "osu" | "taiko" | "fruits" | "mania"
            "sr": sr,
            "ar": ar,
            "cs": cs,
            "hit_length": hit_len,
            "total_length": tot_len,
        })

    if not diffs:
        return None

    mapper_tags = split_mapper_tags(s)
    user_tags = extract_user_tags_from_payload(s)
    creator = s.get("creator") or s.get("user", {}).get("username") or ""
    try:
        creator_id = int(s.get("user_id") or s.get("creator_id") or 0) or None
    except Exception:
        creator_id = None

    return {
        "id": int(s["id"]),
        "nsfw": bool(s.get("nsfw", False)),
        "artist": s.get("artist", "") or "",
        "title": s.get("title", "") or "",
        "status": s.get("status", "") or "",
        "ranked_date": to_year(s.get("ranked_date")),
        "length": int(length_max),
        "mapper_tags": mapper_tags,
        "user_tags": user_tags,
        "creator": creator,
        "creator_id": creator_id,
        "beatmaps": diffs,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main (single pass)
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Build OsuSongData.json (Featured Artists only, ALL modes), resumable, single-pass."
    )
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--out", default="OsuSongData.json", help="Final output JSON path")
    ap.add_argument("--checkpoint", default="pool_checkpoint.json", help="Checkpoint/resume file")
    ap.add_argument("--sleep", type=float, default=0.35, help="Seconds between page requests")
    ap.add_argument("--verbose", action="store_true", help="Print detailed progress")
    ap.add_argument("--minify", action="store_true", help="Minify output JSON (no spaces)")
    ap.add_argument("--user-tags", action="store_true",
                    help="Fetch per-set detail to populate user_tags when missing (slower)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="Stop after N pages per status (for testing)")
    ap.add_argument("--max-sets", type=int, default=None,
                    help="Stop after N kept sets (for testing)")
    ap.add_argument("--only-ranked", action="store_true",
                    help="Skip Loved; process Ranked only")
    args = ap.parse_args()

    # Resumable state now stores *normalized* sets directly (by_id_norm),
    # plus cursors/stats for paging.
    state = load_json(args.checkpoint) or {
        "done_statuses": [],
        "cursor_by_status": {},
        "by_id_norm": {},             # set_id(str) -> normalized set dict (FA-only)
        "stats": {
            "pages": {"r": 0, "l": 0},
            "seen_sets": 0,           # raw seen (before FA/normalize)
            "kept_sets": 0,           # FA+normalized kept
            "user_tags_lookups": 0,
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

        # Build FA allowlist once
        fa_names = fetch_fa_artist_names(sess, verbose=args.verbose)
        fa_names_lower = {n.lower() for n in fa_names}

        def process_status(code: str, label: str):
            if code in state["done_statuses"]:
                if args.verbose:
                    print(f"[{label}] already completed; skipping", file=sys.stderr)
                return

            start_cursor = state["cursor_by_status"].get(code)
            if args.verbose:
                print(f"[{label}] starting; cursor={start_cursor!r}", file=sys.stderr)

            q = f"status={code} nsfw=true"   # broad fetch; we'll filter locally by FA names
            pages_seen = 0

            for page_idx, sets, new_cursor in search_pages(
                sess, token, q=q, start_cursor=start_cursor, sleep_s=args.sleep, verbose=args.verbose
            ):
                pages_seen += 1
                state["stats"]["pages"][code] += 1
                state["cursor_by_status"][code] = new_cursor
                state["stats"]["seen_sets"] += len(sets)

                kept_this_page = 0

                for s in sets:
                    # Normalize now (single pass)
                    norm = normalize_set_all_modes(s)
                    if norm is None:
                        continue

                    # FA filter
                    if not is_fa_set(norm.get("artist", ""), fa_names_lower):
                        continue

                    # Optional user-tags backfill
                    if args.user_tags and not norm.get("user_tags"):
                        try:
                            tags_from_detail = fetch_user_tags_detail(sess, token, norm["id"])
                            if tags_from_detail:
                                norm["user_tags"] = tags_from_detail
                                state["stats"]["user_tags_lookups"] += 1
                                time.sleep(args.sleep)
                        except Exception as e:
                            if args.verbose:
                                print(f"[warn] user_tags lookup failed for set {norm['id']}: {e}", file=sys.stderr)

                    sid = str(norm["id"])
                    if sid not in state["by_id_norm"]:
                        state["by_id_norm"][sid] = norm
                        state["stats"]["kept_sets"] += 1
                        kept_this_page += 1

                    # Stop early if --max-sets reached
                    if args.max_sets and state["stats"]["kept_sets"] >= args.max_sets:
                        break

                if args.verbose:
                    print(f"[{label}] page {pages_seen}: seen+={len(sets)} kept+={kept_this_page} "
                          f"total_kept={state['stats']['kept_sets']} "
                          f"cursor={'<end>' if not new_cursor else '…'}", file=sys.stderr)

                save_checkpoint()

                if (args.max_pages and pages_seen >= args.max_pages) or \
                   (args.max_sets and state["stats"]["kept_sets"] >= args.max_sets) or \
                   interrupted["flag"]:
                    break

            if not interrupted["flag"]:
                if code not in state["done_statuses"]:
                    state["done_statuses"].append(code)
                save_checkpoint()
                if args.verbose:
                    print(f"[{label}] done. pages={state['stats']['pages'][code]} "
                          f"kept_total={state['stats']['kept_sets']}", file=sys.stderr)

        # Ranked
        process_status("r", "ranked")
        # Loved (optional)
        if not interrupted["flag"] and not args.only_ranked:
            process_status("l", "loved")

        # Write output directly from normalized FA-only sets
        converted = list(state["by_id_norm"].values())
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

        if args.verbose:
            print(f"[stats] seen_sets={state['stats']['seen_sets']} "
                  f"kept_sets={state['stats']['kept_sets']} "
                  f"user_tag_lookups={state['stats']['user_tags_lookups']}", file=sys.stderr)

    except KeyboardInterrupt:
        out_obj = [{"beatmapsets": list((load_json(args.checkpoint) or {}).get("by_id_norm", {}).values())}]
        atomic_write_json(args.out + ".partial.json", out_obj, minify=False)
        print(f"\n[write] interrupted; wrote partial -> {args.out+'.partial.json'}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
