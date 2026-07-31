import os
from datetime import datetime
import pandas as pd
import requests


def fetch_qq_music_new_songs():
    # 1. 建立輸出資料夾 (若不存在則自動創建)
    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)

    # 1. 設定台灣時區 (UTC+8)
    tz_taiwan = timezone(timedelta(hours=8))

    # 2. 取得台灣當前時間並格式化（加入小時）
    today_str = datetime.now(tz_taiwan).strftime("%Y-%m-%d_%H時")
    # 2. 取得當前日期字串 (格式：YYYY-MM-DD)
    # 格式：YYYY-MM-DD_HH-MM
    # 產生的檔案名稱會變成：data/2026-07-31_16-30_QQ音樂_新歌榜Top100.csv
    
    # QQ 音樂官方 API 接口
    url = "https://u.y.qq.com/cgi-bin/musicu.fcg"

    # topId: 27 代表「新歌榜」，num 設置為 100 抓取前 100 名
    payload = {
        "detail": {
            "module": "musicToplist.ToplistInfoServer",
            "method": "GetDetail",
            "param": {"topId": 27, "offset": 0, "num": 100, "period": ""},
        },
        "comm": {"ct": 24, "cv": 0},
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://y.qq.com/",
    }

    print(f"[{today_str}] 正在撈取 QQ 音樂新歌榜 Top 100...")

    try:
        # 設定 timeout=15 避免伺服器端連線卡死
        response = requests.post(
            url, json=payload, headers=headers, timeout=15
        )
        response.raise_for_status()
        data = response.json()

        # 解析歌曲列表
        song_list = data["detail"]["data"]["songInfoList"]

        song_data = []
        for rank, song in enumerate(song_list, start=1):
            title = song.get("name", "未知歌名")
            singers = "/".join(
                [s.get("name", "") for s in song.get("singer", [])]
            )
            album = song.get("album", {}).get("name", "未知專輯")

            release_date = song.get("time_public") or song.get(
                "album", {}
            ).get("time_public", "未知日期")

            song_data.append(
                {
                    "抓取日期": today_str,  # 紀錄這筆榜單是哪天抓的
                    "排名": rank,
                    "歌名": title,
                    "歌手": singers,
                    "專輯": album,
                    "發行日期": release_date,
                }
            )

        # 轉換為 Pandas DataFrame 表格格式
        df = pd.DataFrame(song_data)

        # 3. 動態產生帶有日期的 CSV 檔案路徑 (例如: data/2026-07-31_QQ音樂_新歌榜Top100.csv)
        csv_filename = os.path.join(
            output_dir, f"{today_str}_QQ音樂_新歌榜Top100.csv"
        )

        # 僅輸出 CSV 檔案 (採用 utf-8-sig 編碼，防止以 Excel 打開時中文亂碼)
        df.to_csv(csv_filename, index=False, encoding="utf-8-sig")

        print(f"✓ 抓取成功！檔案已輸出至：\n - CSV 檔: {csv_filename}\n")

        # 在終端機預覽前 10 名
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 1000)
        print("【前 10 名預覽】")
        print(df.head(10).to_string(index=False))

        return df

    except Exception as e:
        print(f"❌ 撈取過程發生錯誤：{e}")
        return None


if __name__ == "__main__":
    fetch_qq_music_new_songs()
