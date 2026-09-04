from datetime import datetime, timedelta, timezone
import html  # 修正：補上 html import
import json
import os
import re
import time
import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd
import requests
import zhconv

try:
    from pypinyin import lazy_pinyin
except ImportError:
    lazy_pinyin = None

# ==========================================
# 1. 基礎設定與輔助函式
# ==========================================
DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")

# 噪音關鍵字過濾庫
COMBINED_NOISE_KEYWORDS = [
    "花絮",
    "未播",
    "片段",
    "採訪",
    "預告",
    "解說",
    "幕後",
    "reaction",
]

# ==========================================
# 🔑 Gemini API Keys 取得與語言判定邏輯
# ==========================================
def get_gemini_api_keys():
    """從環境變數或 .streamlit/secrets.toml 取得 Gemini API Keys"""
    keys = []
    env_keys = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY", "")

    if env_keys:
        raw_list = env_keys.replace(",", "\n").splitlines()
        keys = [k.strip() for k in raw_list if k.strip()]
        if keys:
            return keys

    if os.path.exists(".streamlit/secrets.toml"):
        try:
            import tomllib
            with open(".streamlit/secrets.toml", "rb") as f:
                secrets = tomllib.load(f)
                k_config = secrets.get("GEMINI_API_KEYS", secrets.get("GEMINI_API_KEY", []))
                if isinstance(k_config, list):
                    keys = [str(k).strip() for k in k_config if str(k).strip()]
                elif isinstance(k_config, str):
                    keys = [k.strip() for k in k_config.replace(",", "\n").splitlines() if k.strip()]
        except Exception:
            pass
    return keys

GEMINI_API_KEYS = get_gemini_api_keys()
CURRENT_GEMINI_KEY_IDX = 0

