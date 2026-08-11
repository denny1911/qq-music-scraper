import os
import re
import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import zhconv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==========================================
# 1. 基礎設定與輔助函式
# ==========================================
DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")
BASELINE_FILE = os.path.join(DATA_DIR, "yt_baseline.csv")

# 非歌曲噪音黑名單關鍵字
NOISE_KEYWORDS = ["花絮", "未播", "片段", "採訪", "預告", "解說", "幕後", "剪輯", "reaction", "cover"]

def parse_duration(duration_str):
    """將 YouTube ISO 8601 時間字串 (例如 PT3M45S) 轉為總秒數"""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


# ==========================================
# 2. QQ 音樂抓取函式
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
            "AppleWebKit/537.36 (KHTML, like Gecko) "
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


# ==========================================
# 3. 主程式邏輯
# ==========================================
def main():
    # 取得台灣時區日期
    tz_taiwan = timezone(timedelta(hours=8))
    date_str = datetime.now(tz_taiwan).strftime("%Y-%m-%d")
    target_dir = os.path.join(DATA_DIR, date_str)
    os.makedirs(target_dir, exist_ok=True)

    # 準備讀取多組 API Keys (從 GitHub Secrets 注入的環境變數)
    raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    if not api_keys:
        print("❌ 警告：未找到 YOUTUBE_API_KEYS 環境變數，YouTube 搜尋功能將無法運作！")

    # 定義 4 個榜單
    charts = {
        "new": {"top_id": 27, "name": "新歌榜"},
        "film": {"top_id": 29, "name": "影視金曲榜"},
        "show": {"top_id": 64, "name": "綜藝新歌榜"},
        "tik": {"top_id": 60, "name": "抖音熱歌榜"},
    }

    all_charts_df_list = []

    # 步驟 A：抓取今日所有榜單並儲存每日 CSV
    for tag, info in charts.items():
        df = fetch_qq_music_chart(info["top_id"], info["name"], date_str)
        if df is not None and not df.empty:
            csv_filename = os.path.join(target_dir, f"{date_str}_{tag}.csv")
            df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
            print(f" ✓ [{info['name']}] 儲存成功 ➔ {csv_filename}")
            all_charts_df_list.append(df)

    if not all_charts_df_list:
        print("❌ 今日沒有成功抓取任何榜單資料，程式終止。")
        return

    # 合併今日所有榜單歌曲並去除重複
    df_today_all = pd.concat(all_charts_df_list, ignore_index=True)
    df_unique_songs = df_today_all[["歌名", "歌手"]].drop_duplicates().reset_index(drop=True)

    # 步驟 B：讀取中央對照表與基準表
    if os.path.exists(MAPPING_FILE):
        df_mapping = pd.read_csv(MAPPING_FILE)
    else:
        df_mapping = pd.DataFrame(columns=["歌名", "歌手", "Video ID", "YT 影片標題", "影片連結"])

    if os.path.exists(BASELINE_FILE):
        df_baseline = pd.read_csv(BASELINE_FILE)
    else:
        df_baseline = pd.DataFrame(columns=["歌名", "歌手", "Initial Views", "Initial Date"])

    # 步驟 C：檢查哪些歌是新面孔，需要透過 YouTube API 補抓 ID
    if api_keys:
        current_key_idx = 0
        def get_yt_service(idx):
            if idx < len(api_keys):
                return build("youtube", "v3", developerKey=api_keys[idx])
            return None

        youtube_service = get_yt_service(current_key_idx)
        new_mappings = []
        new_baselines = []

        print("🔍 開始檢查今日榜單是否有新歌需要進行 YouTube 點閱測繪...")

        for idx, row in df_unique_songs.iterrows():
            song = str(row["歌名"]).strip()
            singer = str(row["歌手"]).strip()

            # 檢查是否已經存在於中央對照表中
            exists = not df_mapping[(df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)].empty
            if exists:
                continue  # 已經有了就跳過，省下 API 額度！

            print(發現新歌：{song} - {singer}，正在向 YouTube 檢索...)
            
            query_str = f"{song} {singer}"
            matched_id = None
            matched_title = None
            matched_views = 0
            matched_url = None
            success = False

            song_sim = zhconv.convert(song, "zh-hans").lower()
            song_tra = zhconv.convert(song, "zh-hant").lower()
            singer_sim = zhconv.convert(singer, "zh-hans").lower()
            singer_tra = zhconv.convert(singer, "zh-hant").lower()
            singer_tokens = [s.strip() for s in re.split(r'[/&,\+]', singer) if s.strip()]

            while current_key_idx < len(api_keys) and not success:
                if youtube_service is None:
                    youtube_service = get_yt_service(current_key_idx)
                    if not youtube_service:
                        break
                try:
                    search_res = youtube_service.search().list(
                        q=query_str, part="id", maxResults=10, type="video",
                        videoCategoryId="10", order="relevance", regionCode="TW"
                    ).execute()

                    v_ids = [item["id"]["videoId"] for item in search_res.get("items", []) if "videoId" in item.get("id", {})]

                    if v_ids:
                        video_res = youtube_service.videos().list(
                            part="snippet,statistics,contentDetails", id=",".join(v_ids)
                        ).execute()

                        candidates = []
                        for item in video_res.get("items", []):
                            v_id = item["id"]
                            v_title = item["snippet"]["title"]
                            channel_title = item["snippet"].get("channelTitle", "")
                            v_views = int(item["statistics"].get("viewCount", 0))
                            duration_sec = parse_duration(item.get("contentDetails", {}).get("duration", "PT0S"))

                            if duration_sec <= 60 or duration_sec > 600:
                                continue

                            v_title_lower = v_title.lower()
                            channel_lower = channel_title.lower()
                            is_topic = "topic" in channel_lower or "主題" in channel_lower
                            has_noise = any(nk in v_title_lower for nk in NOISE_KEYWORDS)

                            if not is_topic and has_noise:
                                continue

                            if not ((song_sim in v_title_lower) or (song_tra in v_title_lower)):
                                continue

                            singer_in_title = (singer_sim in v_title_lower) or (singer_tra in v_title_lower)
                            singer_in_channel = (singer_sim in channel_lower) or (singer_tra in channel_lower)

                            if not (singer_in_title or singer_in_channel) and singer_tokens:
                                for stkn in singer_tokens:
                                    stkn_sim = zhconv.convert(stkn, "zh-hans").lower()
                                    stkn_tra = zhconv.convert(stkn, "zh-hant").lower()
                                    if (stkn_sim in v_title_lower) or (stkn_tra in v_title_lower) or (stkn_sim in channel_lower) or (stkn_tra in channel_lower):
                                        singer_in_title = True
                                        break

                            cand = {
                                "id": v_id, "title": v_title, "views": v_views,
                                "url": f"https://www.youtube.com/watch?v={v_id}"
                            }

                            if is_topic or singer_in_title or singer_in_channel:
                                candidates.append(cand)

                        if candidates:
                            best = max(candidates, key=lambda x: x["views"])
                            matched_id = best["id"]
                            matched_title = best["title"]
                            matched_views = best["views"]
                            matched_url = best["url"]

                    success = True
                except HttpError as e:
                    if e.resp.status in [403, 429]:
                        print(f"⚠️ API Key 額度用盡，自動切換下一組...")
                        current_key_idx += 1
                        youtube_service = get_yt_service(current_key_idx)
                    else:
                        print(f"⚠️ 搜尋發生錯誤: {e}")
                        break
                except Exception as e:
                    print(f"⚠️ 發生未知錯誤: {e}")
                    break

            # 記錄新找到的對照與基準
            if matched_id:
                new_mappings.append({
                    "歌名": song, "歌手": singer,
                    "Video ID": matched_id, "YT 影片標題": matched_title, "影片連結": matched_url
                })
                new_baselines.append({
                    "歌名": song, "歌手": singer,
                    "Initial Views": matched_views, "Initial Date": date_str
                })
            
            time.sleep(0.1)

        # 步驟 D：更新並寫回中央對照表與基準表
        if new_mappings:
            df_new_m = pd.DataFrame(new_mappings)
            df_mapping = pd.concat([df_mapping, df_new_m], ignore_index=True)
            df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
            print(f"✨ 成功新增 {len(new_mappings)} 筆新歌至 yt_mapping.csv")

        if new_baselines:
            df_new_b = pd.DataFrame(new_baselines)
            df_baseline = pd.concat([df_baseline, df_new_b], ignore_index=True)
            df_baseline.to_csv(BASELINE_FILE, index=False, encoding="utf-8-sig")
            print(f"✨ 成功新增 {len(new_baselines)} 筆初始數據至 yt_baseline.csv")

    print("✅ 每日排程與 YouTube 對照更新全部完成！")

if __name__ == "__main__":
    main()
