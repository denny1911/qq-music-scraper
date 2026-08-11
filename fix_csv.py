import os
import pandas as pd

MAPPING_FILE = os.path.join("data", "yt_mapping.csv")

if os.path.exists(MAPPING_FILE):
  # 讀取現有 CSV
  df = pd.read_csv(MAPPING_FILE, dtype=str)

  # 1. 統一改名（將舊欄位轉為標準欄位名）
  rename_dict = {
      "video_id": "Video ID",
      "yt_title": "YT 影片標題",
      "url": "影片連結",
      "yt_url": "影片連結",
  }
  df.rename(columns=rename_dict, inplace=True)

  # 2. 若同時存在舊欄位與新欄位，進行資料合併（優先保留非 '-' 的有效 ID）
  if (
      "Video ID" in df.columns
      and df.columns.tolist().count("Video ID") > 1
  ):
    # 移除全空的重複欄位
    df = df.loc[:, ~df.columns.duplicated()]

  # 3. 確保 5 個標準欄位都在
  std_cols = ["歌名", "歌手", "Video ID", "YT 影片標題", "影片連結"]
  for col in std_cols:
    if col not in df.columns:
      df[col] = "-"

  # 只保留這 5 個標準欄位並去除重複資料
  df = df[std_cols].drop_duplicates(subset=["歌名", "歌手"], keep="first")

  # 寫回 CSV
  df.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
  print("✅ yt_mapping.csv 欄位已成功修復並轉為統一格式！")
else:
  print("❌ 找不到 data/yt_mapping.csv 檔案。")
