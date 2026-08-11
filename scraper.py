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
BASELINE_FILE = os.path.join(DATA_DIR, "yt_baseline.csv")

REQ_MAPPING_COLS = ["歌名", "歌手", "Video ID", "YT 影片標題", "影片連結"]
REQ_BASELINE_COLS = ["歌名", "歌手", "Initial Views", "Initial Date"]

# 非歌曲噪音黑名單關鍵字
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
  """清理歌名中的前綴（例如綜藝榜特有的 '歌曲：'）以提高 YouTube 搜尋與比對命中率"""
  if not title:
    return ""
  cleaned = re.sub(r"^歌曲[:：]\s*", "", str(title))
  return cleaned.strip()


def normalize_text(text):
  """清理字串中的所有空格與常見標點符號，方便模糊比對 (解決如 G.E.M. 與 GEM 的格式差異)"""
  if not text:
    return ""
  return re.sub(r"[\s\.\-\_\(\)（）]", "", str(text)).lower()


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
      release_date = song.get("time_public") or song.get(
          "album", {}
      ).get("time_public", "未知日期")

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

  # 準備讀取多組 API Keys
  raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
  api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
  if not api_keys:
    print(
        "❌ 警告：未找到 YOUTUBE_API_KEYS"
        " 環境變數，YouTube 搜尋與點閱抓取功能將無法運作！"
    )

  # 定義 4 個榜單
  charts = {
      "new": {"top_id": 27, "name": "新歌榜"},
      "film": {"top_id": 29, "name": "影視金曲榜"},
      "show": {"top_id": 64, "name": "綜藝新歌榜"},
      "tik": {"top_id": 60, "name": "抖音熱歌榜"},
  }

  fetched_charts = {}
  all_charts_df_list = []

  # 步驟 A：撈取今日 4 個榜單資料，暫存記憶體
  for tag, info in charts.items():
    df = fetch_qq_music_chart(info["top_id"], info["name"], date_str)
    if df is not None and not df.empty:
      fetched_charts[tag] = (info["name"], df)
      all_charts_df_list.append(df)

  if not all_charts_df_list:
    print("❌ 今日沒有成功抓取任何榜單資料，程式終止。")
    return

  # 合併今日所有榜單歌曲並去除重複
  df_today_all = pd.concat(all_charts_df_list, ignore_index=True)
  df_unique_songs = (
      df_today_all[["歌名", "歌手"]].drop_duplicates().reset_index(drop=True)
  )

  # 步驟 B：讀取中央對照表與基準表（若檔案或欄位缺乏則自動初始化補齊）
  if os.path.exists(MAPPING_FILE):
    try:
      df_mapping = pd.read_csv(MAPPING_FILE, dtype=str)
    except Exception:
      df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)
  else:
    df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)

  for col in REQ_MAPPING_COLS:
    if col not in df_mapping.columns:
      df_mapping[col] = "-"

  if os.path.exists(BASELINE_FILE):
    try:
      df_baseline = pd.read_csv(BASELINE_FILE, dtype=str)
    except Exception:
      df_baseline = pd.DataFrame(columns=REQ_BASELINE_COLS)
  else:
    df_baseline = pd.DataFrame(columns=REQ_BASELINE_COLS)

  for col in REQ_BASELINE_COLS:
    if col not in df_baseline.columns:
      df_baseline[col] = "-"

  # 步驟 C：檢查今日榜單歌曲，若缺少有效 ID 則發起 API 搜尋重試
  current_key_idx = 0

  def get_yt_service(idx):
    if idx < len(api_keys):
      return build("youtube", "v3", developerKey=api_keys[idx])
    return None

  youtube_service = get_yt_service(current_key_idx)
  mapping_updated = False
  baseline_updated = False

  if api_keys:
    print(
        "🔍 開始檢查今日榜單歌曲是否需要建立對照或補抓 YouTube Video ID..."
    )

    for idx, row in df_unique_songs.iterrows():
      song = str(row["歌名"]).strip()
      singer = str(row["歌手"]).strip()

      # 檢查對照表中是否已存在「有效 Video ID」（即非 '-'、非空白與 nan）
      matched_row = df_mapping[
          (df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)
      ]
      has_valid_id = False
      if not matched_row.empty:
        vid_val = str(matched_row.iloc[0]["Video ID"]).strip()
        if vid_val not in ["-", "", "nan", "None"]:
          has_valid_id = True

      # 若已有有效 ID，代表以前已搜到且正確，直接跳過搜尋
      if has_valid_id:
        continue

      # 清理歌名前綴（如 '歌曲：'）供搜尋與標題匹配使用
      clean_song = clean_song_title(song)

      print(
          f"🔄 [在榜歌曲補抓/搜尋]：{song} - {singer} (搜尋關鍵字:"
          f" {clean_song})..."
      )

      query_str = f"{clean_song} {singer}"
      matched_id = None
      matched_title = None
      matched_views = 0
      matched_url = None
      success = False

      # 比對時使用正規化與簡繁轉換字串
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
                  "title": v_title,
                  "views": v_views,
                  "url": f"https://www.youtube.com/watch?v={v_id}",
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
            print("⚠️ API Key 額度用盡，自動切換下一組...")
            current_key_idx += 1
            youtube_service = get_yt_service(current_key_idx)
          else:
            print(f"⚠️ 搜尋發生錯誤: {e}")
            break
        except Exception as e:
          print(f"⚠️ 發生未知錯誤: {e}")
          break

      # --------------------------------------------------
      # 更新對照表 (df_mapping) 與 基準表 (df_baseline)
      # --------------------------------------------------
      mask = (df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)

      if matched_id:
        print(f"  ✅ 成功匹配 ID: {matched_id} | 點閱: {matched_views:,}")

        # 若原本對照表有舊資料 (為 '-') 則更新，否則新增
        if mask.any():
          df_mapping.loc[mask, "Video ID"] = matched_id
          df_mapping.loc[mask, "YT 影片標題"] = matched_title
          df_mapping.loc[mask, "影片連結"] = matched_url
        else:
          new_m_row = pd.DataFrame([{
              "歌名": song,
              "歌手": singer,
              "Video ID": matched_id,
              "YT 影片標題": matched_title,
              "影片連結": matched_url,
          }])
          df_mapping = pd.concat([df_mapping, new_m_row], ignore_index=True)
        mapping_updated = True

        # 同步檢查/新增至基準表
        b_mask = (df_baseline["歌名"] == song) & (df_baseline["歌手"] == singer)
        if not b_mask.any():
          new_b_row = pd.DataFrame([{
              "歌名": song,
              "歌手": singer,
              "Initial Views": str(matched_views),
              "Initial Date": date_str,
          }])
          df_baseline = pd.concat([df_baseline, new_b_row], ignore_index=True)
          baseline_updated = True

      else:
        print(
            "  ❌ 未找到匹配影片。對照表保持"
            " '-'（若明日仍在榜上將繼續嘗試重試）。"
        )
        # 若原先對照表完全沒有此首歌，新增一筆 '-'
        if not mask.any():
          new_m_row = pd.DataFrame([{
              "歌名": song,
              "歌手": singer,
              "Video ID": "-",
              "YT 影片標題": "-",
              "影片連結": "-",
          }])
          df_mapping = pd.concat([df_mapping, new_m_row], ignore_index=True)
          mapping_updated = True

      time.sleep(0.1)

    # 儲存更新後的對照表與基準表檔案
    if mapping_updated:
      df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
      print(f"💾 對照表更新完成 ➔ {MAPPING_FILE}")

    if baseline_updated:
      df_baseline.to_csv(BASELINE_FILE, index=False, encoding="utf-8-sig")
      print(f"💾 基準表更新完成 ➔ {BASELINE_FILE}")

  # 步驟 D：批次查詢今日榜單所有有效 Video ID 當下的最新點閱率
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
  if unique_vids and api_keys:
    print(
        f"📊 正在批次向 YouTube 查詢 {len(unique_vids)}"
        " 首歌曲的最新點閱率..."
    )
    for i in range(0, len(unique_vids), 50):
      chunk = unique_vids[i : i + 50]
      fetched = False
      while current_key_idx < len(api_keys) and not fetched:
        if youtube_service is None:
          youtube_service = get_yt_service(current_key_idx)
          if not youtube_service:
            break
        try:
          v_res = (
              youtube_service.videos()
              .list(part="statistics", id=",".join(chunk))
              .execute()
          )
          for item in v_res.get("items", []):
            v_id = item["id"]
            v_views = int(item["statistics"].get("viewCount", 0))
            view_counts_dict[v_id] = v_views
          fetched = True
        except HttpError as e:
          if e.resp.status in [403, 429]:
            print("⚠️ API Key 額度用盡，自動切換下一組...")
            current_key_idx += 1
            youtube_service = get_yt_service(current_key_idx)
          else:
            print(f"⚠️ 批次抓取點閱率錯誤: {e}")
            break
        except Exception as e:
          print(f"⚠️ 批次抓取點閱率未知錯誤: {e}")
          break

  # 步驟 E：將 YouTube ID 與點閱率附加寫入每日榜單 CSV
  print("💾 正在附加 YouTube 資訊並儲存今日榜單 CSV 檔案...")
  for tag, (chart_name, df_chart) in fetched_charts.items():
    df_final = pd.merge(
        df_chart,
        df_mapping[["歌名", "歌手", "Video ID"]],
        on=["歌名", "歌手"],
        how="left",
    )

    df_final["YouTube ID"] = df_final["Video ID"].fillna("-")

    raw_views = df_final["Video ID"].map(view_counts_dict).fillna(0)
    df_final["點閱率"] = raw_views.apply(
        lambda x: f"{int(x):,}" if x > 0 else "-"
    )

    df_final = df_final.drop(columns=["Video ID"], errors="ignore")

    csv_filename = os.path.join(target_dir, f"{date_str}_{tag}.csv")
    df_final.to_csv(csv_filename, index=False, encoding="utf-8-sig")
    print(f"   ✓ [{chart_name}] 已成功儲存 ➔ {csv_filename}")

  print("✅ 每日排程、YouTube 對照與點閱率附加寫入全部完成！")


if __name__ == "__main__":
  main()
