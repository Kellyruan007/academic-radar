#!/usr/bin/env python3
"""Fetch 5 top GI endoscopy journals, filter by user's topics, randomly select 2 per journal daily.
English journals: PubMed → extract latest volume/issue → fetch ALL articles in that issue.
Chinese journals: yiigle.com → scrape latest issue table of contents.
Filter: only keep articles matching user's 4 research topics.
Random: select 2 per journal, seeded by date for daily consistency.
All English content translated to Chinese via DeepSeek."""

import json, os, sys, requests, time, re, random, hashlib
from datetime import datetime

DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# ─── Journal Configuration ───
ENGLISH_JOURNALS = [
    {"id": "gastrointest-endosc", "name": "Gastrointestinal Endoscopy", "name_cn": "Gastrointestinal Endoscopy (GIE)",
     "desc": "ASGE官方期刊，内镜领域SCI顶刊", "pubmed_query": '"Gastrointest Endosc"[Journal]'},
    {"id": "endoscopy", "name": "Endoscopy", "name_cn": "Endoscopy",
     "desc": "ESGE官方期刊，与GIE齐名", "pubmed_query": '"Endoscopy"[Journal]'},
    {"id": "dig-endosc", "name": "Digestive Endoscopy", "name_cn": "Digestive Endoscopy",
     "desc": "JGES官方期刊，亚洲内镜权威", "pubmed_query": '"Dig Endosc"[Journal]'},
    {"id": "surg-endosc", "name": "Surgical Endoscopy", "name_cn": "Surgical Endoscopy",
     "desc": "SAGES/EAES官方期刊，介入内镜与微创外科顶刊", "pubmed_query": '"Surg Endosc"[Journal]'},
    {"id": "endosc-ultrasound", "name": "Endoscopic Ultrasound", "name_cn": "Endoscopic Ultrasound (EUS)",
     "desc": "EUS专科期刊，超声内镜领域权威", "pubmed_query": '"Endosc Ultrasound"[Journal]'},
]

# ─── Topic Classification ───
# Matches the user's 4 research focus areas
# Keywords organized by specificity:
#   tier1 = high-confidence (single match sufficient)
#   tier2 = low-confidence (need 2+ matches from tier1+tier2 combined)

TOPIC_KEYWORDS = {
    "胰腺炎": {
        "tier1": ["pancreatitis", "pancreatic necrosis", "acute pancreatitis", "chronic pancreatitis",
                   "胰腺炎", "胰腺坏死", "重症胰腺炎", "急性胰腺炎", "慢性胰腺炎"],
        "tier2": ["pancreatic", "pancreas", "walled-off", "pseudocyst", "胰腺", "胰周", "假性囊肿"]
    },
    "消化道出血": {
        "tier1": ["bleeding", "hemorrhage", "hemostasis", "hemostatic", "variceal bleeding",
                   "dieulafoy", "hematemesis", "melena", "hematochezia", "gi bleed",
                   "出血", "止血", "静脉曲张出血"],
        "tier2": ["ulcer bleed", "variceal", "hemoclip", "coagulation", "sengstaken",
                   "静脉曲张", "溃疡出血", "钛夹"]
    },
    "消化道早癌": {
        "tier1": ["dysplasia", "barrett", "polyp", "adenoma", "early gastric", "early esophageal",
                   "early colorectal", "intramucosal", "squamous cell carcinoma",
                   "submucosal dissection", "endoscopic resection",
                   "早癌", "息肉", "腺瘤", "异型增生", "巴雷特", "黏膜内癌", "上皮内瘤变"],
        "tier2": ["carcinoma", "tumor", "malignan", "adenocarcinoma",
                   "肿瘤", "癌", "不典型增生"]
    },
    "消化内镜": {
        "tier1": ["ercp", "eus", "poem", "esd", "emr", "cholangioscopy",
                   "endoscopic ultrasound", "endoscopic retrograde",
                   "colonoscopy", "gastroscopy", "enteroscopy",
                   "sphincterotomy", "stent placement",
                   "ERCP", "EUS", "ESD", "EMR", "POEM",
                   "内镜", "肠镜", "胃镜", "超声内镜", "胆道镜", "小肠镜"],
        "tier2": ["endoscop", "catheter", "guidewire", "balloon dilatation",
                   "resection", "dissection", "stent",
                   "支架", "切开", "扩张", "圈套器"]
    }
}

# ─── Quality Filter ───
# Article types to EXCLUDE (non-research, non-clinical content)
EXCLUDED_PUB_TYPES = {
    "correction", "published erratum", "editorial", "letter",
    "comment", "news", "retraction of publication", "retracted publication",
    "expression of concern", "duplicate publication"
}

# Article types that are OK but may lack abstracts (e.g. short case reports)
# We still require meaningful content
LOW_CONTENT_TYPES = {"case reports", "brief report"}


