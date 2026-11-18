import json, re, sys

# Usage: python3 make_catalog.py path/to/albums.json
# Builds a small catalog.json (real albums only, one version of each,
# no color data) so the Album Diary site stays light and fast.
MIN_TRACKS = 8
NOT_ALBUM = re.compile(
    r"greatest hits|best of|\bhits\b|collection|anthology|essential|"
    r"remix|karaoke|tribute|demos?\b|b-sides|rarities|2 in 1|compilation|"
    r"\(live|\[live|\blive (at|in|from|on)\b|unplugged", re.I)


def is_album(a):
    t = a["title"].lower()
    if t.endswith(" - single") or t.endswith(" - ep"):
        return False
    if a.get("trackCount", 99) < MIN_TRACKS:
        return False
    return not NOT_ALBUM.search(t)


def base_title(a):
    t = re.sub(r"[\(\[].*?[\)\]]", "", a["title"].lower()).strip()
    return (a["artist"].lower(), t)


catalog, seen = {}, set()
for key, a in json.load(open(sys.argv[1])).items():
    if is_album(a) and base_title(a) not in seen:
        seen.add(base_title(a))
        catalog[key] = {f: a.get(f, "") for f in
                        ("title", "artist", "cover", "link", "year", "genre")}

json.dump(catalog, open("catalog.json", "w"), separators=(",", ":"))
print(len(catalog), "albums written to catalog.json")
