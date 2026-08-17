#!/usr/bin/env python3
"""The BOV website gate. Exit 0 to ship, exit 1 to stop.

    python LAAA-AI-Prompts/scripts/check_bov_site.py <deal-repo>

Run before the first commit and again before deploy. The same checks run
server-side on every push via the GitHub Action in
`reporting/bovsite/workflow/deploy-pages.yml`, which is the copy that cannot be
skipped.

Every check below is an actual failure, not a hypothetical:

  A BOV shipped as a Slidev deck (11025 Blix, 2026-05-27) and another as a
  React/Vite SPA (Brio, 2026-07-30). Both times a correct rule existed and the
  build path did not read it. Checks 1 and 2.

  A build shipped with no webfont link and roughly 2x the locked type scale,
  which is why "it looks nothing like Camarillo" is detectable as a string.
  Checks 3 and 4.

  Maps were rendered with Leaflet and OpenStreetMap, and subjects geocoded via
  the Census interpolator, which put a pin 33m into the street on 3837 College.
  Checks 6 and 7.

  A Google Maps API key was emitted into client HTML. Check 8.

  "Marcus &amp;amp; Millichap" reached production in three headings because a
  pre-escaped string was passed through the template escaper. Check 10.

  The internal audience segment name "Clients" reached client-facing copy.
  Check 11.

  A rebuild silently reverted the live track-record and marketing figures to a
  hardcoded snapshot that was stale and looked correct. Check 12.

CALIBRATION MATTERS. Every rule here was run against the locked reference build
before being enforced. The price-before-reveal rule in particular has an
explicit carve-out because Camarillo itself quotes the price inside a buyer
objection answer in #property-info, and a naive version of that check would
permanently fail the very design it is protecting.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

FRAMEWORK_CONFIGS = ["vite.config.ts", "vite.config.js", "next.config.js", "next.config.mjs",
                     "svelte.config.js", "astro.config.mjs", "nuxt.config.ts", "remix.config.js"]
SPA_MARKERS = [
    (r'<script[^>]+type=["\']module["\']', "ES module script (bundler output)"),
    (r'/assets/[A-Za-z0-9_-]+-[A-Za-z0-9]{6,}\.js', "hashed bundle reference"),
    (r'data-reactroot|__NEXT_DATA__|<div id=["\']root["\']></div>|id=["\']__nuxt["\']',
     "SPA hydration root"),
]
BANNED_SOURCES = [
    (r'leaflet', "Leaflet"),
    (r'openstreetmap|tile\.osm', "OpenStreetMap"),
    (r'unpkg\.com|cdnjs\.|jsdelivr', "third-party CDN"),
    (r'geocoding\.geo\.census\.gov', "Census geocoder"),
    (r'nominatim', "Nominatim"),
]
#: A build older than this is not "current" for a client-facing page. One
#: working day: long enough to build, review and deploy without a rebuild,
#: short enough that a figure cannot quietly drift for a week.
MAX_FIGURE_AGE_HOURS = 24

REQUIRED_FILES = ["index.html", "CNAME", ".nojekyll", "og.jpg"]
SECTION_ORDER = ["track-record", "marketing", "investment", "location", "prop-details",
                 "photos", "property-info", "rent-comps", "sale-comps", "on-market",
                 "financials", "contact"]

fails, warns = [], []


def fail(msg):
    fails.append(msg)


def warn(msg):
    warns.append(msg)


#: Build scratch, not deployable routes. Auditing _site double-counts the
#: staged copy of the same pages and reports a route that does not exist.
NOT_ROUTES = {"_site", "node_modules", "raw", "images", "template", ".git", ".github", "__pycache__"}


def routes(site):
    out = []
    if (site / "index.html").exists():
        out.append(site / "index.html")
    for d in sorted(p for p in site.iterdir() if p.is_dir()):
        if d.name in NOT_ROUTES or d.name.startswith("."):
            continue
        if (d / "index.html").exists():
            out.append(d / "index.html")
    return out


def main(site):
    site = Path(site).resolve()
    if not site.is_dir():
        raise SystemExit(f"not a directory: {site}")

    pages = routes(site)
    if not pages:
        fail("no index.html anywhere. Nothing to deploy.")
        return report(site, pages)

    # Subject prices come from the data file, so the price-discipline check does
    # not depend on how the reveal happens to be marked up.
    prices_by_slug, site_data, property_slugs = {}, {}, set()
    if (site / "bov-site.json").exists():
        try:
            site_data = json.loads((site / "bov-site.json").read_text(encoding="utf-8"))
            for p in site_data.get("properties") or []:
                if p.get("slug"):
                    property_slugs.add(p["slug"])
                if p.get("price"):
                    prices_by_slug[p.get("slug", "")] = f"${p['price']:,.0f}"
            if len(site_data.get("properties") or []) == 1:
                prices_by_slug["__single__"] = next(iter(prices_by_slug.values()), None)
        except (json.JSONDecodeError, KeyError):
            pass   # reported below by the stats check

    # 1. framework config files
    for name in FRAMEWORK_CONFIGS:
        if (site / name).exists():
            fail(f"{name} present. A BOV website is static HTML, never a framework app.")

    # 2. SPA output in the emitted HTML
    for page in pages:
        html = page.read_text(encoding="utf-8", errors="replace")
        rel = page.relative_to(site)
        for pat, what in SPA_MARKERS:
            if re.search(pat, html, re.I):
                fail(f"{rel}: {what}. The deployed output must be plain static HTML.")

        # 3. both locked webfonts
        if "family=Inter" not in html:
            fail(f"{rel}: Inter is not loaded.")
        if "family=DM+Serif+Display" not in html:
            fail(f"{rel}: DM Serif Display is not loaded. A build with no serif is not Camarillo.")

        # 4. the locked container width
        if "max-width: 1100px" not in html:
            fail(f"{rel}: the 1100px .section container is missing.")

        # 5. no inlined images
        if "data:image" in html:
            fail(f"{rel}: inlines a base64 image. External files in images/ only.")

        # 6/7. banned map stacks and geocoders
        for pat, what in BANNED_SOURCES:
            if re.search(pat, html, re.I):
                fail(f"{rel}: references {what}. Maps are pre-rendered Google Static Maps "
                     f"from laaa-geo ROOFTOP coordinates.")

        # 8. no API key in client HTML
        if re.search(r'AIza[0-9A-Za-z_\-]{35}', html):
            fail(f"{rel}: a Google API key is present in the emitted HTML.")

        # 9. M&M orange is deck-only
        if re.search(r'#E0843C', html, re.I):
            fail(f"{rel}: M&M orange #E0843C is a deck colour and must not appear on a website.")

        # 10. double-escaped entities
        bad = set(re.findall(r'&amp;(?:[a-zA-Z][a-zA-Z0-9]{1,10}|#[0-9]+);', html))
        if bad:
            fail(f"{rel}: double-escaped entities rendering as literal text: {', '.join(sorted(bad))}")

        # 11. internal segment name in visible copy
        if re.search(r'>[^<]*\bClients\b[^<]*<', html):
            fail(f"{rel}: the internal segment name \"Clients\" appears in visible copy. "
                 f"Client-facing wording is Principals or Property Owners.")

        # 11b. no published confidence level. Rescued from MASTER_PLAN.md, which
        # lived on one laptop and in no repo: "DO NOT display confidence levels
        # on client-facing BOVs... do not use moderate/high confidence in
        # PRICING_RATIONALE text." Confidence is an internal judgement.
        # The word boundaries are load-bearing. They were once literal U+0008
        # backspace characters because a heredoc ate the escape, so the check
        # matched nothing and passed the exact language it exists to catch.
        m = re.search(r'>[^<]*\b(?:high|moderate|low)\s+confidence\b[^<]*<', html, re.I)
        if m:
            fail(f"{rel}: publishes a confidence level ({m.group(0)[1:60].strip()}). "
                 f"Confidence assessment stays in the internal workspace, never on a client page.")

        # 12. no PDF download button on a website
        if "pdf-float-btn" in html or re.search(r'>\s*Download PDF\s*<', html):
            fail(f"{rel}: carries a Download PDF button. Removed from websites (Glen, 2026-07-30).")

        # 13. size sanity. An empty render and a bloated one both signal a broken build.
        kb = len(html.encode()) // 1024
        if kb < 25:
            fail(f"{rel}: only {kb} KB. A real page does not render that small.")
        elif kb > 400:
            warn(f"{rel}: {kb} KB is unusually large for a static page.")

        # 14. every referenced image resolves, including absolute URLs on our
        # own domain. og:image is written absolute, so skipping absolute URLs
        # skipped the share card, which is the one image whose absence is
        # invisible on the page and obvious in every link preview.
        domain = (site / "CNAME").read_text(encoding="utf-8").strip() if (site / "CNAME").exists() else ""
        refs = (set(re.findall(r'(?:src|href|content)="([^"]+\.(?:jpg|jpeg|png|webp|svg))"', html))
                | set(re.findall(r"url\('([^']+\.(?:jpg|jpeg|png|webp|svg))'\)", html)))
        for ref in refs:
            if ref.startswith("data:"):
                continue
            if ref.startswith(("http://", "https://")):
                if domain and domain in ref:
                    local = site / ref.split(domain, 1)[1].lstrip("/")
                    if not local.exists():
                        fail(f"{rel}: {ref} points at our own domain but the file is not in the build: "
                             f"{local.relative_to(site)}")
                continue
            if not (page.parent / ref).resolve().exists():
                fail(f"{rel}: broken image reference {ref}")

        # 15. section order
        found = [s for s in re.findall(r'id="([a-z-]+)"', html) if s in SECTION_ORDER]
        expected = [s for s in SECTION_ORDER if s in found]
        if found != expected:
            fail(f"{rel}: sections are out of the locked order.\n"
                 f"        got      {found}\n        expected {expected}")
        # Ordering only the IDs that happen to exist let a page delete an
        # entire required section and still pass. Property pages have a fixed
        # mandatory spine; Photos and On-Market remain data-conditional.
        is_property_page = (
            (len(property_slugs) == 1 and page == site / "index.html")
            or page.parent.name in property_slugs
        )
        if is_property_page:
            mandatory = {
                "track-record", "marketing", "investment", "location",
                "prop-details", "property-info", "rent-comps", "sale-comps",
                "financials", "contact",
            }
            missing = [s for s in SECTION_ORDER if s in mandatory and s not in found]
            if missing:
                fail(f"{rel}: missing mandatory section(s): {', '.join(missing)}")

        # 16. BOV price discipline, with the documented Camarillo carve-out.
        #
        # The price comes from the DATA, not from scraping the page. Scraping
        # for "RECOMMENDED LIST PRICE" missed it entirely, because the caps are
        # CSS text-transform and the markup says "Recommended List Price". A
        # gate that silently matches nothing reports PASS forever.
        if "sale-comps" in found and "financials" in found and prices_by_slug:
            token = prices_by_slug.get(page.parent.name) or prices_by_slug.get("__single__")
            if token:
                head = html.split('id="sale-comps"')[0]
                # Camarillo quotes the price inside a buyer-objection answer in
                # #property-info. That IS the locked design, so it is allowed
                # there and nowhere else before the reveal. Without this carve-out
                # the gate would permanently fail the build it exists to protect.
                if 'id="property-info"' in head:
                    head = head.split('id="property-info"')[0]
                if token in head:
                    fail(f"{rel}: the subject price {token} appears before #sale-comps. "
                         f"On a BOV the valuation reveal belongs in Financial Analysis.")

    # 17. required files
    for name in REQUIRED_FILES:
        if not (site / name).exists():
            fail(f"missing required file: {name}")

    # 18. the published figures must come from a live pull FOR THIS BUILD.
    #
    # `stats.live` alone is not evidence. An old bov-site.json still carries
    # live=true, and a missing one used to be a warning, so either an omitted
    # file or a reused one let a deploy through without proving anything. Three
    # things are required now: the file exists, the flag is true, and the pages
    # themselves carry the timestamp of the pull they were rendered from.
    data = site / "bov-site.json"
    if not data.exists():
        fail("no bov-site.json. The published track-record and marketing figures cannot be "
             "shown to have come from a live pull, so this build is not provably current.")
    else:
        try:
            payload = json.loads(data.read_text(encoding="utf-8"))
            stats = payload.get("stats") or {}
            if payload.get("document_type") != "bov":
                fail("bov-site.json must explicitly declare document_type='bov'.")
        except json.JSONDecodeError as exc:
            fail(f"bov-site.json is not valid JSON: {exc}")
            stats = {}
        if stats.get("live") is not True:
            fail("stats.live is not true. This build used fallback figures rather than a live "
                 "pull, so its track-record and marketing numbers may be stale. Rebuild online.")
        generated = stats.get("generated")
        if not generated:
            fail("stats.generated is missing, so the figures cannot be dated.")
        else:
            try:
                generated_at = datetime.fromisoformat(generated)
                if generated_at.tzinfo is None or generated_at.utcoffset() is None:
                    fail("stats.generated must include an explicit timezone offset.")
                else:
                    age = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
                    if age > MAX_FIGURE_AGE_HOURS:
                        fail(f"the live figures are {age:.0f} hours old "
                             f"(limit {MAX_FIGURE_AGE_HOURS}). "
                             f"Rebuild so the published numbers are current.")
            except (TypeError, ValueError):
                fail(f"stats.generated is not a valid timestamp: {generated!r}")
            # Every rendered page must carry the same pull it was built from.
            for page in pages:
                stamp = re.search(r'name="laaa-figures-pulled" content="([^"]*)"',
                                  page.read_text(encoding="utf-8", errors="replace"))
                got = stamp.group(1) if stamp else ""
                if got != generated:
                    fail(f"{page.relative_to(site)} was rendered from a different pull than "
                         f"bov-site.json records ({got or 'no stamp'} vs {generated}). "
                         f"The data file and the pages are out of sync; rebuild.")

    return report(site, pages)


def report(site, pages):
    print(f"\nBOV SITE GATE  {site}")
    print(f"checked {len(pages)} route file(s): {', '.join(str(p.relative_to(site)) for p in pages)}\n")
    for w in warns:
        print(f"  warn  {w}")
    for f in fails:
        print(f"  FAIL  {f}")
    if not fails:
        print(f"\n  PASS  {len(warns)} warning(s), nothing blocking. Safe to commit and deploy.")
        return 0
    print(f"\n  {len(fails)} BLOCKING failure(s). Do not commit or deploy.")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[2].strip())
    sys.exit(main(sys.argv[1]))
