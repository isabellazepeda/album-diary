import json, os, random, secrets, sqlite3
from datetime import datetime
from urllib.parse import quote
from flask import Flask, g, request, redirect, render_template, abort
from jinja2 import DictLoader

app = Flask(__name__)

# Paste your links between the quotes. A link left empty ("") is hidden.
SUPPORT_URL = "https://ko-fi.com/isabellazepeda"  # your Ko-fi page
FEEDBACK_URL = ""  # e.g. a Google Form for reports and feedback
app.jinja_env.globals.update(support_url=SUPPORT_URL, feedback_url=FEEDBACK_URL)

# ---------- 1. The catalog (built by make_catalog.py) ----------
catalog = json.load(open("catalog.json"))


def decade_of(a):
    return a["year"][:3] + "0s" if a.get("year") else ""


# ---------- 2. The database ----------
# On Render, set DATABASE_URL to use a hosted Postgres database that keeps
# ratings forever. With no DATABASE_URL it uses a local file (diary.db).
DATABASE_URL = os.environ.get("DATABASE_URL")
SCHEMA = [
    "CREATE TABLE IF NOT EXISTS diary_ratings (user_id TEXT NOT NULL, album_id TEXT "
    "NOT NULL, rating INTEGER NOT NULL, review TEXT NOT NULL DEFAULT '', rated_at "
    "TEXT NOT NULL, PRIMARY KEY (user_id, album_id))",
    "CREATE TABLE IF NOT EXISTS diary_saved (user_id TEXT NOT NULL, album_id TEXT "
    "NOT NULL, saved_at TEXT NOT NULL, PRIMARY KEY (user_id, album_id))",
]


def run(sql, args=()):
    if DATABASE_URL:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as con:
            cur = con.execute(sql.replace("?", "%s"), args)
            return cur.fetchall() if cur.description else []
    con = sqlite3.connect("diary.db")
    con.row_factory = sqlite3.Row
    try:
        with con:  # saves the change when the block ends
            return con.execute(sql, args).fetchall()
    finally:
        con.close()


for statement in SCHEMA:
    run(statement)


def now():
    return datetime.now().isoformat(timespec="seconds")


def spotify(a):
    return "https://open.spotify.com/search/" + quote(f'{a["artist"]} {a["title"]}')


# ---------- 3. Who is visiting? (a random ID kept in the browser) ----------
@app.before_request
def identify():
    uid = request.cookies.get("vid", "")
    valid = 0 < len(uid) <= 64 and uid.replace("-", "").replace("_", "").isalnum()
    g.new_visitor = not valid
    g.uid = uid if valid else secrets.token_urlsafe(16)


@app.after_request
def remember(resp):
    if g.get("new_visitor"):
        resp.set_cookie("vid", g.uid, max_age=60 * 60 * 24 * 365, httponly=True,
                        samesite="Lax", secure=request.is_secure)
    return resp


def rated_ids():
    rows = run("SELECT album_id FROM diary_ratings WHERE user_id = ?", (g.uid,))
    return {r["album_id"] for r in rows}


# ---------- 4. The pages ----------
@app.route("/")
def home(msg=None):
    done = rated_ids()
    genres, combos = {}, {}  # only count albums you haven't rated yet
    for k, a in catalog.items():
        if k in done:
            continue
        gen, dec = a.get("genre", ""), decade_of(a)
        if gen:
            genres[gen] = genres.get(gen, 0) + 1
        if dec:
            for key in {"", gen}:  # "" = any genre
                combos.setdefault(key, {})
                combos[key][dec] = combos[key].get(dec, 0) + 1
    avg = run("SELECT AVG(rating) AS a FROM diary_ratings WHERE user_id = ?",
              (g.uid,))[0]["a"]
    return render_template("home.html", genres=sorted(genres.items()), combos=combos,
                           n=len(done), avg=float(avg) if avg else None,
                           total=len(catalog), msg=msg)


@app.route("/random")
def random_album():
    done = rated_ids()
    genre, decade = request.args.get("genre", ""), request.args.get("decade", "")
    pool = [k for k, a in catalog.items() if k not in done
            and (not genre or a.get("genre") == genre)
            and (not decade or decade_of(a) == decade)]
    if not pool:
        return home(msg="No unrated albums match that filter.")
    return redirect(f"/album/{random.choice(pool)}")


