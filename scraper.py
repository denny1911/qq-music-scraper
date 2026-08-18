from datetime import datetime, timedelta, timezone
import os
import re
import time
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd
import requests
import zhconv

# ==========================================
# 1. 基礎設定與輔助函式
# ==========================================
DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")

# 對照表僅保留 3 個標準欄位（保持乾淨）
REQ_MAPPING_COLS = ["歌名", "歌手", "Video ID"]

# 噪音關鍵字過濾庫（非 Topic 頻道才套用）
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


def parse_duration(duration_str):
    """將 YouTube ISO 8601 時間字串 (例如 PT3M45S) 轉為總秒數"""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def clean_song_title(title):
    """清理歌名中的前綴（例如綜藝榜特有的 '歌曲：'）以提高 YouTube 搜尋與比對命中率"""
    if not title:
        return ""
    cleaned = re.sub(r"^歌曲[:：]\s*", "", str(title))
    return cleaned.strip()


def parse_song_title(song):
    """清理歌名前綴並拆解出主要歌名（去除括號內容）"""
    clean_s = clean_song_title(song)
    main_s = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", clean_s).strip()
    if not main_s:
        main_s = clean_s
    return clean_s, main_s


def normalize_text(text):
    """清理字串中的所有空格、常見標點符號與羅馬數字轉換，供模糊比對"""
    if not text:
        return ""
    t = str(text).lower()

    # 1. 統一將特殊羅馬數字轉為半角英文字母
    t = t.replace('ⅱ', 'ii').replace('ⅰ', 'i').replace('ⅲ', 'iii').replace('ⅳ', 'iv')

    # 2. 清除空格、括號與引號符號 (包含 「」 《》【】『』)
    return re.sub(r"[\s\.\-\_\(\)（）「」《》【】『』""'']", "", t)


def extract_artist_tokens(singer):
    """拆解多歌手與簡繁體 Token，並支援括號 ()（）別名同組歸納 (與模組四同步)"""
    if not singer or str(singer).lower() in ["-", "nan", "none"]:
        return []

    singer_str = str(singer).strip()

    # 1. 只用「真正的合唱分隔符」拆分不同歌手（不以括號切割）
    raw_artists = re.split(
        r"[/&,\+\·\*\-\|\s]+|feat\.?|ft\.?|X|x",
        singer_str,
        flags=re.IGNORECASE,
    )

    artist_groups = []

    for raw in raw_artists:
        raw = raw.strip()
        if not raw:
            continue

        group_tokens = set()

        # 2. 提取整體（例如 "田園(小園)"）
        group_tokens.add(zhconv.convert(raw, "zh-hans"))
        group_tokens.add(zhconv.convert(raw, "zh-hant"))

        # 3. 提取去除括號後的主名字（例如 "田園"）
        clean_raw = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", raw).strip()
        if clean_raw:
            group_tokens.add(zhconv.convert(clean_raw, "zh-hans"))
            group_tokens.add(zhconv.convert(clean_raw, "zh-hant"))

        # 4. 提取括號內的別名/綽號（例如 "小園"），全部放進「同一組」！
        bracket_content = re.findall(r"[\(\（]([^\)\）]+)[\)\）]", raw)
        for b in bracket_content:
            b = b.strip()
            if b:
                group_tokens.add(zhconv.convert(b, "zh-hans"))
                group_tokens.add(zhconv.convert(b, "zh-hant"))

        # 5. 拆解英文/單字片段
        sub_chunks = re.findall(
            r"[a-zA-Z0-9\.\-\']+|[\u4e00-\u9fa5]+|[\uAC00-\uD7A3]+", raw
        )
        if len(sub_chunks) > 1:
            for chunk in sub_chunks:
                chunk = chunk.strip()
                if len(chunk) >= 1:
                    group_tokens.add(zhconv.convert(chunk, "zh-hans"))
                    group_tokens.add(zhconv.convert(chunk, "zh-hant"))

        # 規格化
        norm_group = [normalize_text(t) for t in group_tokens if normalize_text(t)]
        if norm_group:
            artist_groups.append(list(set(norm_group)))

    return artist_groups


def build_search_queries(song, singer):
    """產生搜尋字串：優先使用去除括號的「主歌名 + 歌手」，並加入繁體補救 (與模組四同步)"""
    clean_s, main_s = parse_song_title(song)
    clean_p = str(singer).strip()

    primary_query = f"{main_s} {clean_p}".strip()
    queries = [primary_query]

    # 繁體中文搜尋補救
    primary_query_tra = f"{zhconv.convert(main_s, 'zh-hant')} {zhconv.convert(clean_p, 'zh-hant')}".strip()
    if primary_query_tra not in queries:
        queries.append(primary_query_tra)

    if clean_s != main_s:
        full_query = f"{clean_s} {clean_p}".strip()
        if full_query not in queries:
            queries.append(full_query)

    extracted_bracket = re.findall(r"[\(\（]([^\)\）]+)[\)\）]", clean_p)
    if extracted_bracket:
        fallback_singer = " ".join(extracted_bracket).strip()
        fallback_query = f"{main_s} {fallback_singer}".strip()
        if fallback_query not in queries:
            queries.append(fallback_query)

    return queries


