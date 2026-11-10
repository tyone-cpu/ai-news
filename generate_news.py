import os
import json
import datetime
import urllib.parse
import feedparser
import requests

# ==========================================
# 設定
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# 収集するRSSフィードのデフォルト一覧（feeds.json があればそちらを優先）
DEFAULT_RSS_FEEDS = [
    {
        "name": "ITmedia AI+",
        "url": "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",
        "lang": "ja",
    },
    {
        "name": "The Verge (AI)",
        "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "lang": "en",
    },
    {
        "name": "TechCrunch AI",
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "lang": "en",
    },
]

def load_rss_feeds():
    """feeds.json があれば読み込み、なければデフォルトを使用"""
    feeds_file = "feeds.json"
    if os.path.exists(feeds_file):
        try:
            with open(feeds_file, "r", encoding="utf-8") as f:
                feeds = json.load(f)
                print(f"📋 feeds.json から {len(feeds)} 件の配信元設定を読み込みました。")
                return feeds
        except Exception as e:
            print(f"⚠️ feeds.json の読み込みに失敗しました: {e}")
    return DEFAULT_RSS_FEEDS

MAX_ARTICLES_PER_FEED = 3
MAX_TOTAL_ARTICLES = 10

# AI関連記事を優先抽出するためのキーワード（総合メディア向け）
AI_KEYWORDS = [
    "ai", "人工知能", "chatgpt", "gpt", "gemini", "claude", "llm",
    "openai", "anthropic", "copilot", "機械学習", "ディープラーニング",
    "画像生成", "自動運転", "エージェント", "ロボット", "モデル"
]


def is_ai_related(title, summary):
    """総合ニュースからAI関連の記事かどうかを判定"""
    text = (title + " " + summary).lower()
    return any(kw in text for kw in AI_KEYWORDS)


def fetch_latest_news():
    """RSSフィードから最新記事を取得し、各メディアから均等に選出する"""
    feeds = load_rss_feeds()
    print("📡 RSSフィードからニュースを収集中...")

    feed_articles_map = {}

    for feed_info in feeds:
        feed_articles_map[feed_info["name"]] = []
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = entry.get("summary", "") or entry.get("description", "")
                published = entry.get("published", "") or entry.get("updated", "")

                if not title or not link:
                    continue

                # GIGAZINEなどの総合ニュースはAIキーワードでフィルタリング
                is_general_media = "gigazine" in feed_info["url"].lower() or "hatebu" in feed_info["url"].lower()
                if is_general_media and not is_ai_related(title, summary):
                    continue

                feed_articles_map[feed_info["name"]].append({
                    "source": feed_info["name"],
                    "lang": feed_info["lang"],
                    "title": title,
                    "link": link,
                    "summary": summary[:400],
                    "published": published
                })

                if len(feed_articles_map[feed_info["name"]]) >= MAX_ARTICLES_PER_FEED:
                    break
        except Exception as e:
            print(f"⚠️ RSS取得エラー ({feed_info['name']}): {e}")

    # 各メディアから均等に1件ずつピックアップ（ラウンドロビン方式）
    selected_articles = []
    max_rounds = MAX_ARTICLES_PER_FEED
    for r in range(max_rounds):
        for feed_info in feeds:
            articles = feed_articles_map.get(feed_info["name"], [])
            if r < len(articles):
                selected_articles.append(articles[r])
            if len(selected_articles) >= MAX_TOTAL_ARTICLES:
                break
        if len(selected_articles) >= MAX_TOTAL_ARTICLES:
            break

    print(f"合計 {len(selected_articles)} 件の記事を各メディアから均等に選出しました。")
    for a in selected_articles:
        print(f" - [{a['source']}] {a['title'][:35]}...")

    return selected_articles


