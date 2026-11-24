#!/usr/bin/env python3
"""Generates 3 reading-practice articles per day for Energy English.
Sources: The Guardian (news) and OpenAlex (academic papers). Summaries: Gemini."""
import json, os, random, re, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

OUT = Path("data/articles.json")
KEEP_DAYS = 14   # articles older than two weeks are deleted
TODAY = datetime.now(timezone(timedelta(hours=-5))).date().isoformat()  # Colombia date
MODELS = [m for m in [os.getenv("GEMINI_MODEL"), "gemini-flash-lite-latest", "gemini-flash-latest"] if m]

THEMES = {
    "energy": {"label": "Energy markets and renewables", "news": True, "section": "environment|business",
               "qn": "renewable energy OR solar OR wind power OR electricity grid", "qp": "renewable energy markets"},
    "logistics": {"label": "Logistics trends", "news": True, "section": "business",
                  "qn": "supply chain OR shipping OR logistics OR freight", "qp": "supply chain logistics trends"},
    "finance": {"label": "Corporate finance", "news": True, "section": "business|money",
                "qn": "corporate profits OR interest rates OR investment", "qp": "corporate finance"},
    "tech": {"label": "Technology trends", "news": True, "section": "technology",
             "qn": "artificial intelligence OR cybersecurity OR cloud computing OR chips", "qp": "emerging technology trends"},
    "soft": {"label": "Interpersonal skills", "news": False, "section": "",
             "qn": "", "qp": "workplace communication skills teamwork"},
}

PROMPT = """You are an English teacher writing reading practice for Spanish-speaking adults at CEFR level B1+.
Using ONLY the {kind} below, write a short article that summarizes its main ideas in your own words.
Rules:
- 4 paragraphs, 230-290 words in total, simple clear sentences, explain technical terms.
- Do not invent facts, numbers or quotes. Do not copy sentences from the source.
- Neutral tone. In the last paragraph, say that the article is based on this {kind} and name the publisher or journal.
Return JSON only, with exactly this shape:
{{"title": "headline, max 10 words", "paragraphs": ["...", "...", "...", "..."],
 "vocab": [{{"w": "word", "d": "simple English definition", "es": "traduccion al espanol"}}],
 "quiz": [{{"q": "question", "o": ["option A", "option B", "option C"], "a": 0, "why": "short explanation"}}],
 "gloss": {{"word": "traduccion"}}}}
vocab: 8 useful words from your article. quiz: 7 questions. Questions 1-5 check comprehension of your article (3 options each). Questions 6-7 are harder, TOEFL-style GRAMMAR questions about a specific structure used in your article (for example passive voice, relative or reduced clauses, conditionals, modal verbs, comparatives, gerunds and infinitives, linking phrases): quote the sentence or phrase from your article and ask about its grammar, with 4 options each, and add "t": "grammar" to these two questions. Vary the correct index. For every question, "why" briefly explains the answer (for grammar questions, the rule).
gloss: the Spanish translation of each word's meaning IN THIS TEXT (1-3 words) for every content word of your article (nouns, verbs, adjectives, adverbs; skip words like a, the, of, and, is, to, in). Use lowercase keys, written exactly as the word appears in your article.
SOURCE ({kind}): {title}
{text}"""


def http_json(url, payload=None, headers=None, retries=3):
    data = json.dumps(payload).encode() if payload is not None else None
    h = {"User-Agent": "energy-english-bot/1.0", **(headers or {})}
    if data:
        h["Content-Type"] = "application/json"
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and i < retries - 1:
                time.sleep(15 * (i + 1)); continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:300]}")
        except urllib.error.URLError as e:
            if i < retries - 1:
                time.sleep(5); continue
            raise RuntimeError(f"Network error: {e.reason}")


def find_news(t, used):
    key = os.getenv("GUARDIAN_API_KEY")
    if not key:
        raise RuntimeError("GUARDIAN_API_KEY is missing")
    p = {"q": t["qn"], "api-key": key, "order-by": "relevance", "page-size": 20, "show-fields": "bodyText",
         "from-date": (date.today() - timedelta(days=21)).isoformat()}
    if t["section"]:
        p["section"] = t["section"]
    res = http_json("https://content.guardianapis.com/search?" + urllib.parse.urlencode(p))["response"]["results"]
    skip = re.compile(r"letters|/commentisfree/|/live/|quiz|crossword", re.I)
    good = [r for r in res if r["webUrl"] not in used and len(r.get("fields", {}).get("bodyText", "")) > 1500
            and not skip.search(r["webUrl"] + " " + r["webTitle"])]
    if not good:
        raise RuntimeError("no suitable news found")
    r = random.choice(good[:10])
    return {"text": r["fields"]["bodyText"][:7000],
            "source": {"title": r["webTitle"], "url": r["webUrl"], "publisher": "The Guardian", "date": r["webPublicationDate"][:10]}}


