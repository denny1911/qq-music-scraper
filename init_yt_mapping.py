import glob
import json
import os
import random
import re
import time
import google.generativeai as genai
import pandas as pd
import zhconv

DATA_DIR = "data"
YT_MAPPING_PATH = "data/yt_mapping.csv"

# ==========================================
# 🔑 1. 取得 Gemini API Keys (修改版)
# ==========================================
def get_api_keys():
  """從環境變數 (GitHub Actions) 或本地設定取得 Key"""
  keys = []

  # 優先讀取環境變數 API_KEYS (對應 GitHub Secrets 設的名字)
  env_keys = os.getenv("API_KEYS", "")
  if env_keys:
    keys = [k.strip() for k in env_keys.splitlines() if k.strip()]
    return keys

  # 備用：讀取本地 .streamlit/secrets.toml
  if os.path.exists(".streamlit/secrets.toml"):
    try:
      import tomllib
      with open(".streamlit/secrets.toml", "rb") as f:
        secrets = tomllib.load(f)
        if "GEMINI_API_KEYS" in secrets:
          k_config = secrets["GEMINI_API_KEYS"]
          if isinstance(k_config, list):
            keys = k_config
          elif isinstance(k_config, str):
            keys = [k.strip() for k in k_config.split(",") if k.strip()]
    except Exception:
      pass

  return keys

# 確保這裡確實有這行，且變數名稱完全拼對
API_KEYS = get_api_keys()

# ==========================================
# 🤖 2. 運用 QQAI 邏輯判定歌曲語言
# ==========================================
def call_gemini_classify_song(song_title, singer_name, yt_id=None):
  if not API_KEYS:
    return {
        "success": False,
        "category": "未知",
        "reason": "未找到 Gemini API Key (請檢查 Streamlit Secrets 或環境變數)",
    }

  shuffled_keys = API_KEYS.copy()
  random.shuffle(shuffled_keys)

  yt_link_info = (
      f"https://www.youtube.com/watch?v={yt_id}" if yt_id else "無"
  )

  prompt = f"""
你是一個專業音樂榜單數據分析專家。請結合歌名、歌手背景知識以及 YouTube 影片資訊，將這首歌曲精準歸類為以下【5 種語言類別】之一：
1. "華語" (歌詞以國語/粵語/台語為主)
2. "西洋" (歌詞以英文/歐美語系為主)
3. "韓語" (歌詞以韓文為主，K-Pop)
4. "日語" (歌詞以日文為主，J-Pop)
5. "其它"

待分析歌曲資料：
- 歌名："{song_title}"
- 歌手："{singer_name}"
- YouTube 連結：{yt_link_info}

【關鍵判斷標準】：
1. 實際演唱語言優先：請根據歌曲實際演唱的歌詞語言做最終判斷。
2. 華語/亞洲歌手的全英文歌：若華語歌手發行的是全英文歌曲 (如張藝興 Crossfire、王嘉爾 Jackson Wang 的英文單曲)，請務必歸類為 "西洋"。
3. 英文歌名的華語歌：若僅是歌名包含英文單字但歌詞與演唱主要是華語 (如周深翻唱或發行的中文歌曲)，請歸類為 "華語"。
4. 參考 YouTube 資訊：若提供了 YouTube 連結，請結合該影片的歌曲知識庫進行精準判定。

請嚴格只輸出 JSON 格式，結構如下：
{{
  "category": "西洋",
  "reason": "結合 YouTube 影片與背景知識，張藝興 Crossfire 為全英文單曲，演唱語言為英文，故歸類為西洋。"
}}
"""

  last_error = None
  for current_key in shuffled_keys:
    try:
      genai.configure(api_key=current_key)
      model = genai.GenerativeModel("gemini-3.1-flash-lite-preview")
      response = model.generate_content(
          prompt, generation_config={"response_mime_type": "application/json"}
      )
      result_json = json.loads(response.text.strip())
      return {
          "success": True,
          "category": result_json.get("category", "其它"),
          "reason": result_json.get("reason", "Gemini 判定完成"),
      }
    except Exception as e:
      last_error = e
      # 如果遇到 429 流量限制，強制讓該組 Key 冷卻一下
      if "429" in str(e) or "exhausted" in str(e).lower():
        print(f"   ⚠️ 觸發 429 流量限制，強制暫停 5 秒讓伺服器喘息...")
        time.sleep(5)
      continue

  return {
      "success": False,
      "category": "未知",
      "reason": f"所有 Key 呼叫失敗: {last_error}",
  }


