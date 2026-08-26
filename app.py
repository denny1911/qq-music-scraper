import os
import re
import time  # 👈 統一放頂部
from datetime import date, datetime, timedelta
import altair as alt
import pandas as pd
import streamlit as st
import zhconv  # 👈 搬到頂部
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import json
import google.generativeai as genai
import requests
import random

# 1. 頁面基本設定
st.set_page_config(
    page_title="QQ音樂熱門歌曲挑選系統", page_icon="🎵", layout="wide"
)

# 2. 全域 CSS 設定：強制全站表格與內容統一靠左對齊（數值欄位亦靠左且保留數值排序特性）
st.markdown(
    """
    <style>
    /* 傳統 HTML 表格全域靠左 */
    table, th, td {
        text-align: left !important;
    }
    /* Streamlit DataFrame 標題與單元格靠左 */
    div[data-testid="stDataFrame"] div[role="columnheader"] {
        justify-content: flex-start !important;
        text-align: left !important;
    }
    div[data-testid="stDataFrame"] div[role="gridcell"] {
        justify-content: flex-start !important;
        text-align: left !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

st.title("🎵 QQ 音樂熱門歌曲挑選系統")
st.caption(
    "少即是多：專注於全網霸榜爆款、飆升黑馬與長青熱歌的智慧選曲平台。"
)

data_dir = "data"

if not os.path.exists(data_dir):
    st.error(
        "❌ 找不到 `data/` 資料夾，請確認 GitHub Actions 是否已成功抓取資料。"
    )
    st.stop()

# 使用 os.walk 遍歷所有深層目錄，抓取格式為 YYYY-MM-DD 的日期資料夾
date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
found_dates = []
for root, dirs, _ in os.walk(data_dir):
    for d in dirs:
        if date_pattern.match(d):
            found_dates.append(d)

dates = sorted(list(set(found_dates)), reverse=True)

if not dates:
    st.info("目前 `data/` 資料夾內尚無日期數據。")
    st.stop()

# 全域共用日期邊界物件
sorted_dates_asc = sorted(dates)
earliest_date_obj = datetime.strptime(sorted_dates_asc[0], "%Y-%m-%d").date()
latest_date_obj = datetime.strptime(sorted_dates_asc[-1], "%Y-%m-%d").date()


# 輔助函式：計算週榜期數標籤 (週四為起算點)
def get_issue_label(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    offset = (dt.weekday() - 3) % 7  # 3 代表週四
    issue_start = dt - timedelta(days=offset)
    return issue_start.strftime("%Y-%m-%d 期")


# 讀取單日所有榜單資料的輔助函式
def load_date_data(date_str):
    year_str = date_str[:4]
    month_str = date_str[:7]
    day_path = os.path.join(data_dir, year_str, month_str, date_str)
    charts = {
        "new": "新歌榜",
        "film": "影視金曲榜",
        "show": "綜藝新歌榜",
        "tik": "抖音熱歌榜",
    }
    dfs = []
    for key, name in charts.items():
        fpath = os.path.join(day_path, f"{date_str}_{key}.csv")
        if os.path.exists(fpath):
          try:
            df = pd.read_csv(fpath)
            df["榜單類型"] = name

            # 👈 【新增這段】從源頭強制將「排名」轉為乾淨的整數
            if "排名" in df.columns:
              df["排名"] = (
                  pd.to_numeric(df["排名"], errors="coerce")
                  .fillna(0)
                  .astype(int)
              )

            dfs.append(df)
          except Exception:
            pass
    if dfs:
        return pd.concat(dfs, ignore_index=True)
    return pd.DataFrame()


# 輔助函式：轉碼 CSV 匯出專用（純淨 4 直欄：歌名、歌手、專輯、發行日期）
def get_clean_export_df(source_df, filtered_songs_df):
    if source_df.empty or filtered_songs_df.empty:
        return pd.DataFrame(columns=["歌名", "歌手", "專輯", "發行日期"])

    song_col = (
        "歌名"
        if "歌名" in source_df.columns
        else ("song" if "song" in source_df.columns else None)
    )
    singer_col = (
        "歌手"
        if "歌手" in source_df.columns
        else ("singer" if "singer" in source_df.columns else None)
    )

    if not song_col or not singer_col:
        return filtered_songs_df

    target_order = ["歌名", "歌手", "專輯", "發行日期"]
    col_map = {}
    for target in target_order:
        if target in source_df.columns:
            col_map[target] = target
        elif target == "歌名" and "song" in source_df.columns:
            col_map["song"] = "歌名"
        elif target == "歌手" and "singer" in source_df.columns:
            col_map["singer"] = "歌手"
        elif target == "專輯" and "album" in source_df.columns:
            col_map["album"] = "專輯"
        elif target == "發行日期" and "public_time" in source_df.columns:
            col_map["public_time"] = "發行日期"

    cols_to_extract = list(col_map.keys())
    keys = filtered_songs_df[[song_col, singer_col]].drop_duplicates()

    merged = pd.merge(
        keys,
        source_df[cols_to_extract].drop_duplicates(
            subset=[song_col, singer_col]
        ),
        on=[song_col, singer_col],
        how="left",
    )

    merged = merged.rename(columns=col_map)
    final_cols = [c for c in target_order if c in merged.columns]
    return merged[final_cols]


# 主介面五大分頁
main_tabs = st.tabs(
    [
        "🔥 模組一：全網霸榜池",
        "👑 模組二：榜單常勝軍",
        "✏️ 模組三：ID、語言修正",
        "📊 原始榜單瀏覽",
        "📺 測試1：YT點閱率",
        "🌐 測試2：語言標籤",
    ]
)

# ==========================================
# 🏆 模組一：全網霸榜池（單榜連續神曲 - 僅限華語）
# ==========================================
with main_tabs[0]:
    st.header("🔥 模組一：全網跨榜霸榜池 (華語專屬)")
    st.markdown(
        "自動比對榜單數據，篩選出在指定區間內**單一榜單連續 $X$ 天不間斷在榜**的**華語神曲**，指標最硬不踩雷！"
    )

    # 1. 自動從 Secrets 提取 API Key 清單
    def fetch_m1_api_keys():
        raw_keys = st.secrets.get(
            "YOUTUBE_API_KEYS", st.secrets.get("YOUTUBE_API_KEY", [])
        )
        if isinstance(raw_keys, str):
            return [k.strip() for k in raw_keys.split(",") if k.strip()]
        elif isinstance(raw_keys, list):
            return [str(k).strip() for k in raw_keys if str(k).strip()]
        return []

    m1_preset = st.radio(
        "🗓️ 選擇分析時間範圍",
        [
            "⚡ 近 7 天",
            "⚡ 近 30 天",
            "📅 自訂月曆區間",
        ],
        index=1,
        horizontal=True,
        key="m1_preset_radio",
    )

    # 決定時間區間
    if m1_preset == "⚡ 近 7 天":
        start_date_obj = max(
            earliest_date_obj, latest_date_obj - timedelta(days=6)
        )
        end_date_obj = latest_date_obj
    elif m1_preset == "⚡ 近 30 天":
        start_date_obj = max(
            earliest_date_obj,
            latest_date_obj - timedelta(days=29),
        )
        end_date_obj = latest_date_obj
    else:
        date_range = st.date_input(
            "請選取月曆區間（點擊開始與結束日期）",
            value=(earliest_date_obj, latest_date_obj),
            min_value=earliest_date_obj,
            max_value=latest_date_obj,
            key="m1_date_picker",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date_obj, end_date_obj = date_range
        else:
            st.info("💡 請在月曆上選取『結束日期』以完成選擇。")
            st.stop()

    start_date = start_date_obj.strftime("%Y-%m-%d")
    end_date = end_date_obj.strftime("%Y-%m-%d")

    # 取得區間內所有存在的日期清單（已排序）
    selected_m1_dates = sorted([d for d in dates if start_date <= d <= end_date])
    X_max_days = len(selected_m1_dates)

    if X_max_days == 0:
        st.warning(f"在 {start_date} ～ {end_date} 區間內尚無榜單資料。")
    else:
        all_dfs = []
        for d in selected_m1_dates:
            d_df = load_date_data(d)
            if not d_df.empty:
                d_df["抓取日期"] = d
                all_dfs.append(d_df)

        if all_dfs:
            df_range = pd.concat(all_dfs, ignore_index=True)
            song_col = (
                "歌名"
                if "歌名" in df_range.columns
                else ("song" if "song" in df_range.columns else None)
            )
            singer_col = (
                "歌手"
                if "歌手" in df_range.columns
                else ("singer" if "singer" in df_range.columns else None)
            )

            if song_col and singer_col:
                yt_id_pair_map = {}
                lang_pair_map = {}

                def clean_key(text):
                    if pd.isna(text):
                        return ""
                    return str(text).strip().replace(" ", "").lower()

                # -------------------------------------------------------------
                # 步驟 1（中央對照表優先）：讀取 yt_mapping.csv
                # -------------------------------------------------------------
                import os
                mapping_file = "data/yt_mapping.csv"
                if os.path.exists(mapping_file):
                    try:
                        map_df = pd.read_csv(mapping_file, dtype=str).fillna("-")
                        m_song = "歌名" if "歌名" in map_df.columns else ("song" if "song" in map_df.columns else None)
                        m_singer = "歌手" if "歌手" in map_df.columns else ("singer" if "singer" in map_df.columns else None)
                        m_vid = next((c for c in ["Video ID", "YouTube ID", "YouTube_ID"] if c in map_df.columns), None)
                        m_lang = "語言" if "語言" in map_df.columns else None

                        if m_song and m_singer:
                            for _, r in map_df.iterrows():
                                s_title = clean_key(r.get(m_song, ""))
                                s_artist = clean_key(r.get(m_singer, ""))
                                
                                if m_vid:
                                    v_id = str(r.get(m_vid, "")).strip()
                                    if s_title and s_artist and v_id and v_id not in ["-", "nan", "None", ""]:
                                        yt_id_pair_map[(s_title, s_artist)] = v_id
                                
                                if m_lang:
                                    v_lang = str(r.get(m_lang, "")).strip()
                                    if s_title and s_artist and v_lang and v_lang not in ["-", "nan", "None", ""]:
                                        lang_pair_map[(s_title, s_artist)] = v_lang
                    except Exception:
                        pass

                # -------------------------------------------------------------
                # 步驟 2（每日 CSV 補缺）：對照表未命中才用每日 CSV 補填
                # -------------------------------------------------------------
                yt_id_col = next(
                    (c for c in ["YouTube ID", "YouTube_ID", "Video ID"] if c in df_range.columns),
                    None
                )
                lang_col = "語言" if "語言" in df_range.columns else None

                for _, row in df_range.iterrows():
                    s_title = clean_key(row.get(song_col, ""))
                    s_artist = clean_key(row.get(singer_col, ""))
                    key = (s_title, s_artist)

                    if yt_id_col:
                        v_id = str(row.get(yt_id_col, "")).strip()
                        if s_title and s_artist and v_id and v_id not in ["-", "nan", "None", ""]:
                            if key not in yt_id_pair_map:
                                yt_id_pair_map[key] = v_id

                    if lang_col:
                        v_lang = str(row.get(lang_col, "")).strip()
                        if s_title and s_artist and v_lang and v_lang not in ["-", "nan", "None", "", "未知"]:
                            if key not in lang_pair_map:
                                lang_pair_map[key] = v_lang

                # 計算連續天數
                def calc_max_streak(dates_present, sorted_all_dates):
                    present_set = set(dates_present)
                    max_s = 0
                    curr_s = 0
                    for d in sorted_all_dates:
                        if d in present_set:
                            curr_s += 1
                            if curr_s > max_s:
                                max_s = curr_s
                        else:
                            curr_s = 0
                    return max_s

                # -------------------------------------------------------------
                # 步驟 3：依歌曲分組計算 streak 並進行華語過濾
                # -------------------------------------------------------------
                records = []
                for (song, singer), sub_df in df_range.groupby([song_col, singer_col]):
                    clean_s = clean_key(song)
                    clean_a = clean_key(singer)
                    
                    # 從「中央優先 ➔ 每日補缺」字典取得語言標籤
                    song_lang = lang_pair_map.get((clean_s, clean_a), "未知")

                    # 若語言不是「華語」，直接跳過該歌曲！
                    if str(song_lang).strip() != "華語":
                        continue

                    history_charts = "、".join(sorted(sub_df["榜單類型"].unique()))

                    chart_streaks = {}
                    for chart_name, chart_sub in sub_df.groupby("榜單類型"):
                        c_dates = chart_sub["抓取日期"].unique()
                        chart_streaks[chart_name] = calc_max_streak(c_dates, selected_m1_dates)

                    if chart_streaks:
                        max_single_streak = max(chart_streaks.values())
                        best_charts = [c for c, s in chart_streaks.items() if s == max_single_streak]
                        continuous_charts = "、".join(sorted(best_charts))
                    else:
                        max_single_streak = 0
                        continuous_charts = "-"

                    if max_single_streak == X_max_days:
                        yt_id = yt_id_pair_map.get((clean_s, clean_a), None)

                        records.append(
                            {
                                "歌名": song,
                                "歌手": singer,
                                "語言": song_lang,
                                "即時點閱率": None,
                                "連續在榜天數": max_single_streak,
                                "最大連續出現榜單": continuous_charts,
                                "歷史出現榜單": history_charts,
                                "Youtube Id": yt_id,
                            }
                        )

                if records:
                    multi_chart = pd.DataFrame(records)

                    # 按鈕觸發：連線 API 抓取此刻即時點閱
                    btn_fetch_realtime = st.button("🔄 抓取此刻即時點閱 (YouTube API)")

                    if btn_fetch_realtime:
                        api_keys = fetch_m1_api_keys()
                        if not api_keys:
                            st.warning("⚠️ 未在 Secrets 中設定 `YOUTUBE_API_KEY` 或 `YOUTUBE_API_KEYS`，無法抓取即時點閱。")
                        else:
                            valid_ids = multi_chart["Youtube Id"].dropna().unique().tolist()
                            valid_ids = [v for v in valid_ids if str(v).strip() not in ["-", "nan", "None", ""]]

                            realtime_views_map = {}
                            current_key_idx = 0
                            fetch_success = False

                            # 多 Key 輪替批次請求機制
                            while current_key_idx < len(api_keys) and not fetch_success:
                                current_key = api_keys[current_key_idx]
                                try:
                                    batch_size = 50
                                    for i in range(0, len(valid_ids), batch_size):
                                        batch_ids = valid_ids[i:i + batch_size]
                                        api_url = f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={','.join(batch_ids)}&key={current_key}"
                                        res = requests.get(api_url, timeout=5)

                                        if res.status_code == 200:
                                            data = res.json()
                                            for item in data.get("items", []):
                                                vid = item.get("id")
                                                v_cnt = item.get("statistics", {}).get("viewCount")
                                                if v_cnt:
                                                    realtime_views_map[vid] = int(v_cnt)
                                        else:
                                            raise Exception(f"HTTP {res.status_code}")

                                    fetch_success = True
                                except Exception:
                                    current_key_idx += 1

                            if fetch_success:
                                multi_chart["即時點閱率"] = multi_chart["Youtube Id"].map(realtime_views_map)
                                st.toast("✅ 已成功載入此刻最新即時點閱！")
                            else:
                                st.error("❌ 所有 API Key 今日配額皆已耗盡或連線失敗。")

                    # 處理 YouTube 連結
                    def build_yt_url(val):
                        v = str(val).strip() if pd.notna(val) else ""
                        if v and v not in ["-", "nan", "None", ""]:
                            return f"https://www.youtube.com/watch?v={v}"
                        return None

                    multi_chart["影片連結"] = multi_chart["Youtube Id"].apply(build_yt_url)

                    # 依「即時點閱率」由高到低排序
                    if "即時點閱率" in multi_chart.columns:
                        multi_chart = multi_chart.sort_values(
                            by=["即時點閱率"], ascending=[False], na_position="last"
                        )

                    display_cols = [
                        "歌名",
                        "歌手",
                        "即時點閱率",
                        "最大連續出現榜單",
                        "歷史出現榜單",
                        "影片連結",
                    ]
                    display_chart = multi_chart[display_cols]

                    st.success(
                        f"🎯 涵蓋區間：{start_date} ～ {end_date}（涵蓋 {X_max_days} 天數據，目標連續天數 $X = {X_max_days}$），共找到 {len(display_chart)} 首華語單榜全程連續霸榜神曲！"
                    )
                    st.dataframe(
                        display_chart,
                        column_config={
                            "即時點閱率": st.column_config.NumberColumn(
                                "即時點閱率", format="%,d", width="small", help="點擊上方按鈕後即時更新數據"
                            ),
                            "影片連結": st.column_config.LinkColumn(
                                "影片連結",
                                display_text="點此觀看",
                                help="點擊前往 YouTube 觀看 MV",
                                width="small",
                            ),
                        },
                        hide_index=True,
                        use_container_width=True,
                    )

                    # 💡 【修改點】：明確指定 CSV 匯出的 6 個欄位
                    export_cols = [
                        "歌名",
                        "歌手",
                        "即時點閱率",
                        "最大連續出現榜單",
                        "歷史出現榜單",
                        "Youtube Id",
                    ]
                    export_df = multi_chart[export_cols]

                    csv_data = export_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        label="📥 匯出連續霸榜池清單 (CSV)",
                        data=csv_data,
                        file_name=f"QQ音樂_華語連續霸榜池_{start_date}_至_{end_date}.csv",
                        mime="text/csv",
                        key="m1_download_range",
                    )
                else:
                    st.info(
                        f"在 {start_date} ～ {end_date} 區間內（$X = {X_max_days}$ 天），暫無華語歌曲達到連續 $X$ 天皆在榜。"
                    )
            else:
                st.warning(
                    "數據欄位解析異常，請確認 CSV 欄位是否包含『歌名』與『歌手』。"
                )
        else:
            st.warning(f"在 {start_date} ～ {end_date} 區間內尚無榜單資料。")
            
# ==========================================
# 👑 模組二：榜單常勝軍（長青熱歌 - 僅限華語）
# ==========================================
with main_tabs[1]:
    st.header("👑 模組二：榜單常勝軍（長青熱歌 - 華語專屬）")
    st.markdown(
        "統計**指定日期區間**內，在個別榜單的累積天數表現（僅顯示**華語歌曲**）。"
    )

    chart_option_m2 = st.radio(
        "選擇要統計常勝軍的榜單",
        ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"],
        horizontal=True,
        key="m2_radio",
    )

    m2_preset = st.radio(
        "🗓️ 選擇統計時間範圍",
        ["⚡ 近 7 天", "⚡ 近 30 天", "🌐 全部歷史區間", "📅 自訂月曆區間"],
        index=1,
        horizontal=True,
        key="m2_preset_radio",
    )

    if m2_preset == "⚡ 近 7 天":
        start_date_obj = max(earliest_date_obj, latest_date_obj - timedelta(days=6))
        end_date_obj = latest_date_obj
    elif m2_preset == "⚡ 近 30 天":
        start_date_obj = max(earliest_date_obj, latest_date_obj - timedelta(days=29))
        end_date_obj = latest_date_obj
    elif m2_preset == "🌐 全部歷史區間":
        start_date_obj = earliest_date_obj
        end_date_obj = latest_date_obj
    else:
        date_range = st.date_input(
            "請選取月曆區間（點擊開始與結束日期）",
            value=(earliest_date_obj, latest_date_obj),
            min_value=earliest_date_obj,
            max_value=latest_date_obj,
            key="m2_date_picker",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date_obj, end_date_obj = date_range
        else:
            st.info("💡 請在月曆上選取『結束日期』以完成選擇。")
            st.stop()

    start_date = start_date_obj.strftime("%Y-%m-%d")
    end_date = end_date_obj.strftime("%Y-%m-%d")

    selected_m2_dates = [d for d in dates if start_date <= d <= end_date]

    all_dfs = []
    for d in selected_m2_dates:
        d_df = load_date_data(d)
        if not d_df.empty:
            d_df["抓取日期"] = d
            all_dfs.append(d_df)

    if all_dfs:
        full_df = pd.concat(all_dfs, ignore_index=True)
        song_col = "歌名" if "歌名" in full_df.columns else "song"
        singer_col = "歌手" if "歌手" in full_df.columns else "singer"

        yt_id_pair_map = {}
        lang_pair_map = {}

        def clean_key(text):
            if pd.isna(text):
                return ""
            return str(text).strip().replace(" ", "").lower()

        # 1. 讀取中央對照表
        import os
        mapping_file = "data/yt_mapping.csv"
        
        if os.path.exists(mapping_file):
            try:
                map_df = pd.read_csv(mapping_file, dtype=str).fillna("-")
                m_song = "歌名" if "歌名" in map_df.columns else ("song" if "song" in map_df.columns else None)
                m_singer = "歌手" if "歌手" in map_df.columns else ("singer" if "singer" in map_df.columns else None)
                m_vid = next((c for c in ["Video ID", "YouTube ID", "YouTube_ID"] if c in map_df.columns), None)
                m_lang = "語言" if "語言" in map_df.columns else None

                if m_song and m_singer:
                    for _, r in map_df.iterrows():
                        s_title = clean_key(r.get(m_song, ""))
                        s_artist = clean_key(r.get(m_singer, ""))
                        
                        if m_vid:
                            v_id = str(r.get(m_vid, "")).strip()
                            if s_title and s_artist and v_id and v_id not in ["-", "nan", "None", ""]:
                                yt_id_pair_map[(s_title, s_artist)] = v_id
                        
                        if m_lang:
                            v_lang = str(r.get(m_lang, "")).strip()
                            if s_title and s_artist and v_lang and v_lang not in ["-", "nan", "None", ""]:
                                lang_pair_map[(s_title, s_artist)] = v_lang
            except Exception:
                pass

        # 2. 補強 ID 與 語言
        yt_id_col = next(
            (c for c in ["YouTube ID", "YouTube_ID", "Video ID"] if c in full_df.columns),
            None
        )
        lang_col = "語言" if "語言" in full_df.columns else None

        for _, row in full_df.iterrows():
            s_title = clean_key(row.get(song_col, ""))
            s_artist = clean_key(row.get(singer_col, ""))
            key = (s_title, s_artist)

            if yt_id_col:
                v_id = str(row.get(yt_id_col, "")).strip()
                if s_title and s_artist and v_id and v_id not in ["-", "nan", "None", ""]:
                    if key not in yt_id_pair_map:
                        yt_id_pair_map[key] = v_id

            if lang_col:
                v_lang = str(row.get(lang_col, "")).strip()
                if s_title and s_artist and v_lang and v_lang not in ["-", "nan", "None", "", "未知"]:
                    if key not in lang_pair_map:
                        lang_pair_map[key] = v_lang

        target_df = full_df[full_df["榜單類型"] == chart_option_m2].copy()

        if not target_df.empty:
            target_df = target_df.sort_values(by="抓取日期", ascending=True)

            evergreen = (
                target_df.groupby([song_col, singer_col])
                .agg(累積上榜天數=("抓取日期", "nunique"))
                .reset_index()
                .sort_values(by=["累積上榜天數"], ascending=[False])
            )

            # 初始將即時點閱率統一設為 None (同模組一邏輯)
            evergreen["即時點閱率"] = None

            if not evergreen.empty:
                # 3. 語言判定與過濾（僅保留「華語」）
                def get_song_lang(row):
                    s = clean_key(row[song_col])
                    a = clean_key(row[singer_col])
                    return lang_pair_map.get((s, a), "未知")

                evergreen["語言"] = evergreen.apply(get_song_lang, axis=1)
                evergreen = evergreen[evergreen["語言"] == "華語"].copy()

            if not evergreen.empty:
                # 4. 反查 YouTube ID 與 建立播放連結
                def get_yt_id(row):
                    s = clean_key(row[song_col])
                    a = clean_key(row[singer_col])
                    return yt_id_pair_map.get((s, a), None)

                evergreen["YouTube ID"] = evergreen.apply(get_yt_id, axis=1)

                def build_yt_url(val):
                    v = str(val).strip() if pd.notna(val) else ""
                    if v and v not in ["-", "nan", "None", ""]:
                        return f"https://www.youtube.com/watch?v={v}"
                    return None

                evergreen["影片連結"] = evergreen["YouTube ID"].apply(build_yt_url)

                # 5. 直連 YouTube API 抓取按鈕 (與模組一完全一致)
                if "m2_live_views" not in st.session_state:
                    st.session_state["m2_live_views"] = {}

                fetch_m2_click = st.button("🔄 抓取此刻即時點閱 (YouTube API)", key="m2_fetch_yt_btn")

                if fetch_m2_click:
                    raw_keys = st.secrets.get("YOUTUBE_API_KEYS", st.secrets.get("YOUTUBE_API_KEY", []))
                    if isinstance(raw_keys, str):
                        api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
                    elif isinstance(raw_keys, list):
                        api_keys = [str(k).strip() for k in raw_keys if str(k).strip()]
                    else:
                        api_keys = []

                    if not api_keys:
                        st.warning("⚠️ 未在 Secrets 中設定 `YOUTUBE_API_KEY`，無法抓取即時點閱。")
                    else:
                        valid_ids = [
                            str(v).strip() for v in evergreen["YouTube ID"].dropna().unique()
                            if str(v).strip() and str(v).strip() not in ["-", "nan", "None", ""]
                        ]
                        
                        if valid_ids:
                            with st.spinner("正在透過 YouTube API 抓取最新即時點閱數..."):
                                current_key_idx = 0
                                fetch_success = False

                                while current_key_idx < len(api_keys) and not fetch_success:
                                    current_key = api_keys[current_key_idx]
                                    try:
                                        batch_size = 50
                                        for i in range(0, len(valid_ids), batch_size):
                                            batch_ids = valid_ids[i:i + batch_size]
                                            api_url = f"https://www.googleapis.com/youtube/v3/videos?part=statistics&id={','.join(batch_ids)}&key={current_key}"
                                            res = requests.get(api_url, timeout=5)

                                            if res.status_code == 200:
                                                data = res.json()
                                                for item in data.get("items", []):
                                                    vid = item.get("id")
                                                    v_cnt = item.get("statistics", {}).get("viewCount")
                                                    if v_cnt:
                                                        st.session_state["m2_live_views"][vid] = int(v_cnt)
                                            else:
                                                raise Exception(f"HTTP {res.status_code}")

                                        fetch_success = True
                                    except Exception:
                                        current_key_idx += 1

                                if fetch_success:
                                    st.toast("✅ 已成功載入此刻最新即時點閱！")
                                else:
                                    st.error("❌ 所有 API Key 今日配額皆已耗盡或連線失敗。")
                        else:
                            st.warning("⚠️ 目前無有效的 YouTube ID 可供查詢。")

                # 若已抓取過即時點閱，映射回表格
                if st.session_state["m2_live_views"]:
                    evergreen["即時點閱率"] = evergreen["YouTube ID"].map(st.session_state["m2_live_views"])

                cols_order = [
                    song_col,
                    singer_col,
                    "即時點閱率",
                    "累積上榜天數",
                    "影片連結",
                ]
                evergreen_display = evergreen[cols_order]

            total_days = target_df["抓取日期"].nunique()

            if not evergreen.empty:
                st.success(
                    f"📈【{chart_option_m2}】統計區間：{start_date} ～ {end_date}（涵蓋 {total_days} 天，共 {len(evergreen)} 首華語歌曲）："
                )

                st.dataframe(
                    evergreen_display,
                    column_config={
                        "即時點閱率": st.column_config.NumberColumn(
                            "即時點閱率", format="%,d", width="small", help="點擊上方按鈕後即時更新數據"
                        ),
                        "累積上榜天數": st.column_config.NumberColumn(
                            "累積上榜天數", format="%d", width="small"
                        ),
                        "影片連結": st.column_config.LinkColumn(
                            "影片連結",
                            display_text="點此觀看",
                            help="點擊前往 YouTube 觀看 MV",
                            width="small",
                        ),
                    },
                    hide_index=True,
                    use_container_width=True,
                )

                # 💡 【修改點】：明確映射並指定 CSV 匯出的 5 個欄位
                export_m2_df = evergreen.copy()
                export_m2_df["歌名"] = export_m2_df[song_col]
                export_m2_df["歌手"] = export_m2_df[singer_col]
                export_m2_df["Youtube Id"] = export_m2_df["YouTube ID"]

                export_cols = [
                    "歌名",
                    "歌手",
                    "即時點閱率",
                    "累積上榜天數",
                    "Youtube Id",
                ]
                export_m2_df = export_m2_df[export_cols]

                csv_data = export_m2_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    label=f"📥 匯出【{chart_option_m2}】華語常勝軍清單 (CSV)",
                    data=csv_data,
                    file_name=f"QQ音樂_華語榜單常勝軍_{chart_option_m2}_{start_date}_至_{end_date}.csv",
                    mime="text/csv",
                    key="m2_download",
                )
            else:
                st.info(
                    f"在 {start_date} ～ {end_date} 區間內，【{chart_option_m2}】尚無華語歌曲上榜。"
                )
        else:
            st.info(
                f"在 {start_date} ～ {end_date} 區間內，尚無【{chart_option_m2}】的數據。"
            )
    else:
        st.info("選定日期區間內無數據。")

# ==========================================
# ✏️ 模組三：指定歌曲手動修正 (正確狀態切換與訊息顯示)
# ==========================================
with main_tabs[2]:
    st.header("✏️ 指定歌曲欄位手動修正")
    st.markdown("雙階段查詢與鎖定流程：查詢解鎖 ➔ 修改儲存 ➔ 鎖定並保留結果顯示（可選擇重新編輯或清除重置）。")

    mapping_file = "data/yt_mapping.csv"

    # 1. 初始化 Session State 狀態
    if "m3_stage" not in st.session_state:
        st.session_state["m3_stage"] = "init"  # 狀態：init（未查詢）, editing（可編輯）, submitted（已提交鎖定）
    if "m3_song" not in st.session_state:
        st.session_state["m3_song"] = ""
    if "m3_singer" not in st.session_state:
        st.session_state["m3_singer"] = ""
    if "m3_vid" not in st.session_state:
        st.session_state["m3_vid"] = ""
    if "m3_lang" not in st.session_state:
        st.session_state["m3_lang"] = "華語"
    if "m3_match_idx" not in st.session_state:
        st.session_state["m3_match_idx"] = None
    if "m3_last_msg" not in st.session_state:
        st.session_state["m3_last_msg"] = ""
    if "m3_github_msg" not in st.session_state:
        st.session_state["m3_github_msg"] = ""

    # 2. 讀取現有對照表
    map_df = pd.DataFrame(columns=["歌名", "歌手", "Video ID", "語言"])
    if os.path.exists(mapping_file):
        try:
            map_df = pd.read_csv(mapping_file, dtype=str).fillna("")
        except Exception:
            pass

    song_col_name = "歌名" if "歌名" in map_df.columns else ("song" if "song" in map_df.columns else "歌名")
    singer_col_name = "歌手" if "歌手" in map_df.columns else ("singer" if "singer" in map_df.columns else "歌手")
    vid_col_name = next((c for c in ["Video ID", "YouTube ID", "YouTube_ID"] if c in map_df.columns), "Video ID")
    lang_col_name = "語言" if "語言" in map_df.columns else "語言"

    # 3. 第一階段：查詢表單
    st.subheader("1️⃣ 第一步：查詢歌曲紀錄")
    with st.form("m3_search_form"):
        col1, col2 = st.columns(2)
        with col1:
            search_song = st.text_input("🎵 輸入歌名", value=st.session_state["m3_song"], placeholder="例如：晴天")
        with col2:
            search_singer = st.text_input("🎤 輸入歌手", value=st.session_state["m3_singer"], placeholder="例如：周杰倫")
        
        search_submitted = st.form_submit_button("🔍 查詢舊紀錄", type="primary")

    if search_submitted:
        clean_s = search_song.strip()
        clean_a = search_singer.strip()
        
        if not clean_s or not clean_a:
            st.warning("⚠️ 請完整填寫「歌名」與「歌手」後再進行查詢！")
        else:
            match_idx = None
            found_vid = ""
            found_lang = "華語"
            
            for idx, row in map_df.iterrows():
                r_song = str(row.get(song_col_name, "")).strip().lower()
                r_singer = str(row.get(singer_col_name, "")).strip().lower()
                if r_song == clean_s.lower() and r_singer == clean_a.lower():
                    match_idx = idx
                    found_vid = str(row.get(vid_col_name, "")).strip()
                    found_lang = str(row.get(lang_col_name, "")).strip()
                    break

            st.session_state["m3_song"] = clean_s
            st.session_state["m3_singer"] = clean_a
            st.session_state["m3_match_idx"] = match_idx
            st.session_state["m3_vid"] = found_vid
            st.session_state["m3_lang"] = found_lang if found_lang else "華語"
            st.session_state["m3_stage"] = "editing"
            st.rerun()  # 必要：狀態變更，即刻重新繪製 UI 解鎖下方輸入框

    st.markdown("---")

    # 4. 第二階段：修改與儲存表單
    st.subheader("2️⃣ 第二步：修改資料並寫入")

    lang_options = ["華語", "西洋", "韓語", "日語", "其它"]
    is_editing = (st.session_state["m3_stage"] == "editing")
    is_submitted = (st.session_state["m3_stage"] == "submitted")
    has_data = is_editing or is_submitted

    # 上方提示：僅在「編輯中 (editing)」時顯示舊紀錄訊息，提交完成後隱藏
    if is_editing:
        if st.session_state["m3_match_idx"] is not None:
            st.info(f"💡 **已找到現有紀錄**：《{st.session_state['m3_song']} - {st.session_state['m3_singer']}》｜ 目前 Video ID：`{st.session_state['m3_vid'] or '無'}` ｜ 目前語言：`{st.session_state['m3_lang']}`")
        else:
            st.caption(f"ℹ️ **查無舊紀錄**：《{st.session_state['m3_song']} - {st.session_state['m3_singer']}》提交後將自動新增至對照表。")
    elif not is_submitted:
        st.warning("👈 請先完成第一步的歌曲查詢，下方修改區將自動解鎖。")

    default_lang_idx = lang_options.index(st.session_state["m3_lang"]) if st.session_state["m3_lang"] in lang_options else 0

    with st.form("m3_update_form"):
        col3, col4 = st.columns(2)
        with col3:
            new_video_id = st.text_input(
                "📺 Video ID（若留空將保留原紀錄）",
                value=st.session_state["m3_vid"] if has_data else "",
                placeholder="例如：dQw4w9WgXcQ",
                disabled=not is_editing
            )
        with col4:
            new_language = st.selectbox(
                "🌐 語言標籤",
                options=lang_options,
                index=default_lang_idx if has_data else 0,
                disabled=not is_editing
            )

        update_submitted = st.form_submit_button(
            "💾 立即更新寫入對照表",
            type="primary",
            disabled=not is_editing
        )

    # 處理寫入邏輯
    if update_submitted and is_editing:
        os.makedirs(os.path.dirname(mapping_file), exist_ok=True)

        for col in [song_col_name, singer_col_name, vid_col_name, lang_col_name]:
            if col not in map_df.columns:
                map_df[col] = ""

        song_title = st.session_state["m3_song"]
        singer_title = st.session_state["m3_singer"]
        match_idx = st.session_state["m3_match_idx"]
        existing_vid = st.session_state["m3_vid"]

        final_vid = new_video_id.strip() if new_video_id.strip() else existing_vid

        if match_idx is not None:
            map_df.at[match_idx, vid_col_name] = final_vid
            map_df.at[match_idx, lang_col_name] = new_language.strip()
        else:
            new_row = {
                song_col_name: song_title,
                singer_col_name: singer_title,
                vid_col_name: final_vid,
                lang_col_name: new_language.strip()
            }
            map_df = pd.concat([map_df, pd.DataFrame([new_row])], ignore_index=True)

        # 本地與 GitHub 同步
        map_df.to_csv(mapping_file, index=False, encoding="utf-8-sig")

        github_status = ""
        if "github" in st.secrets:
            try:
                from github import Github
                g = Github(st.secrets["github"]["token"])
                repo = g.get_repo(st.secrets["github"]["repo"])
                
                contents = repo.get_contents("data/yt_mapping.csv")
                updated_csv_content = map_df.to_csv(index=False, encoding="utf-8-sig")
                
                repo.update_file(
                    path="data/yt_mapping.csv",
                    message=f"Update yt_mapping.csv: {song_title} - {singer_title}",
                    content=updated_csv_content,
                    sha=contents.sha,
                    branch="main"
                )
                github_status = "🚀 已成功同步推送到 GitHub 倉庫！"
            except Exception as e:
                github_status = f"❌ GitHub 同步失敗：{e}"

        # 狀態更新並寫入顯示訊息
        st.session_state["m3_vid"] = final_vid
        st.session_state["m3_lang"] = new_language.strip()
        st.session_state["m3_last_msg"] = f"✅ 已將 Video ID 更新為：`{final_vid or '（無）'}` ｜ 語言標籤更新為：`{new_language.strip()}`"
        st.session_state["m3_github_msg"] = github_status
        st.session_state["m3_stage"] = "submitted"
        st.rerun()  # 必要：狀態變更，即刻重跑以繪製上方的鎖定框與下方的成功訊息

    # 5. 下方結果顯示與控制按鈕
    if st.session_state["m3_stage"] == "submitted":
        st.success(st.session_state["m3_last_msg"])
        if "❌" in st.session_state.get("m3_github_msg", ""):
            st.error(st.session_state["m3_github_msg"])
        
        st.markdown("---")
        btn_col1, btn_col2, _ = st.columns([1.5, 1.5, 3])
        with btn_col1:
            if st.button("✏️ 重新編輯這首歌", key="m3_reedit_btn", use_container_width=True):
                st.session_state["m3_stage"] = "editing"
                st.rerun()
        with btn_col2:
            if st.button("🧹 清除重置（下一首）", key="m3_clear_all_btn", use_container_width=True):
                st.session_state["m3_stage"] = "init"
                st.session_state["m3_song"] = ""
                st.session_state["m3_singer"] = ""
                st.session_state["m3_vid"] = ""
                st.session_state["m3_lang"] = "華語"
                st.session_state["m3_match_idx"] = None
                st.session_state["m3_last_msg"] = ""
                st.session_state["m3_github_msg"] = ""
                st.rerun()

# ==========================================
# 📊 原始榜單瀏覽
# ==========================================
with main_tabs[3]:
    st.header("📊 原始各榜單數據瀏覽")

    selected_date_obj = st.date_input(
        "📅 選擇基準日期 (預設為最新數據)",
        value=latest_date_obj,
        min_value=earliest_date_obj,
        max_value=latest_date_obj,
        key="m5_date_picker",
    )
    selected_date = (
        selected_date_obj.strftime("%Y-%m-%d")
        if isinstance(selected_date_obj, date)
        else None
    )
    if selected_date and selected_date not in dates:
        valid_dates = [d for d in dates if d <= selected_date]
        selected_date = valid_dates[0] if valid_dates else dates[0]

    if selected_date:
        charts = {
            "新歌榜 (日榜)": "new",
            "影視金曲榜 (週榜)": "film",
            "綜藝新歌榜 (週榜)": "show",
            "抖音熱歌榜 (週榜)": "tik",
        }

        sub_tabs = st.tabs(list(charts.keys()))
        year_str = selected_date[:4]
        month_str = selected_date[:7]
        day_path = os.path.join(data_dir, year_str, month_str, selected_date)

        for tab, (chart_name, chart_key) in zip(sub_tabs, charts.items()):
            with tab:
                file_name = f"{selected_date}_{chart_key}.csv"
                file_path = os.path.join(day_path, file_name)

                if os.path.exists(file_path):
                    # 1. 讀取與基礎清洗原始資料 df
                    df = pd.read_csv(file_path)

                    cols_to_drop = [
                        c
                        for c in ["抓取日期", "榜單類型", "榜單種類"]
                        if c in df.columns
                    ]
                    if cols_to_drop:
                        df = df.drop(columns=cols_to_drop)

                    if "排名" in df.columns:
                        df["排名"] = (
                            pd.to_numeric(df["排名"], errors="coerce")
                            .fillna(0)
                            .astype(int)
                        )

                    expected_order = [
                        "排名",
                        "歌名",
                        "歌手",
                        "專輯",
                        "發行日期",
                        "YouTube ID",
                        "點閱率",
                    ]
                    existing_order = [
                        c for c in expected_order if c in df.columns
                    ]
                    other_cols = [
                        c for c in df.columns if c not in existing_order
                    ]
                    df = df[existing_order + other_cols]

                    st.success(
                        f"📅 數據日期：{selected_date}｜共 {len(df)} 筆排名資料"
                    )

                    # 表格內即時關鍵字搜尋
                    search_term = st.text_input(
                        f"🔍 在【{chart_name}】中搜尋歌名或歌手",
                        key=f"raw_{chart_key}",
                    )
                    if search_term:
                        mask = (
                            df.astype(str)
                            .apply(
                                lambda x: x.str.contains(
                                    search_term, case=False
                                )
                            )
                            .any(axis=1)
                        )
                        df = df[mask]

                    # 2. 建立僅用於 UI 前端顯示的 df_display
                    df_display = df.copy()

                    # 【修正 1】：將點閱率轉回純數值，確保大小排序正確
                    if "點閱率" in df_display.columns:
                        df_display["點閱率"] = pd.to_numeric(
                            df_display["點閱率"].astype(str).str.replace(",", ""),
                            errors="coerce",
                        )

                    # 【修正 2】：無影片時傳回 None，避免按鈕可點擊或顯示 None 文字
                    def build_yt_url(val):
                        v = str(val).strip() if pd.notna(val) else ""
                        if v and v not in ["-", "nan", "None", ""]:
                            return f"https://www.youtube.com/watch?v={v}"
                        return None

                    if "YouTube ID" in df_display.columns:
                        df_display["影片連結"] = df_display[
                            "YouTube ID"
                        ].apply(build_yt_url)

                    # 調整欄位順序：移除 YouTube ID，把「影片連結」擺至最右側
                    display_cols = [
                        c
                        for c in df_display.columns
                        if c not in ["YouTube ID", "影片連結"]
                    ]
                    if "影片連結" in df_display.columns:
                        display_cols.append("影片連結")
                    df_display = df_display[display_cols]

                    # 3. 渲染前端表格
                    st.dataframe(
                        df_display,
                        column_config={
                            "排名": st.column_config.NumberColumn(
                                "排名", format="%,d", width="small"
                            ),
                            # 使用 NumberColumn 並指定 format="%,d"，自動幫數字加逗號且保持數值排序
                            "點閱率": st.column_config.NumberColumn(
                                "點閱率", format="%,d", width="small"
                            ),
                            "影片連結": st.column_config.LinkColumn(
                                "影片連結",
                                display_text="點此觀看",
                                help="點擊前往 YouTube 觀看 MV",
                                width="small",
                            ),
                        },
                        hide_index=True,
                        use_container_width=True,
                    )

                    # 4. 匯出按鈕
                    csv_data = df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        label=f"📥 匯出【{chart_name}】原始資料 (CSV)",
                        data=csv_data,
                        file_name=f"{selected_date}_{chart_key}.csv",
                        mime="text/csv",
                        key=f"raw_download_{chart_key}",
                    )
                else:
                    st.warning(
                        f"⚠️ {selected_date} 尚未抓取到 {chart_name} 的 CSV 檔案 ({file_name})。"
                    )
    else:
        st.info("💡 **請先選擇『基準日期』**，即可開始瀏覽原始榜單資料。")

# ==========================================
# 📺 測試1：YT點閱率（完整固定策略版）
# ==========================================
with main_tabs[4]:
    st.header("📺 模組四：YouTube 點閱測繪")
    st.markdown(
        "自動向 Git 數據源讀取最新榜單資料，並進行 YouTube 影片搜尋與點閱數據測繪。"
    )

    # --- 1. 輔助與清理函式 ---
    def parse_duration(duration_str):
        match = re.match(
            r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or ""
        )
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
        t = str(text).lower()

        # 1. 統一將特殊羅馬數字轉為半角英文字母
        t = (
            t.replace("ⅱ", "ii")
            .replace("ⅰ", "i")
            .replace("ⅲ", "iii")
            .replace("ⅳ", "iv")
        )

        # 2. 清除空格、括號與引號符號 (包含 「」 《》)
        return re.sub(r"[\s\.\-\_\(\)（）「」《》【】『』""'']", "", t)

    def extract_artist_tokens(singer):
        if not singer or str(singer).lower() in ["-", "nan", "none"]:
            return []

        singer_str = str(singer).strip()

        # 1. 關鍵修正：只用「真正的合唱分隔符」拆分（移除 \s，避免把空格當成換人）
        raw_artists = re.split(
            r"\s*[/&,\+\·\*\-\|]+\s*|\s+\b(?:feat\.?|ft\.?|X|x)\b\s*",
            singer_str,
            flags=re.IGNORECASE,
        )

        artist_groups = []

        for raw in raw_artists:
            raw = raw.strip()
            if not raw:
                continue

            group_tokens = set()

            # A. 提取整體
            group_tokens.add(zhconv.convert(raw, "zh-hans"))
            group_tokens.add(zhconv.convert(raw, "zh-hant"))

            # B. 提取去除括號後的主名稱
            clean_raw = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", raw).strip()
            if clean_raw:
                group_tokens.add(zhconv.convert(clean_raw, "zh-hans"))
                group_tokens.add(zhconv.convert(clean_raw, "zh-hant"))

                # C. 提取主名稱中的純中文部分（如 "万妮达Vinida Weng" -> "万妮达"）
                zh_only = "".join(
                    re.findall(r"[\u4e00-\u9fa5]+", clean_raw)
                ).strip()
                if len(zh_only) >= 2:
                    group_tokens.add(zhconv.convert(zh_only, "zh-hans"))
                    group_tokens.add(zhconv.convert(zh_only, "zh-hant"))

                # D. 提取主名稱中的純英文部分（如 "万妮达Vinida Weng" -> "Vinida Weng"）
                en_only = "".join(
                    re.findall(r"[a-zA-Z0-9\s]+", clean_raw)
                ).strip()
                if len(en_only) >= 2:
                    group_tokens.add(en_only)

            # E. 提取括號內的別名/綽號（如 "(LIZ)" -> "LIZ"）
            bracket_content = re.findall(r"[\(\（]([^\)\）]+)[\)\）]", raw)
            for b in bracket_content:
                b = b.strip()
                if len(b) >= 2:
                    group_tokens.add(zhconv.convert(b, "zh-hans"))
                    group_tokens.add(zhconv.convert(b, "zh-hant"))

            # 規格化，保留長度 >= 2 的有效關鍵字
            norm_group = [
                normalize_text(t)
                for t in group_tokens
                if len(normalize_text(t)) >= 2
            ]
            if norm_group:
                artist_groups.append(list(set(norm_group)))

        return artist_groups

    def build_search_queries(song, singer):
        clean_s, main_s = parse_song_title(song)
        clean_p = str(singer).strip()

        primary_query = f"{main_s} {clean_p}".strip()
        queries = [primary_query]

        primary_query_tra = f"{zhconv.convert(main_s, 'zh-hant')} {zhconv.convert(clean_p, 'zh-hant')}".strip()
        if primary_query_tra not in queries:
            queries.append(primary_query_tra)

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

    def fetch_api_keys():
        raw_keys = st.secrets.get(
            "YOUTUBE_API_KEYS", st.secrets.get("YOUTUBE_API_KEY", [])
        )
        if isinstance(raw_keys, str):
            return [k.strip() for k in raw_keys.split(",") if k.strip()]
        elif isinstance(raw_keys, list):
            return [str(k).strip() for k in raw_keys if str(k).strip()]
        return []

    # --- 2. 核心搜尋函式（viewCount 30 筆 -> relevance 5 筆） ---
    def search_youtube_video(
        song, singer, api_keys, current_key_idx, youtube_service
    ):
        clean_song, main_song = parse_song_title(song)
        search_queries = build_search_queries(song, singer)

        main_sim_norm = normalize_text(zhconv.convert(main_song, "zh-hans"))
        main_tra_norm = normalize_text(zhconv.convert(main_song, "zh-hant"))
        artist_tokens = extract_artist_tokens(singer)

        matched_info = None

        def build_yt_service(idx):
            return (
                build("youtube", "v3", developerKey=api_keys[idx])
                if idx < len(api_keys)
                else None
            )

        # 固定策略順序：先 viewCount，再 relevance
        order_strategies = ["viewCount", "relevance"]

        for order_mode in order_strategies:
            if matched_info:
                break

            # 根據搜尋模式動態設定筆數：viewCount 抓 30 筆，relevance 抓 5 筆
            max_results_val = 30 if order_mode == "viewCount" else 5

            for query_str in search_queries:
                if matched_info:
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
                                maxResults=max_results_val,
                                type="video",
                                order=order_mode,
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
                            for item in video_res.get("items", []):
                                v_id = item["id"]
                                v_title = item["snippet"]["title"]
                                channel_title = item["snippet"].get(
                                    "channelTitle", ""
                                )
                                v_desc = item["snippet"].get(
                                    "description", ""
                                )
                                v_views = int(
                                    item["statistics"].get("viewCount", 0)
                                )

                                duration_str = item.get(
                                    "contentDetails", {}
                                ).get("duration", "PT0S")
                                duration_sec = parse_duration(duration_str)

                                # 過濾短影音與過長影片 (1分5秒 ~ 8分鐘)
                                if duration_sec <= 65 or duration_sec > 480:
                                    continue

                                v_title_lower = v_title.lower()
                                v_title_norm = normalize_text(v_title)
                                channel_lower = channel_title.lower()
                                channel_norm = normalize_text(channel_title)
                                v_desc_norm = normalize_text(v_desc)

                                is_topic = (
                                    "topic" in channel_lower
                                    or "主題" in channel_lower
                                )

                                has_noise = any(
                                    nk in v_title_lower
                                    for nk in COMBINED_NOISE_KEYWORDS
                                )
                                if not is_topic and has_noise:
                                    continue

                                song_matched = (
                                    main_sim_norm in v_title_norm
                                ) or (main_tra_norm in v_title_norm)
                                if not song_matched:
                                    continue

                                v_full_text = f"{v_title_norm} {channel_norm} {v_desc_norm}"

                                # 歌手比對：同歌手內部任意名稱命中 (any)，跨歌手必須全部滿足 (all)
                                singer_matched = not artist_tokens or all(
                                    any(tkn in v_full_text for tkn in group)
                                    for group in artist_tokens
                                )

                                cand = {
                                    "id": v_id,
                                    "title": v_title,
                                    "channel": channel_title,
                                    "views": v_views,
                                    "url": f"https://www.youtube.com/watch?v={v_id}",
                                    "search_mode": order_mode,
                                }

                                if singer_matched:
                                    candidates.append(cand)

                            if candidates:
                                best = max(candidates, key=lambda x: x["views"])
                                matched_info = best

                        success = True

                    except HttpError as e:
                        is_quota_error = e.resp.status in [403, 429] or any(
                            k in str(e)
                            for k in [
                                "quotaExceeded",
                                "rateLimitExceeded",
                                "Quota exceeded",
                            ]
                        )
                        if is_quota_error:
                            current_key_idx += 1
                            youtube_service = build_yt_service(current_key_idx)
                            if not youtube_service:
                                st.error("❌ 所有 API Key 的每日額度皆已耗盡！")
                                break
                        else:
                            break
                    except Exception:
                        break

        return matched_info, current_key_idx, youtube_service

    # --- 3. UI 介面與頁籤 ---
    m4_subtab1, m4_subtab2 = st.tabs(
        ["📊 榜單批量測繪", "🔍 單首歌即時查詢"]
    )

    # ----------------------------------------------------
    # 功能 1：榜單批量測繪
    # ----------------------------------------------------
    with m4_subtab1:
        m4_col_a, m4_col_b = st.columns(2)
        with m4_col_a:
            m4_date_obj = st.date_input(
                "📅 選擇榜單數據日期",
                value=latest_date_obj,
                min_value=earliest_date_obj,
                max_value=latest_date_obj,
                key="m4_date_picker",
            )
            m4_date = (
                m4_date_obj.strftime("%Y-%m-%d")
                if isinstance(m4_date_obj, date)
                else dates[0]
            )
            if m4_date not in dates:
                valid_dates = [d for d in dates if d <= m4_date]
                m4_date = valid_dates[0] if valid_dates else dates[0]

        with m4_col_b:
            m4_chart_type = st.selectbox(
                "🎵 選擇榜單類型",
                [
                    "新歌榜",
                    "影視金曲榜",
                    "綜藝新歌榜",
                    "抖音熱歌榜",
                    "全部榜單",
                ],
                key="m4_chart_select",
            )

        col1, col2 = st.columns([2, 1])
        with col1:
            test_limit = st.slider(
                "選擇要測試的歌曲數量",
                min_value=1,
                max_value=50,
                value=10,
                key="m4_test_limit_slider",
            )
        with col2:
            start_btn = st.button(
                "🚀 開始執行 YouTube API 點閱測繪",
                type="primary",
                key="m4_start_btn",
            )

        if start_btn:
            api_keys = fetch_api_keys()
            if not api_keys:
                st.error(
                    "❌ 未找到有效的 API Key，請先在 Streamlit Cloud Secrets"
                    " 設定 `YOUTUBE_API_KEYS`！"
                )
                st.stop()

            df_curr = load_date_data(m4_date)

            if not df_curr.empty and m4_chart_type != "全部榜單":
                df_target = df_curr[
                    df_curr["榜單類型"] == m4_chart_type
                ].copy()
            else:
                df_target = df_curr.copy()

            if df_target is None or df_target.empty:
                st.error(
                    f"❌ 無法讀取 `{m4_date}` 的榜單資料，請確認 GitHub"
                    " Actions 是否已下載該日期之數據。"
                )
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()

                results = []
                test_songs = df_target.head(test_limit)

                current_key_idx = 0
                youtube_service = (
                    build("youtube", "v3", developerKey=api_keys[0])
                    if api_keys
                    else None
                )

                for idx, row in test_songs.reset_index(drop=True).iterrows():
                    song = str(
                        row.get(
                            "歌名",
                            row.get("song", row.get("歌曲名稱", "Unknown")),
                        )
                    ).strip()
                    singer = str(
                        row.get(
                            "歌手",
                            row.get("singer", row.get("歌手名稱", "Unknown")),
                        )
                    ).strip()

                    try:
                        rank = int(row.get("排名", 0))
                        if rank <= 0:
                            rank = idx + 1
                    except (ValueError, TypeError):
                        rank = idx + 1

                    status_text.text(
                        f"🔍 ({idx+1}/{test_limit}) 正在檢索點閱：{song} -"
                        f" {singer} [Key {current_key_idx + 1}/{len(api_keys)}]"
                    )
                    progress_bar.progress((idx + 1) / test_limit)

                    matched, current_key_idx, youtube_service = (
                        search_youtube_video(
                            song,
                            singer,
                            api_keys,
                            current_key_idx,
                            youtube_service,
                        )
                    )

                    results.append({
                        "榜單排名": rank,
                        "歌名": song,
                        "歌手": singer,
                        "Video ID": matched["id"] if matched else "-",
                        "YT 觀看次數": int(matched["views"]) if matched else 0,
                        "YT 影片標題": matched["title"] if matched else "-",
                        "搜尋模式": (
                            "觀看量優先"
                            if matched
                            and matched.get("search_mode") == "viewCount"
                            else (
                                "相關性補救"
                                if matched
                                and matched.get("search_mode") == "relevance"
                                else "-"
                            )
                        ),
                        "影片連結": matched["url"] if matched else "-",
                    })

                    time.sleep(0.1)

                status_text.success("✅ 點閱測繪完成！")
                progress_bar.progress(100)

                df_result = pd.DataFrame(results)

                display_cols = [
                    "榜單排名",
                    "歌名",
                    "歌手",
                    "Video ID",
                    "YT 觀看次數",
                    "YT 影片標題",
                    "搜尋模式",
                ]
                df_display = df_result[display_cols].copy()

                st.dataframe(
                    df_display, use_container_width=True, hide_index=True
                )

                csv_data = df_result.to_csv(index=False, encoding="utf-8-sig")
                st.download_button(
                    label="📥 匯出 YouTube ID 綁定對照表 (CSV)",
                    data=csv_data,
                    file_name=f"youtube_mapping_{m4_date}.csv",
                    mime="text/csv",
                    key="m4_download_csv",
                )

    # ----------------------------------------------------
    # 功能 2：單首歌即時查詢
    # ----------------------------------------------------
    with m4_subtab2:
        st.subheader("🔍 單首歌即時 YouTube 點閱查詢")
        st.markdown(
            "輸入任意歌曲與歌手，立即透過 API 進行比對、檢索最佳 YouTube"
            " 影片與實時觀看次數。"
        )

        m4_single_col1, m4_single_col2 = st.columns(2)
        with m4_single_col1:
            single_song = st.text_input(
                "🎵 歌名", placeholder="例如：터널", key="m4_single_song_input"
            )
        with m4_single_col2:
            single_singer = st.text_input(
                "🎤 歌手",
                placeholder="例如：丽兹 (LIZ)",
                key="m4_single_singer_input",
            )

        single_search_btn = st.button(
            "🔍 開始單曲查詢", type="primary", key="m4_single_search_btn"
        )

        if single_search_btn:
            if not single_song.strip():
                st.warning("⚠️ 請輸入歌名再進行查詢！")
            else:
                api_keys = fetch_api_keys()
                if not api_keys:
                    st.error(
                        "❌ 未找到有效的 API Key，請先在 Streamlit Cloud"
                        " Secrets 設定 `YOUTUBE_API_KEYS`！"
                    )
                    st.stop()

                song = single_song.strip()
                singer = single_singer.strip()

                with st.spinner(
                    f"正在檢索《{song}》- {singer or '未指定歌手'} 的 YouTube"
                    " 點閱..."
                ):
                    current_key_idx = 0
                    youtube_service = (
                        build("youtube", "v3", developerKey=api_keys[0])
                        if api_keys
                        else None
                    )

                    matched, _, _ = search_youtube_video(
                        song, singer, api_keys, current_key_idx, youtube_service
                    )

                    if matched:
                        mode_desc = (
                            "觀看量優先"
                            if matched["search_mode"] == "viewCount"
                            else "相關性補救"
                        )
                        st.success(
                            f"🎉 成功找到最佳匹配影片！（命中機制：{mode_desc}）"
                        )

                        res_m1, res_m2, res_m3 = st.columns(3)
                        res_m1.metric("Video ID", matched["id"])
                        res_m2.metric("YT 觀看次數", f"{matched['views']:,}")
                        res_m3.metric("頻道名稱", matched["channel"])

                        st.write(f"**影片標題：** {matched['title']}")
                        st.write(
                            f"**影片連結：** [{matched['url']}]({matched['url']})"
                        )

                        df_single_res = pd.DataFrame([{
                            "歌名": song,
                            "歌手": singer or "-",
                            "Video ID": matched["id"],
                            "YT 觀看次數": int(matched["views"]),
                            "YT 影片標題": matched["title"],
                            "頻道名稱": matched["channel"],
                            "搜尋模式": mode_desc,
                            "影片連結": matched["url"],
                        }])
                        st.dataframe(
                            df_single_res,
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.error(
                            "❌ 未找到符合過濾條件的 YouTube"
                            " 影片，請嘗試微調歌名或歌手關鍵字。"
                        )

# ==========================================
# 🌐 測試2：語言標籤 (僅留單筆歌曲測試)
# ==========================================
with main_tabs[3]:
    st.header("🌐 Gemini AI 歌曲語言智慧檢測 & 本地對照表快搜")
    st.markdown(
        "整合 **Gemini 3.1 Flash Lite Preview** 語意模型與 **Key 輪詢池**，支援單筆歌曲語言智慧檢測與中央對照表 (`yt_mapping.csv`) 快搜。"
    )

    # ----------------------------------------------------
    # 🔑 1. 從 Streamlit Secrets 安全讀取 Key 池
    # ----------------------------------------------------
    API_KEYS = []
    if "GEMINI_API_KEYS" in st.secrets:
        keys_config = st.secrets["GEMINI_API_KEYS"]
        if isinstance(keys_config, list):
            API_KEYS = keys_config
        elif isinstance(keys_config, str):
            API_KEYS = [k.strip() for k in keys_config.split(",") if k.strip()]

    if not API_KEYS:
        st.warning("⚠️ 請先在 Streamlit Secrets 中設定 GEMINI_API_KEYS 金鑰池。")

    # ----------------------------------------------------
    # 🤖 2. Gemini 3.1 Flash Lite API 呼叫函式 (單筆)
    # ----------------------------------------------------
    def call_gemini_single_song(song_title, singer_name, yt_id=None):
        if not API_KEYS:
            return {"success": False, "error": "未設定 Gemini API Key", "attempts": 0}

        shuffled_keys = API_KEYS.copy()
        random.shuffle(shuffled_keys)

        yt_link_info = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else "無"

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
        for idx, current_key in enumerate(shuffled_keys, start=1):
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
                    "reason": result_json.get("reason", "無詳細說明"),
                    "used_key_mask": f"{current_key[:6]}...{current_key[-4:]}",
                    "attempts": idx,
                }
            except Exception as e:
                last_error = e
                continue
        return {
            "success": False,
            "error": str(last_error),
            "attempts": len(shuffled_keys),
        }

    # ----------------------------------------------------
    # 📁 3. 本地中央對照表快搜 logic (yt_mapping.csv)
    # ----------------------------------------------------
    def normalize_str(text):
        if not text:
            return ""
        t = str(text).lower()
        t = (
            t.replace("ⅱ", "ii")
            .replace("ⅰ", "i")
            .replace("ⅲ", "iii")
            .replace("ⅳ", "iv")
        )
        return re.sub(r"[\s\.\-\_\(\)（）「」《》【】『』""'']", "", t)

    def lookup_local_mapping(song_title, singer_name):
        file_paths = []
        if os.path.exists("yt_mapping.csv"):
            file_paths.append("yt_mapping.csv")

        if os.path.exists("data"):
            for root, _, files in os.walk("data"):
                for f in files:
                    if f.endswith(".csv"):
                        file_paths.append(os.path.join(root, f))

        if not file_paths:
            return None

        for path in file_paths:
            try:
                df = pd.read_csv(path)
                yt_col = next(
                    (c for c in ["YouTube ID", "Video ID", "YouTube_ID"] if c in df.columns),
                    None,
                )
                song_col = next(
                    (c for c in ["歌名", "song", "歌曲名稱"] if c in df.columns),
                    None,
                )

                if not yt_col or not song_col:
                    continue

                target_song_sim = normalize_str(zhconv.convert(song_title, "zh-hans"))
                target_song_tra = normalize_str(zhconv.convert(song_title, "zh-hant"))

                for _, row in df.iterrows():
                    row_song = str(row[song_col])
                    row_song_sim = normalize_str(zhconv.convert(row_song, "zh-hans"))
                    row_song_tra = normalize_str(zhconv.convert(row_song, "zh-hant"))

                    if target_song_sim == row_song_sim or target_song_tra == row_song_tra:
                        matched_id = str(row[yt_col]).strip()
                        if matched_id and matched_id not in ["-", "nan", "None", ""]:
                            return {
                                "yt_id": matched_id,
                                "source_file": os.path.basename(path),
                            }
            except Exception:
                continue
        return None

    # ----------------------------------------------------
    # 🎛️ 4. UI 介面區塊（直接展示單筆歌曲測試）
    # ----------------------------------------------------
    st.subheader("🧪 單筆資料分析與對照表檢索")

    c1, c2 = st.columns(2)
    with c1:
        input_song = st.text_input("歌名", value="crossfire", key="single_song_in")
    with c2:
        input_singer = st.text_input("歌手 / 團體", value="张艺兴", key="single_singer_in")

    if st.button(
        "🚀 開始檢測 (Gemini + 對照表)",
        type="primary",
        key="btn_single_run",
    ):
        if not input_song.strip():
            st.warning("請先輸入歌名！")
        else:
            # A. 優先檢索本地對照表
            local_match = lookup_local_mapping(
                input_song.strip(), input_singer.strip()
            )

            yt_id = None
            if local_match:
                yt_id = local_match["yt_id"]
                st.info(
                    f"⚡ **本地對照表命中**：在 `{local_match['source_file']}` 找到"
                    f" YouTube ID: `{yt_id}`，已同步傳給 Gemini 進行輔助判斷！"
                )
            else:
                st.caption(
                    "ℹ️ 本地對照庫無此歌名紀錄，將僅以歌名與歌手傳給 Gemini 判斷。"
                )

            # B. 呼叫 Gemini AI 進行語言判定 (傳入 yt_id)
            with st.spinner("正在使用 gemini-3.1-flash-lite-preview 分析語言..."):
                res = call_gemini_single_song(
                    input_song.strip(), input_singer.strip(), yt_id=yt_id
                )

                if res["success"]:
                    st.success("🎉 AI 語言判定完成！")
                    m1, m2, m3 = st.columns(3)
                    m1.metric("判定類別", res["category"])
                    m2.metric("使用 Key (遮罩)", res["used_key_mask"])
                    m3.metric("重試次數", f"第 {res['attempts']} 次成功")

                    st.write(f"💡 **AI 推理依據**：{res['reason']}")
                else:
                    st.error(f"❌ API 判定失敗。原因：{res['error']}")
