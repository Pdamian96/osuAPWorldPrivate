#!/usr/bin/env python3
from __future__ import annotations
import json, os, random, sys, threading, time
from typing import Iterable, List, Optional, Tuple, Set

import requests
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"

# ──────────────────────────────────────────────────────────────────────────────
# OAuth
# ──────────────────────────────────────────────────────────────────────────────
class OsuAuth:
    def __init__(self, client_id: str, client_secret: str, scope: str = "public"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.access_token: Optional[str] = None
        self.expires_at: float = 0.0

    def _request_token(self) -> Tuple[str, float]:
        r = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self.scope,
            },
            timeout=20,
        )
        if not r.ok:
            raise RuntimeError(f"OAuth failed: {r.status_code} {r.text}")
        j = r.json()
        token = j["access_token"]
        expires_in = float(j.get("expires_in", 3600))
        return token, time.time() + expires_in - 30

    def get_token(self) -> str:
        if not self.access_token or time.time() >= self.expires_at:
            self.access_token, self.expires_at = self._request_token()
        return self.access_token

# ──────────────────────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────────────────────
def as_int(x, default=None):
    try:
        if x is None or x == "": return default
        return int(x)
    except Exception:
        return default

def as_float(x, default=None):
    try:
        if x is None or x == "": return default
        return float(x)
    except Exception:
        return default

def as_bool(x, default=False):
    if isinstance(x, bool): return x
    if isinstance(x, str):
        v = x.strip().lower()
        if v in {"1","true","yes","on"}: return True
        if v in {"0","false","no","off"}: return False
    return default

def as_str_list(x):
    if x is None: return []
    if isinstance(x, list): return [str(i) for i in x]
    if isinstance(x, str):
        return [t.strip() for t in x.split(",") if t.strip()]
    return []

def load_allowlist_txt(path: Optional[str]) -> Set[int]:
    if not path: return set()
    ids: Set[int] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip().strip('"\'')
            if not s: continue
            try: ids.add(int(s))
            except ValueError: pass
    return ids

def build_query(min_len, max_len, min_stars, max_stars, status, tags, user_tags, free_text) -> str:
    parts: List[str] = []
    if min_len is not None: parts.append(f"length>={int(min_len)}")
    if max_len is not None: parts.append(f"length<={int(max_len)}")
    if min_stars is not None and max_stars is not None:
        parts.append(f"stars={min_stars}..{max_stars}")
    else:
        if min_stars is not None: parts.append(f"stars>={min_stars}")
        if max_stars is not None: parts.append(f"stars<={max_stars}")
    if status: parts.append(f"status={status}")  # r/a/l/q/p/w/g
    for t in tags or []:
        t = (t or "").strip()
        if t: parts.append(f'"{t}"' if " " in t else t)
    for ut in user_tags or []:
        ut = (ut or "").strip()
        if ut: parts.append(f'tag="{ut}"')
    if free_text: parts.append(free_text.strip())
    return " ".join(parts)

