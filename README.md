# Matric Papers (SA) — NSC past exam paper text extractor

Pull the text out of South African **Grade 12 NSC (matric) past exam papers** —
Mathematics (English & Afrikaans), Physical Sciences, Accounting, IT, English,
Afrikaans and every other NSC subject — straight from the official source:
the **Department of Basic Education** (education.gov.za).

## Why this exists

There is **no public API** for South African matric past papers. The DBE
publishes them as PDFs behind HTML listing pages. This tool scrapes those
listings, downloads the PDFs and extracts readable text:

- Papers with a text layer (IT, Accounting, English, most subjects) →
  extracted directly with PyMuPDF.
- Scanned papers (**Mathematics is fully scanned**) → automatic OCR with
  RapidOCR (PP-OCRv4), handles English and Afrikaans.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows   (or: source .venv/bin/activate)
pip install -r requirements.txt
```

Tested on Python 3.11+ / Windows and Linux.

## Usage

### 1. Get the index (optional — a pre-built `index.json` is included)

```bash
python matric_papers.py index --years 2020,2024
```

Scrapes the DBE listing pages and writes `index.json` with every paper,
memo, answer book and addendum: subject, year, session (November / May-June),
language, province (where tagged), and the direct download URL.

### 2. Search what's available

```bash
python matric_papers.py search -q "mathematics 2024 english p1"
python matric_papers.py search -q accounting --limit 20
```

### 3. Download + extract text

```bash
# Mathematics, Accounting, Physical Sciences, IT + English/Afrikaans, 2024 papers only
python matric_papers.py fetch \
  --subject mathematics,accounting,physical sciences,information technology,english,afrikaans \
  --years 2024 --lang english,afrikaans

# Gauteng-tagged papers only, memos included, all years
python matric_papers.py fetch --province gauteng --memos

# One session only (nov | may_june | feb_march | exemplars)
python matric_papers.py fetch --subject mathematics --years 2024 --session nov

# PDFs only (no text extraction)
python matric_papers.py fetch --years 2024 --pdf-only
```

Output layout:

```
papers/
  Mathematics/
    2024_nov/
      Paper_1_(English).pdf
      Paper_1_(English).pdf.txt     # extracted text
      Paper_1_(Afrikaans).pdf.txt
      Paper_2_(English).pdf.txt
  Accounting/
    2024_nov/
      ...
```

## Notes

- **Province filters** only apply to rows the DBE tags with a province
  (e.g. `Afrikaans SAL P2 (Gauteng)`); most papers are national.
- **OCR is slow** (~5 s/page for scanned papers). Let it run — or use
  `--pdf-only` to grab the files and extract later.
- Papers remain © **Department of Basic Education**. This tool downloads
  them for personal study; it does not re-host or redistribute them.
- Tool code is MIT licensed; the papers are not.

## Data source

- Listings: https://www.education.gov.za/Curriculum/NationalSeniorCertificate(NSC)Examinations/NSCPastExaminationpapers.aspx
- Papers: `https://www.education.gov.za/LinkClick.aspx?fileticket=...`