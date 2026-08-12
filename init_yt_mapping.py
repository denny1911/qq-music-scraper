import glob
import os
import re
import time
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd
import zhconv

# ==========================================
# 1. 基礎設定與欄位定義
# ==========================================
DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")

# 僅保留 4 個標準對照欄位
REQ_MAPPING_COLS = ["歌名", "歌手", "Video ID", "影片連結"]

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
    """清理歌名中的前綴（例如 '歌曲：'）以提高搜尋與比對命中率"""
    if not title:
        return ""
    cleaned = re.sub(r"^歌曲[:：]\s*", "", str(title))
    return cleaned.strip()


def normalize_text(text):
    """清理字串中的空格與常見標點符號供模糊比對"""
    if not text:
        return ""
    return re.sub(r"[\s\.\-\_\(\)（）]", "", str(text)).lower()


def extract_artist_tokens(singer):
    """拆解多歌手與簡繁體 Token (例如 '周慧敏/刘敏涛' -> ['周慧敏', '刘敏涛', '劉敏濤'])"""
    if not singer or singer in ["-", "nan", "None"]:
        return []

    singer_str = str(singer).strip()
    all_tokens = set()

    # 用常見分隔符切割 (/, &, comma, +, x, feat, ft, etc.)
    raw_tokens = re.split(
        r"[/&,\+\·\s\*\-\|]|feat\.?|ft\.?|X|x", singer_str, flags=re.IGNORECASE
    )

    for raw in raw_tokens:
        raw = raw.strip()
        if not raw:
            continue
        all_tokens.add(zhconv.convert(raw, "zh-hans"))
        all_tokens.add(zhconv.convert(raw, "zh-hant"))

        # 按 中文 / 英文數字 邊界進一步拆分
        sub_chunks = re.findall(r"[a-zA-Z0-9\.\-\']+|[\u4e00-\u9fa5]+", raw)
        if len(sub_chunks) > 1:
            for chunk in sub_chunks:
                chunk = chunk.strip()
                if len(chunk) >= 2 or re.search(r"[\u4e00-\u9fa5]", chunk):
                    all_tokens.add(zhconv.convert(chunk, "zh-hans"))
                    all_tokens.add(zhconv.convert(chunk, "zh-hant"))

    normalized_tokens = []
    for t in all_tokens:
        norm = normalize_text(t)
        if norm and len(norm) >= 2:
            normalized_tokens.append(norm)

    return list(set(normalized_tokens))