# 修正：補上 lyrics="" 參數
def call_gemini_classify_song(song_title, singer_name, yt_id=None, lyrics=""):
    """呼叫 Gemini 判斷歌曲語言，並強制輸出繁體中文類別"""
    global CURRENT_GEMINI_KEY_IDX

    if not GEMINI_API_KEYS:
        print("  ❌ 未找到任何 Gemini API Key")
        return {"success": False, "category": "未知"}

    yt_link_info = f"https://www.youtube.com/watch?v={yt_id}" if yt_id and str(yt_id) not in ["-", "", "nan", "None"] else "無"
    lyrics_info = lyrics if lyrics else "無（未提供或純音樂）"

    prompt = f"""
你是一個專業音樂榜單數據分析專家。請結合歌名、歌手背景知識以及 YouTube 影片資訊以及歌詞片段，將這首歌曲精準歸類為以下【5 種語言類別】之一（請務必使用繁體中文）：
1. "華語" (歌詞以國語/粵語/台語為主)
2. "西洋" (歌詞以英文/歐美語系為主)
3. "韓語" (歌詞以韓文為主，K-Pop)
4. "日語" (歌詞以日文為主，J-Pop)
5. "其它" (純音樂、無歌詞、電子樂伴奏，或上述四者之外的語言)

待分析歌曲資料：
- 歌名："{song_title}"
- 歌手："{singer_name}"
- YouTube 連結：{yt_link_info}
- 歌詞片段：
{lyrics_info}

【關鍵判斷標準】：
1. 實際演唱語言絕對優先（歌詞文字 > 既有印象）：
   - 若有提供「歌詞片段」，【必須嚴格以歌詞實際出現的文字語言（中/英/韓/日）作為最高優先判定依據】！
   - 【絕對禁止】僅憑歌手國籍、所屬團體、發行地區或歌名語言直接歸類。
2. 華語/亞洲歌手的英文歌 vs K-Pop/J-Pop 英文副歌：
   - 全英文/主要語言為英文：若華語或亞洲歌手發行的是「全英文」或「英文佔比超過 70%」的單曲（如：嚴浩翔《No More Tomorrow》、張藝興《Crossfire》、王嘉爾全英文單曲、BTS 英文單曲），【必須歸類為 "西洋"】。
   - K-Pop / J-Pop 流行曲：若主體歌詞為韓文或日文，即使含有大量英文副歌或片語，【仍應歸類為 "韓語" 或 "日語"】。
3. 英文歌名 / 外文標題的華語歌：
   - 歌名雖然是英文或外文，但檢視歌詞片段後發現演唱內容絕大部分為華語（如：周深《Rubia》若歌詞包含華語，或一般華語 Pop 帶英文歌名），【必須歸類為 "華語"】。
4. 混血/多語言混合曲與純音樂：
   - 多語言混合曲：請以歌詞片段中演唱比例超過 50% 的主要語言進行歸類。
   - 無歌詞/純音樂：若歌詞片段顯示為「無/純音樂」，或經判斷為純音樂伴奏（Instrumental），一律歸類為 "其它"。
5. 綜合輔助驗證：
   - 若未提供歌詞片段，請結合 YouTube 資訊與你內建的音樂知識庫進行精準判定。

請嚴格只輸出 JSON 格式，且 "category" 的值必須精確為 ["華語", "西洋", "韓語", "日語", "其它"] 其中之一，結構如下：
{{
  "category": "西洋",
  "reason": "根據歌詞片段主要為英文，演唱語言為英文，故歸類為西洋。"
}}
"""

    total_keys = len(GEMINI_API_KEYS)
    keys_tried = 0

    while keys_tried < total_keys:
        current_key = GEMINI_API_KEYS[CURRENT_GEMINI_KEY_IDX]
        genai.configure(api_key=current_key)
        
        model = genai.GenerativeModel("gemini-3.1-flash-lite-preview")

        try:
            response = model.generate_content(
                prompt,
                generation_config={"response_mime_type": "application/json"},
                request_options={"timeout": 15.0}
            )
            result_json = json.loads(response.text.strip())
            raw_category = result_json.get("category", "其它")
            trad_category = zhconv.convert(raw_category, "zh-tw")

            return {"success": True, "category": trad_category}

        except Exception as e:
            err_str = str(e)
            print(f"  ⚠️ Key {CURRENT_GEMINI_KEY_IDX + 1} 發生錯誤: {err_str}")
            
            # 遭遇 429 / Quota 超限或無效時自動輪替 Key
            CURRENT_GEMINI_KEY_IDX = (CURRENT_GEMINI_KEY_IDX + 1) % total_keys
            keys_tried += 1
            time.sleep(1.0)

    return {"success": False, "category": "未知"}


def parse_duration(duration_str):
    """將 YouTube ISO 8601 時間字串轉為總秒數"""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0
    return int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60 + int(match.group(3) or 0)


def clean_song_title(title):
    if not title:
        return ""
    return re.sub(r"^歌曲[:：]\s*", "", str(title)).strip()


def parse_song_title(song):
    clean_s = clean_song_title(song)
    main_s = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", clean_s).strip()
    return clean_s, (main_s if main_s else clean_s)


def normalize_text(text):
    if not text:
        return ""
    t = str(text).lower()
    t = t.replace('ⅱ', 'ii').replace('ⅰ', 'i').replace('ⅲ', 'iii').replace('ⅳ', 'iv')
    return re.sub(r"[\s\.\-\_\(\)（）「」《》【】『』""'']", "", t)