def abstract(inv):
    pos = {}
    for w, ps in (inv or {}).items():
        for p in ps:
            pos[p] = w
    return " ".join(pos[i] for i in sorted(pos))


def find_paper(t, used):
    key = os.getenv("OPENALEX_API_KEY")
    if not key:
        raise RuntimeError("OPENALEX_API_KEY is missing")
    since = (date.today() - timedelta(days=540)).isoformat()
    p = {"search": t["qp"], "per_page": 25, "api_key": key,
         "filter": f"from_publication_date:{since},has_abstract:true,type:article"}
    res = http_json("https://api.openalex.org/works?" + urllib.parse.urlencode(p))["results"]
    random.shuffle(res)
    for r in res:
        loc = r.get("primary_location") or {}
        url = r.get("doi") or loc.get("landing_page_url")
        text = abstract(r.get("abstract_inverted_index"))
        if url and url not in used and len(text) > 600:
            return {"text": text, "source": {"title": r.get("display_name") or "Research paper", "url": url,
                    "publisher": (loc.get("source") or {}).get("display_name") or "Academic journal",
                    "date": r.get("publication_date") or ""}}
    raise RuntimeError("no suitable paper found")


def gemini(prompt):
    key = os.environ["GEMINI_API_KEY"]
    last = None
    for m in MODELS:
        try:
            r = http_json(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                          payload={"contents": [{"parts": [{"text": prompt}]}],
                                   "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6, "maxOutputTokens": 8192}},
                          headers={"x-goog-api-key": key})
            txt = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
            return json.loads(re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip())
        except Exception as e:
            last = e
            print(f"  model {m} failed: {e}")
    raise RuntimeError(last)


def valid(a):
    try:
        return (isinstance(a["title"], str) and 3 <= len(a["paragraphs"]) <= 6 and len(a["vocab"]) >= 5
                and len(a["quiz"]) >= 5
                and all(len(q["o"]) in (3, 4) and isinstance(q["a"], int) and 0 <= q["a"] < len(q["o"]) for q in a["quiz"]))
    except Exception:
        return False


def main():
    db = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {"articles": []}
    old = db.get("articles", [])
    if os.getenv("FORCE"):  # manual run with the box checked: replace today's articles
        old = [a for a in old if a["date"] != TODAY]
    if sum(a["date"] == TODAY for a in old) >= 3:
        print("Already generated today."); return
    used = {s["url"] for a in old for s in a["sources"]}
    new = []
    for k in random.sample(list(THEMES), 3):
        t = THEMES[k]
        kinds = ["paper"] if not t["news"] else random.sample(["news", "paper"], 2)
        for kind in kinds:
            try:
                src = (find_news if kind == "news" else find_paper)(t, used)
                name = "news article" if kind == "news" else "research paper abstract"
                a = None
                for _ in range(2):
                    a = gemini(PROMPT.format(kind=name, title=src["source"]["title"], text=src["text"]))
                    time.sleep(8)
                    if valid(a):
                        break
                    a = None
                if not a:
                    raise RuntimeError("Gemini output was not valid")
                used.add(src["source"]["url"])
                if len(a["quiz"]) >= 7 and not any(q.get("t") == "grammar" for q in a["quiz"]):
                    for q in a["quiz"][-2:]:
                        q["t"] = "grammar"
                gl = a.get("gloss") if isinstance(a.get("gloss"), dict) else {}
                gloss = {str(k).lower().strip(): str(v).strip()[:60] for k, v in gl.items() if isinstance(v, str) and v.strip()}
                new.append({"id": f"{TODAY}-{k}", "date": TODAY, "theme": k, "group": t["label"], "kind": kind,
                            "title": a["title"], "paras": a["paragraphs"], "vocab": a["vocab"], "quiz": a["quiz"], "gloss": gloss,
                            "sources": [src["source"]]})
                print(f"OK {k} ({kind}): {a['title']}")
                break
            except Exception as e:
                print(f"FAILED {k} ({kind}): {e}")
    if not new:
        sys.exit("No articles were generated.")
    cutoff = (datetime.strptime(TODAY, "%Y-%m-%d").date() - timedelta(days=KEEP_DAYS - 1)).isoformat()
    kept = [a for a in new + old if a["date"] >= cutoff]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"updated": TODAY, "articles": kept}, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
