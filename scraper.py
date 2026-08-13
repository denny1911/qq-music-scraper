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

# 對照表僅保留 3 個標準欄位
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
  """清理字串中的所有空格與常見標點符號供模糊比對"""
  if not text:
    return ""
  return re.sub(r"[\s\.\-\_\(\)（）]", "", str(text)).lower()


def extract_artist_tokens(singer):
  """拆解多歌手與簡繁體 Token，並支援括號 ()（）與韓文字元拆解"""
  if not singer or str(singer).lower() in ["-", "nan", "none"]:
    return []

  singer_str = str(singer).strip()
  all_tokens = set()

  raw_tokens = re.split(
      r"[/&,\+\·\s\*\-\|\(\)（）]|feat\.?|ft\.?|X|x",
      singer_str,
      flags=re.IGNORECASE,
  )

  for raw in raw_tokens:
    raw = raw.strip()
    if not raw:
      continue
    all_tokens.add(zhconv.convert(raw, "zh-hans"))
    all_tokens.add(zhconv.convert(raw, "zh-hant"))

    sub_chunks = re.findall(
        r"[a-zA-Z0-9\.\-\']+|[\u4e00-\u9fa5]+|[\uAC00-\uD7A3]+", raw
    )
    if len(sub_chunks) > 1:
      for chunk in sub_chunks:
        chunk = chunk.strip()
        if len(chunk) >= 1:
          all_tokens.add(zhconv.convert(chunk, "zh-hans"))
          all_tokens.add(zhconv.convert(chunk, "zh-hant"))

  normalized_tokens = []
  for t in all_tokens:
    norm = normalize_text(t)
    if norm and len(norm) >= 1:
      normalized_tokens.append(norm)

  return list(set(normalized_tokens))


