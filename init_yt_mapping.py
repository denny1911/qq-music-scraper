import glob
import os
from googleapiclient.discovery import build
import pandas as pd

DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")


def backfill_historical_charts():
    if not os.path.exists(MAPPING_FILE):
        print(f"❌ 找不到中央對照表：{MAPPING_FILE}")
        return

    # 1. 讀取中央對照表
    df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")

    # 2. 搜尋 2026-07-31 至 2026-08-11 的所有歷史 CSV
    target_files = []
    all_csvs = glob.glob(
        os.path.join(DATA_DIR, "**", "*.csv"), recursive=True
    )

    for f in all_csvs:
        filename = os.path.basename(f)
        # 篩選 07-31 到 08-11 之間的檔案（排除對照表）
        if "yt_mapping" in filename or "yt_baseline" in filename:
            continue
        if any(f"2026-07-31" in f or f"2026-08-{d:02d}" in f for d in range(1, 12)):
            target_files.append(f)

    print(f"📂 找到 {len(target_files)} 個待補齊欄位的歷史榜單檔案。")

    if not target_files:
        print("ℹ️ 未找到符合條件的歷史檔案。")
        return

    # 3. 收集所有需要查詢點閱數的 Video ID
    all_needed_vids = set()
    for f in target_files:
        try:
            df = pd.read_csv(f, dtype=str)
            df_merged = pd.merge(
                df,
                df_mapping[["歌名", "歌手", "Video ID"]],
                on=["歌名", "歌手"],
                how="left",
            )
            vids = df_merged["Video ID"].dropna().unique()
            for vid in vids:
                if str(vid).strip() not in ["-", "", "nan", "None"]:
                    all_needed_vids.add(str(vid).strip())
        except Exception as e:
            print(f"⚠️ 讀取 {f} 失敗: {e}")

    # 4. 批次向 YouTube API 查詢當前點閱數
    raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    view_counts_dict = {}
    if api_keys and all_needed_vids:
        print(f"📊 正在批次查詢 {len(all_needed_vids)} 個 Video ID 的當前點閱率...")
        youtube = build("youtube", "v3", developerKey=api_keys[0])
        vid_list = list(all_needed_vids)

        for i in range(0, len(vid_list), 50):
            chunk = vid_list[i : i + 50]
            try:
                res = (
                    youtube.videos()
                    .list(part="statistics", id=",".join(chunk))
                    .execute()
                )
                for item in res.get("items", []):
                    v_id = item["id"]
                    views = int(item["statistics"].get("viewCount", 0))
                    view_counts_dict[v_id] = views
            except Exception as e:
                print(f"⚠️ 批次查詢 API 發生錯誤: {e}")

    # 5. 回寫欄位並覆蓋 CSV
    for f in target_files:
        try:
            df = pd.read_csv(f, dtype=str)

            # 移除舊的 YouTube 相關欄位避免重複
            for old_col in ["YouTube ID", "Video ID", "點閱率"]:
                if old_col in df.columns:
                    df.drop(columns=[old_col], inplace=True)

            # 比對對照表附加 Video ID
            df_final = pd.merge(
                df,
                df_mapping[["歌名", "歌手", "Video ID"]],
                on=["歌名", "歌手"],
                how="left",
            )

            df_final["YouTube ID"] = df_final["Video ID"].fillna("-")

            # 附加點閱率
            raw_views = df_final["Video ID"].map(view_counts_dict).fillna(0)
            df_final["點閱率"] = raw_views.apply(
                lambda x: f"{int(x):,}" if x > 0 else "-"
            )

            df_final.drop(columns=["Video ID"], errors="ignore", inplace=True)

            # 儲存覆蓋原檔
            df_final.to_csv(f, index=False, encoding="utf-8-sig")
            print(f"   ✓ 已補齊並更新 ➔ {f}")
        except Exception as e:
            print(f"❌ 更新 {f} 失敗: {e}")

    print("\n🎉 所有歷史榜單檔案補齊完成！")


if __name__ == "__main__":
    backfill_historical_charts()
