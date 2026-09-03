"""End-to-end test of page_harness against a local fixture server.

Real sockets, real curl_cffi, real HTTP status codes -- so the fetch ladder,
block detection and retry logic are actually exercised, not mocked.

    python test_harness.py
"""
import json, threading, time, http.server, socketserver, tempfile, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from page_harness import harness, save, looks_blocked

PORT = 8731
BASE = f"http://127.0.0.1:{PORT}"

ARTICLE_BODY = ("Acme Corp today announced a definitive agreement to acquire Beta "
                "Industries in an all-cash transaction valued at $450 million. " * 10)

GOOD = f"""<!doctype html><html><head><title>Acme to Acquire Beta Industries</title>
<meta property="og:title" content="Acme to Acquire Beta Industries">
<meta name="description" content="All-cash transaction valued at $450 million.">
<script type="application/ld+json">
{{"@type":"NewsArticle","headline":"Acme to Acquire Beta Industries",
"datePublished":"2026-08-26T07:00:00Z"}}</script></head>
<body><nav>{"<a href='/x'>Category</a>" * 200}</nav>
<article><h1>Acme to Acquire Beta Industries</h1><p>{ARTICLE_BODY}</p></article>
<footer>{"<a href='/y'>Footer link</a>" * 200}</footer></body></html>"""

# 200 OK, but it's a wall. The case status codes miss entirely.
WALL_200 = ("<html><head><title>Just a moment...</title></head><body>"
            "<h1>Checking your browser before accessing the site.</h1>"
            "<p>Please enable JavaScript and cookies to continue.</p></body></html>")

# fails twice with 503, then succeeds -- exercises retry/backoff
_flaky_hits = {"n": 0}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/good":
            self._send(200, GOOD)
        elif self.path == "/wall200":
            self._send(200, WALL_200)
        elif self.path == "/forbidden":
            self._send(403, "<html><body>Access Denied. Reference #18.2a</body></html>")
        elif self.path == "/flaky":
            _flaky_hits["n"] += 1
            if _flaky_hits["n"] <= 2:
                self._send(503, "<html><body>Service Unavailable</body></html>")
            else:
                self._send(200, GOOD)
        elif self.path == "/thin":
            self._send(200, "<html><body><p>hi</p></body></html>")
        else:
            self._send(404, "<html><body>nope</body></html>")


class Server(socketserver.TCPServer):
    allow_reuse_address = True


srv = Server(("127.0.0.1", PORT), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.4)

results = []


def check(label, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))


# ---- happy path -----------------------------------------------------------
p = harness(f"{BASE}/good", retries=0)
check("fetches over real HTTP", p.ok and p.status == 200, f"via={p.via}")
check("html returned", p.html and len(p.html) > 1000, f"{len(p.html or '')} bytes")
check("title parsed", p.title == "Acme to Acquire Beta Industries", p.title)
check("json-ld parsed", p.jsonld and p.jsonld[0].get("headline"), str(p.jsonld)[:60])
check("og meta parsed", p.meta.get("og:title") == "Acme to Acquire Beta Industries",
      str(p.meta))
check("text extracted", "definitive agreement to acquire" in (p.text or ""),
      f"{len(p.text or '')} chars via {p.extractor}")
check("nav/footer chrome stripped", "Footer link" not in (p.text or ""))
check("multiple extractors compared", len(p.candidates) >= 2, str(p.candidates))
check("not flagged suspect", not p.suspect)

# ---- the 200-OK wall ------------------------------------------------------
p2 = harness(f"{BASE}/wall200", retries=0)
check("200-OK wall detected as blocked", p2.blocked and not p2.ok,
      f"status={p2.status} reason={p2.block_reason}")
check("wall html not returned as content", p2.text is None)

# ---- hard block -----------------------------------------------------------
p3 = harness(f"{BASE}/forbidden", retries=0)
check("403 treated as blocked", p3.blocked and not p3.ok, p3.block_reason)

# ---- retry / backoff ------------------------------------------------------
t0 = time.time()
p4 = harness(f"{BASE}/flaky", retries=2, backoff=1.2)
check("recovers after transient 503s", p4.ok, f"{_flaky_hits['n']} requests made")
check("backoff actually waited", time.time() - t0 > 1.0, f"{time.time()-t0:.1f}s")

# ---- thin page ------------------------------------------------------------
p5 = harness(f"{BASE}/thin", retries=0)
check("thin page flagged rather than stored", p5.blocked, p5.block_reason)

# ---- unit: block detector -------------------------------------------------
check("detector: incapsula", looks_blocked("<html>Request unsuccessful. Incapsula "
                                           "incident ID: 123</html>", 200)[0])
check("detector: datadome", looks_blocked("<html>datadome captcha</html>", 200)[0])
check("detector: good page passes",
      not looks_blocked(GOOD, 200)[0])

# ---- storage --------------------------------------------------------------
with tempfile.TemporaryDirectory() as d:
    path = save(p, d)
    files = sorted(os.listdir(path))
    check("writes html/txt/meta", files == ["meta.json", "page.html", "page.txt"],
          str(files))
    meta = json.load(open(os.path.join(path, "meta.json")))
    check("meta records winning strategy", meta["via"].startswith("curl_cffi"),
          meta["via"])
    check("meta has no content blob", "html" not in meta and "text" not in meta)

srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
print("ALL PASS" if all(results) else "FAILURES ABOVE")
sys.exit(0 if all(results) else 1)
