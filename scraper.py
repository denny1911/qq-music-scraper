import os
import re
import time
import pandas as pd
import requests
import zhconv
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==========================================
# 設定環境與變數
# ==========================================
# 取得 API Keys
api_keys_raw = os.getenv("YOUTUBE_API_KEYS", "")
API_KEYS = [k.strip() for k in api_keys_raw.split(",") if k.strip()]
NOISE_KEYWORDS = ["花絮", "未播", "片段", "採訪", "預告", "解說", "幕後", "剪輯", "reaction", "cover"]

# 檔案路徑
DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")
BASELINE_FILE = os.path.join(DATA_DIR, "yt_baseline.csv")

def parse_duration(duration_str):
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str or "")
    if not match: return 0
    return int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60 + int(match.group(3) or 0)

def search_youtube(query_str, song, singer, service):
    """搜尋邏輯 (簡化自模組四)"""
    # [這裡放入原本模組四的搜尋與篩選邏輯]
    # 為保持程式碼簡潔，請確保這裡的篩選邏輯與你原本測試過的邏輯一致
    # 若搜尋到結果，回傳 dict: {'id': ..., 'title': ..., 'views': ..., 'url': ...}
    # 若搜尋不到，回傳 None
    return None # 範例用

def main():
    tz_taiwan = timezone(timedelta(hours=8))
    date_str = datetime.now(tz_taiwan).strftime("%Y-%m-%d")
    target_dir = os.path.join(DATA_DIR, date_str)
    os.makedirs(target_dir, exist_ok=True)

    # 1. 讀取現有 Mapping (如果沒有就建立空的)
    if os.path.exists(MAPPING_FILE):
        df_mapping = pd.read_csv(MAPPING_FILE)
    else:
        df_mapping = pd.DataFrame(columns=["歌名", "歌手", "Video ID", "YT 影片標題", "影片連結"])

    # 2. 抓取 QQ 音樂榜單 (維持你原本的抓取功能)
    # ... (此處填入你原本的 fetch_qq_music_chart 邏輯) ...
    
    # 假設我們將所有榜單合併為一個 df_all_songs
    # df_all_songs = pd.concat(...) 

    # 3. 檢查缺失的影片 ID 並補抓
    youtube_service = build("youtube", "v3", developerKey=API_KEYS[0]) # 這裡需加入 Key 切換邏輯
    
    for idx, row in df_all_songs.iterrows():
        song, singer = row["歌名"], row["歌手"]
        
        # 檢查是否已在對照表中
        match = df_mapping[(df_mapping["歌名"] == song) & (df_mapping["歌手"] == singer)]
        if match.empty:
            print(f"🔍 發現新歌：{song} - {singer}，正在搜尋 YouTube...")
            # 執行搜尋...
            # 若搜尋成功，將結果 append 到 df_mapping，並更新 baseline
            
    # 4. 存檔
    df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
    # 將每日的結果儲存到 data/2026-08-11/ 下...
    print("✅ 每日排程更新完成！")

if __name__ == "__main__":
    main()
