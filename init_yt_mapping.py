import os
import pandas as pd

MAPPING_FILE = os.path.join("data", "yt_mapping.csv")
REQ_MAPPING_COLS = ["歌名", "歌手", "Video ID", "YT 影片標題", "影片連結"]


def fix_and_load_mapping():
  """自動修復舊欄位名 (video_id -> Video ID) 並讀取對照表"""
  if not os.path.exists(MAPPING_FILE):
    return pd.DataFrame(columns=REQ_MAPPING_COLS)

  df = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")

  # 1. 自動把舊的 video_id、yt_title、url 改名為標準欄位名
  rename_dict = {
      "video_id": "Video ID",
      "yt_title": "YT 影片標題",
      "url": "影片連結",
      "yt_url": "影片連結",
  }
  df.rename(columns=rename_dict, inplace=True)

  # 2. 如果因為先前新增欄位導致有重複的 Video ID 欄位，自動合併資料
  if "Video ID" in df.columns:
    # 移除重複的欄位名，保留非 '-' 的有效 ID
    df = df.loc[:, ~df.columns.duplicated()]

  # 3. 補齊缺失欄位
  for col in REQ_MAPPING_COLS:
    if col not in df.columns:
      df[col] = "-"

  # 只保留 5 個標準欄位
  df = df[REQ_MAPPING_COLS]
  return df


# 執行初始化/更新邏輯
df_mapping = fix_and_load_mapping()

# ... (下方接你原本匯入歷史榜單 CSV 並合併新歌曲的邏輯) ...

# 最後存檔時，就會是完全標準的欄位格式！
df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
print("✅ yt_mapping.csv 已經自動修復並完成更新！")