def extract_artist_tokens(singer):
    if not singer or str(singer).lower() in ["-", "nan", "none"]:
        return []

    singer_str = str(singer).strip()

    raw_artists = re.split(
        r"\s*[/&,\+\·\*\-\|]+\s*|\s+\b(?:feat\.?|ft\.?|X|x)\b\s*",
        singer_str,
        flags=re.IGNORECASE,
    )

    artist_groups = []

    for raw in raw_artists:
        raw = raw.strip()
        if not raw:
            continue

        group_tokens = set()

        group_tokens.add(zhconv.convert(raw, "zh-hans"))
        group_tokens.add(zhconv.convert(raw, "zh-hant"))

        clean_raw = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", raw).strip()
        if clean_raw:
            group_tokens.add(zhconv.convert(clean_raw, "zh-hans"))
            group_tokens.add(zhconv.convert(clean_raw, "zh-hant"))

            zh_only = "".join(re.findall(r"[\u4e00-\u9fa5]+", clean_raw)).strip()
            if len(zh_only) >= 2:
                group_tokens.add(zhconv.convert(zh_only, "zh-hans"))
                group_tokens.add(zhconv.convert(zh_only, "zh-hant"))

            en_only = "".join(re.findall(r"[a-zA-Z0-9\s]+", clean_raw)).strip()
            if len(en_only) >= 2:
                group_tokens.add(en_only)

        bracket_content = re.findall(r"[\(\（]([^\)\）]+)[\)\）]", raw)
        for b in bracket_content:
            b = b.strip()
            if len(b) >= 2:
                group_tokens.add(zhconv.convert(b, "zh-hans"))
                group_tokens.add(zhconv.convert(b, "zh-hant"))

        norm_group = [
            normalize_text(t)
            for t in group_tokens
            if len(normalize_text(t)) >= 2
        ]
        if norm_group:
            artist_groups.append(list(set(norm_group)))

    return artist_groups


def build_search_queries(song, singer):
    clean_s, main_s = parse_song_title(song)
    clean_p = str(singer).strip()

    clean_p_spaced = re.sub(r"[/&,\+\·\*\-\|]+", " ", clean_p).strip()

    primary_query = f"{main_s} {clean_p_spaced}".strip()
    queries = [primary_query]

    primary_query_tra = f"{zhconv.convert(main_s, 'zh-hant')} {zhconv.convert(clean_p_spaced, 'zh-hant')}".strip()
    if primary_query_tra not in queries:
        queries.append(primary_query_tra)

    first_artist = re.split(r"[/&,\+\·\*\-\|]|\s+\b(?:feat\.?|ft\.?|X|x)\b", clean_p, flags=re.IGNORECASE)[0].strip()
    if first_artist and first_artist != clean_p:
        first_artist_query = f"{main_s} {first_artist}".strip()
        if first_artist_query not in queries:
            queries.append(first_artist_query)

    if clean_s != main_s:
        full_query = f"{clean_s} {clean_p_spaced}".strip()
        if full_query not in queries:
            queries.append(full_query)

    return queries


