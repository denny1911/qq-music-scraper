import glob
import os
import re
import time
import zhconv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd

DATA_DIR = "data"
MAPPING_FILE = os.path.join(DATA_DIR, "yt_mapping.csv")


def parse_duration(duration_str):
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def clean_song_title(title):
    if not title:
        return ""
    cleaned = re.sub(r"^歌曲[:：]\s*", "", str(title))
    return cleaned.strip()


def parse_song_title(song):
    clean_s = clean_song_title(song)
    main_s = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", clean_s).strip()
    if not main_s:
        main_s = clean_s
    return clean_s, main_s


def normalize_text(text):
    if not text:
        return ""
    return re.sub(r"[\s\.\-\_\(\)（）]", "", str(text)).lower()


def extract_artist_tokens(singer):
    if not singer or singer in ["-", "nan", "None"]:
        return []

    singer_str = str(singer).strip()
    all_tokens = set()

    raw_tokens = re.split(
        r"[/&,\+\·\s\*\-\|\(\)（）]|feat\.?|ft\.?|X|x",
        singer_str,
        flags=re.IGNORECASE,
    )

    for raw in raw_tokens:
        raw = raw.strip()
        if not raw:
            continue
        all_tokens.add(zhconv.convert(raw, "zh-hans"))
        all_tokens.add(zhconv.convert(raw, "zh-hant"))

        sub_chunks = re.findall(
            r"[a-zA-Z0-9\.\-\']+|[\u4e00-\u9fa5]+|[\uAC00-\uD7A3]+", raw
        )
        if len(sub_chunks) > 1:
            for chunk in sub_chunks:
                chunk = chunk.strip()
                if len(chunk) >= 1:
                    all_tokens.add(zhconv.convert(chunk, "zh-hans"))
                    all_tokens.add(zhconv.convert(chunk, "zh-hant"))

    normalized_tokens = []
    for t in all_tokens:
        norm = normalize_text(t)
        if norm and len(norm) >= 1:
            normalized_tokens.append(norm)

    return list(set(normalized_tokens))


def build_search_queries(song, singer):
    clean_s, main_s = parse_song_title(song)
    clean_p = str(singer).strip()

    primary_query = f"{main_s} {clean_p}".strip()
    queries = [primary_query]

    if clean_s != main_s:
        full_query = f"{clean_s} {clean_p}".strip()
        if full_query not in queries:
            queries.append(full_query)

    extracted_bracket = re.findall(r"[\(\（]([^\)\）]+)[\)\）]", clean_p)
    if extracted_bracket:
        fallback_singer = " ".join(extracted_bracket).strip()
        fallback_query = f"{main_s} {fallback_singer}".strip()
        if fallback_query not in queries:
            queries.append(fallback_query)

    return queries


COMBINED_NOISE_KEYWORDS = [
    "花絮",
    "未播",
    "片段",
    "採訪",
    "預告",
    "解說",
    "幕後",
    "reaction",
]