# ==========================================
# 🛠️ 3. 對 yt_mapping.csv 補充「語言」直欄
# ==========================================
def update_yt_mapping_languages(csv_path=YT_MAPPING_PATH):
  if not os.path.exists(csv_path):
    print(f"⚠️ 找不到對照表：{csv_path}，跳過語言更新。")
    return

  print(f"\n📂 讀取對照表：{csv_path} ...")
  df = pd.read_csv(csv_path, dtype=str)

  song_col = next(
      (c for c in ["歌名", "song", "歌曲名稱"] if c in df.columns), None
  )
  singer_col = next(
      (c for c in ["歌手", "singer", "歌手名稱"] if c in df.columns), None
  )
  yt_col = next(
      (
          c
          for c in ["YouTube ID", "Video ID", "YouTube_ID"]
          if c in df.columns
      ),
      None,
  )

  if not song_col:
    print("❌ 對照表中找不到『歌名』欄位！")
    return

  # 新增「語言」與「語言判斷依據」直欄
  if "語言" not in df.columns:
    df["語言"] = ""
  if "語言判斷依據" not in df.columns:
    df["語言判斷依據"] = ""

  total_rows = len(df)
  updated_count = 0

  print(f"🚀 開始檢測 {total_rows} 筆歌曲的語言類別 (共找到 {len(API_KEYS)} 組 API Key)...")

  for idx, row in df.iterrows():
    song_title = str(row[song_col]).strip() if pd.notna(row[song_col]) else ""
    singer_name = (
        str(row[singer_col]).strip()
        if singer_col and pd.notna(row[singer_col])
        else ""
    )
    yt_id = (
        str(row[yt_col]).strip() if yt_col and pd.notna(row[yt_col]) else None
    )

    print(
        f"[{idx+1}/{total_rows}] 分析中：《{song_title}》| 歌手：{singer_name}"
    )

    res = call_gemini_classify_song(
        song_title=song_title, singer_name=singer_name, yt_id=yt_id
    )

    if res["success"]:
      df.at[idx, "語言"] = res["category"]
      df.at[idx, "語言判斷依據"] = res["reason"]
      updated_count += 1
      print(f"   └─ 🎯 判定：【{res['category']}】({res['reason']})")
    else:
      df.at[idx, "語言"] = "未知"
      df.at[idx, "語言判斷依據"] = res["reason"]
      print(f"   └─ ⚠️ 失敗：{res['reason']}")

    sleep_time = random.uniform(1.5, 3.0)
    time.sleep(sleep_time)

    # 每 10 筆存檔一次
    if updated_count > 0 and updated_count % 10 == 0:
      df.to_csv(csv_path, index=False, encoding="utf-8-sig")
      print(f"💾 進度已備份至 {csv_path} ...")

    # 每 10 筆存檔一次
    if updated_count > 0 and updated_count % 10 == 0:
      df.to_csv(csv_path, index=False, encoding="utf-8-sig")
      print(f"💾 進度已備份至 {csv_path} ...")

  df.to_csv(csv_path, index=False, encoding="utf-8-sig")
  print(f"🎉 語言欄位更新完成！已寫入 {csv_path}\n")

if __name__ == "__main__":
  # 1. 執行語言欄位查詢與補全
  update_yt_mapping_languages()

