"""Safety check: only the events-data JSON block of index.html may change, and it must be valid."""
import json, re, sys

TAG = '<script type="application/json" id="events-data">'
TYPES = {"road", "fun", "tri", "trail", "club"}


def split(path):
    h = open(path, encoding="utf-8").read()
    i = h.index(TAG) + len(TAG)
    j = h.index("</script>", i)
    return h[:i], h[i:j], h[j:]


old_pre, _, old_post = split(sys.argv[1])
new_pre, block, new_post = split(sys.argv[2])
if (old_pre, old_post) != (new_pre, new_post):
    sys.exit("index.html changed outside the events-data block")

data = json.loads(block)
assert isinstance(data.get("updated"), str), "missing 'updated'"
events = data.get("events")
assert isinstance(events, list) and events, "no events"
ids = set()
for e in events:
    for k in ("id", "name", "type", "city", "dist", "org", "about", "url", "src"):
        assert isinstance(e.get(k), str) and e[k], f"{e.get('id')}: missing {k}"
    assert e["type"] in TYPES, f"{e['id']}: bad type"
    assert e["url"].startswith("https://"), f"{e['id']}: bad url"
    assert e["id"] not in ids, f"duplicate id {e['id']}"
    ids.add(e["id"])
    if "rec" in e:
        assert isinstance(e["rec"], int) and 0 <= e["rec"] <= 6, f"{e['id']}: bad rec"
    else:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.get("start", "")), f"{e['id']}: bad start"
print(f"OK: {len(events)} events, updated {data['updated']}")
