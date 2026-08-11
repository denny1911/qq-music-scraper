import glob
import os
import pandas as pd

# 設定檔案路徑與標準欄位名稱（完全統一）
MAPPING_FILE = os.path.join("data", "yt_mapping.csv")
DATA_DIR = "data"
REQ_MAPPING_COLS = ["歌名", "歌手", "Video ID", "YT 影片標題", "影片連結"]


def clean_and_update_mapping():
  # ----------------------------------------------------
  # 步驟 1：讀取並保護現有的 yt_mapping.csv 資料
  # ----------------------------------------------------
  existing_mapping = pd.DataFrame(columns=REQ_MAPPING_COLS)

  if os.path.exists(MAPPING_FILE):
    df_old = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")

    # 強制將舊欄位名統一改為標準名稱（防止產生新欄位）
    rename_dict = {
        "video_id": "Video ID",
        "yt_title": "YT 影片標題",
        "url": "影片連結",
        "yt_url": "影片連結",
    }
    df_old.rename(columns=rename_dict, inplace=True)

    # 若歷史原因造成欄位重複，只保留第一個非重複欄位
    if df_old.columns.tolist().count("Video ID") > 1:
      df_old = df_old.loc[:, ~df_old.columns.duplicated()]

    # 補齊標準欄位
    for col in REQ_MAPPING_COLS:
      if col not in df_old.columns:
        df_old[col] = "-"

    # 提煉出目前已經存在的歌名與對應資料
    existing_mapping = df_old[REQ_MAPPING_COLS].drop_duplicates(
        subset=["歌名", "歌手"], keep="first"
    )

  # ----------------------------------------------------
  # 步驟 2：掃描 data/ 下所有歷史榜單 CSV 收集所有歌曲
  # ----------------------------------------------------
  all_chart_files = glob.glob(os.path.join(DATA_DIR, "*.csv"))
  song_list = []

  for f in all_chart_files:
    # 排除對照表與基準表本身
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

  # ----------------------------------------------------
  # 步驟 3：智慧合併（保護既有 ID，無 ID 者標註 '-' 待補抓）
  # ----------------------------------------------------
  if not df_all_songs.empty:
    # 以所有歷史榜單歌曲為主體，左連接既有資料
    merged = pd.merge(
        df_all_songs, existing_mapping, on=["歌名", "歌手"], how="left"
    ).fillna("-")
  else:
    merged = existing_mapping

  # 清理空值與異常字串，統一用 '-'
  for col in REQ_MAPPING_COLS:
    if col not in merged.columns:
      merged[col] = "-"

  merged["Video ID"] = (
      merged["Video ID"].replace(["nan", "None", ""], "-").fillna("-")
  )

  # 嚴格僅留存 5 個標準欄位
  df_final = merged[REQ_MAPPING_COLS].drop_duplicates(
      subset=["歌名", "歌手"], keep="first"
  )

  # ----------------------------------------------------
  # 步驟 4：寫回對照表並輸出統計摘要
  # ----------------------------------------------------
  df_final.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")

  valid_count = len(df_final[df_final["Video ID"] != "-"])
  missing_count = len(df_final[df_final["Video ID"] == "-"])

  print("🎉 yt_mapping.csv 資料清洗與修復完成！")
  print(f"📊 總歌曲數：{len(df_final)} 首")
  print(f"✅ 保留有效 Video ID 的歌曲：{valid_count} 首")
  print(f"⏳ 標註為 '-' (等待重新抓取) 的歌曲：{missing_count} 首")


if __name__ == "__main__":
  clean_and_update_mapping()