def is_quality_article(article):
    """Filter out low-quality articles: corrections, editorials, no-abstract items."""
    # 1. Filter by publication type
    pub_types = [pt.lower() for pt in article.get("pub_types", [])]
    for pt in pub_types:
        if pt in EXCLUDED_PUB_TYPES:
            return False

    # 2. Require meaningful abstract (English journals)
    abstract = (article.get("abstract_en") or "").strip()
    if len(abstract) < 80:
        # Allow case reports with shorter abstracts if they have meaningful content
        title = (article.get("title_en") or "").lower()
        is_case = any(t in pub_types for t in LOW_CONTENT_TYPES) or \
                  "case" in title or "rare case" in title
        if not is_case or len(abstract) < 30:
            return False

    # 3. Filter out titles that are clearly non-research
    title_lower = (article.get("title_en") or "").lower()
    bad_title_patterns = [
        "correction to", "erratum", "retraction:", "reply to",
        "response to", "letter to the editor", "letter:",
        "author's reply", "authors' reply"
    ]
    for pat in bad_title_patterns:
        if pat in title_lower:
            return False

    return True


def classify_article(title_en, title_cn, abstract_en, abstract_cn):
    """Classify an article into one of the 4 topic categories using two-tier keywords.
    Tier1 match alone is sufficient; tier2 needs 2+ matches (tier1+tier2 combined).
    Returns category name or None if no match."""
    text_en = f"{title_en or ''} {abstract_en or ''}".lower()
    text_cn = f"{title_cn or ''} {abstract_cn or ''}"

    for cat, kw in TOPIC_KEYWORDS.items():
        tier1_hits = 0
        tier2_hits = 0
        # Check English keywords
        for w in kw["tier1"]:
            if w.lower() in text_en:
                tier1_hits += 1
        for w in kw["tier2"]:
            if w.lower() in text_en:
                tier2_hits += 1
        # Check Chinese keywords
        for w in kw["tier1"]:
            if w in text_cn:
                tier1_hits += 1
        for w in kw["tier2"]:
            if w in text_cn:
                tier2_hits += 1

        total = tier1_hits + tier2_hits
        if tier1_hits >= 1 or total >= 2:
            return cat
    return None


# ─── DeepSeek Translation ───
def translate(text, is_title=False):
    if not text or not text.strip():
        return ""
    try:
        if is_title:
            prompt = f"将以下医学文献标题翻译成简洁的中文，只返回翻译结果，不要加引号或解释：\n{text}"
            max_tokens = 200
        else:
            prompt = f"将以下英文医学文献摘要完整翻译成中文。保留专业术语的英文缩写（如ERCP、EUS、ESD、POEM等），保持学术严谨。翻译要完整，不要省略任何部分。只返回翻译结果，不要加引号或解释：\n{text}"
            max_tokens = 3000
        r = requests.post(DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max_tokens, "temperature": 0.1}, timeout=60)
        return r.json()["choices"][0]["message"]["content"].strip().strip('"').strip("'")
    except Exception as e:
        print(f"  Translate error: {e}", file=sys.stderr)
        return ""


