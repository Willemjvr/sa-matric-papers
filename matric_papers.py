#!/usr/bin/env python3
"""
Matric Papers (SA NSC) — download and extract text from South African
Grade 12 National Senior Certificate past exam papers published by the
Department of Basic Education (education.gov.za).

No public API exists; this tool scrapes the DBE's official paper listings,
downloads the PDFs, and pulls the text out of them (direct text layer when
present, OCR fallback for scanned papers such as Mathematics).

Commands:
  index    Scrape the DBE listing pages -> index.json
  fetch    Download PDFs from index.json and extract text -> papers/
  search   List matching entries in index.json

Examples:
  python matric_papers.py index --years 2020,2024
  python matric_papers.py search --subject mathematics --lang english
  python matric_papers.py fetch --subject mathematics,accounting --years 2024 \
      --lang english,afrikaans --province gauteng --out papers

Dependencies (pip install -r requirements.txt):
  pymupdf            PDF rendering + text extraction
  rapidocr-onnxruntime   OCR for scanned papers (auto-used when no text layer)

Papers remain copyright of the Department of Basic Education; use for
personal study only.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import pathlib
import re
import socket
import sys
import time
import urllib.parse
import urllib.request

socket.setdefaulttimeout(30)  # DBE pages are slow; cap each request

BASE = "https://www.education.gov.za"
INDEX_URL = (
    BASE
    + "/Curriculum/NationalSeniorCertificate(NSC)Examinations/"
    + "NSCPastExaminationpapers.aspx"
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

TAG = "matric-papers"


def log(msg: str) -> None:
    print(f"[{TAG}] {msg}", flush=True)


def http_get(url: str, referer: str | None = None, binary: bool = False) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Referer": referer or INDEX_URL,
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


# ---------------------------------------------------------------- index ----

MODULE_RE = re.compile(
    r'<h2>\s*<span[^>]*class="eds_containerTitle"[^>]*>(.*?)</span>\s*</h2>'
    r"(.*?)(?=<div class=\"DnnModule|<h2>\s*<span[^>]*class=\"eds_containerTitle\"|$)",
    re.S | re.I,
)
ROW_RE = re.compile(
    r'<td class="TitleCell"><a[^>]+href="([^"]*fileticket=[^"]*)"[^>]*>(.*?)</a>',
    re.S | re.I,
)
YEAR_LINK_RE = re.compile(r'<a[^>]+href="(/LinkClick\.aspx\?link=[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", s)).strip()


def parse_year_page(page_url: str, label: str = "") -> list[dict]:
    raw = http_get(page_url).decode("utf-8", errors="replace")
    year_match = re.search(r"(\d{4})", label) or re.search(r"(\d{4})", page_url.rsplit("/", 1)[-1])
    year = int(year_match.group(1)) if year_match else 0
    lo = (page_url + " " + label).lower()
    if re.search(r"\bexemplar", lo):
        sess = "exemplars"
    elif re.search(r"\bfeb|february|march", lo):
        sess = "feb_march"
    elif re.search(r"\bmay|june", lo):
        sess = "may_june"
    else:
        sess = "nov"
    entries: list[dict] = []
    for m in MODULE_RE.finditer(raw):
        subject = strip_tags(m.group(1))
        body = m.group(2)
        for r in ROW_RE.finditer(body):
            href = html.unescape(r.group(1))
            title = strip_tags(r.group(2))
            if not title:
                continue
            url = BASE + href.split("&forcedownload")[0]
            typ = "memo" if re.search(r"\bmemo\b", title, re.I) else "paper"
            entries.append({
                "year": year,
                "session": sess,
                "subject": subject,
                "title": title,
                "type": typ,
                "language": detect_language(title),
                "province": detect_province(title),
                "url": url,
            })
    return entries


def detect_language(title: str) -> str:
    t = title.lower()
    if "afrikaans" in t:
        return "afrikaans"
    if "english" in t:
        return "english"
    if "&" in t or "and" in t:
        return "both"
    return "unknown"


def detect_province(title: str) -> str | None:
    m = re.search(r"\(([^)]*)\)", title)
    if not m:
        return None
    prov = m.group(1).strip()
    return prov if prov else None


def resolve_year_urls() -> list[tuple[int, str, str]]:
    """Read the DBE index page and return (year, page_url, label) per year."""
    raw = http_get(INDEX_URL).decode("utf-8", errors="replace")
    out: list[tuple[int, str, str]] = []
    for m in YEAR_LINK_RE.finditer(raw):
        label = strip_tags(m.group(2))
        ym = re.search(r"(\d{4})", label)
        if not ym or "NSC" not in label:
            continue
        href = html.unescape(m.group(1))
        full = BASE + href
        # numeric LinkClick ids redirect; follow once (loop guard)
        for _ in range(2):
            body = http_get(full).decode("utf-8", errors="replace")
            lm = re.search(r'<title>(.*?)</title>', body, re.S)
            if lm and "error" in lm.group(1).lower() and len(body) < 5000:
                new = re.search(r'<a[^>]+href="([^"]+)"', body)
                if new:
                    full = urllib.parse.urljoin(full, new.group(1))
                    continue
            break
        out.append((int(ym.group(1)), full, label))
    # sort newest first but keep session pairing obvious
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def cmd_index(args: argparse.Namespace) -> None:
    years_want = {int(y) for y in args.years.split(",")} if args.years else None
    sessions_want = set(args.sessions.split(",")) if args.sessions else None

    url_pairs = resolve_year_urls()
    # overwrite session from the actual URL basename for correctness
    by_year: dict[int, dict[str, str]] = {}
    labels: dict[tuple[int, str], str] = {}
    for y, u, label in url_pairs:
        lo = (u + " " + label).lower()
        if re.search(r"\bexemplar", lo):
            s = "exemplars"
        elif re.search(r"\bfeb|february|march", lo):
            s = "feb_march"
        elif re.search(r"\bmay|june", lo):
            s = "may_june"
        else:
            s = "nov"
        by_year.setdefault(y, {})[s] = u
        labels[(y, s)] = label

    pages: list[tuple[int, str, str, str]] = []
    for year in sorted(by_year, reverse=True):
        if years_want is not None and year not in years_want:
            continue
        for sess, url in by_year[year].items():
            if sessions_want is not None and sess not in sessions_want:
                continue
            pages.append((year, sess, url, labels[(year, sess)]))

    def fetch_one(item: tuple[int, str, str, str]) -> tuple[int, str, list[dict]]:
        year, sess, url, label = item
        try:
            return year, sess, parse_year_page(url, label)
        except Exception as e:  # noqa: BLE001
            log(f"failed {year} {sess}: {e}")
            return year, sess, []

    all_entries: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for year, sess, entries in ex.map(fetch_one, pages):
            all_entries.extend(entries)
            n_subj = len({e["subject"] for e in entries})
            log(f"indexed {year} {sess}: {len(entries)} docs across {n_subj} subjects")

    out = {
        "source": "Department of Basic Education (education.gov.za) NSC past papers",
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(all_entries),
        "papers": all_entries,
    }
    out_path = pathlib.Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"wrote {out_path} ({len(all_entries)} docs from {len(pages)} pages)")


# ---------------------------------------------------------------- fetch ----

def slug(title: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", title, flags=re.UNICODE).strip().replace(" ", "_")
    return s or "paper"


def pdf_text(pdf_bytes: bytes) -> tuple[str, bool]:
    """Return (text, used_ocr). Direct text layer first, OCR fallback."""
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        texts = [p.get_text() for p in doc]
    finally:
        doc.close()
    joined = "\n".join(texts)
    if len(joined.strip()) > 100:
        return joined, False

    # scanned paper -> OCR
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    parts: list[str] = []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=300)
            png = pix.tobytes("png")
            res, _ = ocr(png)
            lines = [ln[1] for ln in (res or [])]
            parts.append(f"--- page {i + 1} ---\n" + "\n".join(lines))
            log(f"  OCR page {i + 1}/{doc.page_count}")
    finally:
        doc.close()
    return "\n".join(parts), True


def download(url: str, referer: str) -> bytes:
    return http_get(url + "&forcedownload=true", referer=referer, binary=True)


def norm_words(s: str) -> list[str]:
    return re.sub(r"\s+", " ", s.strip().lower()).split()


def subject_match(subject: str, query: str) -> bool:
    """Match allowing DBE's inconsistent spellings ('Physical Science' vs
    'Physical Sciences', suffixes like '(Senior Certificate)')."""
    q = norm_words(query)
    p = norm_words(subject)
    n = min(len(q), 1 if len(q) == 1 else 2)
    return p[:n] == q[:n] and p[: max(1, len(q))] == q[: max(1, len(q))]


def cmd_fetch(args: argparse.Namespace) -> None:
    index = json.loads(pathlib.Path(args.index).read_text(encoding="utf-8"))
    papers = index["papers"]

    subj_pat = [s.strip() for s in args.subject.split(",") if s.strip()] or None
    years = {int(y) for y in args.years.split(",")} if args.years else None
    langs = {l.strip().lower() for l in args.lang.split(",") if l.strip()} or None

    def keep(p: dict) -> bool:
        if subj_pat and not any(subject_match(p["subject"], q) for q in subj_pat):
            return False
        if years and p["year"] not in years:
            return False
        if args.session and p["session"] != args.session:
            return False
        if langs and p["language"] not in langs and p["language"] != "both":
            return False
        if args.province and (p["province"] or "").lower() != args.province.lower():
            return False
        if not args.memos and p["type"] == "memo":
            return False
        return True

    selected = [p for p in papers if keep(p)]
    log(f"{len(selected)} matching docs")
    if not selected:
        sys.exit("nothing matched — check --subject/--years/--lang filters")

    out_dir = pathlib.Path(args.out)
    done = 0
    for p in selected:
        subj_dir = out_dir / re.sub(r"[^\w\- ]+", "", p["subject"]).strip().replace(" ", "_")
        file_dir = subj_dir / f"{p['year']}_{p['session']}"
        file_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = file_dir / f"{slug(p['title'])}.pdf"
        txt_path = pdf_path.with_suffix(".pdf.txt")
        if pdf_path.exists() and txt_path.exists() and not args.force:
            log(f"skip (exists): {p['subject']} {p['year']} {p['title']}")
            continue
        try:
            pdf = download(p["url"], referer=p["url"])
        except Exception as e:  # noqa: BLE001
            log(f"download failed: {p['subject']} {p['year']} {p['title']}: {e}")
            continue
        if not pdf.startswith(b"%PDF"):
            log(f"skip: {p['subject']} {p['year']} {p['title']} — server did not return a PDF ({len(pdf)} bytes)")
            continue
        pdf_path.write_bytes(pdf)
        if not args.pdf_only:
            try:
                txt, used_ocr = pdf_text(pdf)
            except Exception as e:  # noqa: BLE001
                log(f"extract failed: {p['subject']} {p['year']} {p['title']}: {e}")
                pdf_path.unlink(missing_ok=True)
                continue
            txt_path.write_text(txt, encoding="utf-8")
            log(f"{'OCR ' if used_ocr else 'text'} {p['subject']} {p['year']} {p['title']} ({len(txt)} chars)")
        else:
            log(f"pdf {p['subject']} {p['year']} {p['title']} ({len(pdf)} bytes)")
        done += 1
        time.sleep(0.3)  # be polite
    log(f"done: {done} docs -> {out_dir}")


# --------------------------------------------------------------- search ----

def cmd_search(args: argparse.Namespace) -> None:
    index = json.loads(pathlib.Path(args.index).read_text(encoding="utf-8"))
    rows = []
    for p in index["papers"]:
        hay = " ".join([p["subject"], str(p["year"]), p["session"], p["title"]]).lower()
        if args.q and args.q.lower() not in hay:
            continue
        rows.append(p)
    log(f"{len(rows)} matches (of {index['count']})")
    for p in rows[: args.limit]:
        print(f"{p['year']} {p['session']:8s} {p['subject']:28s} {p['title']:45s} {p['url']}")


# ------------------------------------------------------------------ main ---

def main() -> None:
    ap = argparse.ArgumentParser(prog="matric_papers", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="scrape DBE listings -> index.json")
    pi.add_argument("--years", help="comma list, e.g. 2020,2024 (default: all found)")
    pi.add_argument("--sessions", help="comma list: nov,may_june (default: both)")
    pi.add_argument("--out", default="index.json")
    pi.set_defaults(fn=cmd_index)

    pf = sub.add_parser("fetch", help="download + extract text from index.json")
    pf.add_argument("--index", default="index.json")
    pf.add_argument("--subject", default="", help="comma list, e.g. mathematics,accounting")
    pf.add_argument("--years", help="comma list, e.g. 2024")
    pf.add_argument("--lang", default="", help="comma list: english,afrikaans (default: all)")
    pf.add_argument("--province", help="e.g. gauteng (filters province-tagged rows)")
    pf.add_argument("--session", help="nov, may_june, feb_march, exemplars (default: all)")
    pf.add_argument("--memos", action="store_true", help="include memos (default: papers only)")
    pf.add_argument("--pdf-only", action="store_true", help="download PDFs but skip text extraction")
    pf.add_argument("--force", action="store_true", help="re-download existing files")
    pf.add_argument("--out", default="papers")
    pf.set_defaults(fn=cmd_fetch)

    ps = sub.add_parser("search", help="search index.json entries")
    ps.add_argument("--index", default="index.json")
    ps.add_argument("-q", default="", help="free-text filter")
    ps.add_argument("--limit", type=int, default=50)
    ps.set_defaults(fn=cmd_search)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()