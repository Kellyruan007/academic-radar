#!/usr/bin/env python3
"""Generate daily data with Chinese translations using DeepSeek API."""
import json, os, glob, sys
import requests
from datetime import datetime
from pathlib import Path

RADAR_DIR = os.environ.get("RADAR_DIR", os.path.expanduser("~/academic-radar"))
SCRIPTS_DIR = Path(__file__).parent
WEBAPP_DIR = Path(os.environ.get("WEBAPP_DIR", str(SCRIPTS_DIR.parent / "webapp")))
DATA_DIR = WEBAPP_DIR / "data"
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "REPLACE_WITH_SECRET")

CATEGORY_RULES = [
    ("胰腺炎", ["pancreatitis", "胰腺炎"]),
    ("消化道出血", ["bleeding", "hemorrhage", "variceal", "hemostasis", "出血", "Dieulafoy"]),
    ("消化道早癌", ["cancer", "carcinoma", "neoplas", "dysplasia", "Barrett", "polyp", "lesion", "tumor", "早癌", "肿瘤", "癌"]),
    ("消化内镜", ["endoscop", "ERCP", "EUS", "POEM", "ESD", "EMR", "colonoscopy", "gastroscopy", "内镜", "息肉"]),
]

def classify(text: str) -> str:
    t = text.lower()
    scores = {cat: sum(1 for kw in kws if kw.lower() in t) for cat, kws in CATEGORY_RULES}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "其他"

def translate(text: str, is_abstract: bool = False) -> str:
    """Translate English to Chinese using DeepSeek."""
    if not text or not text.strip(): return ""
    try:
        if is_abstract:
            prompt = f"将以下英文医学文献摘要完整翻译成中文。保留专业术语的英文缩写（如ERCP、EUS、ESD等），保持学术严谨。翻译要完整，不要省略任何部分。只返回翻译结果，不要加引号或解释：\n{text}"
            max_tokens = 3000
        else:
            prompt = f"将以下医学文献标题翻译成简洁的中文，只返回翻译结果，不要加引号或解释：\n{text}"
            max_tokens = 200
        r = requests.post("https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user",
                "content": prompt}],
                "max_tokens": max_tokens, "temperature": 0.1}, timeout=60)
        return r.json()["choices"][0]["message"]["content"].strip().strip('"').strip("'")
    except Exception as e:
        print(f"  Translate error: {e}", file=sys.stderr)
        return ""

def generate():
    DATE = datetime.now().strftime("%Y-%m-%d")
    
    # Load radar data
    pattern = os.path.join(RADAR_DIR, "data", "radar", "*.json")
    files = sorted(glob.glob(pattern), reverse=True)
    items_raw = []
    for f in files:
        if os.path.getsize(f) > 0:
            with open(f, encoding="utf-8") as fh:
                items_raw = json.load(fh).get("items", [])
                break

    # Sort by score and take top 8
    items_raw.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    items_raw = items_raw[:8]
    
    # Transform with translation
    papers = []
    for i, item in enumerate(items_raw):
        title_en = item.get("title", "")
        category = classify(title_en + " " + item.get("abstract", ""))
        
        print(f"  Translating {i+1}/8: {title_en[:60]}...")
        title_cn = translate(title_en)
        
        abstract_en_full = (item.get("abstract","") or "")
        if abstract_en_full.strip():
            print(f"    Translating abstract {i+1}/8 ({len(abstract_en_full)} chars)...")
            abstract_cn = translate(abstract_en_full, is_abstract=True)
        else:
            abstract_cn = ""
        
        papers.append({
            "id": item.get("doi") or item.get("pmid") or f"p{i}",
            "type": "paper",
            "category": category,
            "title_cn": title_cn,
            "title_en": title_en,
            "abstract_cn": abstract_cn,
            "abstract_en": abstract_en_full,
            "authors": item.get("authors", []),
            "journal": item.get("source_journal", ""),
            "year": str(item.get("publication_date", "2026") or "2026")[:4],
            "doi": item.get("doi", ""),
            "pmid": item.get("pmid", ""),
            "url": f"https://doi.org/{item['doi']}" if item.get("doi") else (f"https://pubmed.ncbi.nlm.nih.gov/{item['pmid']}/" if item.get("pmid") else None),
            "score": item.get("relevance_score", 3),
            "source": (item.get("fetch_sources", ["PubMed"]) or ["PubMed"])[0],
            "status": item.get("status", "published"),
            "summary_cn": item.get("summary", ""),
        })
    
    # Patents
    patents = []
    patent_file = SCRIPTS_DIR / "patents_today.json"
    if patent_file.exists():
        with open(patent_file, encoding="utf-8") as f:
            patents = json.load(f)
    if not patents:
        # fallback: try webapp dir
        patent_file2 = WEBAPP_DIR / "patents_today.json"
        if patent_file2.exists():
            with open(patent_file2, encoding="utf-8") as f:
                patents = json.load(f)
    
    output = {
        "date": DATE,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "stats": {
            "total_papers": len(papers),
            "total_patents": len(patents),
            "sources": list(set(p["source"] for p in papers)),
        },
        "items": papers + patents,
    }
    
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Save latest
    with open(DATA_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # Save dated
    with open(DATA_DIR / f"{DATE}.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # Update history
    history = sorted([p.stem for p in DATA_DIR.glob("20*.json")], reverse=True)
    with open(DATA_DIR / "history.json", "w", encoding="utf-8") as f:
        json.dump({"dates": history}, f, ensure_ascii=False)
    
    print(f"\nDone: {len(papers)} papers + {len(patents)} patents saved to {DATA_DIR}")
    print(f"History: {history}")

if __name__ == "__main__":
    generate()
