"""Generate site/nfl, site/nhl and site/nba index pages from site/index.html.

Each sport page is the same app pointed at its own data folder. A <base> tag
makes asset and sport-switcher links resolve from the site root.

Script and stylesheet links get a ?v=<content hash> so browsers (phones
especially) fetch new code after each deploy instead of a cached copy.
"""
import hashlib
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"
ASSETS = ("app.js", "styles.css", "themes/dashboard.css")


def stamp(html):
    """Add ?v=<hash of the asset's contents> to each local asset link."""
    for name in ASSETS:
        path = SITE / name
        if not path.exists():
            continue
        v = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
        for attr in ("href", "src"):
            html = html.replace(f'{attr}="{name}"', f'{attr}="{name}?v={v}"')
    return html


def build():
    src = stamp((SITE / "index.html").read_text())
    (SITE / "index.html").write_text(src)
    for sport in ("nfl", "nhl", "nba"):
        page = src.replace(
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'  <base href="../">\n  <script>window.DATA_PATH = "{sport}/data/";</script>',
            1,
        ).replace("<title>ASPicks Model · CFB</title>", f"<title>ASPicks Model · {sport.upper()}</title>", 1)
        (SITE / sport).mkdir(exist_ok=True)
        (SITE / sport / "index.html").write_text(page)


if __name__ == "__main__":
    build()