@app.route("/search")
def search():
    q = request.args.get("q", "").strip().lower()
    hits = []
    if q:
        hits = [(k, a) for k, a in catalog.items()
                if q in a["title"].lower() or q in a["artist"].lower()][:40]
    rated = {r["album_id"]: r["rating"] for r in run(
        "SELECT album_id, rating FROM diary_ratings WHERE user_id = ?", (g.uid,))}
    return render_template("search.html", q=q, hits=hits, rated=rated)


@app.route("/album/<key>")
def album(key):
    a = catalog.get(key) or abort(404)
    mine = run("SELECT rating, review FROM diary_ratings WHERE user_id = ? AND "
               "album_id = ?", (g.uid, key))
    is_saved = bool(run("SELECT 1 AS x FROM diary_saved WHERE user_id = ? AND "
                        "album_id = ?", (g.uid, key)))
    return render_template("album.html", a=a, key=key, spotify=spotify(a),
                           current=mine[0]["rating"] if mine else None,
                           review=mine[0]["review"] if mine else "", is_saved=is_saved)


@app.route("/rate/<key>", methods=["POST"])
def rate(key):
    catalog.get(key) or abort(404)
    try:
        stars = int(request.form["rating"])
    except (KeyError, ValueError):
        abort(400)
    if not 1 <= stars <= 5:
        abort(400)
    review = request.form.get("review", "").strip()[:500]
    run("INSERT INTO diary_ratings (user_id, album_id, rating, review, rated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id, album_id) DO UPDATE SET "
        "rating = excluded.rating, review = excluded.review, "
        "rated_at = excluded.rated_at", (g.uid, key, stars, review, now()))
    run("DELETE FROM diary_saved WHERE user_id = ? AND album_id = ?", (g.uid, key))
    return redirect("/diary")


@app.route("/delete/<key>", methods=["POST"])
def delete(key):
    run("DELETE FROM diary_ratings WHERE user_id = ? AND album_id = ?", (g.uid, key))
    return redirect("/diary")


@app.route("/save/<key>", methods=["POST"])
def save(key):
    catalog.get(key) or abort(404)
    run("INSERT INTO diary_saved (user_id, album_id, saved_at) VALUES (?, ?, ?) "
        "ON CONFLICT DO NOTHING", (g.uid, key, now()))
    return redirect(f"/album/{key}")


@app.route("/unsave/<key>", methods=["POST"])
def unsave(key):
    run("DELETE FROM diary_saved WHERE user_id = ? AND album_id = ?", (g.uid, key))
    return redirect("/saved" if request.form.get("next") == "saved" else f"/album/{key}")


@app.route("/saved")
def saved():
    rows = run("SELECT album_id FROM diary_saved WHERE user_id = ? ORDER BY "
               "saved_at DESC", (g.uid,))
    items = [{"a": catalog[r["album_id"]], "key": r["album_id"]}
             for r in rows if r["album_id"] in catalog]
    return render_template("saved.html", items=items)


@app.route("/diary")
def diary():
    rows = run("SELECT * FROM diary_ratings WHERE user_id = ? ORDER BY rated_at DESC",
               (g.uid,))
    items = [{"a": catalog[r["album_id"]], "key": r["album_id"], "rating": r["rating"],
              "review": r["review"], "date": r["rated_at"][:10]}
             for r in rows if r["album_id"] in catalog]
    return render_template("diary.html", items=items)