def backfill_historical_charts():
    if not os.path.exists(MAPPING_FILE):
        print(f"❌ 找不到中央對照表：{MAPPING_FILE}")
        return

    # 1. 直接讀取中央對照表
    df_mapping = pd.read_csv(MAPPING_FILE, dtype=str).fillna("-")
    print(f"📂 成功讀取 `yt_mapping.csv`，共 {len(df_mapping)} 筆不重複歌曲。")

    # 2. 準備 API Keys
    raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    if not api_keys:
        print("❌ 未找到有效的 API Key (`YOUTUBE_API_KEYS`)，無法執行查找！")
        return

    current_key_idx = 0

    def build_yt_service(key_idx):
        if key_idx < len(api_keys):
            return build("youtube", "v3", developerKey=api_keys[key_idx])
        return None

    youtube_service = build_yt_service(current_key_idx)

    # 3. 逐筆遍歷 yt_mapping.csv 重新檢索 Video ID
    new_video_ids = []
    total_songs = len(df_mapping)

    for idx, row in df_mapping.iterrows():
        song = str(row.get("歌名", "")).strip()
        singer = str(row.get("歌手", "")).strip()

        print(f"🔍 ({idx+1}/{total_songs}) 正在重新測繪：{song} - {singer}")

        matched_id = None
        clean_song, main_song = parse_song_title(song)
        search_queries = build_search_queries(song, singer)

        main_sim_norm = normalize_text(zhconv.convert(main_song, "zh-hans"))
        main_tra_norm = normalize_text(zhconv.convert(main_song, "zh-hant"))
        artist_tokens = extract_artist_tokens(singer)

        for query_str in search_queries:
            if matched_id:
                break

            success = False
            while current_key_idx < len(api_keys) and not success:
                if youtube_service is None:
                    youtube_service = build_yt_service(current_key_idx)
                    if youtube_service is None:
                        break

                try:
                    search_res = (
                        youtube_service.search()
                        .list(
                            q=query_str,
                            part="id",
                            maxResults=30,
                            type="video",
                            order="viewCount",
                            regionCode="TW",
                        )
                        .execute()
                    )

                    v_ids = [
                        item["id"]["videoId"]
                        for item in search_res.get("items", [])
                        if "videoId" in item.get("id", {})
                    ]

                    if v_ids:
                        video_res = (
                            youtube_service.videos()
                            .list(
                                part="snippet,statistics,contentDetails",
                                id=",".join(v_ids),
                            )
                            .execute()
                        )

                        candidates = []

                        for v_item in video_res.get("items", []):
                            v_id = v_item["id"]
                            v_title = v_item["snippet"]["title"]
                            channel_title = v_item["snippet"].get("channelTitle", "")
                            v_views = int(v_item["statistics"].get("viewCount", 0))

                            duration_str = v_item.get("contentDetails", {}).get("duration", "PT0S")
                            duration_sec = parse_duration(duration_str)

                            if duration_sec <= 60 or duration_sec > 600:
                                continue

                            v_title_lower = v_title.lower()
                            v_title_norm = normalize_text(v_title)
                            channel_lower = channel_title.lower()
                            channel_norm = normalize_text(channel_title)

                            is_topic = "topic" in channel_lower or "主題" in channel_lower
                            has_noise = any(nk in v_title_lower for nk in COMBINED_NOISE_KEYWORDS)
                            if not is_topic and has_noise:
                                continue

                            song_matched = (main_sim_norm in v_title_norm) or (main_tra_norm in v_title_norm)
                            if not song_matched:
                                continue

                            singer_matched = (
                                not artist_tokens
                                or any(tkn in v_title_norm for tkn in artist_tokens)
                                or any(tkn in channel_norm for tkn in artist_tokens)
                            )

                            cand = {"id": v_id, "views": v_views}

                            if is_topic or singer_matched:
                                candidates.append(cand)

                        if candidates:
                            best = max(candidates, key=lambda x: x["views"])
                            matched_id = best["id"]

                    success = True

                except HttpError as e:
                    is_quota_error = e.resp.status in [403, 429] or any(
                        k in str(e) for k in ["quotaExceeded", "rateLimitExceeded", "Quota exceeded"]
                    )
                    if is_quota_error:
                        current_key_idx += 1
                        youtube_service = build_yt_service(current_key_idx)
                        if not youtube_service:
                            print("❌ 所有 API Key 的每日額度皆已耗盡！")
                            break
                    else:
                        break
                except Exception:
                    break

        new_video_ids.append(matched_id or "-")
        time.sleep(0.1)

    # 4. 更新並覆蓋中央對照表
    df_mapping["Video ID"] = new_video_ids
    df_mapping.to_csv(MAPPING_FILE, index=False, encoding="utf-8-sig")
    print(f"✅ 中央對照表已完成更新並儲存至：{MAPPING_FILE}")

    # 5. 回寫 2026-07-31 至 2026-08-11 的歷史榜單 CSV
    target_files = []
    all_csvs = glob.glob(os.path.join(DATA_DIR, "**", "*.csv"), recursive=True)

    for f in all_csvs:
        filename = os.path.basename(f)
        if "yt_mapping" in filename or "yt_baseline" in filename:
            continue
        if any(f"2026-07-31" in f or f"2026-08-{d:02d}" in f for d in range(1, 12)):
            target_files.append(f)

    print(f"📂 正在將全新對照結果回寫至 {len(target_files)} 個歷史榜單檔案...")

    for f in target_files:
        try:
            df = pd.read_csv(f, dtype=str)

            # 移除舊欄位
            for old_col in ["YouTube ID", "Video ID", "點閱率"]:
                if old_col in df.columns:
                    df.drop(columns=[old_col], inplace=True)

            # 透過對照表合併最新 Video ID
            df_final = pd.merge(
                df,
                df_mapping[["歌名", "歌手", "Video ID"]],
                on=["歌名", "歌手"],
                how="left",
            )

            df_final["YouTube ID"] = df_final["Video ID"].fillna("-")

            # 批次查詢觀看數
            valid_vids = [
                str(v).strip()
                for v in df_final["Video ID"].dropna().unique()
                if str(v).strip() not in ["-", "", "nan", "None"]
            ]

            view_counts_dict = {}
            if valid_vids and api_keys:
                youtube_v_service = build("youtube", "v3", developerKey=api_keys[0])
                for i in range(0, len(valid_vids), 50):
                    chunk = valid_vids[i : i + 50]
                    try:
                        res = (
                            youtube_v_service.videos()
                            .list(part="statistics", id=",".join(chunk))
                            .execute()
                        )
                        for item in res.get("items", []):
                            v_id = item["id"]
                            views = int(item["statistics"].get("viewCount", 0))
                            view_counts_dict[v_id] = views
                    except Exception:
                        pass

            raw_views = df_final["Video ID"].map(view_counts_dict).fillna(0)
            df_final["點閱率"] = raw_views.apply(
                lambda x: f"{int(x):,}" if x > 0 else "-"
            )

            df_final.drop(columns=["Video ID"], errors="ignore", inplace=True)
            df_final.to_csv(f, index=False, encoding="utf-8-sig")
            print(f"   ✓ 已更新 ➔ {f}")
        except Exception as e:
            print(f"❌ 更新 {f} 失敗: {e}")

    print("\n🎉 全部處理完成！")


if __name__ == "__main__":
    backfill_historical_charts()