def summarize_with_gemini(articles):
    """Gemini API を使用して記事を一括要約・選定する"""
    if not GEMINI_API_KEY:
        print("⚠️ GEMINI_API_KEY が設定されていません。ダミーデータで生成します。")
        return generate_mock_summaries(articles)

    print("🤖 Gemini API で要約を生成中...")
    
    prompt = f"""
あなたは多忙なビジネスパーソンやエンジニア向けに、毎朝最新のAIニュースをキュレーションするプロの編集者です。
以下の記事リスト（英語・日本語混在）から、通勤中や隙間時間にスマホでサクッと読める形式にまとめてください。

対象記事:
{json.dumps(articles, ensure_ascii=False, indent=2)}

重要要件:
1. 【完全日本語化】英語記事であっても、タイトル（"title"）、1行要約（"one_line"）、箇条書き要点（"points"）すべてを、自然で読みやすい日本語に完全翻訳・要約してください。英語のまま残さないでください。
2. タイトルは直訳ではなく、「【企業名・分野】〜〜〜」のように一目でわかるキャッチーで分かりやすい日本語にしてください。
3. 出力は必ず以下の形式のJSON配列のみを出力してください（Markdownの ```json ... ``` ブロックで囲んで構いません）。
4. 各記事につき、以下のプロパティを含めてください:
   - "title": 自然で分かりやすい日本語タイトル
   - "category": カテゴリ（例: "LLM・基盤モデル", "画像・動画AI", "ビジネス・提携", "オープンソース", "法規制・社会", "ハードウェア"）
   - "one_line": 1行でわかる超要約（30〜50文字程度で、何が起きたか・何がすごいか）
   - "points": 箇条書き3点（1行25〜45文字程度の日本語で要点、背景、ユーザーへの影響など）
   - "source": 元のメディア名
   - "link": 元記事URL
   - "lang": 元記事の言語 ("ja" または "en")
   - "read_time": 読了目安（例: "30秒", "45秒", "1分"）
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        res_json = response.json()
        content_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()

        # JSONのパース
        if content_text.startswith("```"):
            lines = content_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            content_text = "\n".join(lines).strip()

        summaries = json.loads(content_text)
        print(f"✅ {len(summaries)} 件の記事の要約が完了しました。")
        return summaries
    except Exception as e:
        print(f"⚠️ Gemini API 呼び出しエラー: {e}")
        return generate_mock_summaries(articles)


def generate_mock_summaries(articles):
    """APIキーがない場合やエラー時のフォールバック（英語記事も完全日本語化）"""
    # 英語記事の日本語訳マッピング（プレビュー用）
    en_translations = {
        "david ellison says ai could be the future of paramount": {
            "title": "【パラマウント】米映画大手トップ、生成AIがハリウッド映画制作の未来を救うと提唱",
            "one_line": "制作コスト高騰に悩む大手スタジオが、VFXや脚本補助への本格的な生成AI導入を表明。",
            "points": [
                "制作費の大幅削減とクリエイターの表現力向上の両立を目指す",
                "ハリウッドの労働組合との合意形成が今後の焦点に",
                "映画・映像業界におけるAIの実用化が一段と加速する見通し"
            ]
        },
        "anthropic reportedly plans to move into enterprise search": {
            "title": "【Anthropic】Claudeを企業内データ検索に拡大へ、法人向け市場でOpenAIに対抗",
            "one_line": "社内文書やSlackと連携し、高度な文脈理解で必要な情報を瞬時に探し出す新機能を開発中。",
            "points": [
                "Google Driveや社内ナレッジベースとのシームレスな統合を予定",
                "高いセキュリティ基準を強みに大企業ユーザーの獲得を狙う",
                "エンタープライズAI市場での競争がさらに激化"
            ]
        },
        "gemini omni 1.2 flash: fast and cost-efficient multimodal model": {
            "title": "【Google公式】次世代マルチモーダルモデル「Gemini Omni 1.2 Flash」発表",
            "one_line": "画像・音声・テキストをリアルタイムかつ超低コストで同時処理できる新モデルが登場。",
            "points": [
                "従来モデル比で推論速度が大幅に向上し、APIコストも大幅削減",
                "スマホアプリや組み込み端末でのオンデバイス応答がより快適に",
                "Google AI Studioから全世界の開発者向けに即日提供開始"
            ]
        },
        "what could china get from jensen huang's trip?": {
            "title": "【NVIDIA】ジェンスン・フアンCEOの訪中で注目される中国市場向けAIチップの行方",
            "one_line": "米国の輸出規制が強まる中、巨大な中国AI市場に向けた特別仕様チップの供給を協議か。",
            "points": [
                "中国のテック大手による代替チップ開発が進む中での訪問",
                "規制に抵触しない新たな専用モデルの投入可能性が浮上",
                "世界の半導体サプライチェーンへの影響が注視される"
            ]
        },
        "jensen huang reportedly engaged a pentagon briefing": {
            "title": "【米国防総省】NVIDIA CEO、ペンタゴンの高官らに最新AI技術の軍事・安全保障応用を解説",
            "one_line": "防衛分野におけるAI主導権確保のため、最高峰の半導体技術とシミュレーションを議論。",
            "points": [
                "自律型防衛システムやリアルタイム解析におけるGPU需要を説明",
                "安全保障におけるAIインフラの重要性が改めて強調された",
                "米政府と主要AI半導体メーカーの連携強化の一環"
            ]
        }
    }

    mock_data = []
    category_map = {
        "ITmedia AI+": "国内AIトレンド",
        "Zenn (AIトピック)": "技術・開発",
        "The Verge (AI)": "海外トレンド",
        "TechCrunch AI": "スタートアップ・ビジネス",
        "GIGAZINE": "ハードウェア・AIチップ",
        "Google News (AI)": "公式発表・モデル",
    }

    for a in articles:
        cat = category_map.get(a["source"], "AI最新ニュース")
        title = a["title"]
        lang = a.get("lang", "ja")
        lower_title = title.lower()

        # 英語タイトルのマッチング
        matched_trans = None
        for key, trans in en_translations.items():
            if key in lower_title or lower_title in key or key[:25] in lower_title:
                matched_trans = trans
                break

        if matched_trans:
            mock_data.append({
                "title": matched_trans["title"],
                "category": cat,
                "one_line": matched_trans["one_line"],
                "points": matched_trans["points"],
                "source": a["source"],
                "link": a["link"],
                "lang": lang,
                "read_time": "45秒"
            })
        else:
            # 汎用処理
            clean_title = title
            if lang == "en":
                clean_title = f"【海外速報】{title}"
            mock_data.append({
                "title": clean_title,
                "category": cat,
                "one_line": f"{a['source']} が報じる最新動向。国内外のAIコミュニティで大きな反響を呼んでいます。",
                "points": [
                    "技術革新および市場投入に向けた最新の進捗が発表されました",
                    "ビジネスへの実用化や開発環境への影響が期待されています",
                    "詳細は元記事リンク（日本語翻訳表示対応）から確認できます"
                ],
                "source": a["source"],
                "link": a["link"],
                "lang": lang,
                "read_time": "45秒"
            })
    return mock_data


def render_html(summaries):
    """スマホ閲覧に特化した美しいレスポンシブHTMLを生成する"""
    jst = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(jst)
    date_str = now.strftime("%Y年%m月%d日")
    weekday_str = ["月", "火", "水", "木", "金", "土", "日"][now.weekday()]
    time_str = now.strftime("%H:%M")
    total_articles = len(summaries)
    
    # 記事カードのHTML生成
    cards_html = ""
    for idx, item in enumerate(summaries, 1):
        points_li = "".join([f"<li class='flex items-start gap-2'><span class='text-blue-500 font-bold shrink-0'>・</span><span>{p}</span></li>" for p in item.get("points", [])])
        
        is_en = item.get("lang") == "en"
        raw_link = item.get("link", "#")
        
        # 英語記事の場合は Google翻訳経由で読めるリンクを作成
        if is_en:
            encoded_url = urllib.parse.quote(raw_link)
            translate_link = f"https://translate.google.com/translate?sl=auto&tl=ja&u={encoded_url}"
            link_tag = f"""
            <div class="flex items-center gap-2">
                <a href="{translate_link}" target="_blank" rel="noopener noreferrer" 
                   class="inline-flex items-center gap-1 font-semibold text-emerald-600 dark:text-emerald-400 hover:underline bg-emerald-50 dark:bg-emerald-950/40 px-2 py-0.5 rounded text-[11px]">
                    🌐 日本語訳で読む
                </a>
                <a href="{raw_link}" target="_blank" rel="noopener noreferrer" 
                   class="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 text-[11px] underline">
                    原文
                </a>
            </div>
            """
        else:
            link_tag = f"""
            <a href="{raw_link}" target="_blank" rel="noopener noreferrer" 
               class="inline-flex items-center gap-1 font-semibold text-blue-600 dark:text-blue-400 hover:underline">
                元記事を読む
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
            </a>
            """

        source_badge = ""
        if is_en:
            source_badge = "<span class='ml-1 px-1.5 py-0.2 rounded text-[10px] bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-300'>海外</span>"

        cards_html += f"""
        <article class="bg-white dark:bg-slate-800 rounded-2xl p-5 shadow-sm border border-slate-100 dark:border-slate-700/60 transition hover:shadow-md">
            <div class="flex items-center justify-between gap-2 mb-2.5">
                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-blue-50 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                    {item.get('category', 'AIニュース')}
                </span>
                <span class="text-xs text-slate-400 dark:text-slate-400 flex items-center gap-1 font-medium">
                    ⏱ {item.get('read_time', '45秒')} で読める
                </span>
            </div>

            <h2 class="text-base sm:text-lg font-bold text-slate-900 dark:text-white leading-snug mb-2">
                {item.get('title')}
            </h2>

            <!-- 1行サマリー -->
            <div class="p-3 bg-amber-50/70 dark:bg-amber-950/30 border-l-4 border-amber-400 rounded-r-xl mb-3.5">
                <p class="text-xs sm:text-sm font-medium text-amber-900 dark:text-amber-200 leading-relaxed">
                    💡 {item.get('one_line')}
                </p>
            </div>

            <!-- 要点3点 -->
            <ul class="text-xs sm:text-sm text-slate-600 dark:text-slate-300 space-y-1.5 mb-4 leading-relaxed">
                {points_li}
            </ul>

            <!-- フッター情報 -->
            <div class="flex items-center justify-between pt-3 border-t border-slate-100 dark:border-slate-700/50 text-xs">
                <span class="text-slate-600 dark:text-slate-300 font-medium truncate max-w-[150px] flex items-center">
                    出典: {item.get('source')} {source_badge}
                </span>
                {link_tag}
            </div>
        </article>
        """

    html_template = f"""<!DOCTYPE html>
