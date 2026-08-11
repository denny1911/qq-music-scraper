import glob
import os
import re
import time
import pandas as pd
import zhconv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==========================================
# 1. 基礎設定與輔助函式
# ==========================================
DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")
BASELINE_FILE = os.path.join(DATA_DIR, "yt_baseline.csv")

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
  """將 YouTube ISO 8601 時間字串轉為總秒數"""
  match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
  if not match:
    return 0
  return (
      int(match.group(1) or 0) * 3600
      + int(match.group(2) or 0) * 60
      + int(match.group(3) or 0)
  )


def clean_song_title(title):
  """清理歌名中的前綴"""
  if not title:
    return ""
  cleaned = re.sub(r"^歌曲[:：]\s*", "", str(title))
  return cleaned.strip()


def normalize_text(text):
  """清理字串中的所有空格與常見標點符號，方便模糊比對 (如 G.E.M. 與 GEM)"""
  if not text:
    return ""
  return re.sub(r"[\s\.\-\_\(\)（）]", "", str(text)).lower()


# ==========================================
# 2. 主初始化與修復邏輯
# ==========================================
def init_yt_mapping():
  raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
  api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
  if not api_keys:
    print("❌ 錯誤：未找到 YOUTUBE_API_KEYS 環境變數，程式無法執行！")
    return

  # 步驟 1：讀取現有對照表與基準表（若無則建立）
  if os.path.exists(MAPPING_FILE):
    df_mapping = pd.read_csv(MAPPING_FILE)
  else:
    df_mapping = pd.DataFrame(
        columns=["歌名", "歌手", "Video ID", "YT 影片標題", "影片連結"]
    )

  if os.path.exists(BASELINE_FILE):
    df_baseline = pd.read_csv(BASELINE_FILE)
  else:
    df_baseline = pd.DataFrame(
        columns=["歌名", "歌手", "Initial Views", "Initial Date"]
    )

  # 步驟 2：掃描 data/ 下所有歷史每日榜單 CSV，蒐集歷史所有不重複歌曲與其最早出現日期
  all_csv_files = glob.glob(os.path.join(DATA_DIR, "*", "*.csv"))
  if not all_csv_files:
    print("⚠️ 找不到任何歷史榜單 CSV 檔案！")
    return

  print(f"📁 發現 {len(all_csv_files)} 個歷史榜單檔案，正在彙整歷史歌曲...")

  historical_songs = {}  # (song, singer) -> earliest_date
  for filepath in all_csv_files:
    # 排除 mappings 與 baseline 檔案本身
    if "yt_mapping.csv" in filepath or "yt_baseline.csv" in filepath:
      continue
    try:
      df_hist = pd.read_csv(filepath)
      if "歌名" in df_hist.columns and "歌手" in df_hist.columns:
        file_date = os.path.basename(os.path.dirname(filepath))
        for _, row in df_hist.iterrows():
          s_name = str(row["歌名"]).strip()
          s_singer = str(row["歌手"]).strip()
          key = (s_name, s_singer)

          if key not in historical_songs or file_date < historical_songs[key]:
            historical_songs[key] = file_date
    except Exception as e:
      print(f"⚠️ 讀取檔案 {filepath} 失敗: {e}")

  print(f"📊 歷史榜單中共有 {len(historical_songs)} 首不重複歌曲。")

  # 步驟 3：建立對照字典，判斷哪些歌曲需要補抓或重新查詢（沒有在對照表中，或 Video ID 為無效字串）
  existing_mappings = {}
  for _, row in df_mapping.iterrows():
    s_name = str(row["歌名"]).strip()
    s_singer = str(row["歌手"]).strip()
    v_id = str(row["Video ID"]).strip()
    existing_mappings[(s_name, s_singer)] = v_id

  unmapped_songs = []
  for (song, singer), earliest_date in historical_songs.items():
    current_vid = existing_mappings.get((song, singer), None)
    # 若不在對照表中，或 Video ID 為空/NaN/'-'，則列入需要查詢的目標
    if (
        current_vid is None
        or current_vid == ""
        or current_vid == "-"
        or current_vid == "nan"
    ):
      unmapped_songs.append((song, singer, earliest_date))

  print(f"🔍 共有 {len(unmapped_songs)} 首歌曲需要對照或重新補抓 Video ID...")

  if not unmapped_songs:
    print("🎉 所有歷史歌曲均已有有效的 Video ID，無需補抓！")
    return

  # 步驟 4：開始向 YouTube API 查詢
  current_key_idx = 0

  def get_yt_service(idx):
    if idx < len(api_keys):
      return build("youtube", "v3", developerKey=api_keys[idx])
    return None

  youtube_service = get_yt_service(current_key_idx)

  mapping_dict_updates = (
      {}
  )  # (song, singer) -> {'id': ..., 'title': ..., 'url': ...}
  baseline_updates = (
      []
  )  # list of {'歌名': ..., '歌手': ..., 'Initial Views': ..., 'Initial Date': ...}

  for idx, (song, singer, earliest_date) in enumerate(unmapped_songs, start=1):
    clean_song = clean_song_title(song)
    query_str = f"{clean_song} {singer}"

    print(f"[{idx}/{len(unmapped_songs)}] 🛠️ 檢索歌曲: {song} - {singer}...")

    matched_id = None
    matched_title = None
    matched_views = 0
    matched_url = None
    success = False

    song_sim_norm = normalize_text(zhconv.convert(clean_song, "zh-hans"))
    song_tra_norm = normalize_text(zhconv.convert(clean_song, "zh-hant"))
    singer_sim_norm = normalize_text(zhconv.convert(singer, "zh-hans"))
    singer_tra_norm = normalize_text(zhconv.convert(singer, "zh-hant"))
    singer_tokens = [
        s.strip() for s in re.split(r"[/&,\+]", singer) if s.strip()
    ]

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
              youtube_service.videos()
              .list(part="snippet,statistics,contentDetails", id=",".join(v_ids))
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

            if duration_sec <= 60 or duration_sec > 600:
              continue

            v_title_lower = v_title.lower()
            channel_lower = channel_title.lower()
            v_title_norm = normalize_text(v_title)
            channel_norm = normalize_text(channel_title)

            is_topic = "topic" in channel_lower or "主題" in channel_lower
            has_noise = any(nk in v_title_lower for nk in NOISE_KEYWORDS)

            if not is_topic and has_noise:
              continue

            if not (
                (song_sim_norm in v_title_norm)
                or (song_tra_norm in v_title_norm)
            ):
              continue

            singer_in_title = (singer_sim_norm in v_title_norm) or (
                singer_tra_norm in v_title_norm
            )
            singer_in_channel = (singer_sim_norm in channel_norm) or (
                singer_tra_norm in channel_norm
            )

            if not (singer_in_title or singer_in_channel) and singer_tokens:
              for stkn in singer_tokens:
                stkn_sim_norm = normalize_text(zhconv.convert(stkn, "zh-hans"))
                stkn_tra_norm = normalize_text(zhconv.convert(stkn, "zh-hant"))
                if (
                    (stkn_sim_norm in v_title_norm)
                    or (stkn_tra_norm in v_title_norm)
                    or (stkn_sim_norm in channel_norm)
                    or (stkn_tra_norm in channel_norm)
                ):
                  singer_in_title = True
                  break

            if is_topic or singer_in_title or singer_in_channel:
              candidates.append({
                  "id": v_id,
                  "title": v_title,
                  "views": v_views,
                  "url": f"https://www.youtube.com/watch?v={v_id}",
              })

          if candidates:
            best = max(candidates, key=lambda x: x["views"])
            matched_id = best["id"]
            matched_title = best["title"]
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

    # 💡 無論是否有搜尋到，都寫入 mapping 紀錄 (沒找到則填入 '-')
    mapping_dict_updates[(song, singer)] = {
        "Video ID": matched_id if matched_id else "-",
        "YT 影片標題": matched_title if matched_title else "-",
        "影片連結": matched_url if matched_url else "-",
    }

    if matched_id:
      print(f"  ✅ 成功匹配 ID: {matched_id} | 點閱: {matched_views:,}")
      # 檢查是否已在 baseline 中，若無才建立
      already_in_baseline = not df_baseline[
          (df_baseline["歌名"] == song) & (df_baseline["歌手"] == singer)
      ].empty
      if not already_in_baseline:
        baseline_updates.append({
            "歌名": song,
            "歌手": singer,
            "Initial Views": matched_views,
            "Initial Date": earliest_date,
        })
    else:
      print("  ❌ 未能找到匹配影片，將標註 '-' 寫入對照表以防重複檢索。")

    time.sleep(0.1)

  # 步驟 5：更新並寫回 yt_mapping.csv 與 yt_baseline.csv
  for (song, singer), info in mapping_dict_updates.items():
    mask = (df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)
    if not df_mapping[mask].empty:
      # 更新已有紀錄 (原本 Video ID 為空的行)
      df_mapping.loc[mask, "Video ID"] = info["Video ID"]
      df_mapping.loc[mask, "YT 影片標題"] = info["YT 影片標題"]
      df_mapping.loc[mask, "影片連結"] = info["影片連結"]
    else:
      # 新增不存在的紀錄
      new_row = pd.DataFrame([{
          "歌名": song,
          "歌手": singer,
          "Video ID": info["Video ID"],
          "YT 影片標題": info["YT 影片標題"],
          "影片連結": info["影片連結"],
      }])
      df_mapping = pd.concat([df_mapping, new_row], ignore_index=True)

  df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
  print(f"\n✨ {MAPPING_FILE} 更新完成！")

  if baseline_updates:
    df_new_b = pd.DataFrame(baseline_updates)
    df_baseline = pd.concat([df_baseline, df_new_b], ignore_index=True)
    df_baseline.to_csv(BASELINE_FILE, index=False, encoding="utf-8-sig")
    print(f"✨ 成功新增 {len(baseline_updates)} 筆初始數據至 {BASELINE_FILE}")

  print("✅ yt_mapping 與 yt_baseline 全面初始化修復完畢！")


if __name__ == "__main__":
  init_yt_mapping()
