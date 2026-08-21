這是整合了三階段完整邏輯的 init_yt_mapping.py 程式碼：

Python
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
# 🔑 1. 取得 Gemini API Keys
# ==========================================
def get_api_keys():
  """從環境變數 (GitHub Actions) 或本地設定取得 Key，支援換行與逗號分隔"""
  keys = []
  env_keys = os.getenv("API_KEYS", "") or os.getenv("GEMINI_API_KEYS", "")

  if env_keys:
    raw_list = env_keys.replace(",", "\n").splitlines()
    keys = [k.strip() for k in raw_list if k.strip()]
    if keys:
      return keys

  if os.path.exists(".streamlit/secrets.toml"):
    try:
      import tomllib
      with open(".streamlit/secrets.toml", "rb") as f:
        secrets = tomllib.load(f)
        k_config = secrets.get("GEMINI_API_KEYS", secrets.get("API_KEYS", []))
        if isinstance(k_config, list):
          keys = [str(k).strip() for k in k_config if str(k).strip()]
        elif isinstance(k_config, str):
          keys = [k.strip() for k in k_config.replace(",", "\n").splitlines() if k.strip()]
    except Exception:
      pass

  return keys

API_KEYS = get_api_keys()

# ==========================================
# 🤖 2. 運用 Gemini 判定歌曲語言
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
      f"https://www.youtube.com/watch?v={yt_id}"
      if yt_id and str(yt_id) not in ["-", "", "nan", "None"]
      else "無"
  )

  prompt = f"""
你是一個專業音樂榜單數據分析專家。請結合歌名、歌手背景知識以及 YouTube 影片資訊，將這首歌曲精準歸類為以下【5 種語言類別】之一（請務必使用繁體中文）：
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
1. 實際演唱語言絕對優先：請嚴格根據「實際演唱歌詞」做判定，絕對禁止僅憑「歌手國籍、所屬團體或背景」就直接預設歌曲語言！
2. 華語/亞洲歌手的英文歌（重點修正）：若華語歌手發行的是全英文歌曲（如：嚴浩翔《No More Tomorrow》、張藝興《Crossfire》、王嘉爾英文單曲），不論歌手是誰，請務必歸類為 "西洋"。
3. 英文歌名的華語歌：只有在歌名包含英文單字、但實際演唱歌詞「絕大部分為華語」時（如周深包含英文歌名的中文歌曲），才可歸類為 "華語"。
4. 參考 YouTube 資訊：若提供了 YouTube 連結，請結合該影片與知識庫進行精準判定。

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
      raw_cat = result_json.get("category", "其它")
      trad_cat = zhconv.convert(raw_cat, "zh-tw")

      return {
          "success": True,
          "category": trad_cat,
          "reason": result_json.get("reason", "Gemini 判定完成"),
      }
    except Exception as e:
      last_error = e
      err_str = str(e).lower()

      if "429" in err_str or "exhausted" in err_str or "quota" in err_str:
        wait_sec = random.uniform(3.0, 5.0)
        print(f"   ⚠️ 觸發 429 限流，Key 暫時失效，冷卻 {wait_sec:.1f} 秒後切換下一組 Key...")
        time.sleep(wait_sec)
      else:
        time.sleep(1.0)

      continue

  return {
      "success": False,
      "category": "未知",
      "reason": f"所有 Key 呼叫失敗: {last_error}",
  }

# ==========================================
# 🧹 階段一：僅清空 yt_mapping.csv 的「語言」欄位
# ==========================================
def clear_yt_mapping_languages(csv_path=YT_MAPPING_PATH):
  if not os.path.exists(csv_path):
    print(f"⚠️ 找不到對照表：{csv_path}")
    return

  print(f"🧹 [階段 1] 正在清空 {csv_path} 的『語言』欄位...")
  df = pd.read_csv(csv_path, dtype=str)
  if "語言" in df.columns:
    df["語言"] = ""
  df.to_csv(csv_path, index=False, encoding="utf-8-sig")
  print("✅ 語言欄位清空完成！\n")

# ==========================================
# 🛠️ 階段二：對 yt_mapping.csv 重新判定並填回「語言」
# ==========================================
def update_yt_mapping_languages(csv_path=YT_MAPPING_PATH):
  if not os.path.exists(csv_path):
    print(f"⚠️ 找不到對照表：{csv_path}，跳過語言更新。")
    return

  print(f"📂 [階段 2] 讀取對照表：{csv_path} ...")
  df = pd.read_csv(csv_path, dtype=str)

  song_col = next((c for c in ["歌名", "song", "歌曲名稱"] if c in df.columns), None)
  singer_col = next((c for c in ["歌手", "singer", "歌手名稱"] if c in df.columns), None)
  yt_col = next((c for c in ["YouTube ID", "Video ID", "YouTube_ID"] if c in df.columns), None)

  if not song_col:
    print("❌ 對照表中找不到『歌名』欄位！")
    return

  if "語言" not in df.columns:
    df["語言"] = ""

  total_rows = len(df)
  updated_count = 0

  print(f"🚀 開始檢測 {total_rows} 筆歌曲的語言類別 (共找到 {len(API_KEYS)} 組 API Key)...")

  for idx, row in df.iterrows():
    song_title = str(row[song_col]).strip() if pd.notna(row[song_col]) else ""
    singer_name = str(row[singer_col]).strip() if singer_col and pd.notna(row[singer_col]) else ""
    yt_id = str(row[yt_col]).strip() if yt_col and pd.notna(row[yt_col]) else None

    print(f"[{idx+1}/{total_rows}] 分析中：《{song_title}》| 歌手：{singer_name}")

    res = call_gemini_classify_song(
        song_title=song_title, singer_name=singer_name, yt_id=yt_id
    )

    if res["success"]:
      df.at[idx, "語言"] = res["category"]
      updated_count += 1
      print(f"   └─ 🎯 判定：【{res['category']}】({res['reason']})")
    else:
      df.at[idx, "語言"] = "未知"
      print(f"   └─ ⚠️ 失敗：{res['reason']}")

    time.sleep(random.uniform(1.5, 3.0))

    # 每 10 筆備份存檔一次
    if updated_count > 0 and updated_count % 10 == 0:
      df.to_csv(csv_path, index=False, encoding="utf-8-sig")
      print(f"💾 進度已備份至 {csv_path} ...")

  df.to_csv(csv_path, index=False, encoding="utf-8-sig")
  print(f"🎉 語言欄位更新完成！已寫入 {csv_path}\n")

# 字串正規化輔助函式 (比對時相容簡繁體與特殊符號)
def normalize_str(text):
  if not text:
    return ""
  t = str(text).lower()
  t = t.replace("ⅱ", "ii").replace("ⅰ", "i").replace("ⅲ", "iii").replace("ⅳ", "iv")
  return re.sub(r"[\s\.\-\_\(\)（）「」《》【】『』""'']", "", t)

# ==========================================
# 🔄 階段三：將 yt_mapping.csv 最新語言資料覆蓋至所有每日 CSV
# ==========================================
def sync_languages_to_all_daily_csvs(data_dir=DATA_DIR, mapping_path=YT_MAPPING_PATH):
  if not os.path.exists(mapping_path):
    print(f"⚠️ 找不到對照表：{mapping_path}，無法進行每日 CSV 覆蓋同步。")
    return

  print(f"🔄 [階段 3] 正在將 {mapping_path} 的最新語言資料同步至所有每日 CSV 檔案...")

  mapping_df = pd.read_csv(mapping_path, dtype=str)
  m_song_col = next((c for c in ["歌名", "song", "歌曲名稱"] if c in mapping_df.columns), None)

  if not m_song_col or "語言" not in mapping_df.columns:
    print("❌ 對照表缺少『歌名』或『語言』欄位，同步中止。")
    return

  # 建立歌曲語言對照字典 (簡體與繁體雙向索引)
  lang_dict = {}
  for _, row in mapping_df.iterrows():
    song = str(row[m_song_col]).strip() if pd.notna(row[m_song_col]) else ""
    lang = str(row["語言"]).strip() if pd.notna(row["語言"]) else ""

    if song and lang:
      key_sim = normalize_str(zhconv.convert(song, "zh-hans"))
      key_tra = normalize_str(zhconv.convert(song, "zh-hant"))
      lang_dict[key_sim] = lang
      lang_dict[key_tra] = lang

  # 搜尋 data/ 資料夾下的所有每日 CSV 檔 (排除對照表本身)
  csv_files = []
  for root, _, files in os.walk(data_dir):
    for file in files:
      if file.endswith(".csv") and os.path.abspath(os.path.join(root, file)) != os.path.abspath(mapping_path):
        csv_files.append(os.path.join(root, file))

  print(f"📂 共找到 {len(csv_files)} 個每日 CSV 檔案待更新。")

  synced_file_count = 0
  for file_path in csv_files:
    try:
      df = pd.read_csv(file_path, dtype=str)
      s_col = next((c for c in ["歌名", "song", "歌曲名稱"] if c in df.columns), None)

      if not s_col:
        continue

      if "語言" not in df.columns:
        df["語言"] = ""

      updated = False
      for idx, row in df.iterrows():
        s_name = str(row[s_col]).strip() if pd.notna(row[s_col]) else ""
        if not s_name:
          continue

        s_sim = normalize_str(zhconv.convert(s_name, "zh-hans"))
        s_tra = normalize_str(zhconv.convert(s_name, "zh-hant"))

        matched_lang = lang_dict.get(s_sim) or lang_dict.get(s_tra)
        if matched_lang:
          df.at[idx, "語言"] = matched_lang
          updated = True

      if updated:
        df.to_csv(file_path, index=False, encoding="utf-8-sig")
        synced_file_count += 1
        print(f"   └─ ✅ 已覆蓋更新：{os.path.basename(file_path)}")
    except Exception as e:
      print(f"   └─ ❌ 更新失敗 {file_path}: {e}")

  print(f"🎉 [階段 3 完成] 共成功同步更新 {synced_file_count} 個每日 CSV 檔案！\n")

if __name__ == "__main__":
  # 依序執行三個階段
  clear_yt_mapping_languages()
  update_yt_mapping_languages()
  sync_languages_to_all_daily_csvs()
