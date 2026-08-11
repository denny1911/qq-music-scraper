from datetime import datetime, timedelta, timezone
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
BASELINE_FILE = os.path.join(DATA_DIR, "yt_baseline.csv")

# 徹底刪除「YT 影片標題」，僅保留 4 個標準欄位
REQ_MAPPING_COLS = ["歌名", "歌手", "Video ID", "影片連結"]
REQ_BASELINE_COLS = ["歌名", "歌手", "Initial Views", "Initial Date"]

# 硬拒絕噪音關鍵字（明確非音樂本體的影片）
HARD_NOISE_KEYWORDS = [
    "解說",
    "reaction",
    "反應",
    "教學",
    "翻唱教學",
    "吉他教學",
    "鋼琴教學",
    "樂譜",
    "開箱",
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
    """進階歌手拆解：支援符號拆分與中英文邊界拆分 (例如 'h3R3刘清云' -> ['h3r3', '刘清云', '劉清雲'])"""
    if not singer or singer in ["-", "nan", "None"]:
        return []

    singer_str = str(singer).strip()
    all_tokens = set()

    # 1. 用常見分隔符切割 (/, &, comma, +, x, feat, ft, etc.)
    raw_tokens = re.split(
        r"[/&,\+\·\s\*\-\|]|feat\.?|ft\.?|X|x", singer_str, flags=re.IGNORECASE
    )

    for raw in raw_tokens:
        raw = raw.strip()
        if not raw:
            continue

        # 加入原片段繁簡體
        all_tokens.add(zhconv.convert(raw, "zh-hans"))
        all_tokens.add(zhconv.convert(raw, "zh-hant"))

        # 2. 按 中文 / 英文數字 邊界進一步拆分 (解決 h3R3刘清云、告五人Accusefive 這類組合)
        sub_chunks = re.findall(r"[a-zA-Z0-9\.\-\']+|[\u4e00-\u9fa5]+", raw)
        if len(sub_chunks) > 1:
            for chunk in sub_chunks:
                chunk = chunk.strip()
                if len(chunk) >= 2 or re.search(r"[\u4e00-\u9fa5]", chunk):
                    all_tokens.add(zhconv.convert(chunk, "zh-hans"))
                    all_tokens.add(zhconv.convert(chunk, "zh-hant"))

    # 清理正規化
    normalized_tokens = []
    for t in all_tokens:
        norm = normalize_text(t)
        if norm and len(norm) >= 2:  # 避免過短的字母誤判
            normalized_tokens.append(norm)

    return list(set(normalized_tokens))


# ==========================================
# 2. 核心清洗與主動 YouTube 搜尋補抓邏輯
# ==========================================
def run_init_and_retry():
    tz_taiwan = timezone(timedelta(hours=8))
    date_str = datetime.now(tz_taiwan).strftime("%Y-%m-%d")

    raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    # ----------------------------------------------------
    # 步驟 1：讀取並整理現有的 yt_mapping.csv
    # ----------------------------------------------------
    if os.path.exists(MAPPING_FILE):
        df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")

        # 舊欄位名稱相容
        rename_dict = {
            "video_id": "Video ID",
            "url": "影片連結",
            "yt_url": "影片連結",
        }
        df_mapping.rename(columns=rename_dict, inplace=True)

        # 主動徹底刪除「YT 影片標題」相關欄位
        for col_to_drop in ["YT 影片標題", "yt_title", "title", "Video Title"]:
            if col_to_drop in df_mapping.columns:
                df_mapping.drop(columns=[col_to_drop], inplace=True)

        df_mapping = df_mapping.loc[:, ~df_mapping.columns.duplicated()]
    else:
        df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)

    for col in REQ_MAPPING_COLS:
        if col not in df_mapping.columns:
            df_mapping[col] = "-"

    df_mapping = df_mapping[REQ_MAPPING_COLS].drop_duplicates(
        subset=["歌名", "歌手"], keep="first"
    )

    # ----------------------------------------------------
    # 步驟 2：讀取歷史榜單 CSV 收集所有歌曲
    # ----------------------------------------------------
    all_chart_files = glob.glob(
        os.path.join(DATA_DIR, "**", "*.csv"), recursive=True
    )
    song_list = []

    for f in all_chart_files:
        if "yt_mapping" in f or "yt_baseline" in f:
            continue
        try:
            df_chart = pd.read_csv(f, dtype=str)
            if "歌名" in df_chart.columns and "歌手" in df_chart.columns:
                song_list.append(df_chart[["歌名", "歌手"]])
        except Exception as e:
            print(f"⚠️ 讀取 {f} 失敗: {e}")

    if song_list:
        df_all_songs = pd.concat(song_list, ignore_index=True).drop_duplicates(
            subset=["歌名", "歌手"]
        )
    else:
        df_all_songs = pd.DataFrame(columns=["歌名", "歌手"])

    if not df_all_songs.empty:
        df_mapping = pd.merge(
            df_all_songs, df_mapping, on=["歌名", "歌手"], how="left"
        ).fillna("-")

    df_mapping["Video ID"] = (
        df_mapping["Video ID"].replace(["nan", "None", ""], "-").fillna("-")
    )
    df_mapping["影片連結"] = (
        df_mapping["影片連結"].replace(["nan", "None", ""], "-").fillna("-")
    )
    df_mapping = df_mapping[REQ_MAPPING_COLS].drop_duplicates(
        subset=["歌名", "歌手"], keep="first"
    )

    # ----------------------------------------------------
    # 步驟 3：讀取或初始化 yt_baseline.csv
    # ----------------------------------------------------
    if os.path.exists(BASELINE_FILE):
        try:
            df_baseline = pd.read_csv(BASELINE_FILE, dtype=str).fillna("-")
        except Exception:
            df_baseline = pd.DataFrame(columns=REQ_BASELINE_COLS)
    else:
        df_baseline = pd.DataFrame(columns=REQ_BASELINE_COLS)

    for col in REQ_BASELINE_COLS:
        if col not in df_baseline.columns:
            df_baseline[col] = "-"

    # ----------------------------------------------------
    # 步驟 4：針對 ID 為 '-' 的歌曲發起 YouTube API 搜尋
    # ----------------------------------------------------
    missing_mask = df_mapping["Video ID"] == "-"
    missing_songs = df_mapping[missing_mask]

    print(
        f"📊 對照表共有 {len(df_mapping)} 首歌曲，其中 {len(missing_songs)}"
        " 首無有效 ID (為 '-')。"
    )

    if missing_songs.empty:
        print("✅ 所有歌曲均已擁有有效 Video ID，無需補抓。")
    elif not api_keys:
        print("⚠️ 檢測到無 YOUTUBE_API_KEYS 環境變數，無法發起搜尋補抓！")
    else:
        print("🚀 開始為所有 ID 為 '-' 的歌曲向 YouTube API 發起熱門搜尋補抓...")

        current_key_idx = 0

        def get_yt_service(idx):
            if idx < len(api_keys):
                return build("youtube", "v3", developerKey=api_keys[idx])
            return None

        youtube_service = get_yt_service(current_key_idx)
        updated_count = 0

        for idx, row in missing_songs.iterrows():
            song = str(row["歌名"]).strip()
            singer = str(row["歌手"]).strip()
            clean_song = clean_song_title(song)

            query_str = f"{clean_song} {singer}"
            print(f"🔍 [熱門補抓] {song} - {singer} ...")

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
                    # 💡 重大優化 1：order="viewCount" 讓 API 直接按點閱率熱門排序
                    # 💡 重大優化 2：maxResults=20 擴大搜尋深度（同為 100 配額 units）
                    search_res = (
                        youtube_service.search()
                        .list(
                            q=query_str,
                            part="id",
                            maxResults=20,
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
                            duration_sec = parse_duration(
                                item.get("contentDetails", {}).get("duration", "PT0S")
                            )

                            # 放寬影片時間限制 (30 秒 ~ 20 分鐘)
                            if duration_sec < 30 or duration_sec > 1200:
                                continue

                            v_title_lower = v_title.lower()
                            v_title_norm = normalize_text(v_title)
                            channel_norm = normalize_text(channel_title)

                            # 1. 排除硬拒絕噪音詞 (例如解說、reaction)
                            if any(nk in v_title_lower for nk in HARD_NOISE_KEYWORDS):
                                continue

                            # 2. 歌名比對
                            song_matched = (song_sim_norm in v_title_norm) or (
                                song_tra_norm in v_title_norm
                            )
                            if not song_matched:
                                continue

                            # 3. 歌手比對（Topic 頻道標題為 "歌手名 - Topic"，channel_norm 會包含歌手 Token）
                            singer_matched = (
                                any(tkn in v_title_norm for tkn in artist_tokens)
                                or any(tkn in channel_norm for tkn in artist_tokens)
                            )

                            if singer_matched:
                                candidates.append({
                                    "id": v_id,
                                    "views": v_views,
                                    "url": f"https://www.youtube.com/watch?v={v_id}",
                                })

                        if candidates:
                            # 挑選符合條件中點閱最高的影片
                            best = max(candidates, key=lambda x: x["views"])
                            matched_id = best["id"]
                            matched_views = best["views"]
                            matched_url = best["url"]

                    success = True
                except HttpError as e:
                    if e.resp.status in [403, 429]:
                        print("⚠️ API Key 額度用盡，自動切換下一組...")
                        current_key_idx += 1
                        youtube_service = get_yt_service(current_key_idx)
                    else:
                        print(f"⚠️ 搜尋發生錯誤: {e}")
                        break
                except Exception as e:
                    print(f"⚠️ 發生未知錯誤: {e}")
                    break

            # 覆蓋對照表資料並更新基準表
            if matched_id:
                print(f"  ✅ 成功補抓 ID: {matched_id} | 點閱: {matched_views:,}")
                mask = (df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)
                df_mapping.loc[mask, "Video ID"] = matched_id
                df_mapping.loc[mask, "影片連結"] = matched_url
                updated_count += 1

                # 同步寫入/更新 yt_baseline.csv
                b_mask = (df_baseline["歌名"] == song) & (df_baseline["歌手"] == singer)
                if b_mask.any():
                    if df_baseline.loc[b_mask, "Initial Views"].values[0] in [
                        "-",
                        "",
                        "nan",
                    ]:
                        df_baseline.loc[b_mask, "Initial Views"] = str(matched_views)
                        df_baseline.loc[b_mask, "Initial Date"] = date_str
                else:
                    new_b_row = pd.DataFrame([{
                        "歌名": song,
                        "歌手": singer,
                        "Initial Views": str(matched_views),
                        "Initial Date": date_str,
                    }])
                    df_baseline = pd.concat([df_baseline, new_b_row], ignore_index=True)

            else:
                print("  ❌ 未找到對應影片，維持 '-'")

            time.sleep(0.1)

        print(
            f"🎉 補抓完成！本次成功將 {updated_count} 首歌曲更新為有效"
            " Video ID 與連結！"
        )

    # ----------------------------------------------------
    # 步驟 5：寫回 CSV 檔案 (格式精簡無多餘欄位)
    # ----------------------------------------------------
    df_mapping = df_mapping[REQ_MAPPING_COLS].drop_duplicates(
        subset=["歌名", "歌手"], keep="first"
    )
    df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
    print(f"💾 對照表已成功寫入 ➔ {MAPPING_FILE} (僅 4 個標準欄位)")

    df_baseline.to_csv(BASELINE_FILE, index=False, encoding="utf-8-sig")
    print(f"💾 基準表已同步更新 ➔ {BASELINE_FILE}")


if __name__ == "__main__":
    run_init_and_retry()
