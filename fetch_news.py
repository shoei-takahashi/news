"""RSSからニュースを集め、Claudeで選別・日本語要約して docs/index.html を生成する。"""
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

import feedparser

def load_env(path=".env"):
    """ローカル実行用: .env の KEY=VALUE を環境変数に読み込む(既存値は上書きしない)。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if "ここに" not in v:  # プレースホルダのままなら無視
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_env()

MODEL = "claude-sonnet-5-5"
PER_CATEGORY = 8        # 各ジャンルに載せる件数
CANDIDATES = 40         # 各ジャンルでClaudeに渡す候補数
JST = timezone(timedelta(hours=9))

FEEDS = {
    "テクノロジー/AI": [
        "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml",
        "https://gigazine.net/news/rss_2.0/",
        "https://techcrunch.com/feed/",
    ],
    "経済/株式": [
        "https://www3.nhk.or.jp/rss/news/cat5.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],
    "国内": [
        "https://www3.nhk.or.jp/rss/news/cat0.xml",
        "https://www3.nhk.or.jp/rss/news/cat1.xml",
        "https://www3.nhk.or.jp/rss/news/cat4.xml",
    ],
    "世界": [
        "https://www3.nhk.or.jp/rss/news/cat6.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
    ],
}


def clean(text: str, limit: int = 300) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()[:limit]


def collect(urls):
    seen, items = set(), []
    for url in urls:
        try:
            feed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {url}: {e}")
            continue
        for e in feed.entries[:15]:
            link = e.get("link")
            if not link or link in seen:
                continue
            seen.add(link)
            items.append({
                "title": clean(e.get("title", ""), 200),
                "summary": clean(e.get("summary", "")),
                "link": link,
                "source": clean(feed.feed.get("title", ""), 40),
            })
    return items[:CANDIDATES]


def curate(category, items):
    """Claudeで重要な記事を選び、日本語で要約する。失敗時は先頭から素のまま返す。"""
    fallback = [{**i, "ja_title": i["title"], "digest": i["summary"][:100]}
                for i in items[:PER_CATEGORY]]
    if not os.environ.get("ANTHROPIC_API_KEY") or not items:
        return fallback
    try:
        import anthropic

        listing = "\n".join(
            f"[{n}] {i['title']} | {i['summary'][:150]} ({i['source']})"
            for n, i in enumerate(items)
        )
        prompt = (
            f"以下は「{category}」ジャンルのニュース候補です。\n"
            f"重要度・新規性が高いものを最大{PER_CATEGORY}件選び、重複する話題は1件にまとめてください。\n"
            "偏向を避けるため、次のものは選ばないでください: 社説・コラム・論評・オピニオン、"
            "特定の立場や人物を一方的に批判/称賛する記事、憶測や噂が中心の記事、"
            "センセーショナルな煽りタイトルの記事。事実の報道(発表、統計、出来事)を優先してください。\n"
            "各記事について、日本語のタイトル(ja_title)と、要点が分かる1〜2文の日本語要約(digest)を付けてください。\n"
            "タイトルと要約は、感情的・断定的・評価的な表現を避け、誰が何を発表/報告したかという事実だけを中立的に書いてください。\n"
            'JSON配列のみを出力: [{"id": 番号, "ja_title": "...", "digest": "..."}]\n\n' + listing
        )
        resp = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        picks = json.loads(text[text.index("["): text.rindex("]") + 1])
        out = []
        for p in picks[:PER_CATEGORY]:
            base = items[int(p["id"])]
            out.append({**base, "ja_title": p["ja_title"], "digest": p["digest"]})
        return out or fallback
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Claude failed for {category}: {e}")
        return fallback


def render(sections):
    now = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")
    body = []
    for cat, arts in sections.items():
        cards = "".join(
            f'<a class="card" href="{html.escape(a["link"])}" target="_blank" rel="noopener">'
            f'<h3>{html.escape(a["ja_title"])}</h3><p>{html.escape(a["digest"])}</p>'
            f'<small>{html.escape(a["source"])}</small></a>'
            for a in arts
        )
        body.append(f"<section><h2>{html.escape(cat)}</h2>{cards}</section>")
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1a73e8">
<title>今日のニュース</title>
<style>
:root{{--bg:#f4f5f7;--fg:#1c1e21;--card:#fff;--mute:#6b7280;--acc:#1a73e8}}
@media(prefers-color-scheme:dark){{:root{{--bg:#121316;--fg:#e8eaed;--card:#1e2025;--mute:#9aa0a6;--acc:#8ab4f8}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,sans-serif}}
header{{padding:20px 16px 4px}}header h1{{margin:0;font-size:22px}}header small{{color:var(--mute)}}
main{{padding:0 16px 40px;max-width:720px;margin:auto}}
h2{{font-size:17px;color:var(--acc);margin:28px 0 8px}}
.card{{display:block;background:var(--card);border-radius:12px;padding:12px 14px;margin-bottom:10px;color:inherit;text-decoration:none}}
.card h3{{margin:0 0 4px;font-size:16px}}.card p{{margin:0 0 6px;font-size:14px}}.card small{{color:var(--mute)}}
</style></head><body>
<header><h1>今日のニュース</h1><small>{now} 更新</small></header>
<main>{''.join(body)}</main></body></html>"""


def main():
    sections = {}
    for cat, urls in FEEDS.items():
        print(f"== {cat}")
        sections[cat] = curate(cat, collect(urls))
    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(render(sections))
    print("wrote docs/index.html")


if __name__ == "__main__":
    main()
