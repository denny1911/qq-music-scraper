import os
import re
import time
from datetime import datetime
import pandas as pd
import zhconv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==========================================
# 1. 設定與 API Key 清單
# ==========================================
DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")
BASELINE_FILE = os.path.join(DATA_DIR, "yt_baseline.csv")

# 填入你的 17 組 API Keys（或從環境變數 / Secrets 讀取）
API_KEYS = os.getenv("YOUTUBE_API_KEYS", "").split(",")
API_KEYS = [k.strip() for k in API_KEYS if k.strip()]

NOISE_KEYWORDS = [
    "花絮",
    "未播",
    "片段",
    "採訪",
    "預告",
    "解說",
    "幕後",
    "剪輯",
    "reaction",
    "cover",
]


def parse_duration(duration_str):
    """解析 ISO 8601 時間字串 (如 PT3M45S) 為總秒數"""
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or ""
    )
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


# ==========================================
# 2. 掃描歷史檔案並擷取全域唯一歌曲
# ==========================================
def extract_unique_songs():
    songs = set()
    if not os.path.exists(DATA_DIR):
        print(f"❌ 找不到 `{DATA_DIR}` 目錄！")
        return list(songs)

    for root, _, files in os.walk(DATA_DIR):
        for f in files:
            if f.endswith(".csv") and f not in [
                "yt_mapping.csv",
                "yt_baseline.csv",
            ]:
                fpath = os.path.join(root, f)
                try:
                    df = pd.read_csv(fpath)
                    song_col = "歌名" if "歌名" in df.columns else "song"
                    singer_col = "歌手" if "歌手" in df.columns else "singer"

                    if song_col in df.columns and singer_col in df.columns:
                        for _, row in df[[song_col, singer_col]].iterrows():
                            s_name = str(row[song_col]).strip()
                            s_sing = str(row[singer_col]).strip()
                            if s_name and s_sing and s_name != "nan":
                                songs.add((s_name, s_sing))
                except Exception as e:
                    print(f"⚠️ 讀取 {fpath} 失敗: {e}")

    print(f"✅ 歷史數據掃描完成，共計 {len(songs)} 首唯一歌曲。")
    return list(songs)


# ==========================================
# 3. 主執行流程 (檢索與 API Key 輪詢)
# ==========================================
def main():
    if not API_KEYS or API_KEYS == [""]:
        print("❌ 請設定環境變數 YOUTUBE_API_KEYS (以逗號分隔)！")
        return

    unique_songs = extract_unique_songs()
    if not unique_songs:
        print("沒有找到需要處理的歌曲。")
        return

    # 載入現有 mapping (若存在)
    existing_mapping = {}
    if os.path.exists(MAPPING_FILE):
        df_exist = pd.read_csv(MAPPING_FILE)
        for _, r in df_exist.iterrows():
            existing_mapping[(str(r["歌名"]), str(r["歌手"]))] = str(
                r["video_id"]
            )

    curr_key_idx = 0

    def get_yt_service(idx):
        if idx < len(API_KEYS):
            return build("youtube", "v3", developerKey=API_KEYS[idx])
        return None

    yt_service = get_yt_service(curr_key_idx)

    mapping_rows = []
    baseline_rows = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    total = len(unique_songs)
    for idx, (song, singer) in enumerate(unique_songs, start=1):
        # 若已存在 Mapping 則跳過搜尋
        if (song, singer) in existing_mapping:
            v_id = existing_mapping[(song, singer)]
            mapping_rows.append(
                {"歌名": song, "歌手": singer, "video_id": v_id}
            )
            continue

        print(f"🔍 [{idx}/{total}] 檢索中：{song} - {singer}...")

        matched_id, matched_title, matched_views = None, None, 0
        query_str = f"{song} {singer}"

        song_sim = zhconv.convert(song, "zh-hans").lower()
        song_tra = zhconv.convert(song, "zh-hant").lower()
        singer_sim = zhconv.convert(singer, "zh-hans").lower()
        singer_tra = zhconv.convert(singer, "zh-hant").lower()
        singer_tokens = [
            s.strip() for s in re.split(r"[/&,\+]", singer) if s.strip()
        ]

        success = False
        while curr_key_idx < len(API_KEYS) and not success:
            if not yt_service:
                yt_service = get_yt_service(curr_key_idx)
                if not yt_service:
                    break

            try:
                search_res = (
                    yt_service.search()
                    .list(
                        q=query_str,
                        part="id",
                        maxResults=10,
                        type="video",
                        videoCategoryId="10",
                        order="relevance",
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
                        yt_service.videos()
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

                        if duration_sec <= 60 or duration_sec > 600:
                            continue

                        v_title_lower = v_title.lower()
                        channel_lower = channel_title.lower()

                        is_topic = (
                            "topic" in channel_lower or "主題" in channel_lower
                        )
                        has_noise = any(
                            nk in v_title_lower for nk in NOISE_KEYWORDS
                        )

                        if not is_topic and has_noise:
                            continue

                        song_in_title = (song_sim in v_title_lower) or (
                            song_tra in v_title_lower
                        )
                        if not song_in_title:
                            continue

                        singer_in_title = (singer_sim in v_title_lower) or (
                            singer_tra in v_title_lower
                        )
                        singer_in_channel = (singer_sim in channel_lower) or (
                            singer_tra in channel_lower
                        )

                        if (
                            not (singer_in_title or singer_in_channel)
                            and singer_tokens
                        ):
                            for stkn in singer_tokens:
                                stkn_sim = zhconv.convert(
                                    stkn, "zh-hans"
                                ).lower()
                                stkn_tra = zhconv.convert(
                                    stkn, "zh-hant"
                                ).lower()
                                if (
                                    (stkn_sim in v_title_lower)
                                    or (stkn_tra in v_title_lower)
                                    or (stkn_sim in channel_lower)
                                    or (stkn_tra in channel_lower)
                                ):
                                    singer_in_title = True
                                    break

                        cand = {
                            "id": v_id,
                            "title": v_title,
                            "views": v_views,
                        }
                        if is_topic or singer_in_title or singer_in_channel:
                            candidates.append(cand)

                    if candidates:
                        best = max(candidates, key=lambda x: x["views"])
                        matched_id = best["id"]
                        matched_title = best["title"]
                        matched_views = best["views"]

                success = True

            except HttpError as e:
                if e.resp.status in [403, 429]:
                    print(
                        f"⚠️ Key #{curr_key_idx+1} 額度耗盡，自動切換至下一組 Key..."
                    )
                    curr_key_idx += 1
                    yt_service = get_yt_service(curr_key_idx)
                else:
                    print(f"⚠️ API 錯誤: {e}")
                    break
            except Exception as e:
                print(f"⚠️ 未知錯誤: {e}")
                break

        mapping_rows.append(
            {"歌名": song, "歌手": singer, "video_id": matched_id or ""}
        )

        if matched_id:
            baseline_rows.append(
                {
                    "video_id": matched_id,
                    "yt_title": matched_title,
                    "baseline_views": matched_views,
                    "baseline_date": today_str,
                }
            )

        time.sleep(0.1)

    # 保存結果
    df_map = pd.DataFrame(mapping_rows)
    df_map.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
    print(f"💾 已更新中央對照表：{MAPPING_FILE}")

    if baseline_rows:
        df_base = pd.DataFrame(baseline_rows).drop_duplicates(
            subset=["video_id"]
        )
        df_base.to_csv(BASELINE_FILE, index=False, encoding="utf-8-sig")
        print(f"💾 已儲存 Day 0 點閱基準表：{BASELINE_FILE}")


if __name__ == "__main__":
    main()