# ==========================================
# 2. YouTube 雙階段搜尋函式
# ==========================================
def search_youtube_video(song, singer, api_keys, current_key_idx, youtube_service):
    clean_song, main_song = parse_song_title(song)
    search_queries = build_search_queries(song, singer)

    main_sim_norm = normalize_text(zhconv.convert(main_song, "zh-hans"))
    main_tra_norm = normalize_text(zhconv.convert(main_song, "zh-hant"))
    artist_tokens = extract_artist_tokens(singer)

    matched_info = None

    def build_yt_service(idx):
        return build("youtube", "v3", developerKey=api_keys[idx]) if idx < len(api_keys) else None

    order_strategies = ["viewCount", "relevance"]

    for order_mode in order_strategies:
        if matched_info:
            break

        max_results_val = 30 if order_mode == "viewCount" else 5

        for query_str in search_queries:
            if matched_info:
                break

            success = False
            while current_key_idx < len(api_keys) and not success:
                if youtube_service is None:
                    youtube_service = build_yt_service(current_key_idx)
                    if youtube_service is None:
                        break

                try:
                    search_res = (
                        youtube_service.search()
                        .list(
                            q=query_str,
                            part="id",
                            maxResults=max_results_val,
                            type="video",
                            order=order_mode,
                            regionCode="TW",
                        )
                        .execute()
                    )

                    v_ids = [
                        item["id"]["videoId"]
                        for item in search_res.get("items", [])
                        if "videoId" in item.get("id", {})
                    ]

                    if v_ids:
                        video_res = (
                            youtube_service.videos()
                            .list(part="snippet,statistics,contentDetails", id=",".join(v_ids))
                            .execute()
                        )

                        candidates = []
                        for item in video_res.get("items", []):
                            v_id = item["id"]
                            v_title = item["snippet"]["title"]
                            channel_title = item["snippet"].get("channelTitle", "")
                            v_desc = item["snippet"].get("description", "")
                            v_views = int(item["statistics"].get("viewCount", 0))

                            duration_str = item.get("contentDetails", {}).get("duration", "PT0S")
                            duration_sec = parse_duration(duration_str)

                            if duration_sec <= 65 or duration_sec > 480:
                                continue

                            v_title_lower = v_title.lower()
                            v_title_norm = normalize_text(v_title)
                            channel_lower = channel_title.lower()
                            channel_norm = normalize_text(channel_title)
                            v_desc_norm = normalize_text(v_desc)

                            is_topic = "topic" in channel_lower or "主題" in channel_lower
                            has_noise = any(nk in v_title_lower for nk in COMBINED_NOISE_KEYWORDS)
                            
                            if not is_topic and has_noise:
                                continue

                            v_title_sans = zhconv.convert(v_title, "zh-hans")
                            v_title_hant = zhconv.convert(v_title, "zh-hant")

                            is_ascii_song = bool(re.search(r'[a-zA-Z]', main_song))

                            if is_ascii_song:
                                title_no_brackets = re.sub(r"[\(\（\[【][^\)\）\]】]*[\)\）\]】]", "", v_title)

                                for grp in artist_tokens:
                                    for tkn in grp:
                                        if tkn and len(tkn) >= 2:
                                            title_no_brackets = re.sub(re.escape(tkn), "", title_no_brackets, flags=re.IGNORECASE)

                                core_title = re.sub(r"\b(official|music|video|audio|visualizer|lyric|lyrics|live|mv|hd|4k)\b", "", title_no_brackets, flags=re.IGNORECASE)

                                core_words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9']+\b", core_title)]
                                target_words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9']+\b", main_song)]

                                song_matched = (core_words == target_words) or (
                                    re.search(rf"\b{re.escape(main_song)}\b", v_title, re.IGNORECASE) is not None
                                    and not re.search(rf"\b\w+\s+{re.escape(main_song)}\b", core_title, re.IGNORECASE)
                                )
                            else:
                                pattern_sim = rf"(?<![\u4e00-\u9fa5]){re.escape(main_sim_norm)}(?![\u4e00-\u9fa5])"
                                pattern_tra = rf"(?<![\u4e00-\u9fa5]){re.escape(main_tra_norm)}(?![\u4e00-\u9fa5])"

                                song_matched = (
                                    re.search(pattern_sim, v_title_sans) is not None or
                                    re.search(pattern_tra, v_title_hant) is not None
                                )

                            if not song_matched:
                                continue

                            is_official = (
                                "topic" in channel_lower
                                or "official" in channel_lower
                                or "官方" in channel_lower
                                or "主題" in channel_lower
                                or "universal" in channel_lower
                            )

                            if is_official:
                                v_check_text = f"{v_title_norm} {channel_norm} {v_desc_norm}"
                            else:
                                v_check_text = f"{v_title_norm} {channel_norm}"

                            extended_artist_tokens = []
                            for group in artist_tokens:
                                new_group = list(group)
                                for tkn in group:
                                    if lazy_pinyin is not None:
                                        py_list = lazy_pinyin(tkn)
                                        if py_list:
                                            new_group.append("".join(py_list).lower())
                                            new_group.append(" ".join(py_list).lower())
                                extended_artist_tokens.append(list(set(new_group)))

                            if not extended_artist_tokens:
                                singer_matched = True
                            else:
                                primary_matched = any(tkn in v_check_text for tkn in extended_artist_tokens[0])
                                if not primary_matched:
                                    singer_matched = False
                                else:
                                    other_groups = extended_artist_tokens[1:]
                                    all_others_matched = True
                                    for grp in other_groups:
                                        grp_matched = any(tkn in v_check_text for tkn in grp)
                                        is_brand_or_org = any(len(tkn) >= 5 for tkn in grp)
                                        if not grp_matched and not (is_official or is_brand_or_org):
                                            all_others_matched = False
                                            break
                                    singer_matched = all_others_matched

                            if singer_matched:
                                candidates.append({
                                    "id": v_id,
                                    "title": v_title,
                                    "channel": channel_title,
                                    "views": v_views,
                                    "search_mode": order_mode,
                                })

                        if candidates:
                            DUET_PATTERN = r"[\&\+]|\b(?:feat\.?|ft\.?|X|x)\b|合唱|合唱版"

                            clean_cands = []
                            modified_cands = []

                            for cand in candidates:
                                v_t = cand["title"]
                                v_t_lower = v_t.lower()
                                v_channel = cand["channel"].lower()

                                is_duet = (len(artist_tokens) == 1) and bool(re.search(DUET_PATTERN, v_t, re.IGNORECASE))

                                is_demo_or_cover = False
                                if "試聽" in v_t or "试听" in v_t:
                                    is_demo_or_cover = True
                                elif "cover" in v_t_lower or "翻唱" in v_t_lower:
                                    singer_is_main = any(
                                        any(tkn in v_channel or v_t_lower.startswith(tkn) for tkn in group)
                                        for group in artist_tokens
                                    )
                                    if not singer_is_main:
                                        is_demo_or_cover = True

                                if is_duet or is_demo_or_cover:
                                    modified_cands.append(cand)
                                else:
                                    clean_cands.append(cand)

                            if clean_cands:
                                best = max(clean_cands, key=lambda x: x["views"])
                            else:
                                best = max(modified_cands, key=lambda x: x["views"])

                            matched_info = best

                    success = True

                except HttpError as e:
                    if e.resp.status in [403, 429] or any(k in str(e) for k in ["quotaExceeded", "rateLimitExceeded", "Quota exceeded"]):
                        print(f"⚠️ YouTube Key {current_key_idx + 1} 額度耗盡，自動切換...")
                        current_key_idx += 1
                        youtube_service = build_yt_service(current_key_idx)
                        if not youtube_service:
                            break
                    else:
                        break
                except Exception:
                    break

    return matched_info, current_key_idx, youtube_service


