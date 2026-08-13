import glob
import os
import pandas as pd

DATA_DIR = "data"


def reset_historical_charts():
    # 鎖定 2026-07-31 至 2026-08-11 的歷史榜單 CSV 檔案
    target_files = []
    all_csvs = glob.glob(os.path.join(DATA_DIR, "**", "*.csv"), recursive=True)

    for f in all_csvs:
        filename = os.path.basename(f)
        # 完全排除對照表與基準檔，確保不動到中央對照表
        if "yt_mapping" in filename or "yt_baseline" in filename:
            continue

        # 篩選 2026-07-31 以及 2026-08-01 ~ 2026-08-11
        if "2026-07-31" in f or any(
            f"2026-08-{d:02d}" in f for d in range(1, 12)
        ):
            target_files.append(f)

    if not target_files:
        print("⚠️ 未找到 2026-07-31 至 2026-08-11 範圍內的榜單 CSV 檔案。")
        return

    print(
        f"📂 找到 {len(target_files)} 個目標歷史榜單檔案，準備重置點閱率與 YouTube ID..."
    )

    for f in target_files:
        try:
            df = pd.read_csv(f, dtype=str)

            # 將 YouTube ID 與 點閱率 欄位強制清洗為 "-"
            df["YouTube ID"] = "-"
            df["點閱率"] = "-"

            # 若舊檔中有相容欄位 Video ID 亦一併清洗
            if "Video ID" in df.columns:
                df["Video ID"] = "-"

            # 寫回檔案
            df.to_csv(f, index=False, encoding="utf-8-sig")
            print(f"   ✓ 已清洗重置 ➔ {f}")

        except Exception as e:
            print(f"❌ 清洗 {f} 失敗: {e}")

    print("\n🎉 7/31 ~ 8/11 歷史榜單洗資料完成！中央對照表未受任何影響。")


if __name__ == "__main__":
    reset_historical_charts()