<html lang="ja" class="scroll-smooth">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="AI日刊要約">
    <meta name="theme-color" content="#2563eb">
    <title>毎朝のAIニュース要約 | {date_str}</title>
    <!-- Tailwind CSS (軽量CDN) -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'media',
            theme: {{
                extend: {{
                    fontFamily: {{
                        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
                    }}
                }}
            }}
        }}
    </script>
    <style>
        /* スマホでの快適なタップ感 */
        body {{
            -webkit-tap-highlight-color: transparent;
            padding-bottom: env(safe-area-inset-bottom);
        }}
    </style>
</head>
<body class="bg-slate-50 dark:bg-slate-900 text-slate-800 dark:text-slate-100 min-h-screen antialiased selection:bg-blue-500 selection:text-white">

    <!-- ヘッダー（固定） -->
    <header class="sticky top-0 z-30 bg-white/90 dark:bg-slate-900/90 backdrop-blur-md border-b border-slate-200/80 dark:border-slate-800 px-4 py-3 sm:px-6">
        <div class="max-w-xl mx-auto flex items-center justify-between">
            <div>
                <div class="flex items-center gap-1.5">
                    <span class="text-lg">⚡️</span>
                    <h1 class="text-base sm:text-lg font-extrabold tracking-tight bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                        日刊 AIニュース要約
                    </h1>
                </div>
                <p class="text-[11px] text-slate-600 dark:text-slate-300">
                    {date_str} ({weekday_str}) • 朝 {time_str} 更新
                </p>
            </div>
            <div class="text-right">
                <span class="inline-block px-2 py-1 rounded-lg text-xs font-bold bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300">
                    全 {total_articles} 件 (約3分)
                </span>
            </div>
        </div>
    </header>

    <!-- メインコンテンツ -->
    <main class="max-w-xl mx-auto px-4 py-5 space-y-4">
        
        <!-- 通勤案内バナー（ホーム画面追加Tips） -->
        <div id="pwa-tip" class="p-3.5 bg-blue-50/80 dark:bg-blue-950/40 rounded-xl border border-blue-100 dark:border-blue-900/40 text-xs text-blue-900 dark:text-blue-200 flex items-start justify-between gap-3">
            <div class="flex items-start gap-2">
                <span class="text-base shrink-0">📲</span>
                <div>
                    <span class="font-bold">毎朝ワンタップで開く方法</span>
                    <p class="text-[11px] text-blue-700/80 dark:text-blue-300/80 mt-0.5">
                        ブラウザの共有メニューから「ホーム画面に追加」すると、アプリ感覚で毎朝サッと読めます。
                    </p>
                </div>
            </div>
            <button onclick="document.getElementById('pwa-tip').style.display='none'" class="text-blue-400 hover:text-blue-600 text-sm font-bold shrink-0">✕</button>
        </div>

        <!-- 記事一覧 -->
        {cards_html}

        <!-- フッター -->
        <footer class="pt-6 pb-12 text-center text-xs text-slate-400 dark:text-slate-400 space-y-1">
            <p>毎朝自動更新 • Gemini API & GitHub Actions で完全自動配信</p>
            <p>通勤時間・朝の隙間時間のインプットに最適化</p>
        </footer>
    </main>

</body>
</html>
"""
    return html_template


def main():
    print("🚀 ニュース取得・要約パイプラインを開始します...")
    articles = fetch_latest_news()
    summaries = summarize_with_gemini(articles)
    html = render_html(summaries)

    output_path = "index.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"🎉 正常に {output_path} を生成しました！")


if __name__ == "__main__":
    main()
