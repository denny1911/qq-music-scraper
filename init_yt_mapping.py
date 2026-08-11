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

# 完全刪除「YT 影片標題」，僅保留 4 個標準欄位
REQ_MAPPING_COLS = ["歌名", "歌手", "Video ID", "影片連結"]
REQ_BASELINE_COLS = ["歌名", "歌手", "Initial Views", "Initial Date"]

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


# ==========================================
# 2. 核心清洗與主動 YouTube 搜尋補抓邏輯
# ==========================================
def run_init_and_retry():
  # 時區與日期
  tz_taiwan = timezone(timedelta(hours=8))
  date_str = datetime.now(tz_taiwan).strftime("%Y-%m-%d")

  # 讀取 API Keys
  raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
  api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

  # ----------------------------------------------------
  # 步驟 1：讀取並整理現有的 yt_mapping.csv
  # ----------------------------------------------------
  if os.path.exists(MAPPING_FILE):
    df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")

    # 舊欄位名稱自動轉換相容
    rename_dict = {
        "video_id": "Video ID",
        "url": "影片連結",
        "yt_url": "影片連結",
    }
    df_mapping.rename(columns=rename_dict, inplace=True)

    # 主動刪除舊有的「YT 影片標題」欄位
    if "YT 影片標題" in df_mapping.columns:
      df_mapping.drop(columns=["YT 影片標題"], inplace=True)
    if "yt_title" in df_mapping.columns:
      df_mapping.drop(columns=["yt_title"], inplace=True)

    # 若歷史原因造成欄位名重複，只保留第一個
    df_mapping = df_mapping.loc[:, ~df_mapping.columns.duplicated()]
  else:
    df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)

  # 確保必要的 4 個標準欄位存在
  for col in REQ_MAPPING_COLS:
    if col not in df_mapping.columns:
      df_mapping[col] = "-"

  df_mapping = df_mapping[REQ_MAPPING_COLS].drop_duplicates(
      subset=["歌名", "歌手"], keep="first"
  )

  # ----------------------------------------------------
  # 步驟 2：讀取歷史榜單 CSV 收集所有曾出現過的歌曲
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

  # 左連接確保所有歷史歌曲都有進入對照表中
  if not df_all_songs.empty:
    df_mapping = pd.merge(
        df_all_songs, df_mapping, on=["歌名", "歌手"], how="left"
    ).fillna("-")

  # 格式清理
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
  # 步驟 4：針對 ID 為 '-' 的歌曲主動發起 YouTube API 搜尋補抓
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
    print("🚀 開始為所有 ID 為 '-' 的歌曲向 YouTube API 發起搜尋補抓...")

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
      print(f"🔍 [主動補抓] {song} - {singer} ...")

      matched_id = None
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
                  stkn_sim_norm = normalize_text(
                      zhconv.convert(stkn, "zh-hans")
                  )
                  stkn_tra_norm = normalize_text(
                      zhconv.convert(stkn, "zh-hant")
                  )
                  if (
                      (stkn_sim_norm in v_title_norm)
                      or (stkn_tra_norm in v_title_norm)
                      or (stkn_sim_norm in channel_norm)
                      or (stkn_tra_norm in channel_norm)
                  ):
                    singer_in_title = True
                    break

              cand = {
                  "id": v_id,
                  "views": v_views,
                  "url": f"https://www.youtube.com/watch?v={v_id}",
              }

              if is_topic or singer_in_title or singer_in_channel:
                candidates.append(cand)

            if candidates:
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

        # 更新或新增至 yt_baseline.csv
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
  # 步驟 5：寫回 CSV 檔案
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
