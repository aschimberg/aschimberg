"""Generate site/nfl/index.html and site/nhl/index.html from site/index.html.

Each sport page is the same app pointed at its own data folder. A <base> tag
makes asset and sport-switcher links resolve from the site root.
"""
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def build():
    src = (SITE / "index.html").read_text()
    for sport in ("nfl", "nhl"):
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
