import glob
import os
import pandas as pd

DATA_DIR = "data"
YT_MAPPING_PATH = os.path.join(DATA_DIR, "yt_mapping.csv")


def clean_and_sync_history():
    # ==========================================
    # 第一階段：清理中央對照表 (yt_mapping.csv)
    # ==========================================
    if not os.path.exists(YT_MAPPING_PATH):
        print(f"❌ 找不到中央對照表：{YT_MAPPING_PATH}，程式終止。")
        return

    print(f"📂 正在讀取中央對照表：{YT_MAPPING_PATH} ...")
    df_mapping = pd.read_csv(YT_MAPPING_PATH, dtype=str).fillna("-")

    # 1. 刪除「語言判斷依據」欄位
    if "語言判斷依據" in df_mapping.columns:
        df_mapping = df_mapping.drop(columns=["語言判斷依據"])
        print("  └─ ✂️ 已成功刪除『語言判斷依據』欄位")

    # 確保基本欄位完整
    req_cols = ["歌名", "歌手", "Video ID", "語言"]
    for col in req_cols:
        if col not in df_mapping.columns:
            df_mapping[col] = "-"

    # 只留標準 4 欄並去除重複項
    df_mapping = df_mapping[req_cols].drop_duplicates(subset=["歌名", "歌手"], keep="first")
    df_mapping.to_csv(YT_MAPPING_PATH, index=False, encoding="utf-8-sig")
    print(f"💾 中央對照表清理完成並儲存 ➔ {YT_MAPPING_PATH}\n")

    # ==========================================
    # 第二階段：使用對照表回填歷史榜單 CSV 檔案
    # ==========================================
    # 建立 (歌名, 歌手) -> 語言 的快速對照字典
    lang_dict = {}
    for _, row in df_mapping.iterrows():
        song = str(row["歌名"]).strip()
        singer = str(row["歌手"]).strip()
        lang = str(row["語言"]).strip()
        lang_dict[(song, singer)] = lang if lang not in ["", "-", "nan", "None"] else "未知"

    # 搜尋 data/ 目錄下所有歷史榜單 CSV（排除 yt_mapping.csv）
    search_pattern = os.path.join(DATA_DIR, "**", "*.csv")
    all_csv_files = glob.glob(search_pattern, recursive=True)
    history_files = [f for f in all_csv_files if os.path.basename(f) != "yt_mapping.csv"]

    print(f"🔍 找到 {len(history_files)} 個歷史榜單 CSV 檔案，開始進行語言資料回填...")

    updated_file_count = 0

    for file_path in history_files:
        try:
            df_hist = pd.read_csv(file_path, dtype=str)

            if "歌名" not in df_hist.columns or "歌手" not in df_hist.columns:
                continue

            # 將 (歌名, 歌手) 對應至字典取得語言
            languages = []
            for _, h_row in df_hist.iterrows():
                h_song = str(h_row["歌名"]).strip()
                h_singer = str(h_row["歌手"]).strip()
                languages.append(lang_dict.get((h_song, h_singer), "未知"))

            # 新增或覆蓋「語言」欄位
            df_hist["語言"] = languages

            # 儲存回原檔案
            df_hist.to_csv(file_path, index=False, encoding="utf-8-sig")
            updated_file_count += 1
            print(f"  └─ ✅ 已更新：{file_path}")

        except Exception as e:
            print(f"  └─ ❌ 更新失敗 [{file_path}]：{e}")

    print(f"\n🎉 全部完成！共成功補全/更新 {updated_file_count} 個歷史榜單檔案。")


if __name__ == "__main__":
    clean_and_sync_history()