# ─── PubMed: Full Issue Fetch ───
def pubmed_search(query, max_results=500):
    """POST esearch to PubMed with retry on failure."""
    for attempt in range(3):
        try:
            r = requests.post(ESEARCH, data={"db": "pubmed", "term": query, "retmax": max_results,
                                              "retmode": "json", "sort": "pubdate"}, timeout=30)
            r.raise_for_status()
            return r.json().get("esearchresult", {}).get("idlist", [])
        except Exception as e:
            print(f"  PubMed search error (attempt {attempt+1}/3): {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    return []


def pubmed_fetch(pmids):
    """Fetch article details from PubMed by PMIDs with retry."""
    if not pmids:
        return []
    for attempt in range(3):
        try:
            r = requests.post(EFETCH, data={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}, timeout=60)
            r.raise_for_status()
            break
        except Exception as e:
            print(f"  PubMed fetch error (attempt {attempt+1}/3): {e}", file=sys.stderr)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                return []

    xml = r.text
    articles = []
    blocks = re.split(r'<PubmedArticle>', xml)[1:]

    for block in blocks:
        pmid_m = re.search(r'<PMID[^>]*>(\d+)</PMID>', block)
        title_m = re.search(r'<ArticleTitle>(.*?)</ArticleTitle>', block, re.DOTALL)

        # Abstract (all parts)
        abs_parts = re.findall(r'<AbstractText[^>]*>(.*?)</AbstractText>', block, re.DOTALL)
        if not abs_parts:
            abs_parts = re.findall(r'<AbstractText>(.*?)</AbstractText>', block, re.DOTALL)
        abstract = " ".join(abs_parts).strip() if abs_parts else ""

        # Authors
        authors = []
        for ab in re.findall(r'<Author[ >].*?</Author>', block, re.DOTALL):
            last_m = re.search(r'<LastName>(.*?)</LastName>', ab)
            init_m = re.search(r'<Initials>(.*?)</Initials>', ab)
            if last_m:
                a = last_m.group(1)
                if init_m: a += " " + init_m.group(1)
                authors.append(a)

        # Journal
        j_m = re.search(r'<Title>(.*?)</Title>', block)
        journal = j_m.group(1).strip() if j_m else ""

        # Year
        y_m = re.search(r'<PubDate>.*?<Year>(\d+)</Year>', block, re.DOTALL)
        year = y_m.group(1) if y_m else ""

        # Volume / Issue
        vol_m = re.search(r'<Volume>(\d+)</Volume>', block)
        iss_m = re.search(r'<Issue>(\d+)</Issue>', block)
        volume = vol_m.group(1) if vol_m else ""
        issue = iss_m.group(1) if iss_m else ""

        # DOI
        doi_m = re.search(r'<ArticleId IdType="doi">(.*?)</ArticleId>', block)
        doi = doi_m.group(1).strip() if doi_m else ""

        # Publication types (filter out non-research content later)
        pub_types = re.findall(r'<PublicationType[^>]*>(.*?)</PublicationType>', block)

        title = title_m.group(1).strip() if title_m else ""
        if title:
            articles.append({"pmid": pmid_m.group(1) if pmid_m else "",
                "title_en": title, "abstract_en": abstract, "authors": authors[:8],
                "journal": journal, "year": year, "volume": volume, "issue": issue, "doi": doi,
                "pub_types": pub_types})
    return articles


def quick_topic_match(article):
    """Quick topic check using only English title+abstract (no translation needed)."""
    text_en = f"{article.get('title_en', '') or ''} {article.get('abstract_en', '') or ''}".lower()
    for cat, kw in TOPIC_KEYWORDS.items():
        tier1 = 0
        tier2 = 0
        for w in kw["tier1"]:
            if w.lower() in text_en:
                tier1 += 1
        for w in kw["tier2"]:
            if w.lower() in text_en:
                tier2 += 1
        if tier1 >= 1 or (tier1 + tier2) >= 2:
            return True
    return False


def fetch_articles_for_issue(j, vol, iss):
    """Fetch and quality-filter articles for a specific volume/issue. Returns list of articles."""
    issue_query = f'{j["pubmed_query"]} AND {vol}[Volume] AND {iss}[Issue]'
    issue_pmids = pubmed_search(issue_query, max_results=500)
    print(f"  Vol {vol}, Issue {iss}: {len(issue_pmids)} PMIDs")

    if len(issue_pmids) == 0:
        return []

    time.sleep(1.0)
    articles = pubmed_fetch(issue_pmids)
    if len(articles) == 0 and len(issue_pmids) > 0:
        print(f"  Retrying fetch after delay...")
        time.sleep(3.0)
        articles = pubmed_fetch(issue_pmids)

    before_qc = len(articles)
    articles = [a for a in articles if is_quality_article(a)]
    skipped = before_qc - len(articles)
    if skipped:
        print(f"  QC: removed {skipped} low-quality articles")

    # Count topic matches
    matched = sum(1 for a in articles if quick_topic_match(a))
    print(f"  {len(articles)} QC-passed, {matched} topic-matched")
    return articles


def fetch_english_journal(j, max_retry_issues=3):
    """Fetch articles from an English journal. If latest issue has 0 topic-matched articles,
    try earlier issues (up to max_retry_issues)."""
    print(f"\n{'='*50}")
    print(f"Journal: {j['name_cn']}")

    # Step 1: Get recent PMIDs to find latest volume/issue
    pmids = pubmed_search(j["pubmed_query"], max_results=50)
    print(f"  Got {len(pmids)} recent PMIDs")

    if not pmids:
        return {**j, "articles": [], "note": "No results"}

    # Step 2: Fetch sample to extract volume/issue
    sample_articles = pubmed_fetch(pmids[:20])
    print(f"  Parsed {len(sample_articles)} sample articles")
    time.sleep(1.0)

    # Find the latest volume+issue
    latest_vol = ""
    latest_iss = 0
    for art in sample_articles:
        if art["volume"] and art["issue"]:
            try:
                latest_vol = art["volume"]
                latest_iss = int(art["issue"])
                break
            except ValueError:
                continue

    if not latest_vol:
        print("  Warning: Could not determine latest volume/issue")
        articles = [a for a in sample_articles[:20] if is_quality_article(a)]
        latest_iss_str = ""
    else:
        print(f"  Latest issue: Vol {latest_vol}, Issue {latest_iss}")
        articles = []
        final_vol = latest_vol
        final_iss = latest_iss

        for offset in range(max_retry_issues):
            try_iss = latest_iss - offset
            if try_iss < 1:
                continue
            print(f"  Trying #{offset+1}: Vol {latest_vol}, Issue {try_iss}")
            articles = fetch_articles_for_issue(j, latest_vol, str(try_iss))
            topic_hits = sum(1 for a in articles if quick_topic_match(a))
            if topic_hits > 0 or offset == max_retry_issues - 1:
                final_iss = try_iss
                break
            print(f"  → 0 topic-matched, looking at earlier issue...")

        if len(articles) == 0:
            # Ultimate fallback: use volume-only search
            vol_query = f'{j["pubmed_query"]} AND {latest_vol}[Volume]'
            fallback_pmids = pubmed_search(vol_query, max_results=100)
            if fallback_pmids:
                print(f"  Fallback: volume-only search → {len(fallback_pmids)} PMIDs")
                time.sleep(1.0)
                articles = pubmed_fetch(fallback_pmids[:50])
                articles = [a for a in articles if is_quality_article(a)]
            if len(articles) == 0:
                articles = [a for a in sample_articles[:20] if is_quality_article(a)]

        latest_vol = final_vol
        latest_iss_str = str(final_iss)

    print(f"  → Final: {len(articles)} articles for translation")

    # Translate
    translated = []
    for i, art in enumerate(articles):
        print(f"  [{i+1}/{len(articles)}] {art['title_en'][:70]}...")
        title_cn = translate(art["title_en"], is_title=True)
        time.sleep(0.2)
        abstract_cn = translate(art["abstract_en"], is_title=False) if art["abstract_en"] else ""
        time.sleep(0.3)
        translated.append({**art, "title_cn": title_cn, "abstract_cn": abstract_cn})

    return {"id": j["id"], "name": j["name"], "name_cn": j["name_cn"], "desc": j["desc"],
            "issue": f"Vol {latest_vol}, Issue {latest_iss_str}" if latest_vol else "",
            "source": "PubMed", "articles": translated}


# ─── Filter & Select ───
def filter_and_select(journals, picks_per_journal=2, seed_date=None):
    """For each journal: classify articles, filter by topics, randomly select N.
    Uses date-based seed so same day always gets same picks."""
    if seed_date is None:
        seed_date = datetime.now().strftime("%Y%m%d")

    for jn in journals:
        articles = jn.get("articles", [])
        total = len(articles)

        # Classify each article
        matched = []
        for art in articles:
            cat = classify_article(
                art.get("title_en", ""), art.get("title_cn", ""),
                art.get("abstract_en", ""), art.get("abstract_cn", "")
            )
            if cat:
                art["category"] = cat
                matched.append(art)

        # Random select (seeded by date + journal id for reproducibility)
        seed = int(hashlib.md5(f"{seed_date}-{jn['id']}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        selected = rng.sample(matched, min(picks_per_journal, len(matched))) if matched else []

        # Add URL field for each article (preserve existing URLs)
        for art in selected:
            if not art.get("url"):
                if art.get("doi"):
                    art["url"] = f"https://doi.org/{art['doi']}"
                elif art.get("pmid"):
                    art["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{art['pmid']}/"
                else:
                    art["url"] = ""

        # Update journal: replace full articles with selected ones
        jn["articles"] = selected
        jn["total_in_issue"] = total
        jn["topic_matched"] = len(matched)
        jn["picked"] = len(selected)
        jn["pick_date"] = seed_date

        print(f"  {jn['name_cn']}: {total} total → {len(matched)} matched topic → {len(selected)} picked")

    return journals


# ─── Main ───
def main():
    output_dir = os.path.join(os.path.dirname(__file__), "..", "webapp", "data")
    os.makedirs(output_dir, exist_ok=True)

    today_str = datetime.now().strftime("%Y%m%d")

    # Fetch all journals (full issues)
    journals = []
    for j in ENGLISH_JOURNALS:
        journals.append(fetch_english_journal(j))
        time.sleep(2.0)  # Avoid PubMed rate limiting between journals

    # Filter by topics + randomly select 2 per journal
    print(f"\n{'='*50}")
    print(f"Filtering by topics & selecting 2 per journal (seed={today_str}):")
    journals = filter_and_select(journals, picks_per_journal=2, seed_date=today_str)

    result = {"journals": journals, "updated": datetime.now().isoformat()}

    # Write output
    output_path = os.path.join(output_dir, "journals.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"Done! {len(result['journals'])} journals → {output_path}")
    for jn in result["journals"]:
        picked = len(jn.get("articles", []))
        print(f"  {jn['name_cn']}: {picked} picked / {jn.get('topic_matched',0)} matched / {jn.get('total_in_issue',0)} in issue ({jn.get('issue','?')})")


if __name__ == "__main__":
    main()
