from datetime import datetime, timedelta, timezone
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
    # 相容 GitHub Actions 傳入的 GEMINI_API_KEYS 或舊的 API_KEYS
    env_keys = os.getenv("GEMINI_API_KEYS") or os.getenv("GEMINI_API_KEY", "")

    if env_keys:
        # 同時支援「多行換行」與「逗號分隔」的 Key 格式
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

def call_gemini_classify_song(song_title, singer_name, yt_id=None):
    """呼叫 Gemini 判斷歌曲語言，並強制輸出繁體中文類別"""
    global CURRENT_GEMINI_KEY_IDX

    if not GEMINI_API_KEYS:
        print("  ❌ 未找到任何 Gemini API Key")
        return {"success": False, "category": "未知"}

    yt_link_info = f"https://www.youtube.com/watch?v={yt_id}" if yt_id and str(yt_id) not in ["-", "", "nan", "None"] else "無"

    prompt = f"""
你是一個專業音樂榜單數據分析專家。請結合歌名、歌手背景知識以及 YouTube 影片資訊，將這首歌曲精準歸類為以下【5 種語言類別】之一（請務必使用繁體中文）：
1. "華語" (歌詞以國語/粵語/台語為主)
2. "西洋" (歌詞以英文/歐美語系為主)
3. "韓語" (歌詞以韓文為主，K-Pop)
4. "日語" (歌詞以日文為主，J-Pop)
5. "其它" (純音樂、無歌詞、電子樂伴奏，或上述四者之外的語言)

待分析歌曲資料：
- 歌名："{song_title}"
- 歌手："{singer_name}"
- YouTube 連結：{yt_link_info}

【關鍵判斷標準】：
1. 實際演唱語言絕對優先：必須嚴格根據「實際演唱歌詞的主要語言」判定，【絕對禁止】僅憑「歌手國籍、所屬團體或發行地區」直接歸類！
2. 華語/亞洲歌手的英文歌：若華語或亞洲歌手發行的是「全英文」或「英文為主」的歌曲（如：嚴浩翔《No More Tomorrow》、張藝興《Crossfire》、王嘉爾全英文單曲、BTS 英文單曲），不論歌手是誰，【必須歸類為 "西洋"】。
3. 英文歌名的華語歌：歌名雖包含英文單字，但實際演唱歌詞絕大部分為華語（如：周深《Rubia》若歌詞包含華語，或一般華語 pop 帶英文歌名），才可歸類為 "華語"。
4. 混血/跨國合作曲：若為多國語言混合，請以演唱比例超過 50% 的語言為主；若為無歌詞的純音樂（Instrumental），一律歸類為 "其它"。
5. 參考 YouTube 資訊：若提供了 YouTube 連結，請結合該影片與知識庫進行精準判定。

請嚴格只輸出 JSON 格式，結構如下：
{{
  "category": "西洋",
  "reason": "結合 YouTube 影片與背景知識，該歌曲為全英文單曲，演唱語言為英文，故歸類為西洋。"
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

    raw_artists = re.split(r"[/&,\+\·\*\-\|\s]+|feat\.?|ft\.?|X|x", str(singer).strip(), flags=re.IGNORECASE)
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

        for b in re.findall(r"[\(\（]([^\)\）]+)[\)\）]", raw):
            if b.strip():
                group_tokens.add(zhconv.convert(b.strip(), "zh-hans"))
                group_tokens.add(zhconv.convert(b.strip(), "zh-hant"))

        zh_only = "".join(re.findall(r"[\u4e00-\u9fa5]+", clean_raw)).strip()
        if len(zh_only) >= 2:
            group_tokens.add(zhconv.convert(zh_only, "zh-hans"))
            group_tokens.add(zhconv.convert(zh_only, "zh-hant"))

        en_only = "".join(re.findall(r"[a-zA-Z0-9\s]+", clean_raw)).strip()
        if len(en_only) >= 2:
            group_tokens.add(en_only)

        norm_group = [normalize_text(t) for t in group_tokens if normalize_text(t)]
        if norm_group:
            artist_groups.append(list(set(norm_group)))

    return artist_groups


def build_search_queries(song, singer):
    clean_s, main_s = parse_song_title(song)
    clean_p = str(singer).strip()

    queries = [f"{main_s} {clean_p}".strip()]
    tra_q = f"{zhconv.convert(main_s, 'zh-hant')} {zhconv.convert(clean_p, 'zh-hant')}".strip()
    if tra_q not in queries:
        queries.append(tra_q)

    if clean_s != main_s:
        full_q = f"{clean_s} {clean_p}".strip()
        if full_q not in queries:
            queries.append(full_q)

    bracket_singers = re.findall(r"[\(\（]([^\)\）]+)[\)\）]", clean_p)
    if bracket_singers:
        fb_q = f"{main_s} {' '.join(bracket_singers)}".strip()
        if fb_q not in queries:
            queries.append(fb_q)

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

    for order_mode in ["viewCount", "relevance"]:
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

                            duration_sec = parse_duration(item.get("contentDetails", {}).get("duration", "PT0S"))
                            if duration_sec <= 65 or duration_sec > 480:
                                continue

                            v_title_lower = v_title.lower()
                            v_title_norm = normalize_text(v_title)
                            channel_lower = channel_title.lower()
                            channel_norm = normalize_text(channel_title)
                            v_desc_norm = normalize_text(v_desc)

                            is_topic = "topic" in channel_lower or "主題" in channel_lower
                            if not is_topic and any(nk in v_title_lower for nk in COMBINED_NOISE_KEYWORDS):
                                continue

                            song_matched = (main_sim_norm in v_title_norm) or (main_tra_norm in v_title_norm)
                            if not song_matched:
                                continue

                            v_full_text = f"{v_title_norm} {channel_norm} {v_desc_norm}"
                            singer_matched = not artist_tokens or all(
                                any(tkn in v_full_text for tkn in group) for group in artist_tokens
                            )

                            if singer_matched:
                                candidates.append({
                                    "id": v_id,
                                    "views": v_views,
                                    "search_mode": order_mode,
                                })

                        if candidates:
                            matched_info = max(candidates, key=lambda x: x["views"])

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
# 3. QQ 音樂抓取函式
# ==========================================
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

    # 💡 因為半夜執行排程抓取的是前一天榜單，故減去 1 天作為資料日期
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
    df_unique_songs = df_today_all[["歌名", "歌手"]].drop_duplicates().reset_index(drop=True)

    # 1. 讀取現有對照表
    expected_cols = ["歌名", "歌手", "Video ID", "語言"]
    if os.path.exists(MAPPING_FILE):
        try:
            df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")
        except Exception:
            df_mapping = pd.DataFrame(columns=expected_cols)
    else:
        df_mapping = pd.DataFrame(columns=expected_cols)

    # 確保欄位齊全
    for col in expected_cols:
        if col not in df_mapping.columns:
            df_mapping[col] = "-"

    df_mapping = df_mapping[expected_cols].drop_duplicates(subset=["歌名", "歌手"], keep="first")

    current_yt_idx = 0
    youtube_service = build("youtube", "v3", developerKey=yt_api_keys[0]) if yt_api_keys else None
    mapping_updated = False

    # 2. 逐一檢查今日歌曲的 Video ID 與 語言
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

        # Step B: 檢查/判定 語言 (若對照表已有有效語言則跳過，實現 Gemini API 省用)
        has_valid_lang = current_lang not in ["-", "", "nan", "None", "未知"]
        if not has_valid_lang:
            print(f"🤖 [分析語言類別]：{song} - {singer} (YouTube ID: {current_vid}) ...")
            lang_res = call_gemini_classify_song(song_title=song, singer_name=singer, yt_id=current_vid)
            current_lang = lang_res["category"]
            print(f"  └─ 🎯 語言判定結果：【{current_lang}】")
            mapping_updated = True

        # Step C: 即時寫回或新增至對照表記憶體中
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

    # 對照表如果有新增或更新，存回 CSV 檔
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

# ==========================================
# 新增：排程更新日誌紀錄函式
# ==========================================
import json
from datetime import datetime

def update_schedule_log(workflow_name="每日排程更新", status="成功"):
    log_path = "data/schedule_logs.json"
    
    new_entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
    # 在這裡呼叫它，讓每次程式跑完時自動記錄
    update_schedule_log()