def search_beatmapsets(session: requests.Session, token: str, q: str, page_limit: int, pause_sec: float = 0.2) -> Iterable[List[dict]]:
    headers = {"Authorization": f"Bearer {token}"}
    cursor: Optional[str] = None
    pages = 0
    while True:
        params = {}
        if q: params["q"] = q
        if cursor: params["cursor_string"] = cursor
        r = session.get(f"{BASE}/beatmapsets/search", headers=headers, params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(1.0); continue
        if not r.ok:
            raise RuntimeError(f"search failed: {r.status_code} {r.text}")
        data = r.json()
        yield data.get("beatmapsets", []) or []
        cursor = data.get("cursor_string")
        pages += 1
        if not cursor or pages >= page_limit: break
        time.sleep(pause_sec)

def is_mode_allowed(bm: dict, allowed_modes: Set[str]) -> bool:
    m = bm.get("mode")
    if m is None:
        mi = bm.get("mode_int")
        m = {0:"osu", 1:"taiko", 2:"fruits", 3:"mania"}.get(mi, None)
    return (m in allowed_modes)

def within_bounds(seconds, stars, min_len, max_len, min_stars, max_stars) -> bool:
    if seconds is None or stars is None: return False
    if min_len  is not None and seconds <  min_len:  return False
    if max_len  is not None and seconds >  max_len:  return False
    if min_stars is not None and stars   <  min_stars: return False
    if max_stars is not None and stars   >  max_stars: return False
    return True

def collect_candidates_from_search(
    pages, min_len, max_len, min_stars, max_stars, one_per_set,
    allow: Set[int], allowed_modes: Set[str], verbose=False
) -> List[dict]:
    out: List[dict] = []
    total_seen, total_kept = 0, 0
    for sets in pages:
        page_kept = 0
        for s in sets:
            total_seen += 1
            if allow and s.get("id") not in allow:
                continue
            taken = False
            for bm in s.get("beatmaps", []) or []:
                if not is_mode_allowed(bm, allowed_modes):
                    continue
                seconds = bm.get("hit_length") or bm.get("total_length")
                stars = bm.get("difficulty_rating") or bm.get("stars")
                if within_bounds(seconds, stars, min_len, max_len, min_stars, max_stars):
                    out.append({
                        "id": bm.get("id"),
                        "set_id": s.get("id"),
                        "seconds": int(seconds or 0),
                        "stars": float(stars or 0.0),
                        "mapper": s.get("creator"),
                        "artist": s.get("artist"),
                        "title": s.get("title"),
                        "version": bm.get("version"),
                    })
                    page_kept += 1
                    if one_per_set:
                        taken = True
                        break
            if one_per_set and taken:
                continue
        total_kept += page_kept
        if verbose:
            print(f"[page] seen={total_seen} kept={total_kept} pool={len(out)}", file=sys.stderr)
    return out

def sample_weighted(items: List[dict], k: int, strategy: str, min_len: Optional[int], max_len: Optional[int]) -> List[dict]:
    if not items: return []
    if strategy == "uniform" or (min_len is None and max_len is None):
        random.shuffle(items)
        return items[:k]
    if min_len is None or max_len is None:
        random.shuffle(items)
        return items[:k]
    mid = 0.5 * (min_len + max_len)
    scored = []
    for x in items:
        dist = abs(x["seconds"] - mid)
        w = 1.0 / (1.0 + dist)
        u = random.random()
        key = u ** (1.0 / max(w, 1e-6))
        scored.append((key, x))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [x for _, x in scored[:k]]

# ──────────────────────────────────────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────────────────────────────────────
class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master = master
        self.pack(fill="both", expand=True, padx=10, pady=10)
        self._last_dir = os.getcwd()
        self.build_ui()
        self.bind_shortcuts()

    # --- UI build -------------------------------------------------------------
    def build_ui(self):
        # Credentials
        creds = ttk.LabelFrame(self, text="osu! OAuth")
        creds.grid(row=0, column=0, sticky="we")
        ttk.Label(creds, text="Client ID").grid(row=0, column=0, sticky="w")
        self.client_id_var = tk.StringVar(value="")
        ttk.Entry(creds, textvariable=self.client_id_var, width=20).grid(row=0, column=1, sticky="w", padx=(4,12))

        ttk.Label(creds, text="Client Secret").grid(row=0, column=2, sticky="w")
        self.client_secret_var = tk.StringVar(value="")
        ttk.Entry(creds, textvariable=self.client_secret_var, width=36, show="•").grid(row=0, column=3, sticky="w", padx=(4,0))

        # Picker basics
        g = ttk.LabelFrame(self, text="Picker")
        g.grid(row=1, column=0, sticky="nsew", padx=0, pady=(6,0))

        ttk.Label(g, text="Count").grid(row=0, column=0, sticky="w")
        self.count_var = tk.IntVar(value=5)
        ttk.Entry(g, textvariable=self.count_var, width=6).grid(row=0, column=1, sticky="w", padx=(2,10))

        ttk.Label(g, text="Random Strategy").grid(row=0, column=2, sticky="w")
        self.strategy_var = tk.StringVar(value="uniform")
        ttk.Combobox(g, values=["uniform", "peak-mid-length"], textvariable=self.strategy_var, width=16, state="readonly").grid(row=0, column=3, sticky="w")

        ttk.Label(g, text="Min Length (s)").grid(row=1, column=0, sticky="w")
        self.minlen_var = tk.StringVar(value="")
        ttk.Entry(g, textvariable=self.minlen_var, width=8).grid(row=1, column=1, sticky="w", padx=(2,10))
        ttk.Label(g, text="Max Length (s)").grid(row=1, column=2, sticky="w")
        self.maxlen_var = tk.StringVar(value="")
        ttk.Entry(g, textvariable=self.maxlen_var, width=8).grid(row=1, column=3, sticky="w")

        ttk.Label(g, text="Min SR").grid(row=2, column=0, sticky="w")
        self.minstar_var = tk.StringVar(value="")
        ttk.Entry(g, textvariable=self.minstar_var, width=8).grid(row=2, column=1, sticky="w", padx=(2,10))
        ttk.Label(g, text="Max SR").grid(row=2, column=2, sticky="w")
        self.maxstar_var = tk.StringVar(value="")
        ttk.Entry(g, textvariable=self.maxstar_var, width=8).grid(row=2, column=3, sticky="w")

        ttk.Label(g, text="Status").grid(row=3, column=0, sticky="w")
        self.status_var = tk.StringVar(value="")
        ttk.Combobox(g, values=["", "r","a","l","q","p","w","g"], textvariable=self.status_var, width=6, state="readonly").grid(row=3, column=1, sticky="w", padx=(2,10))

        self.one_set_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(g, text="One per set", variable=self.one_set_var).grid(row=3, column=2, sticky="w", columnspan=2)

        ttk.Label(g, text="Mapper Tags").grid(row=4, column=0, sticky="w")
        self.tags_var = tk.StringVar(value="")
        ttk.Entry(g, textvariable=self.tags_var, width=28).grid(row=4, column=1, columnspan=3, sticky="we")

        ttk.Label(g, text='User Tags').grid(row=5, column=0, sticky="w")
        self.user_tags_var = tk.StringVar(value="")
        ttk.Entry(g, textvariable=self.user_tags_var, width=28).grid(row=5, column=1, columnspan=3, sticky="we")

        ttk.Label(g, text="Free text").grid(row=6, column=0, sticky="w")
        self.text_var = tk.StringVar(value="")
        ttk.Entry(g, textvariable=self.text_var, width=28).grid(row=6, column=1, columnspan=3, sticky="we")

        # Modes
        modes = ttk.LabelFrame(self, text="Modes")
        modes.grid(row=2, column=0, sticky="we", pady=(6,0))
        self.mode_osu    = tk.BooleanVar(value=True)
        self.mode_taiko  = tk.BooleanVar(value=False)
        self.mode_fruits = tk.BooleanVar(value=False)
        self.mode_mania  = tk.BooleanVar(value=False)
        ttk.Checkbutton(modes, text="osu (standard)", variable=self.mode_osu).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(modes, text="taiko",            variable=self.mode_taiko).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(modes, text="fruits (catch)",   variable=self.mode_fruits).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(modes, text="mania",            variable=self.mode_mania).grid(row=0, column=3, sticky="w")

        # Advanced
        adv = ttk.LabelFrame(self, text="Advanced")
        adv.grid(row=3, column=0, sticky="we", pady=(6,0))
        ttk.Label(adv, text="Pages").grid(row=0, column=0, sticky="w")
        self.pages_var = tk.StringVar(value="25")
        ttk.Entry(adv, textvariable=self.pages_var, width=6).grid(row=0, column=1, sticky="w", padx=(2,10))

        ttk.Label(adv, text="Pool ×").grid(row=0, column=2, sticky="w")
        self.pool_var = tk.StringVar(value="4.0")
        ttk.Entry(adv, textvariable=self.pool_var, width=6).grid(row=0, column=3, sticky="w", padx=(2,10))

        ttk.Label(adv, text="Seed").grid(row=0, column=4, sticky="w")
        self.seed_var = tk.StringVar(value="")
        ttk.Entry(adv, textvariable=self.seed_var, width=10).grid(row=0, column=5, sticky="w")

        self.verbose_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(adv, text="Verbose", variable=self.verbose_var).grid(row=0, column=6, sticky="w", padx=(10,0))
        self.enforce_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(adv, text="Enforce count", variable=self.enforce_var).grid(row=0, column=7, sticky="w")

        # Allowlist
        allow = ttk.LabelFrame(self, text="Allowlist")
        allow.grid(row=4, column=0, sticky="we", pady=(6,0))
        self.allow_path = tk.StringVar(value="")
        ttk.Entry(allow, textvariable=self.allow_path, width=60).grid(row=0, column=0, sticky="we")
        ttk.Button(allow, text="Browse...", command=self.pick_allow).grid(row=0, column=1, padx=5)
        ttk.Label(allow, text="(txt: one set_id per line)").grid(row=0, column=2, sticky="w")

        # Buttons
        btns = ttk.Frame(self)
        btns.grid(row=5, column=0, sticky="we", pady=(6,0))
        ttk.Button(btns, text="Pick Beatmaps", command=self.start_pick).grid(row=0, column=0, padx=(0,6))
        ttk.Button(btns, text="Copy Output", command=self.copy_output).grid(row=0, column=1, padx=(0,6))
        ttk.Button(btns, text="Load Settings...", command=self.load_settings).grid(row=0, column=2, padx=(0,6))
        ttk.Button(btns, text="Save Settings...", command=self.save_settings).grid(row=0, column=3)

        # Output + Log
        io = ttk.PanedWindow(self, orient="vertical")
        io.grid(row=6, column=0, sticky="nsew", pady=(6,0))
        self.output = tk.Text(io, height=4, wrap="none")
        self.log = tk.Text(io, height=12, wrap="word")
        io.add(self.output)
        io.add(self.log)

        # Resizing
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(6, weight=1)
        self.columnconfigure(0, weight=1)
        g.columnconfigure(3, weight=1)

    def bind_shortcuts(self):
        self.master.bind("<Control-s>", lambda e: self.save_settings())
        self.master.bind("<Control-o>", lambda e: self.load_settings())

    # --- IO helpers -----------------------------------------------------------
    def pick_allow(self):
        path = filedialog.askopenfilename(
            title="Select allowlist text file",
            initialdir=self._last_dir,
            filetypes=[("Text files","*.txt"), ("All files","*.*")],
        )
        if path:
            self._last_dir = os.path.dirname(path)
            self.allow_path.set(path)

    def append_log(self, s: str):
        self.log.insert("end", s + "\n")
        self.log.see("end")
        self.master.update_idletasks()

    def set_output(self, s: str):
        self.output.delete("1.0", "end")
        self.output.insert("1.0", s)
        self.output.see("1.0")

    def copy_output(self):
        txt = self.output.get("1.0", "end").strip()
        if not txt:
            messagebox.showinfo("Copy Output", "No output to copy")
            return
        self.master.clipboard_clear()
        self.master.clipboard_append(txt)
        messagebox.showinfo("Copy Output", "Copied to clipboard")

    # --- Settings (Save/Load) -------------------------------------------------
    def gather_settings(self) -> dict:
        modes = []
        if self.mode_osu.get():    modes.append("osu")
        if self.mode_taiko.get():  modes.append("taiko")
        if self.mode_fruits.get(): modes.append("fruits")
        if self.mode_mania.get():  modes.append("mania")

        return {
            "client_id": self.client_id_var.get(),
            "client_secret": self.client_secret_var.get(),
            "count": self.count_var.get(),
            "strategy": self.strategy_var.get(),
            "min_len": self.minlen_var.get(),
            "max_len": self.maxlen_var.get(),
            "min_stars": self.minstar_var.get(),
            "max_stars": self.maxstar_var.get(),
            "status": self.status_var.get(),
            "one_per_set": self.one_set_var.get(),
            "tags": self.tags_var.get(),
            "user_tags": self.user_tags_var.get(),
            "free_text": self.text_var.get(),
            "modes": modes,
            "pages": self.pages_var.get(),
            "pool_multiplier": self.pool_var.get(),
            "seed": self.seed_var.get(),
            "verbose": self.verbose_var.get(),
            "enforce_count": self.enforce_var.get(),
            "allowlist_path": self.allow_path.get(),
        }

    def apply_settings(self, cfg: dict):
        # Strings / ints / floats
        self.client_id_var.set(str(cfg.get("client_id", self.client_id_var.get())))
        self.client_secret_var.set(str(cfg.get("client_secret", self.client_secret_var.get())))

        self.count_var.set(as_int(cfg.get("count"), self.count_var.get()))
        self.strategy_var.set(str(cfg.get("strategy", self.strategy_var.get())))

        self.minlen_var.set("" if cfg.get("min_len") in (None,"") else str(as_int(cfg.get("min_len"), "")))
        self.maxlen_var.set("" if cfg.get("max_len") in (None,"") else str(as_int(cfg.get("max_len"), "")))

        self.minstar_var.set("" if cfg.get("min_stars") in (None,"") else str(as_float(cfg.get("min_stars"), "")))
        self.maxstar_var.set("" if cfg.get("max_stars") in (None,"") else str(as_float(cfg.get("max_stars"), "")))

        self.status_var.set(str(cfg.get("status", self.status_var.get())))

        self.one_set_var.set(as_bool(cfg.get("one_per_set"), self.one_set_var.get()))
        self.tags_var.set(",".join(as_str_list(cfg.get("tags"))) if isinstance(cfg.get("tags"), (list,str)) else self.tags_var.get())
        self.user_tags_var.set(",".join(as_str_list(cfg.get("user_tags"))) if isinstance(cfg.get("user_tags"), (list,str)) else self.user_tags_var.get())
        self.text_var.set(str(cfg.get("free_text", self.text_var.get())))

        modes = set(as_str_list(cfg.get("modes")))
        self.mode_osu.set("osu" in modes if modes else self.mode_osu.get())
        self.mode_taiko.set("taiko" in modes if modes else self.mode_taiko.get())
        self.mode_fruits.set("fruits" in modes if modes else self.mode_fruits.get())
        self.mode_mania.set("mania" in modes if modes else self.mode_mania.get())

        self.pages_var.set(str(cfg.get("pages", self.pages_var.get())))
        self.pool_var.set(str(cfg.get("pool_multiplier", self.pool_var.get())))
        self.seed_var.set(str(cfg.get("seed", self.seed_var.get())))

        self.verbose_var.set(as_bool(cfg.get("verbose"), self.verbose_var.get()))
        self.enforce_var.set(as_bool(cfg.get("enforce_count"), self.enforce_var.get()))

        allow_path = cfg.get("allowlist_path")
        if isinstance(allow_path, str) and allow_path.strip():
            self.allow_path.set(allow_path)
            self._last_dir = os.path.dirname(allow_path)

    def save_settings(self):
        cfg = self.gather_settings()
        path = filedialog.asksaveasfilename(
            title="Save Settings",
            initialdir=self._last_dir,
            defaultextension=".json",
            filetypes=[("JSON files","*.json"), ("All files","*.*")],
        )
        if not path: return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self._last_dir = os.path.dirname(path)
            messagebox.showinfo("Save Settings", f"Saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Save Settings", str(e))

    def load_settings(self):
        path = filedialog.askopenfilename(
            title="Load Settings",
            initialdir=self._last_dir,
            filetypes=[("JSON files","*.json"), ("All files","*.*")],
        )
        if not path: return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError("Settings file must be a JSON object.")
            self.apply_settings(cfg)
            self._last_dir = os.path.dirname(path)
            self.append_log(f"[loaded] {path}")
            messagebox.showinfo("Load Settings", "Settings applied.")
        except Exception as e:
            messagebox.showerror("Load Settings", str(e))

    # --- Picker core ----------------------------------------------------------
    def do_pick(self):
        try:
            client_id = self.client_id_var.get().strip()
            client_secret = self.client_secret_var.get().strip()
            if not client_id or not client_secret:
                raise RuntimeError("Enter your osu! Client ID and Client Secret.")

            count = int(self.count_var.get())
            min_len = as_int(self.minlen_var.get())
            max_len = as_int(self.maxlen_var.get())
            min_stars = as_float(self.minstar_var.get())
            max_stars = as_float(self.maxstar_var.get())
            status = self.status_var.get() or None
            tags = as_str_list(self.tags_var.get())
            user_tags = as_str_list(self.user_tags_var.get())
            free_text = self.text_var.get().strip() or None
            strategy = self.strategy_var.get()
            one_per_set = bool(self.one_set_var.get())
            pages = as_int(self.pages_var.get(), 25)
            pool_mult = as_float(self.pool_var.get(), 4.0)
            seed = as_int(self.seed_var.get())
            verbose = bool(self.verbose_var.get())
            enforce = bool(self.enforce_var.get())

            allowed_modes = set()
            if self.mode_osu.get():    allowed_modes.add("osu")
            if self.mode_taiko.get():  allowed_modes.add("taiko")
            if self.mode_fruits.get(): allowed_modes.add("fruits")
            if self.mode_mania.get():  allowed_modes.add("mania")
            if not allowed_modes:
                raise RuntimeError("Select at least one mode (osu/taiko/fruits/mania).")

            if seed is not None:
                random.seed(seed)

            allow = load_allowlist_txt(self.allow_path.get()) if self.allow_path.get() else set()
            page_limit = (200 if allow and pages == 25 else pages)

            sess = requests.Session()
            auth = OsuAuth(client_id=str(client_id), client_secret=str(client_secret))
            token = auth.get_token()

            q = build_query(min_len, max_len, min_stars, max_stars, status, tags, user_tags, free_text)
            if verbose: self.append_log(f"[query] {q or '(none)'}")
            if allow:   self.append_log(f"[allowlist] {len(allow)} set IDs; page limit = {page_limit}")

            pool: List[dict] = []
            need = max(int(count * max(pool_mult, 1.0)), count)

            page_iter = search_beatmapsets(sess, token, q=q, page_limit=page_limit)
            for sets in page_iter:
                batch = collect_candidates_from_search(
                    pages=[sets],
                    min_len=min_len, max_len=max_len,
                    min_stars=min_stars, max_stars=max_stars,
                    one_per_set=one_per_set,
                    allow=allow,
                    allowed_modes=allowed_modes,
                    verbose=verbose
                )
                pool.extend(batch)
                if verbose:
                    uniq_sets = len({x["set_id"] for x in pool})
                    self.append_log(f"[pool] items={len(pool)} unique_sets={uniq_sets} need≈{need}")
                if len(pool) >= need:
                    break

            if not pool:
                self.set_output("[]")
                self.append_log("[result] no candidates within page limit")
                if enforce:
                    raise RuntimeError("Not enough overlap to satisfy count.")
                return

            picked = sample_weighted(pool, count, strategy, min_len, max_len)
            set_ids = sorted({x["set_id"] for x in picked})

            if enforce and len(set_ids) < count:
                self.set_output("[]")
                self.append_log(f"[result] only {len(set_ids)} unique sets; wanted {count}")
                raise RuntimeError("Not enough unique sets.")

            out = json.dumps([str(i) for i in set_ids])
            self.set_output(out)

            if verbose:
                self.append_log("\n[details]")
                for x in picked:
                    mins, secs = x["seconds"] // 60, x["seconds"] % 60
                    self.append_log(
                        f"- set {x['set_id']:>8} | map {x['id']:>9} {x['stars']:.2f}★ "
                        f"{mins}:{secs:02d}  {x['artist']} - {x['title']} "
                        f"[{x.get('version','')}] (m:{x['mapper']})"
                    )
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def start_pick(self):
        t = threading.Thread(target=self.do_pick, daemon=True)
        t.start()

# ──────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    root.title("osu! Beatmap Picker")
    try:
        if sys.platform.startswith("win"):
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(root)
    root.minsize(780, 600)
    root.mainloop()

if __name__ == "__main__":
    main()
