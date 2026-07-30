#!/usr/bin/env python3
"""Fetch recent medical device/endoscopy patents.
Primary source: Google Patents (if accessible), fallback: pre-curated real patent data.
Summaries are faithful translations of the original abstract, not AI fabrications."""
import json, requests, sys, os
from datetime import datetime

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "REPLACE_WITH_SECRET")
SCRIPTS_DIR = os.path.dirname(__file__) or "."
OUTPUT = os.path.join(SCRIPTS_DIR, "..", "webapp", "patents_today.json")
FALLBACK_FILE = os.path.join(SCRIPTS_DIR, "patents_today.json")

SEARCH_QUERIES = [
    "endoscopic device OR ERCP catheter",
    "endoscopic submucosal dissection instrument",
    "hemostatic clip endoscopy",
    "pancreatitis treatment device",
    "GI bleeding detection device",
]

def search_patents(query: str, limit: int = 3) -> list:
    """Search Google Patents."""
    results = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AcademicRadar/1.0)"}
        url = f"https://patents.google.com/xhr/query?url=q%3D{requests.utils.quote(query)}%26num%3D{limit}&exp="
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        
        clusters = data.get("results", {}).get("cluster", [])
        for cluster in clusters[:limit]:
            try:
                patent = cluster.get("result", [{}])[0]
                pub_num = patent.get("publication_number", "")
                if not pub_num:
                    continue
                title = patent.get("title", "")
                abstract = patent.get("abstract", "")
                assignee = patent.get("assignee", "")
                filing = patent.get("filing_date", "")
                
                results.append({
                    "patent_number": pub_num,
                    "title_en": title or "",
                    "abstract_en": (abstract or "")[:400],
                    "assignee": assignee or "未知",
                    "filing_date": filing or "",
                    "url": "",
                })
            except Exception:
                continue
    except Exception as e:
        print(f"  Search error for '{query}': {e}", file=sys.stderr)
    return results

def translate(text: str, prompt_type: str = "title") -> str:
    """Translate via DeepSeek."""
    if not text or not text.strip():
        return ""
    prompts = {
        "title": "将以下专利标题翻译成简洁的中文，只返回翻译结果，不要加引号或解释：\n",
        "abstract": "将以下专利摘要翻译成中文，只返回翻译结果：\n",
    }
    try:
        r = requests.post("https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user",
                "content": prompts.get(prompt_type, "") + text}],
                "max_tokens": 500, "temperature": 0.1}, timeout=30)
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  Translate error: {e}", file=sys.stderr)
        return ""

def categorize(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["endoscop", "ercp", "eus", "colono", "polyp", "scope"]):
        return "消化内镜"
    if any(w in t for w in ["bleeding", "hemostat", "hemorrhage", "clip", "variceal"]):
        return "消化道出血"
    if any(w in t for w in ["pancreat", "pancrea"]):
        return "胰腺炎"
    if any(w in t for w in ["tumor", "cancer", "neoplas", "lesion", "dysplasia", "polyp"]):
        return "消化道早癌"
    return "消化内镜"

def generate_summary(title_cn: str, abstract_cn: str) -> str:
    """Generate a faithful Chinese summary (≤300 chars) strictly from the patent abstract.
    No fabrication — only summarizes what the abstract actually says."""
    if not abstract_cn or not abstract_cn.strip():
        return ""
    prompt = f"""请用中文对以下专利摘要做精炼总结，严格控制在300字以内。要求：
1. 只基于摘要原文的信息，不要添加任何原文没有的内容
2. 用临床医生能理解的语言概括：这项专利是什么、解决什么问题、怎么做的
3. 直接返回总结内容，不要前缀

专利标题：{title_cn}
专利摘要：{abstract_cn[:800]}"""
    try:
        r = requests.post("https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400, "temperature": 0.1}, timeout=30)
        result = r.json()["choices"][0]["message"]["content"].strip()
        for prefix in ["简介：", "简介:", "【简介】", "专利简介：", "总结："]:
            if result.startswith(prefix):
                result = result[len(prefix):].strip()
        return result
    except Exception as e:
        print(f"  Summary generation error: {e}", file=sys.stderr)
        return ""

def fetch_patents():
    """Fetch patents: try Google Patents first, fall back to pre-curated real patent data."""
    print("Searching for recent patents...")
    all_patents = []
    seen = set()
    
    for query in SEARCH_QUERIES:
        print(f"  Query: {query}")
        results = search_patents(query, limit=2)
        for r in results:
            if r["patent_number"] not in seen:
                seen.add(r["patent_number"])
                all_patents.append(r)
    
    all_patents.sort(key=lambda x: x["patent_number"], reverse=True)
    all_patents = all_patents[:2]
    
    output = []
    
    if all_patents:
        # Google Patents data available: translate + summarize
        for i, p in enumerate(all_patents):
            print(f"  Translating patent {i+1}/2: {p['title_en'][:60]}...")
            title_cn = translate(p["title_en"], "title")
            abstract_cn = translate(p["abstract_en"], "abstract")
            category = categorize(p["title_en"] + " " + p["abstract_en"])
            summary_cn = generate_summary(title_cn, abstract_cn)
            
            output.append({
                "id": f"patent-{p['patent_number']}",
                "type": "patent",
                "category": category,
                "title_cn": title_cn,
                "title_en": p["title_en"],
                "abstract_cn": abstract_cn,
                "abstract_en": p["abstract_en"],
                "summary_cn": summary_cn or abstract_cn[:200] if abstract_cn else "",
                "patent_number": p["patent_number"],
                "filing_date": p["filing_date"],
                "assignee": p["assignee"],
                "url": "",
                "score": 3,
                "source": "Google Patents",
                "journal": "", "authors": [], "doi": "",
                "year": (p["filing_date"] or "2026")[:4],
                "status": "published",
            })
    else:
        # Fallback: pre-curated real patent data from scripts/patents_today.json
        print("  Google Patents not accessible, using pre-curated real patent data...")
        if os.path.exists(FALLBACK_FILE):
            with open(FALLBACK_FILE, encoding="utf-8") as f:
                existing = json.load(f)
            if isinstance(existing, list) and len(existing) > 0:
                output = existing
                # Regenerate summaries if abstract exists but summary is empty
                for p in output:
                    if not p.get("summary_cn") and p.get("abstract_cn"):
                        p["summary_cn"] = generate_summary(
                            p.get("title_cn", ""), p.get("abstract_cn", ""))
                print(f"  Loaded {len(output)} pre-curated patents")
            else:
                print("  Fallback file is empty!")
        else:
            print(f"  Fallback file not found: {FALLBACK_FILE}")
    
    if not output:
        print("  No patents generated!")
        with open(OUTPUT, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(output)} patents to {OUTPUT}")
    return output

if __name__ == "__main__":
    fetch_patents()
