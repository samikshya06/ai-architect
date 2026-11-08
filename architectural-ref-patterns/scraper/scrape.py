"""Scrape web pages into LLM-friendly markdown files.

Providers:
  firecrawl - Firecrawl REST API (needs FIRECRAWL_API_KEY)
  jina      - Jina Reader (https://r.jina.ai/<url>); JINA_API_KEY optional
  html      - LangChain AsyncHtmlLoader + BeautifulSoup/markdownify (no external service)
  auto      - firecrawl if key is set, else jina, falling back to html on failure

Usage:
  python -m scraper.scrape [--provider auto] [--urls-file scraper/urls.txt] [--out architectural-ref-patterns] [--strip-links] [URL ...]
"""

import argparse
import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_community.document_loaders import AsyncHtmlLoader
from markdownify import markdownify

load_dotenv()
os.environ.setdefault("USER_AGENT", "ai-architect-scraper/0.1")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "architectural-ref-patterns"
DEFAULT_URLS = Path(__file__).resolve().parent / "urls.txt"
TIMEOUT = aiohttp.ClientTimeout(total=90)
NOISE_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe", "svg"]
PROVIDERS = ("auto", "firecrawl", "jina", "html")


async def _firecrawl(url: str) -> str:
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY is not set")
    payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    headers = {"Authorization": f"Bearer {key}"}
    async with (
        aiohttp.ClientSession(timeout=TIMEOUT) as session,
        session.post("https://api.firecrawl.dev/v1/scrape", json=payload, headers=headers) as resp,
    ):
        resp.raise_for_status()
        data = await resp.json()
    markdown = (data.get("data") or {}).get("markdown")
    if not markdown:
        raise RuntimeError("Firecrawl returned no markdown")
    return markdown


async def _jina(url: str) -> str:
    headers = {"Accept": "text/markdown", "X-Return-Format": "markdown", "X-No-Cache": "true"}
    if key := os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    async with (
        aiohttp.ClientSession(timeout=TIMEOUT) as session,
        session.get(f"https://r.jina.ai/{url}", headers=headers) as resp,
    ):
        resp.raise_for_status()
        markdown = await resp.text()
    if not markdown.strip():
        raise RuntimeError("Jina returned empty content")
    return markdown


async def _html(url: str) -> str:
    loader = AsyncHtmlLoader([url], requests_per_second=2, autoset_encoding=False, encoding="utf-8")
    docs = await loader.aload()
    soup = BeautifulSoup(docs[0].page_content, "html.parser")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    title = soup.title.get_text(strip=True) if soup.title else url
    body = markdownify(str(main), heading_style="ATX")
    return f"# {title}\n\n{body}"


_BACKENDS = {"firecrawl": _firecrawl, "jina": _jina, "html": _html}
# Remote browsers sometimes return an ad/redirect error page instead of the article
_BAD_MARKERS = ("ERR_BLOCKED_BY_CLIENT", "This page has been blocked", "Just a moment...", "Access Denied")
MIN_CHARS = 1500


def _validate(markdown: str) -> str:
    if len(markdown) < MIN_CHARS:
        raise RuntimeError(f"content too short ({len(markdown)} chars)")
    if marker := next((m for m in _BAD_MARKERS if m in markdown), None):
        raise RuntimeError(f"blocked/placeholder page detected ({marker})")
    return markdown


_URL = r'<?(?:[^()\s]|\([^()]*\))*>?(?:\s+"[^"]*")?'
_LINK = rf"\[(?P<text>(?:[^\[\]]|\[[^\]]*\])*)\]\({_URL}\)"
_FENCE_RE = re.compile(r"(```.*?```|~~~.*?~~~)", re.DOTALL)
_IMAGE_RE = re.compile(rf"!\[(?P<alt>[^\]]*)\]\({_URL}\)")
_LINK_RE = re.compile(_LINK)
_REF_LINK_RE = re.compile(r"\[(?P<text>[^\[\]]+)\]\[[^\]]*\]")
_REF_DEF_RE = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.MULTILINE)
# Lines made only of links (nav menus, "related articles", link-list entries), optionally bulleted
_LINK_ONLY_RE = re.compile(rf"^[ \t]*(?:(?:[*+>-]|\d+\.)[ \t]+)?(?:!?{_LINK}[ \t|,·•-]*)+$", re.MULTILINE)
_BARE_URL_RE = re.compile(r"<?https?://[^\s)>\]]+>?")
_EMPTY_BULLET_RE = re.compile(r"^[ \t]*(?:[*+-]|\d+\.)[ \t]*$", re.MULTILINE)