# ==========================================
# 3. QQ 音樂與歌詞抓取函式
# ==========================================
def safe_str(val):
    """安全字串轉換器：防止 None / NaN 導致崩潰"""
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ["nan", "none", "null"] else s


def is_same_title(line_text, song_name):
    """判斷該行是否為歌名（忽略括號內容與簡繁體差異）"""
    if not song_name or not line_text:
        return False
    
    # 剔除括號內容
    clean_s = re.sub(r"[\(\（\[【][^\)\）\]】]*[\)\）\]】]", "", song_name).strip()
    clean_l = re.sub(r"[\(\（\[【][^\)\）\]】]*[\)\）\]】]", "", line_text).strip()

    # 統一轉簡體與正規化
    s_norm = normalize_text(zhconv.convert(clean_s, "zh-hans"))
    l_norm = normalize_text(zhconv.convert(clean_l, "zh-hans"))

    if not s_norm or not l_norm:
        return False

    return s_norm in l_norm or l_norm in s_norm


def clean_lyrics_for_gemini(raw_lyrics, song_name="", artist_name=""):
    """🧹 零死角歌詞淨化器（整合簡繁體人員過濾與歌名比對）"""
    raw_lyrics = safe_str(raw_lyrics).replace('\xa0', ' ')
    song_name = safe_str(song_name)
    artist_name = safe_str(artist_name)

    if not raw_lyrics:
        return ""

    INSTRUMENTAL_TAGS = ["instrumental", "純音樂", "請欣賞純音樂", "無歌詞"]
    if raw_lyrics.lower() in INSTRUMENTAL_TAGS:
        return ""

    # 清除 LRC 標頭中非時間軸的元資料 [ti:...], [ar:...] 等
    raw_lyrics = re.sub(r'\[[a-zA-Z\s_-]+:.*?\]', '', raw_lyrics)

    lines = [line.strip() for line in raw_lyrics.split('\n') if line.strip()]
    valid_lines = []
    
    # 包含繁體與簡體的工作人員標籤正則
    STAFF_PATTERN = re.compile(
        r'^(作詞|作词|作曲|詞曲|词曲|填詞|填词|譜曲|谱曲|詞|词|曲|編曲|编曲|製作人|制作人|錄音|录音|混音|吉他|貝斯|贝斯|鼓手|鍵盤|键盘|和聲|和声|母帶|母带|OP|SP|出品|發行|发行|版權|版权|演唱|Lyricist|Composer|Producer|Arranger)\s*[:：\s]', 
        re.IGNORECASE
    )

    is_head_section = True  

    for line_str in lines:
        # 排除時間軸與空括號
        if re.match(r'^\[\d{2}:\d{2}', line_str) or line_str in ["[]", ""]:
            continue

        # 累積有效歌詞超過 5 行後關閉開頭過濾區段
        if len(valid_lines) > 5:
            is_head_section = False

        if is_head_section:
            # 1. 重複歌名過濾
            if is_same_title(line_str, song_name):
                continue
                
            # 2. 工作人員資訊過濾（需小於 30 字）
            if STAFF_PATTERN.search(line_str) and len(line_str) < 30:
                continue

        valid_lines.append(line_str)

    if not valid_lines and lines:
        return "\n".join([l for l in lines if not l.startswith('[')])

    return "\n".join(valid_lines)