# ---------- 5. The look (HTML templates) ----------
BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Album Diary</title><style>
body{margin:0;font-family:system-ui,sans-serif;background:#121212;color:#f2f2f2}
main{max-width:640px;margin:0 auto;padding:20px 16px 60px}
nav{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin-bottom:26px}
nav a{color:#a8a8a8;text-decoration:none;font-weight:600}nav a:hover{color:#1db954}
nav form{margin-left:auto}nav input{padding:8px 14px;border-radius:999px;border:1px solid #444;
background:#1e1e1e;color:#f2f2f2;font-size:15px;width:150px}
h1{margin:0 0 6px}.sub{color:#a8a8a8;margin:0 0 20px}
.box{background:#1e1e1e;border-radius:14px;padding:18px;margin-bottom:16px}
select,button{font-size:16px;padding:10px 14px;border-radius:999px;border:1px solid #444;
background:#2a2a2a;color:#f2f2f2;margin:0 6px 8px 0}
button{cursor:pointer}button.go{background:#1db954;color:#000;border:0;font-weight:700}
button.del{color:#ff7b7b;border-color:#5a3a3a}
textarea{width:100%;box-sizing:border-box;min-height:80px;border-radius:10px;border:1px solid #444;
background:#2a2a2a;color:#f2f2f2;font:inherit;padding:10px;margin-bottom:12px}
.cover{width:100%;max-width:340px;border-radius:12px;display:block;margin:0 auto 16px}
.title{font-size:24px;font-weight:700;margin:0}.artist{color:#a8a8a8;margin:4px 0 14px}
.stars button{font-size:18px}.stars button.on{background:#1db954;color:#000;border:0}
a.pill{display:inline-block;padding:8px 14px;border-radius:999px;border:1px solid #555;
color:#f2f2f2;text-decoration:none;margin:0 8px 8px 0}a.pill:hover{border-color:#1db954}
.row{display:flex;gap:14px;align-items:center}.row img{width:70px;height:70px;border-radius:6px}
.row a.t{color:#f2f2f2;text-decoration:none;font-weight:700}.gold{color:#f5c518}
.review{margin:6px 0 0;color:#d6d6d6;font-size:15px;white-space:pre-wrap}
.msg{color:#ffb86b}form.inline{display:inline}
footer{margin-top:40px;padding-top:18px;border-top:1px solid #2a2a2a;color:#a8a8a8;
font-size:14px;display:flex;gap:18px;flex-wrap:wrap}footer a{color:#a8a8a8}
footer a.coffee{color:#1db954;font-weight:700}footer p{margin:0;width:100%;font-size:12px}
</style></head><body><main>
<nav><a href="/">Discover</a><a href="/saved">To listen</a><a href="/diary">My ratings</a>
<form action="/search"><input name="q" placeholder="Search albums" value="{{ q or '' }}"></form></nav>
{% block body %}{% endblock %}
<footer>
{% if feedback_url %}<a href="{{ feedback_url }}" target="_blank" rel="noopener">Report a problem or share feedback</a>{% endif %}
{% if support_url %}<a class="coffee" href="{{ support_url }}" target="_blank" rel="noopener">&#9749; Buy me a coffee</a>{% endif %}
<p>No account needed: your ratings are saved to this browser. Clearing your cookies or switching browsers starts a fresh list.</p>
</footer></main></body></html>"""

HOME = """{% extends "base.html" %}{% block body %}
<h1>Album Diary</h1><p class="sub">Get an album. Listen. Rate it.</p>
{% if msg %}<p class="msg">{{ msg }}</p>{% endif %}
<div class="box"><form action="/random">
<select name="genre" id="genre"><option value="">Any genre</option>
{% for g, c in genres %}<option value="{{ g }}">{{ g }} ({{ c }})</option>{% endfor %}</select>
<select name="decade" id="decade"><option value="">Any decade</option></select><br>
<button class="go">Give me an album</button></form></div>
<p class="sub">{{ n }} rated of {{ total }} albums{% if avg %} &middot; your average is {{ "%.1f"|format(avg) }} &#9733;{% endif %}</p>
<script>
// Only offer decades that still have albums for the genre you picked
const combos = {{ combos|tojson }};
const g = document.getElementById('genre'), d = document.getElementById('decade');
function fill() {
  const keep = d.value, opts = combos[g.value] || {};
  d.innerHTML = '<option value="">Any decade</option>';
  Object.keys(opts).sort().forEach(k => {
    const o = document.createElement('option');
    o.value = k; o.textContent = k + ' (' + opts[k] + ')'; d.appendChild(o);
  });
  if (opts[keep]) d.value = keep;
}
g.addEventListener('change', fill); fill();
</script>
{% endblock %}"""

ALBUM = """{% extends "base.html" %}{% block body %}
<img class="cover" src="{{ a.cover }}" alt="Cover of {{ a.title }}">
<p class="title">{{ a.title }}</p>
<p class="artist">{{ a.artist }}{% if a.year %} &middot; {{ a.year }}{% endif %}{% if a.genre %} &middot; {{ a.genre }}{% endif %}</p>
<a class="pill" href="{{ spotify }}" target="_blank" rel="noopener">Listen on Spotify</a>
<a class="pill" href="{{ a.link }}" target="_blank" rel="noopener">Apple Music</a>
<div class="box"><p class="sub">{{ "Your rating:" if current else "Listen first, then rate it:" }}</p>
<form class="stars" method="post" action="/rate/{{ key }}">
<textarea name="review" maxlength="500" placeholder="Add a short review (optional)">{{ review or '' }}</textarea>
{% for i in range(1, 6) %}<button name="rating" value="{{ i }}" class="{{ 'on' if current == i }}">{{ i }} &#9733;</button>{% endfor %}
</form>
{% if current %}<form class="inline" method="post" action="/delete/{{ key }}">
<button class="del" onclick="return confirm('Delete your rating?')">Delete my rating</button></form>{% endif %}
</div>
{% if not current %}
{% if is_saved %}<form class="inline" method="post" action="/unsave/{{ key }}"><button>Remove from to-listen list</button></form>
{% else %}<form class="inline" method="post" action="/save/{{ key }}"><button>Save for later</button></form>{% endif %}
<a class="pill" href="/random">Skip, give me another</a>{% endif %}
{% endblock %}"""

DIARY = """{% extends "base.html" %}{% block body %}
<h1>My ratings</h1><p class="sub">{{ items|length }} albums rated</p>
{% for i in items %}<div class="box row">
<img src="{{ i.a.cover }}" alt=""><div><a class="t" href="/album/{{ i.key }}">{{ i.a.title }}</a>
<div class="artist" style="margin:2px 0">{{ i.a.artist }}</div>
<span class="gold">{{ "★" * i.rating }}</span> <span class="sub">{{ i.date }}</span>
{% if i.review %}<p class="review">{{ i.review }}</p>{% endif %}</div></div>
{% else %}<p class="sub">Nothing yet. <a href="/" style="color:#1db954">Get your first album</a>.</p>{% endfor %}
{% endblock %}"""

SAVED = """{% extends "base.html" %}{% block body %}
<h1>To listen</h1><p class="sub">{{ items|length }} saved</p>
{% for i in items %}<div class="box row">
<img src="{{ i.a.cover }}" alt=""><div><a class="t" href="/album/{{ i.key }}">{{ i.a.title }}</a>
<div class="artist" style="margin:2px 0 8px">{{ i.a.artist }}</div>
<form class="inline" method="post" action="/unsave/{{ i.key }}">
<input type="hidden" name="next" value="saved"><button>Remove</button></form></div></div>
{% else %}<p class="sub">Nothing saved. Tap "Save for later" on any album.</p>{% endfor %}
{% endblock %}"""

SEARCH = """{% extends "base.html" %}{% block body %}
<h1>Search</h1>
{% if not q %}<p class="sub">Type an album or artist in the box above.</p>
{% else %}<p class="sub">{{ hits|length }} result{{ "" if hits|length == 1 else "s" }} for "{{ q }}"</p>{% endif %}
{% for k, a in hits %}<div class="box row">
<img src="{{ a.cover }}" alt=""><div><a class="t" href="/album/{{ k }}">{{ a.title }}</a>
<div class="artist" style="margin:2px 0">{{ a.artist }}{% if a.year %} &middot; {{ a.year }}{% endif %}</div>
{% if k in rated %}<span class="gold">{{ "★" * rated[k] }}</span>{% endif %}</div></div>{% endfor %}
{% endblock %}"""

app.jinja_env.loader = DictLoader({"base.html": BASE, "home.html": HOME,
    "album.html": ALBUM, "diary.html": DIARY, "saved.html": SAVED, "search.html": SEARCH})

if __name__ == "__main__":
    app.run(port=5002)
