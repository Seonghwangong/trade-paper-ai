# Trade Paper AI

Trade Paper AI is a SaaS platform for generating trade documents automatically.

## Features

- Company Information Management
- Commercial Invoice PDF Generation
- Packing List PDF Generation
- Invoice List
- Packing List
- JSON Data Storage

## Technology Stack

- FastAPI
- Python
- HTML
- CSS
- JavaScript
- ReportLab

## Installation

```bash
pip install -r requirements.txt
```

## Run

```bash
python3 -m uvicorn app.main:app --reload --port 8002
```

## Open Browser

```text
http://127.0.0.1:8002
```

## Project Structure

```text
app/
 ├── main.py
 ├── invoice.py
 ├── packing.py
 ├── static/
 │   ├── index.html
 │   ├── company.html
 │   ├── invoice.html
 │   └── packing.html
 └── data/
     ├── company.json
     ├── invoices.json
     └── packing_lists.json
```