# 修正：移除重複的 def，保留完整邏輯
def get_qq_lyrics(songmid, song_name="", artist_name=""):
    """根據 songmid 抓取 QQ 音樂歌詞，並透過 clean_lyrics_for_gemini 進行清洗"""
    if not songmid:
        return ""
    
    url = f"https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?songmid={songmid}&format=json&nobase64=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://y.qq.com/",
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            raw_lyric = data.get("lyric", "")
            if not raw_lyric:
                return ""
            
            clean_lyric = html.unescape(raw_lyric)
            cleaned_text = clean_lyrics_for_gemini(clean_lyric, song_name=song_name, artist_name=artist_name)
            trad_text = zhconv.convert(cleaned_text, 'zh-tw')
            lines = [l for l in trad_text.split('\n') if l.strip()]
            
            return "\n".join(lines[:30])
    except Exception as e:
        print(f"  ⚠️ 抓取 QQ 歌詞失敗 ({songmid}): {e}")
    
    return ""


def fetch_qq_music_chart(top_id, chart_name, date_str):
    url = "https://u.y.qq.com/cgi-bin/musicu.fcg"
    payload = {
        "detail": {
            "module": "musicToplist.ToplistInfoServer",
            "method": "GetDetail",
            "param": {"topId": top_id, "offset": 0, "num": 100, "period": ""},
        },
        "comm": {"ct": 24, "cv": 0},
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://y.qq.com/",
    }
    print(f"[{date_str}] 正在撈取 QQ 音樂 [{chart_name}] Top 100...")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        song_list = response.json()["detail"]["data"]["songInfoList"]
        song_data = []
        for rank, song in enumerate(song_list, start=1):
            song_data.append({
                "抓取日期": date_str,
                "榜單類型": chart_name,
                "排名": rank,
                "歌名": song.get("name", "未知歌名"),
                "歌手": "/".join([s.get("name", "") for s in song.get("singer", [])]),
                "專輯": song.get("album", {}).get("name", "未知專輯"),
                "發行日期": song.get("time_public") or song.get("album", {}).get("time_public", "未知日期"),
                "songmid": song.get("mid", ""),
            })
        return pd.DataFrame(song_data)
    except Exception as e:
        print(f"❌ 撈取 [{chart_name}] 失敗：{e}")
        return None