def _strip_links_text(text: str) -> str:
    text = _LINK_ONLY_RE.sub("", text)
    text = _REF_DEF_RE.sub("", text)
    text = _IMAGE_RE.sub(lambda m: m["alt"].strip(), text)
    text = _LINK_RE.sub(lambda m: m["text"], text)
    text = _REF_LINK_RE.sub(lambda m: m["text"], text)
    text = _BARE_URL_RE.sub("", text)
    return _EMPTY_BULLET_RE.sub("", text)


def _strip_links(markdown: str) -> str:
    """Drop links/images/bare URLs (keeping link text) and link-only lines; fenced code is untouched."""
    parts = _FENCE_RE.split(markdown)
    return "".join(p if i % 2 else _strip_links_text(p) for i, p in enumerate(parts))


def _clean(markdown: str) -> str:
    markdown = re.sub(r"[ \t]+$", "", markdown, flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", markdown).strip() + "\n"


def _slug(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "-")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", f"{parsed.netloc}-{path}").strip("-").lower()
    return slug[:120]


async def scrape_url(url: str, provider: str = "auto", strip_links: bool = False) -> tuple[str, str]:
    """Return (markdown, provider_used) for a URL."""
    if provider == "auto":
        order = ["firecrawl"] if os.environ.get("FIRECRAWL_API_KEY") else []
        order += ["jina", "html"]
    else:
        order = [provider]
    errors = []
    for name in order:
        try:
            markdown = _validate(await _BACKENDS[name](url))
            return _clean(_strip_links(markdown) if strip_links else markdown), name
        except Exception as exc:  # noqa: BLE001 - any backend failure falls through to the next provider
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors))


async def scrape_urls(
    urls: list[str],
    out_dir: Path = DEFAULT_OUT,
    provider: str = "auto",
    concurrency: int = 3,
    strip_links: bool = False,
) -> dict[str, Path | None]:
    """Scrape URLs concurrently, writing one markdown file each. Failed URLs map to None."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    async def one(url: str) -> tuple[str, Path | None]:
        async with sem:
            try:
                markdown, used = await scrape_url(url, provider, strip_links)
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {url}: {exc}")
                return url, None
            path = out_dir / f"{_slug(url)}.md"
            path.write_text(f"<!-- source: {url} | provider: {used} -->\n\n{markdown}", encoding="utf-8")
            print(f"OK   [{used}] {url} -> {path.name}")
            return url, path

    return dict(await asyncio.gather(*(one(u) for u in urls)))


def _read_urls(path: Path) -> list[str]:
    lines = (ln.strip() for ln in path.read_text(encoding="utf-8").splitlines())
    return [ln for ln in lines if ln and not ln.startswith("#")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls", nargs="*", help="URLs to scrape (default: all in --urls-file)")
    parser.add_argument("--provider", choices=PROVIDERS, default="auto")
    parser.add_argument("--urls-file", type=Path, default=DEFAULT_URLS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--strip-links",
        action="store_true",
        help="remove links, images and bare URLs (keeping link text) and drop link-only lines",
    )
    args = parser.parse_args()

    urls = list(dict.fromkeys(args.urls or _read_urls(args.urls_file)))
    results = asyncio.run(scrape_urls(urls, args.out, args.provider, args.concurrency, args.strip_links))
    failed = [u for u, p in results.items() if p is None]
    print(f"Done: {len(results) - len(failed)}/{len(results)} saved to {args.out}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
