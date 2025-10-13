# osuAPWorldPrivate

Tools for Creating an osu! APWorld with custom Song Data \
This is a **private** toolset for making a custom osu! Archipelago.
Reason: the official pool only includes Featured Artists. This lets you build your own pool (Ranked + Loved) and create a `include_songs` lists with more custom speficiations (artist, mapper tag, user tag, etc).

Right now, sample data is included (`OsuSongData.json`, `allowed_sets.txt`) but that may be removed later. This means you can Skip 1) and 2) if you want to use the full list.

---

### Tool files

* `build_pool.py`
    -> Builds a custom `OsuSongData.json` used by the osu Archipelago
* `extractor.py` 
    -> reads `OsuSongData.json` and turns it into a plain text file where every line is a beatmapset
* `gui.py`
    -> produces final include_songs List with filters

### Data files

* `OsuSongData.json`
    -> Pre Genned Pool (Ranked and Loved, standard only)
* `allowed_sets.txt`
    -> converted Pool
---

## How to use
(only for Windows!)
### 0) Prereqs 

* Python **3.13** recommended.
* Install requests:

```powershell
py --m pip install -U pip
py -m pip install requests
```

#### OAuth setup
1. Go to your Osu Account settings
2. Create a OAuth Application
3. Set Redirect Url to https://localhost
4. Save this somewhere. You need later twice.

---

### 1) Build private pool (build_pool.py)

**What it does**

* Pages `/api/v2/beatmapsets/search`
* Hardcoded to always do Ranked + Loved and only STD right now. If wanted I may make it useable for the other modes and statuses

**How to run**

Example:
```powershell
py build_pool.py --client-id YOUR_ID --client-secret YOUR_SECRET --out OsuSongData.json --checkpoint pool_checkpoint.json --sleep 0.35 --max-rpm 60 --verbose
```

**Notes**

* --sleep is a limiter so you dont get rate limited. I recommend to keep it at default, if you wanna be more polite to peppy use 1.1
* If you CTR+C to stop it, it safely writes to a checkpoint. Use the exact same command as before to just continue

---

### 2) Make an allowed_set.txt from the pool (extractor.py)

**Common use:**

```powershell
py extractor.py --in OsuSongData.json --out allowed_sets.txt
```

**Notes**
* used for the gui later
* there are technically extra filters from a legacy thing, but you dont need to use them


---

### 3) Pick `include_songs` with the GUI (`gui.py`)

**Run:**

```powershell
py gui.py
```

**Steps**

1. Enter Client ID + Client Secret in the top fields.
    (if you dont know how to set this up: Do the exact same thing you do when you set up osu for archipelago, but for the URL you just use https://localhost   without any port)
2. Choose the earlier generated `allowed_sets.txt`
3. Set filters
4. Click Pick Beatmaps
5. Copy to Clickboard
6. Paste the array into your YAML:

   ```yaml
   include_songs: ["29230","594170", ...]
   ```

---

### 4) Creating your Own APWorld based on your Own Pool

1. Download the osu.apworld file
2. Extract it 
3. in the folder, replace OsuSongData.json with the one you generated earlier
4. Zip everything up again (Dont zip the files, zip the folder)
5. rename the .zip to osu.apworld
6. Go to your custom_worlds folder in archipelago, and delete your previous osu.apworld
7. run osu.apworld
8. Done, yay!



---

## Troubleshooting
**It wont Generate**:
Make sure that the person generating has the .apworld you made the include_song list for

**Picker returns empty**:
Filters not broad enough or allowlist too small. Increase Page by a lot, relax filters,

**Custom world won’t load**:
If using a ".apworld", the zip root needs to be:

```
worlds/osu/__init__.py
```

Not "osu/" at root, not files at root. Keep filename lowercase: "osu.apworld"

**Sleep Limit**:
Default builder is faster than the 60rpm recommended limit. If you still see 429s, increase "--sleep"

---