def build_search_queries(song, singer):
  """產生搜尋字串：優先使用去除括號的「主歌名 + 歌手」，大幅提升 YouTube API 搜尋命中率"""
  clean_s, main_s = parse_song_title(song)
  clean_p = str(singer).strip()

  primary_query = f"{main_s} {clean_p}".strip()
  queries = [primary_query]

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
      release_date = song.get("time_public") or song.get("album", {}).get(
          "time_public", "未知日期"
      )

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
  tz_taiwan = timezone(timedelta(hours=8))
  now = datetime.now(tz_taiwan)
  year_str = now.strftime("%Y")
  month_str = now.strftime("%Y-%m")
  date_str = now.strftime("%Y-%m-%d")

  target_dir = os.path.join(DATA_DIR, year_str, month_str, date_str)
  os.makedirs(target_dir, exist_ok=True)

  raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
  api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
  if not api_keys:
    print(
        "❌ 警告：未找到 YOUTUBE_API_KEYS"
        " 環境變數，YouTube 搜尋與點閱抓取功能將無法運作！"
    )

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
  df_unique_songs = (
      df_today_all[["歌名", "歌手"]].drop_duplicates().reset_index(drop=True)
  )

  if os.path.exists(MAPPING_FILE):
    try:
      df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")
      for col_to_drop in [
          "YT 影片標題",
          "yt_title",
          "title",
          "Video Title",
          "影片連結",
      ]:
        if col_to_drop in df_mapping.columns:
          df_mapping.drop(columns=[col_to_drop], inplace=True)
    except Exception:
      df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)
  else:
    df_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)

  for col in REQ_MAPPING_COLS:
    if col not in df_mapping.columns:
      df_mapping[col] = "-"

  df_mapping = df_mapping[REQ_MAPPING_COLS].drop_duplicates(
      subset=["歌名", "歌手"], keep="first"
  )

  current_key_idx = 0

  def get_yt_service(idx):
    if idx < len(api_keys):
      return build("youtube", "v3", developerKey=api_keys[idx])
    return None

  youtube_service = get_yt_service(current_key_idx)
  mapping_updated = False

  if api_keys:
    print(
        "🔍 開始檢查今日榜單歌曲是否需要建立對照或補抓 YouTube Video ID..."
    )

    for idx, row in df_unique_songs.iterrows():
      song = str(row["歌名"]).strip()
      singer = str(row["歌手"]).strip()

      matched_row = df_mapping[
          (df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)
      ]
      has_valid_id = False
      if not matched_row.empty:
        vid_val = str(matched_row.iloc[0]["Video ID"]).strip()
        if vid_val not in ["-", "", "nan", "None"]:
          has_valid_id = True

      if has_valid_id:
        continue

      clean_song, main_song = parse_song_title(song)
      search_queries = build_search_queries(song, singer)
      print(f"🔄 [在榜歌曲補抓/搜尋]：{song} - {singer} ...")

      matched_id = None
      matched_views = 0
      matched_mode = None

      main_sim_norm = normalize_text(zhconv.convert(main_song, "zh-hans"))
      main_tra_norm = normalize_text(zhconv.convert(main_song, "zh-hant"))

      artist_tokens = extract_artist_tokens(singer)

      # 兩階段搜尋：先查 viewCount（高觀看量），查無結果再退回 relevance（相關性補救）
      order_strategies = ["viewCount", "relevance"]

      for order_mode in order_strategies:
        if matched_id:
          break

        for query_str in search_queries:
          if matched_id:
            break

          success = False
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
                      maxResults=30,
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
                  v_views = int(item["statistics"].get("viewCount", 0))
                  duration_sec = parse_duration(
                      item.get("contentDetails", {}).get("duration", "PT0S")
                  )

                  if duration_sec <= 60 or duration_sec > 600:
                    continue

                  v_title_lower = v_title.lower()
                  v_title_norm = normalize_text(v_title)
                  channel_lower = channel_title.lower()
                  channel_norm = normalize_text(channel_title)

                  is_topic = "topic" in channel_lower or "主題" in channel_lower

                  has_noise = any(
                      nk in v_title_lower for nk in COMBINED_NOISE_KEYWORDS
                  )
                  if not is_topic and has_noise:
                    continue

                  song_matched = (main_sim_norm in v_title_norm) or (
                      main_tra_norm in v_title_norm
                  )
                  if not song_matched:
                    continue

                  singer_matched = (
                      not artist_tokens
                      or any(tkn in v_title_norm for tkn in artist_tokens)
                      or any(tkn in channel_norm for tkn in artist_tokens)
                  )

                  cand = {
                      "id": v_id,
                      "views": v_views,
                  }

                  if is_topic or singer_matched:
                    candidates.append(cand)

                if candidates:
                  best = max(candidates, key=lambda x: x["views"])
                  matched_id = best["id"]
                  matched_views = best["views"]
                  matched_mode = order_mode

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
                    f"⚠️ 第 {current_key_idx + 1} 組 API Key"
                    " 額度用盡，自動切換至下一組 Key..."
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

      mask = (df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)

      if matched_id:
        mode_label = (
            "觀看量" if matched_mode == "viewCount" else "相關性補救"
        )
        print(
            f"  ✅ 成功匹配 ID: {matched_id} | 點閱: {matched_views:,} |"
            f" 模式: {mode_label}"
        )
        if mask.any():
          df_mapping.loc[mask, "Video ID"] = matched_id
        else:
          new_m_row = pd.DataFrame([{
              "歌名": song,
              "歌手": singer,
              "Video ID": matched_id,
          }])
          df_mapping = pd.concat([df_mapping, new_m_row], ignore_index=True)
        mapping_updated = True
      else:
        print(
            "  ❌ 未找到匹配影片。對照表保持"
            " '-'（若明日仍在榜上將繼續嘗試重試）。"
        )
        if not mask.any():
          new_m_row = pd.DataFrame([{"歌名": song, "歌手": singer, "Video ID": "-"}])
          df_mapping = pd.concat([df_mapping, new_m_row], ignore_index=True)
          mapping_updated = True

      time.sleep(0.1)

    if mapping_updated:
      df_mapping = df_mapping[REQ_MAPPING_COLS].drop_duplicates(
          subset=["歌名", "歌手"], keep="first"
      )
      df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
      print(f"💾 對照表更新完成 ➔ {MAPPING_FILE}")

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
                f"⚠️ 第 {current_key_idx + 1} 組 API Key"
                " 額度用盡，自動切換至下一組 Key..."
            )
            current_key_idx += 1
            youtube_service = get_yt_service(current_key_idx)
          else:
            print(f"⚠️ 批次抓取點閱率錯誤: {e}")
            break
        except Exception as e:
          print(f"⚠️ 批次抓取點閱率未知錯誤: {e}")
          break

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
