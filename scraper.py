import os
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests


def fetch_qq_music_chart(top_id, chart_name, date_str):
    """通用函式：輸入 topId 與榜單名稱，撈取前 100 名資料"""
    url = "https://u.y.qq.com/cgi-bin/musicu.fcg"

    payload = {
        "detail": {
            "module": "musicToplist.ToplistInfoServer",
            "method": "GetDetail",
            "param": {"topId": top_id, "offset": 0, "num": 100, "period": ""},
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

    print(f"[{date_str}] 正在撈取 QQ 音樂 [{chart_name}] Top 100...")

    try:
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
                    "抓取日期": date_str,
                    "榜單類型": chart_name,
                    "排名": rank,
                    "歌名": title,
                    "歌手": singers,
                    "專輯": album,
                    "發行日期": release_date,
                }
            )

        return pd.DataFrame(song_data)

    except Exception as e:
        print(f"❌ 撈取 [{chart_name}] 過程發生錯誤：{e}")
        return None


def main():
    # 1. 設定台灣時區 (UTC+8) 取得日期字串 (例如: 2026-01-01)
    tz_taiwan = timezone(timedelta(hours=8))
    date_str = datetime.now(tz_taiwan).strftime("%Y-%m-%d")

    # 2. 建立當天日期的專屬資料夾 (例如: data/2026-01-01)
    target_dir = os.path.join("data", date_str)
    os.makedirs(target_dir, exist_ok=True)

    # 3. 定義 4 個榜單名稱、對應的 topId 與檔案後綴標籤
    charts = {
        "new": {"top_id": 27, "name": "新歌榜"},
        "film": {"top_id": 29, "name": "影視金曲榜"},
        "show": {"top_id": 64, "name": "綜藝新歌榜"},
        "tik": {"top_id": 60, "name": "抖音熱歌榜"},
    }

    # 4. 依序抓取每個榜單並寫入對應 CSV
    for tag, info in charts.items():
        df = fetch_qq_music_chart(info["top_id"], info["name"], date_str)

        if df is not None and not df.empty:
            # 檔案路徑格式如：data/2026-01-01/2026-01-01_new.csv
            csv_filename = os.path.join(target_dir, f"{date_str}_{tag}.csv")
            df.to_csv(csv_filename, index=False, encoding="utf-8-sig")
            print(f" ✓ [{info['name']}] 儲存成功 ➔ {csv_filename}")
        else:
            print(f" ⚠️ [{info['name']}] 無法取得資料")


if __name__ == "__main__":
    main()