# ==========================================
# 2. 核心邏輯：僅針對缺少 ID 的歌曲進行補抓
# ==========================================
def run_init_and_retry():
    raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    # ----------------------------------------------------
    # 步驟 1：讀取既有 yt_mapping.csv（保留已知 ID）
    # ----------------------------------------------------
    if os.path.exists(MAPPING_FILE):
        try:
            df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")
        except Exception as e:
            print(f"⚠️ 讀取既有 {MAPPING_FILE} 失敗: {e}")
            df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)
    else:
        df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)

    # 補齊必要欄位
    for col in REQ_MAPPING_COLS:
        if col not in df_mapping.columns:
            df_mapping[col] = "-"
    df_mapping = df_mapping[REQ_MAPPING_COLS]

    # ----------------------------------------------------
    # 步驟 2：掃描歷史榜單 CSV，補充未存在於對照表中的【全新歌曲】
    # ----------------------------------------------------
    all_chart_files = glob.glob(
        os.path.join(DATA_DIR, "**", "*.csv"), recursive=True
    )
    new_song_rows = []

    for f in all_chart_files:
        if "yt_mapping" in f or "yt_baseline" in f:
            continue
        try:
            df_chart = pd.read_csv(f, dtype=str)
            if "歌名" in df_chart.columns and "歌手" in df_chart.columns:
                for _, c_row in (
                    df_chart[["歌名", "歌手"]].drop_duplicates().iterrows()
                ):
                    s_name = str(c_row["歌名"]).strip()
                    s_singer = str(c_row["歌手"]).strip()

                    # 檢查是否已在對照表中
                    exists = (
                        (df_mapping["歌名"] == s_name)
                        & (df_mapping["歌手"] == s_singer)
                    ).any()

                    if not exists:
                        new_song_rows.append({
                            "歌名": s_name,
                            "歌手": s_singer,
                            "Video ID": "-",
                            "影片連結": "-",
                        })
        except Exception as e:
            print(f"⚠️ 讀取 {f} 失敗: {e}")

    if new_song_rows:
        df_new = pd.DataFrame(new_song_rows).drop_duplicates(
            subset=["歌名", "歌手"]
        )
        df_mapping = pd.concat([df_mapping, df_new], ignore_index=True)

    df_mapping = df_mapping.drop_duplicates(
        subset=["歌名", "歌手"], keep="first"
    ).reset_index(drop=True)

    # 找出缺少有效 ID 的歌曲清單
    missing_mask = df_mapping["Video ID"].astype(str).str.strip().isin(
        ["-", "", "nan", "None"]
    )
    missing_count = missing_mask.sum()
    total_songs = len(df_mapping)

    print(
        f"📊 目前對照表共累積 {total_songs} 首歌曲。"
        f"其中已知 ID 有 {total_songs - missing_count} 首，待補抓 ID 有 {missing_count} 首。"
    )

    if missing_count == 0:
        print("🎉 所有歌曲皆已有有效 Video ID，無需進行 API 搜尋！")
        return

    if not api_keys:
        print("❌ 未找到 YOUTUBE_API_KEYS 環境變數，無法執行 YouTube 搜尋補抓！")
        return

    # ----------------------------------------------------
    # 步驟 3：僅針對 Video ID 為 '-' 的歌曲調用 API 搜尋
    # ----------------------------------------------------
    print(f"🚀 開始向 YouTube API 發起剩餘 {missing_count} 首歌曲的搜尋...")

    current_key_idx = 0

    def get_yt_service(idx):
        if idx < len(api_keys):
            return build("youtube", "v3", developerKey=api_keys[idx])
        return None

    youtube_service = get_yt_service(current_key_idx)
    updated_count = 0

    for idx, row in df_mapping.iterrows():
        song = str(row["歌名"]).strip()
        singer = str(row["歌手"]).strip()
        vid_val = str(row["Video ID"]).strip()

        # 💡 核心重點：若已經有有效 Video ID，直接跳過！
        if vid_val not in ["-", "", "nan", "None"]:
            continue

        clean_song = clean_song_title(song)
        query_str = f"{clean_song} {singer}"
        print(f"🔍 [{idx + 1}/{total_songs}] 搜尋補抓中: {song} - {singer} ...")

        matched_id = None
        matched_title = None
        matched_views = 0
        matched_url = None
        success = False

        # 準備歌名比對格式 (簡體與繁體)
        song_sim_norm = normalize_text(zhconv.convert(clean_song, "zh-hans"))
        song_tra_norm = normalize_text(zhconv.convert(clean_song, "zh-hant"))

        # 進階拆解歌手 Token
        artist_tokens = extract_artist_tokens(singer)

        while current_key_idx < len(api_keys) and not success:
            if youtube_service is None:
                youtube_service = get_yt_service(current_key_idx)
                if not youtube_service:
                    break
            try:
                # 引用新模組四的 YouTube 搜尋邏輯 (maxResults=30, order="viewCount", regionCode="TW")
                search_res = (
                    youtube_service.search()
                    .list(
                        q=query_str,
                        part="id",
                        maxResults=30,
                        type="video",
                        order="viewCount",
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
                        v_views = int(item["statistics"].get("viewCount", 0))

                        duration_str = item.get("contentDetails", {}).get(
                            "duration", "PT0S"
                        )
                        duration_sec = parse_duration(duration_str)

                        # 片長限制：61 秒 ~ 10 分鐘
                        if duration_sec <= 60 or duration_sec > 600:
                            continue

                        v_title_lower = v_title.lower()
                        v_title_norm = normalize_text(v_title)
                        channel_lower = channel_title.lower()
                        channel_norm = normalize_text(channel_title)

                        # 1. 判定是否為 Topic 官方生成音源頻道
                        is_topic = (
                            "topic" in channel_lower
                            or "主題" in channel_lower
                        )

                        # 2. 噪音詞過濾（非 Topic 頻道才過濾）
                        has_noise = any(
                            nk in v_title_lower
                            for nk in COMBINED_NOISE_KEYWORDS
                        )
                        if not is_topic and has_noise:
                            continue

                        # 3. 歌名簡繁體比對
                        song_matched = (song_sim_norm in v_title_norm) or (
                            song_tra_norm in v_title_norm
                        )
                        if not song_matched:
                            continue

                        # 4. 歌手比對
                        singer_matched = any(
                            tkn in v_title_norm for tkn in artist_tokens
                        ) or any(
                            tkn in channel_norm for tkn in artist_tokens
                        )

                        cand = {
                            "id": v_id,
                            "title": v_title,
                            "channel": channel_title,
                            "views": v_views,
                            "url": f"https://www.youtube.com/watch?v={v_id}",
                        }

                        if is_topic or singer_matched:
                            candidates.append(cand)

                    if candidates:
                        best = max(candidates, key=lambda x: x["views"])
                        matched_id = best["id"]
                        matched_title = best["title"]
                        matched_views = best["views"]
                        matched_url = best["url"]

                success = True

            except HttpError as e:
                is_quota_error = e.resp.status in [403, 429] or any(
                    k in str(e)
                    for k in [
                        "quotaExceeded",
                        "rateLimitExceeded",
                        "Quota exceeded",
                    ]
                )
                if is_quota_error:
                    print(
                        f"⚠️ 第 {current_key_idx + 1} 組 API Key 額度用盡，自動切換至下一組 Key..."
                    )
                    current_key_idx += 1
                    youtube_service = get_yt_service(current_key_idx)
                    if not youtube_service:
                        print("❌ 所有 API Key 的每日額度皆已耗盡！")
                        break
                else:
                    print(f"⚠️ 搜尋 {song} 時發生 API 錯誤: {e}")
                    break
            except Exception as e:
                print(f"⚠️ 搜尋 {song} 時發生未知錯誤: {e}")
                break

        if matched_id:
            print(f"  ✅ 補抓成功 ➔ ID: {matched_id} | 點閱: {matched_views:,}")
            df_mapping.loc[idx, "Video ID"] = matched_id
            df_mapping.loc[idx, "影片連結"] = matched_url
            updated_count += 1
        else:
            print("  ❌ 未找到符合影片，保持 '-'")

        time.sleep(0.1)

    print(
        f"\n🎉 補抓完成！本次共成功補充 {updated_count} / {missing_count} 首歌曲的 Video ID！"
    )

    # ----------------------------------------------------
    # 步驟 4：更新儲存回 yt_mapping.csv
    # ----------------------------------------------------
    df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
    print(f"💾 對照表已成功更新儲存 ➔ {MAPPING_FILE}")


if __name__ == "__main__":
    run_init_and_retry()
    singer_str = str(singer).strip()
    all_tokens = set()

    # 用常見分隔符切割 (/, &, comma, +, x, feat, ft, etc.)
    raw_tokens = re.split(
        r"[/&,\+\·\s\*\-\|]|feat\.?|ft\.?|X|x", singer_str, flags=re.IGNORECASE
    )

    for raw in raw_tokens:
        raw = raw.strip()
        if not raw:
            continue
        all_tokens.add(zhconv.convert(raw, "zh-hans"))
        all_tokens.add(zhconv.convert(raw, "zh-hant"))

        # 按 中文 / 英文數字 邊界進一步拆分
        sub_chunks = re.findall(r"[a-zA-Z0-9\.\-\']+|[\u4e00-\u9fa5]+", raw)
        if len(sub_chunks) > 1:
            for chunk in sub_chunks:
                chunk = chunk.strip()
                if len(chunk) >= 2 or re.search(r"[\u4e00-\u9fa5]", chunk):
                    all_tokens.add(zhconv.convert(chunk, "zh-hans"))
                    all_tokens.add(zhconv.convert(chunk, "zh-hant"))

    normalized_tokens = []
    for t in all_tokens:
        norm = normalize_text(t)
        if norm and len(norm) >= 2:
            normalized_tokens.append(norm)

    return list(set(normalized_tokens))


# ==========================================
# 2. 核心邏輯：僅針對缺少 ID 的歌曲進行補抓
# ==========================================
def run_init_and_retry():
    raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    # ----------------------------------------------------
    # 步驟 1：讀取既有 yt_mapping.csv（保留已知 ID）
    # ----------------------------------------------------
    if os.path.exists(MAPPING_FILE):
        try:
            df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")
        except Exception as e:
            print(f"⚠️ 讀取既有 {MAPPING_FILE} 失敗: {e}")
            df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)
    else:
        df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)

    # 補齊必要欄位
    for col in REQ_MAPPING_COLS:
        if col not in df_mapping.columns:
            df_mapping[col] = "-"
    df_mapping = df_mapping[REQ_MAPPING_COLS]

    # ----------------------------------------------------
    # 步驟 2：掃描歷史榜單 CSV，補充未存在於對照表中的【全新歌曲】
    # ----------------------------------------------------
    all_chart_files = glob.glob(
        os.path.join(DATA_DIR, "**", "*.csv"), recursive=True
    )
    new_song_rows = []

    for f in all_chart_files:
        if "yt_mapping" in f or "yt_baseline" in f:
            continue
        try:
            df_chart = pd.read_csv(f, dtype=str)
            if "歌名" in df_chart.columns and "歌手" in df_chart.columns:
                for _, c_row in (
                    df_chart[["歌名", "歌手"]].drop_duplicates().iterrows()
                ):
                    s_name = str(c_row["歌名"]).strip()
                    s_singer = str(c_row["歌手"]).strip()

                    # 檢查是否已在對照表中
                    exists = (
                        (df_mapping["歌名"] == s_name)
                        & (df_mapping["歌手"] == s_singer)
                    ).any()

                    if not exists:
                        new_song_rows.append({
                            "歌名": s_name,
                            "歌手": s_singer,
                            "Video ID": "-",
                            "影片連結": "-",
                        })
        except Exception as e:
            print(f"⚠️ 讀取 {f} 失敗: {e}")

    if new_song_rows:
        df_new = pd.DataFrame(new_song_rows).drop_duplicates(
            subset=["歌名", "歌手"]
        )
        df_mapping = pd.concat([df_mapping, df_new], ignore_index=True)

    df_mapping = df_mapping.drop_duplicates(
        subset=["歌名", "歌手"], keep="first"
    ).reset_index(drop=True)

    # 找出缺少有效 ID 的歌曲清單
    missing_mask = df_mapping["Video ID"].astype(str).str.strip().isin(
        ["-", "", "nan", "None"]
    )
    missing_count = missing_mask.sum()
    total_songs = len(df_mapping)

    print(
        f"📊 目前對照表共累積 {total_songs} 首歌曲。"
        f"其中已知 ID 有 {total_songs - missing_count} 首，待補抓 ID 有 {missing_count} 首。"
    )

    if missing_count == 0:
        print("🎉 所有歌曲皆已有有效 Video ID，無需進行 API 搜尋！")
        return

    if not api_keys:
        print("❌ 未找到 YOUTUBE_API_KEYS 環境變數，無法執行 YouTube 搜尋補抓！")
        return

    # ----------------------------------------------------
    # 步驟 3：僅針對 Video ID 為 '-' 的歌曲調用 API 搜尋
    # ----------------------------------------------------
    print(f"🚀 開始向 YouTube API 發起剩餘 {missing_count} 首歌曲的搜尋...")

    current_key_idx = 0

    def get_yt_service(idx):
        if idx < len(api_keys):
            return build("youtube", "v3", developerKey=api_keys[idx])
        return None

    youtube_service = get_yt_service(current_key_idx)
    updated_count = 0

    for idx, row in df_mapping.iterrows():
        song = str(row["歌名"]).strip()
        singer = str(row["歌手"]).strip()
        vid_val = str(row["Video ID"]).strip()

        # 💡 核心重點：若已經有有效 Video ID，直接跳過！
        if vid_val not in ["-", "", "nan", "None"]:
            continue

        clean_song = clean_song_title(song)
        query_str = f"{clean_song} {singer}"
        print(f"🔍 [{idx + 1}/{total_songs}] 搜尋補抓中: {song} - {singer} ...")

        matched_id = None
        matched_views = 0
        matched_url = None
        success = False

        # 準備歌名比對格式 (簡體與繁體)
        song_sim_norm = normalize_text(zhconv.convert(clean_song, "zh-hans"))
        song_tra_norm = normalize_text(zhconv.convert(clean_song, "zh-hant"))

        # 進階拆解歌手 Token
        artist_tokens = extract_artist_tokens(singer)

        while current_key_idx < len(api_keys) and not success:
            if youtube_service is None:
                youtube_service = get_yt_service(current_key_idx)
                if not youtube_service:
                    break
            try:
                search_res = (
                    youtube_service.search()
                    .list(
                        q=query_str,
                        part="id",
                        maxResults=10,
                        type="video",
                        order="relevance",
                        videoCategoryId="10",
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
                        v_views = int(item["statistics"].get("viewCount", 0))

                        duration_sec = parse_duration(
                            item.get("contentDetails", {}).get(
                                "duration", "PT0S"
                            )
                        )

                        # 片長限制：61 秒 ~ 10 分鐘
                        if duration_sec <= 60 or duration_sec > 600:
                            continue

                        v_title_lower = v_title.lower()
                        v_title_norm = normalize_text(v_title)
                        channel_lower = channel_title.lower()
                        channel_norm = normalize_text(channel_title)

                        # 1. 判定是否為 Topic 官方生成音源頻道
                        is_topic = (
                            "topic" in channel_lower
                            or "主題" in channel_lower
                        )

                        # 2. 噪音詞過濾（非 Topic 頻道才過濾）
                        has_noise = any(
                            nk in v_title_lower
                            for nk in COMBINED_NOISE_KEYWORDS
                        )
                        if not is_topic and has_noise:
                            continue

                        # 3. 歌名簡繁體比對
                        song_matched = (song_sim_norm in v_title_norm) or (
                            song_tra_norm in v_title_norm
                        )
                        if not song_matched:
                            continue

                        # 4. 歌手比對
                        singer_matched = any(
                            tkn in v_title_norm for tkn in artist_tokens
                        ) or any(
                            tkn in channel_norm for tkn in artist_tokens
                        )

                        cand = {
                            "id": v_id,
                            "views": v_views,
                            "url": f"https://www.youtube.com/watch?v={v_id}",
                        }

                        if is_topic:
                            candidates.append(cand)
                        elif singer_matched:
                            candidates.append(cand)

                    if candidates:
                        best = max(candidates, key=lambda x: x["views"])
                        matched_id = best["id"]
                        matched_views = best["views"]
                        matched_url = best["url"]

                success = True

            except HttpError as e:
                is_quota_error = e.resp.status in [403, 429] or any(
                    k in str(e)
                    for k in [
                        "quotaExceeded",
                        "rateLimitExceeded",
                        "Quota exceeded",
                    ]
                )
                if is_quota_error:
                    print(
                        f"⚠️ 第 {current_key_idx + 1} 組 API Key 額度用盡，自動切換至下一組 Key..."
                    )
                    current_key_idx += 1
                    youtube_service = get_yt_service(current_key_idx)
                    if not youtube_service:
                        print("❌ 所有 API Key 的每日額度皆已耗盡！")
                        break
                else:
                    print(f"⚠️ 搜尋 {song} 時發生 API 錯誤: {e}")
                    break
            except Exception as e:
                print(f"⚠️ 搜尋 {song} 時發生未知錯誤: {e}")
                break

        if matched_id:
            print(f"  ✅ 補抓成功 ➔ ID: {matched_id} | 點閱: {matched_views:,}")
            df_mapping.loc[idx, "Video ID"] = matched_id
            df_mapping.loc[idx, "影片連結"] = matched_url
            updated_count += 1
        else:
            print("  ❌ 未找到符合影片，保持 '-'")

        time.sleep(0.1)

    print(
        f"\n🎉 補抓完成！本次共成功補充 {updated_count} / {missing_count} 首歌曲的 Video ID！"
    )

    # ----------------------------------------------------
    # 步驟 4：更新儲存回 yt_mapping.csv
    # ----------------------------------------------------
    df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
    print(f"💾 對照表已成功更新儲存 ➔ {MAPPING_FILE}")


if __name__ == "__main__":
    run_init_and_retry()