# ==========================================
# 2. 核心 YouTube 雙階段搜尋函式 (與模組四完全同步)
# ==========================================
def search_youtube_video(song, singer, api_keys, current_key_idx, youtube_service):
    """雙階段搜尋機制：先 viewCount 抓 30 筆 ➔ 再 relevance 抓 5 筆補救"""
    clean_song, main_song = parse_song_title(song)
    search_queries = build_search_queries(song, singer)

    main_sim_norm = normalize_text(zhconv.convert(main_song, "zh-hans"))
    main_tra_norm = normalize_text(zhconv.convert(main_song, "zh-hant"))
    artist_tokens = extract_artist_tokens(singer)

    matched_info = None

    def build_yt_service(idx):
        return build("youtube", "v3", developerKey=api_keys[idx]) if idx < len(api_keys) else None

    # 固定兩階段策略
    order_strategies = ["viewCount", "relevance"]

    for order_mode in order_strategies:
        if matched_info:
            break

        # 根據搜尋模式動態設定筆數：viewCount 抓 30 筆，relevance 抓 5 筆
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
                            .list(
                                part="snippet,statistics,contentDetails",
                                id=",".join(v_ids),
                            )
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

                            # 過濾短影音與長影片 (1分5秒 ~ 8分鐘，即 65 秒 ~ 480 秒)
                            if duration_sec <= 65 or duration_sec > 480:
                                continue

                            v_title_lower = v_title.lower()
                            v_title_norm = normalize_text(v_title)
                            channel_lower = channel_title.lower()
                            channel_norm = normalize_text(channel_title)
                            v_desc_norm = normalize_text(v_desc)

                            is_topic = "topic" in channel_lower or "主題" in channel_lower

                            # 噪音過濾（Topic 頻道豁免）
                            has_noise = any(nk in v_title_lower for nk in COMBINED_NOISE_KEYWORDS)
                            if not is_topic and has_noise:
                                continue

                            # 歌名檢驗
                            song_matched = (main_sim_norm in v_title_norm) or (main_tra_norm in v_title_norm)
                            if not song_matched:
                                continue

                            # 組合全文（標題 + 頻道 + 說明欄）
                            v_full_text = f"{v_title_norm} {channel_norm} {v_desc_norm}"

                            # 歌手檢驗：要求每一位歌手組 (Group) 都必須至少有一個 Token 命中
                            singer_matched = not artist_tokens or all(
                                any(tkn in v_full_text for tkn in group)
                                for group in artist_tokens
                            )

                            cand = {
                                "id": v_id,
                                "title": v_title,
                                "channel": channel_title,
                                "views": v_views,
                                "url": f"https://www.youtube.com/watch?v={v_id}",
                                "search_mode": order_mode,
                            }

                            if singer_matched:
                                candidates.append(cand)

                        if candidates:
                            best = max(candidates, key=lambda x: x["views"])
                            matched_info = best

                    success = True

                except HttpError as e:
                    is_quota_error = e.resp.status in [403, 429] or any(
                        k in str(e)
                        for k in ["quotaExceeded", "rateLimitExceeded", "Quota exceeded"]
                    )
                    if is_quota_error:
                        print(f"⚠️ 第 {current_key_idx + 1} 組 API Key 額度用盡，自動切換至下一組 Key...")
                        current_key_idx += 1
                        youtube_service = build_yt_service(current_key_idx)
                        if not youtube_service:
                            print("❌ 所有 API Key 的每日額度皆已耗盡！")
                            break
                    else:
                        print(f"⚠️ 搜尋 {song} 時發生 API 錯誤: {e}")
                        break
                except Exception as e:
                    print(f"⚠️ 搜尋 {song} 時發生未知錯誤: {e}")
                    break

    return matched_info, current_key_idx, youtube_service


# ==========================================
# 3. QQ 音樂抓取函式
# ==========================================
def fetch_qq_music_chart(top_id, chart_name, date_str):
    """通用函式：輸入 topId 與榜單名稱，撈取前 100 名資料"""
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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://y.qq.com/",
    }
    print(f"[{date_str}] 正在撈取 QQ 音樂 [{chart_name}] Top 100...")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        song_list = data["detail"]["data"]["songInfoList"]
        song_data = []
        for rank, song in enumerate(song_list, start=1):
            title = song.get("name", "未知歌名")
            singers = "/".join([s.get("name", "") for s in song.get("singer", [])])
            album = song.get("album", {}).get("name", "未知專輯")
            release_date = song.get("time_public") or song.get("album", {}).get("time_public", "未知日期")
            song_data.append({
                "抓取日期": date_str,
                "榜單類型": chart_name,
                "排名": rank,
                "歌名": title,
                "歌手": singers,
                "專輯": album,
                "發行日期": release_date,
            })
        return pd.DataFrame(song_data)
    except Exception as e:
        print(f"❌ 撈取 [{chart_name}] 過程發生錯誤：{e}")
        return None