# ==========================================
# 4. 主程式邏輯
# ==========================================
def main():
    tz_taiwan = timezone(timedelta(hours=8))
    now = datetime.now(tz_taiwan)

    target_date = now - timedelta(days=1)
    
    year_str = target_date.strftime("%Y")
    month_str = target_date.strftime("%Y-%m")
    date_str = target_date.strftime("%Y-%m-%d")

    target_dir = os.path.join(DATA_DIR, year_str, month_str, date_str)
    os.makedirs(target_dir, exist_ok=True)

    raw_yt_keys = os.getenv("YOUTUBE_API_KEYS", "")
    yt_api_keys = [k.strip() for k in raw_yt_keys.split(",") if k.strip()]

    charts = {
        "new": {"top_id": 27, "name": "新歌榜"},
        "film": {"top_id": 29, "name": "影視金曲榜"},
        "show": {"top_id": 64, "name": "綜藝新歌榜"},
        "tik": {"top_id": 60, "name": "抖音熱歌榜"},
    }

    fetched_charts = {}
    all_charts_df_list = []

    for tag, info in charts.items():
        df = fetch_qq_music_chart(info["top_id"], info["name"], date_str)
        if df is not None and not df.empty:
            fetched_charts[tag] = (info["name"], df)
            all_charts_df_list.append(df)

    if not all_charts_df_list:
        print("❌ 今日沒有成功抓取任何榜單資料，程式終止。")
        return

    df_today_all = pd.concat(all_charts_df_list, ignore_index=True)
    
    # 修正：去重時保留 songmid
    df_unique_songs = df_today_all[["歌名", "歌手", "songmid"]].drop_duplicates(subset=["歌名", "歌手"]).reset_index(drop=True)

    expected_cols = ["歌名", "歌手", "Video ID", "語言"]
    if os.path.exists(MAPPING_FILE):
        try:
            df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")
        except Exception:
            df_mapping = pd.DataFrame(columns=expected_cols)
    else:
        df_mapping = pd.DataFrame(columns=expected_cols)

    for col in expected_cols:
        if col not in df_mapping.columns:
            df_mapping[col] = "-"

    df_mapping = df_mapping[expected_cols].drop_duplicates(subset=["歌名", "歌手"], keep="first")

    current_yt_idx = 0
    youtube_service = build("youtube", "v3", developerKey=yt_api_keys[0]) if yt_api_keys else None
    mapping_updated = False

    print("🔍 開始比對與處理今日歌曲之 YouTube ID 與 語言類別...")

    for idx, row in df_unique_songs.iterrows():
        song = str(row["歌名"]).strip()
        singer = str(row["歌手"]).strip()

        mask = (df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)
        matched_row = df_mapping[mask]

        current_vid = "-"
        current_lang = "-"

        if not matched_row.empty:
            current_vid = str(matched_row.iloc[0]["Video ID"]).strip()
            current_lang = str(matched_row.iloc[0]["語言"]).strip()

        # Step A: 檢查/補抓 YouTube Video ID
        has_valid_id = current_vid not in ["-", "", "nan", "None"]
        if not has_valid_id and yt_api_keys:
            print(f"🔄 [補抓 YouTube ID]：{song} - {singer} ...")
            matched_info, current_yt_idx, youtube_service = search_youtube_video(
                song, singer, yt_api_keys, current_yt_idx, youtube_service
            )
            if matched_info:
                current_vid = matched_info["id"]
                print(f"  └─ ✅ 匹配成功 ID: {current_vid}")
            else:
                current_vid = "-"
                print(f"  └─ ❌ 匹配失敗，標記為 '-'")
            mapping_updated = True

        # Step B: 檢查/判定 語言
        has_valid_lang = current_lang not in ["-", "", "nan", "None", "未知"]
        if not has_valid_lang:
            songmid = row.get("songmid", "")
            
            lyrics_text = get_qq_lyrics(songmid, song_name=song, artist_name=singer) if songmid else ""
            
            print(f"🤖 [分析語言類別]：{song} - {singer} (淨化後歌詞: {len(lyrics_text)} 字) ...")
            lang_res = call_gemini_classify_song(
                song_title=song, 
                singer_name=singer, 
                yt_id=current_vid, 
                lyrics=lyrics_text
            )
            current_lang = lang_res["category"]
            print(f"  └─ 🎯 語言判定結果：【{current_lang}】")
            mapping_updated = True

        # Step C: 即時寫回記憶體
        if mask.any():
            df_mapping.loc[mask, "Video ID"] = current_vid
            df_mapping.loc[mask, "語言"] = current_lang
        else:
            new_m_row = pd.DataFrame([{
                "歌名": song,
                "歌手": singer,
                "Video ID": current_vid,
                "語言": current_lang,
            }])
            df_mapping = pd.concat([df_mapping, new_m_row], ignore_index=True)
            mapping_updated = True

    if mapping_updated:
        df_mapping = df_mapping[expected_cols].drop_duplicates(subset=["歌名", "歌手"], keep="first")
        df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
        print(f"💾 對照表資料更新完成 ➔ {MAPPING_FILE}")

    # 3. 批次查詢點閱率
    all_today_mapped = pd.merge(
        df_unique_songs,
        df_mapping[["歌名", "歌手", "Video ID"]],
        on=["歌名", "歌手"],
        how="left",
    )

    unique_vids = [
        str(vid).strip()
        for vid in all_today_mapped["Video ID"].dropna().unique()
        if str(vid).strip() not in ["-", "", "nan", "None"]
    ]

    view_counts_dict = {}
    if unique_vids and yt_api_keys:
        print(f"📊 正在批次向 YouTube 查詢 {len(unique_vids)} 首歌曲的最新點閱率...")
        for i in range(0, len(unique_vids), 50):
            chunk = unique_vids[i : i + 50]
            fetched = False
            while current_yt_idx < len(yt_api_keys) and not fetched:
                if youtube_service is None:
                    youtube_service = build("youtube", "v3", developerKey=yt_api_keys[current_yt_idx])
                    if not youtube_service:
                        break
                try:
                    v_res = youtube_service.videos().list(part="statistics", id=",".join(chunk)).execute()
                    for item in v_res.get("items", []):
                        v_id = item["id"]
                        v_views = int(item["statistics"].get("viewCount", 0))
                        view_counts_dict[v_id] = v_views
                    fetched = True
                except HttpError as e:
                    if e.resp.status in [403, 429] or any(k in str(e) for k in ["quotaExceeded", "rateLimitExceeded", "Quota exceeded"]):
                        current_yt_idx += 1
                        youtube_service = build("youtube", "v3", developerKey=yt_api_keys[current_yt_idx]) if current_yt_idx < len(yt_api_keys) else None
                    else:
                        break
                except Exception:
                    break

    # 4. 合併欄位並輸出今日榜單 CSV
    print("💾 正在整合點閱率與語言欄位，儲存今日榜單 CSV...")
    for tag, (chart_name, df_chart) in fetched_charts.items():
        df_final = pd.merge(
            df_chart,
            df_mapping[["歌名", "歌手", "Video ID", "語言"]],
            on=["歌名", "歌手"],
            how="left",
        )

        df_final["YouTube ID"] = df_final["Video ID"].fillna("-")
        df_final["語言"] = df_final["語言"].fillna("未知")

        raw_views = df_final["Video ID"].map(view_counts_dict).fillna(0)
        df_final["點閱率"] = raw_views.apply(lambda x: f"{int(x):,}" if x > 0 else "-")

        df_final = df_final.drop(columns=["Video ID"], errors="ignore")

        csv_filename = os.path.join(target_dir, f"{date_str}_{tag}.csv")
        df_final.to_csv(csv_filename, index=False, encoding="utf-8-sig")
        print(f"   ✓ [{chart_name}] 已成功儲存 ➔ {csv_filename}")

    print("✅ 排程執行完畢，所有資料與對照表皆已同步更新！")


def update_schedule_log(workflow_name="每日排程更新", status="成功"):
    log_path = "data/schedule_logs.json"

    tz_taiwan = timezone(timedelta(hours=8))
    now_tw = datetime.now(tz_taiwan)
    
    new_entry = {
        "time": now_tw.strftime("%Y-%m-%d %H:%M:%S"),
        "name": workflow_name,
        "status": status
    }
    
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            try:
                logs = json.load(f)
            except json.JSONDecodeError:
                logs = []
    else:
        logs = []
        
    logs.insert(0, new_entry)
    
    if len(logs) > 10:
        logs = logs[:10]
        
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    main()
    update_schedule_log()
