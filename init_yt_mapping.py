import os
import pandas as pd

DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")


def remove_url_column():
    if not os.path.exists(MAPPING_FILE):
        print(f"❌ 找不到對照表檔案：{MAPPING_FILE}")
        return

    try:
        # 讀取既有對照表
        df = pd.read_csv(MAPPING_FILE, dtype=str)

        # 檢查並刪除「影片連結」欄位
        if "影片連結" in df.columns:
            df.drop(columns=["影片連結"], inplace=True)
            print("✂️ 已成功移除「影片連結」欄位！")
        else:
            print("ℹ️ 對照表中不存在「影片連結」欄位，無需動作。")

        # 重新儲存覆蓋原檔案
        df.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
        print(f"💾 對照表已成功更新並儲存 ➔ {MAPPING_FILE}")

    except Exception as e:
        print(f"❌ 處理過程中發生錯誤：{e}")


if __name__ == "__main__":
    remove_url_column()
