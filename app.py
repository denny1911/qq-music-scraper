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
        "🚀 模組二：黑馬雷達與動態追蹤",
        "👑 模組三：榜單常勝軍",
        "📺 模組四：YouTube 點閱測繪",
        "🌐 QQ 語言標籤測試",
        "📊 原始榜單瀏覽",
    ]
)

# ==========================================
# 🏆 模組一：全網霸榜池（單榜連續神曲）
# ==========================================
with main_tabs[0]:
    st.header("🔥 模組一：全網跨榜霸榜池")
    st.markdown(
        "自動比對榜單數據，篩選出在指定區間內**單一榜單連續 $X$ 天不間斷在榜**的神曲，指標最硬不踩雷！"
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
                yt_id_col = (
                    "YouTube ID"
                    if "YouTube ID" in df_range.columns
                    else (
                        "YouTube_ID"
                        if "YouTube_ID" in df_range.columns
                        else (
                            "Video ID"
                            if "Video ID" in df_range.columns
                            else None
                        )
                    )
                )

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

                records = []
                for (song, singer), sub_df in df_range.groupby([song_col, singer_col]):
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
                        sub_df_sorted = sub_df.sort_values(by="抓取日期")

                        # 取得 YouTube ID
                        yt_id = None
                        if yt_id_col:
                            valid_ids_df = sub_df_sorted[
                                ~sub_df_sorted[yt_id_col]
                                .astype(str)
                                .str.strip()
                                .isin(["-", "nan", "None", ""])
                            ].dropna(subset=[yt_id_col])
                            if not valid_ids_df.empty:
                                yt_id = valid_ids_df[yt_id_col].iloc[-1]

                        # 預設不抓歷史點閱率，直接設為 None（顯示為待抓取）
                        records.append(
                            {
                                song_col: song,
                                singer_col: singer,
                                "即時點閱率": None,
                                "連續在榜天數": max_single_streak,
                                "連續出現榜單": continuous_charts,
                                "歷史出現榜單": history_charts,
                                "YouTube ID": yt_id,
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
                            valid_ids = multi_chart["YouTube ID"].dropna().unique().tolist()
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
                                multi_chart["點閱率"] = multi_chart["YouTube ID"].map(realtime_views_map)
                                st.toast("✅ 已成功載入此刻最新即時點閱！")
                            else:
                                st.error("❌ 所有 API Key 今日配額皆已耗盡或連線失敗。")

                    # 處理 YouTube 連結
                    def build_yt_url(val):
                        v = str(val).strip() if pd.notna(val) else ""
                        if v and v not in ["-", "nan", "None", ""]:
                            return f"https://www.youtube.com/watch?v={v}"
                        return None

                    multi_chart["影片連結"] = multi_chart["YouTube ID"].apply(build_yt_url)

                    # 依點閱率由高到低排序（未點擊按鈕時點閱率為 None，不影響預設順序）
                    if "點閱率" in multi_chart.columns:
                        multi_chart = multi_chart.sort_values(
                            by=["點閱率"], ascending=[False], na_position="last"
                        )

                    cols_order = [
                        song_col,
                        singer_col,
                        "即時點閱率",
                        "連續在榜天數",
                        "連續出現榜單",
                        "歷史出現榜單",
                        "影片連結",
                    ]
                    multi_chart = multi_chart[cols_order]

                    st.success(
                        f"🎯 涵蓋區間：{start_date} ～ {end_date}（涵蓋 {X_max_days} 天數據，目標連續天數 $X = {X_max_days}$），共找到 {len(multi_chart)} 首單榜全程連續霸榜神曲！"
                    )
                    st.dataframe(
                        multi_chart,
                        column_config={
                            "點閱率": st.column_config.NumberColumn(
                                "即時點閱率", format="%,d", width="small", help="點擊上方按鈕後即時更新數據"
                            ),
                            "連續在榜天數": st.column_config.NumberColumn(
                                "連續在榜天數", format="%d 天", width="small"
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

                    export_df = get_clean_export_df(df_range, multi_chart)
                    csv_data = export_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        label="📥 匯出連續霸榜池清單 (CSV)",
                        data=csv_data,
                        file_name=f"QQ音樂_連續霸榜池_{start_date}_至_{end_date}.csv",
                        mime="text/csv",
                        key="m1_download_range",
                    )
                else:
                    st.info(
                        f"在 {start_date} ～ {end_date} 區間內（$X = {X_max_days}$ 天），暫無單榜達到連續 $X$ 天皆在榜的歌曲。"
                    )
            else:
                st.warning(
                    "數據欄位解析異常，請確認 CSV 欄位是否包含『歌名』與『歌手』。"
                )
        else:
            st.warning(f"在 {start_date} ～ {end_date} 區間內尚無榜單資料。")
            
# ==========================================
# 🚀 模組二：新進黑馬雷達（7天窗口對齊與動態期數修復版）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：新進黑馬雷達與動態追蹤")
    st.markdown(
        "偵測近期新進榜、名次持續爬升且點閱率大幅增長的潛力黑馬！"
    )

    m2_chart_option = st.radio(
        "選擇要分析的榜單",
        ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"],
        horizontal=True,
        key="m2_chart_radio",
    )

    base_date_obj = st.date_input(
        "📅 選擇基準日期 (預設為最新數據)",
        value=latest_date_obj,
        min_value=earliest_date_obj,
        max_value=latest_date_obj,
        key="m2_base_date_picker",
    )
    base_date = (
        base_date_obj.strftime("%Y-%m-%d")
        if isinstance(base_date_obj, date)
        else dates[0]
    )
    
    # 🎯 修正 1：若選取的日期不在 dates 中，自動對齊至不小於該日期的最新一天
    if base_date not in dates:
        valid_dates = [d for d in dates if d <= base_date]
        base_date = valid_dates[-1] if valid_dates else dates[-1]

    if base_date:
        base_dt = datetime.strptime(base_date, "%Y-%m-%d")

        if m2_chart_option == "新歌榜":
            target_past_dt = base_dt - timedelta(days=7)
            range_dates = sorted(
                [
                    d
                    for d in dates
                    if target_past_dt
                    <= datetime.strptime(d, "%Y-%m-%d")
                    <= base_dt
                ]
            )
            label_text = f"📊 {len(range_dates)}日連續追蹤"
        else:
            # 🎯 修正 2：改用 7 天窗口尋找近幾期的代表檔案，不再硬性限制 weekday() == 3
            # 這樣 7/31 (星期五)、8/12 (星期三)、8/13 (星期四) 都能順利被納入！
            days_since_thu = (base_dt.weekday() - 3) % 7
            base_thu = base_dt - timedelta(days=days_since_thu)

            selected_weekly_dates = []
            for k in range(7):  # 最多向前追蹤 7 期
                target_thu = base_thu - timedelta(weeks=k)
                found_date = None
                
                # 掃描該週 7 天內是否有資料
                for day_offset in range(7):
                    cand_str = (target_thu + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                    if cand_str in dates and cand_str <= base_date:
                        found_date = cand_str
                        break
                
                if found_date:
                    selected_weekly_dates.append(found_date)

            range_dates = sorted(list(set(selected_weekly_dates)))
            # 🎯 修正 3：動態顯示實際找到的期數，不再硬寫「七期」
            label_text = f"📊 {len(range_dates)}期連續追蹤"

        st.caption(
            f"{label_text}：`{min(range_dates)}` ➡️ `{max(range_dates)}`"
        )

        range_dfs = []
        for d in range_dates:
            d_full = load_date_data(d)
            if not d_full.empty:
                d_chart = d_full[d_full["榜單類型"] == m2_chart_option].copy()
                if not d_chart.empty:
                    d_chart["追蹤日期"] = d
                    range_dfs.append(d_chart)

        if range_dfs:
            df_all_range = pd.concat(range_dfs, ignore_index=True)
            song_col = "歌名" if "歌名" in df_all_range.columns else "song"
            singer_col = "歌手" if "歌手" in df_all_range.columns else "singer"
            rank_col = "排名" if "排名" in df_all_range.columns else "rank"

            yt_id_col = (
                "YouTube ID"
                if "YouTube ID" in df_all_range.columns
                else (
                    "YouTube_ID"
                    if "YouTube_ID" in df_all_range.columns
                    else ("Video ID" if "Video ID" in df_all_range.columns else None)
                )
            )
            yt_views_col = (
                "點閱率"
                if "點閱率" in df_all_range.columns
                else ("觀看次數" if "觀看次數" in df_all_range.columns else None)
            )

            # 建立名次 Pivot
            pivot_rank = df_all_range.pivot_table(
                index=[song_col, singer_col],
                columns="追蹤日期",
                values=rank_col,
                aggfunc="min",
            )

            # 建立點閱率 Pivot (如果有的話)
            pivot_views = None
            if yt_views_col and yt_views_col in df_all_range.columns:
                df_all_range[yt_views_col] = (
                    df_all_range[yt_views_col]
                    .astype(str)
                    .str.replace(",", "", regex=False)
                )
                df_all_range[yt_views_col] = pd.to_numeric(
                    df_all_range[yt_views_col], errors="coerce"
                )
                pivot_views = df_all_range.pivot_table(
                    index=[song_col, singer_col],
                    columns="追蹤日期",
                    values=yt_views_col,
                    aggfunc="last",
                )

            # 抓取 YouTube ID (取第一筆不重複的)
            yt_id_map = {}
            if yt_id_col and yt_id_col in df_all_range.columns:
                for _, row in df_all_range.iterrows():
                    k = (row[song_col], row[singer_col])
                    if k not in yt_id_map or pd.isna(yt_id_map[k]):
                        v = row[yt_id_col]
                        if pd.notna(v) and str(v).strip() not in ["", "nan", "None", "-"]:
                            yt_id_map[k] = v

            actual_base_date = max(range_dates) if range_dates else base_date

            if actual_base_date in pivot_rank.columns:
                # 門檻：至少要有 2 期資料才能計算比較
                min_required = min(2, len(range_dates))
                processed_rows = []

                for idx, row in pivot_rank.iterrows():
                    song, singer = idx
                    valid_history = row[range_dates].dropna()

                    if len(valid_history) < min_required:
                        continue

                    if actual_base_date not in valid_history.index or pd.isna(row[actual_base_date]):
                        continue

                    initial_rank = int(valid_history.iloc[0])
                    current_rank = int(row[actual_base_date])

                    # 計算名次爬升幅
                    rank_surge = initial_rank - current_rank

                    # 計算點閱率淨增量
                    view_growth = 0
                    if pivot_views is not None and idx in pivot_views.index:
                        # 取得最新一期與期初一期的點閱值
                        latest_val = (
                            pivot_views.loc[idx, actual_base_date]
                            if actual_base_date in pivot_views.columns
                            else None
                        )

                        if pd.notna(latest_val):
                            end_views = int(latest_val)

                            # 尋找期初點閱（排除最新一期）
                            past_cols = [
                                d
                                for d in range_dates
                                if d in pivot_views.columns and d != actual_base_date
                            ]
                            past_series = (
                                pivot_views.loc[idx, past_cols].dropna()
                                if past_cols
                                else pd.Series()
                            )

                            if not past_series.empty:
                                start_views = int(past_series.iloc[0])
                            else:
                                start_views = 0  # 若先前皆無紀錄，視為從 0 成長

                            view_growth = max(0, end_views - start_views)

                    if rank_surge <= 0:
                        continue

                    yt_val = yt_id_map.get(idx, None)

                    processed_rows.append(
                        {
                            song_col: song,
                            singer_col: singer,
                            "點閱淨增量": view_growth,
                            "名次總爬升幅": rank_surge,
                            "追蹤期初名次": initial_rank,
                            "基準日名次": current_rank,
                            "YouTube ID": yt_val,
                            "raw_song": song,
                            "raw_singer": singer,
                        }
                    )

                df_result = pd.DataFrame(processed_rows)
                if not df_result.empty:
                    df_result = (
                        df_result.sort_values(
                            by=["點閱淨增量", "名次總爬升幅"], ascending=[False, False]
                        )
                        .head(10)
                        .reset_index(drop=True)
                    )

                    def build_yt_url(val):
                        v = str(val).strip() if pd.notna(val) else ""
                        if v and v not in ["-", "nan", "None", ""]:
                            return f"https://www.youtube.com/watch?v={v}"
                        return None

                    df_result["影片連結"] = df_result["YouTube ID"].apply(build_yt_url)

                    display_cols = [
                        song_col,
                        singer_col,
                        "點閱淨增量",
                        "名次總爬升幅",
                        "追蹤期初名次",
                        "基準日名次",
                        "影片連結",
                    ]
                    df_display = df_result[display_cols].copy()

                    st.success("🎯 已鎖定流量暴衝與名次爬升的潛力黑馬！")
                    st.dataframe(
                        df_display,
                        column_config={
                            "點閱淨增量": st.column_config.NumberColumn(
                                "點閱淨增量", format="%,d", width="small"
                            ),
                            "名次總爬升幅": st.column_config.NumberColumn(
                                "名次總爬升幅", format="+%d", width="small"
                            ),
                            "追蹤期初名次": st.column_config.NumberColumn(
                                "追蹤期初名次", format="%d", width="small"
                            ),
                            "基準日名次": st.column_config.NumberColumn(
                                "基準日名次", format="%d", width="small"
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

                    st.markdown("### 📈 黑馬反彈與爬升走勢")
                    top_keys = list(
                        zip(df_result["raw_song"], df_result["raw_singer"])
                    )
                    chart_data = pivot_rank.loc[top_keys, range_dates].T

                    chart_data.columns = [f"{s} - {si}" for s, si in top_keys]
                    chart_data.index = [
                        (
                            f"第 {i+1} 天"
                            if m2_chart_option == "新歌榜"
                            else f"第 {i+1} 期"
                        )
                        for i in range(len(range_dates))
                    ]
                    chart_data = chart_data.reset_index().rename(
                        columns={"index": "追蹤時間"}
                    )

                    df_melted = chart_data.melt(
                        id_vars="追蹤時間",
                        var_name="歌曲",
                        value_name="名次",
                    )

                    c = (
                        alt.Chart(df_melted)
                        .mark_line(point=True, strokeWidth=2.5)
                        .encode(
                            x=alt.X(
                                "追蹤時間:N",
                                sort=None,
                                title="追蹤時間",
                                axis=alt.Axis(labelAngle=0),
                            ),
                            y=alt.Y(
                                "名次:Q",
                                scale=alt.Scale(
                                    domain=[1, 100],
                                    reverse=True,
                                    clamp=True,
                                    zero=False,
                                ),
                                title="名次",
                                axis=alt.Axis(titleAngle=0),
                            ),
                            color=alt.Color("歌曲:N", title="黑馬清單"),
                            tooltip=["追蹤時間", "歌曲", "名次"],
                        )
                        .properties(width="container", height=450)
                    )

                    st.altair_chart(c, use_container_width=True)

                    export_df = df_display.copy()
                    csv = export_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "📥 匯出黑馬清單 (CSV)",
                        csv,
                        f"黑馬清單_{base_date}.csv",
                        "text/csv",
                        key="m2_download_btn",
                    )
                else:
                    st.info("暫無符合條件的黑馬歌曲。")
            else:
                st.info("基準日無資料。")
        else:
            st.info("選定日期區間內無數據。")
            
# ==========================================
# 👑 模組三：榜單常勝軍（長青熱歌）
# ==========================================
with main_tabs[2]:
    st.header("👑 模組三：榜單常勝軍（長青熱歌）")
    st.markdown(
        "統計**指定日期區間**內，在個別榜單的累積表現（**新歌榜統計天數，其餘三榜依官方週四更新期數統計**）。"
    )

    chart_option_m3 = st.radio(
        "選擇要統計常勝軍的榜單",
        ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"],
        horizontal=True,
        key="m3_radio",
    )

    is_weekly_chart = chart_option_m3 != "新歌榜"

    m3_preset = st.radio(
        "🗓️ 選擇統計時間範圍",
        ["⚡ 近 7 天", "⚡ 近 30 天", "🌐 全部歷史區間", "📅 自訂月曆區間"],
        horizontal=True,
        key="m3_preset_radio",
    )

    if m3_preset == "⚡ 近 7 天":
        start_date_obj = max(
            earliest_date_obj, latest_date_obj - timedelta(days=6)
        )
        end_date_obj = latest_date_obj
    elif m3_preset == "⚡ 近 30 天":
        start_date_obj = max(
            earliest_date_obj, latest_date_obj - timedelta(days=29)
        )
        end_date_obj = latest_date_obj
    elif m3_preset == "🌐 全部歷史區間":
        start_date_obj = earliest_date_obj
        end_date_obj = latest_date_obj
    else:
        date_range = st.date_input(
            "請選取月曆區間（點擊開始與結束日期）",
            value=(earliest_date_obj, latest_date_obj),
            min_value=earliest_date_obj,
            max_value=latest_date_obj,
            key="m3_date_picker",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_date_obj, end_date_obj = date_range
        else:
            st.info("💡 請在月曆上選取『結束日期』以完成選擇。")
            st.stop()

    start_date = start_date_obj.strftime("%Y-%m-%d")
    end_date = end_date_obj.strftime("%Y-%m-%d")

    selected_m3_dates = [d for d in dates if start_date <= d <= end_date]

    all_dfs = []
    for d in selected_m3_dates:
        d_df = load_date_data(d)
        if not d_df.empty:
            d_df["抓取日期"] = d
            all_dfs.append(d_df)

    if all_dfs:
        full_df = pd.concat(all_dfs, ignore_index=True)
        song_col = "歌名" if "歌名" in full_df.columns else "song"
        singer_col = "歌手" if "歌手" in full_df.columns else "singer"

        target_df = full_df[full_df["榜單類型"] == chart_option_m3].copy()

        if not target_df.empty:
            yt_id_col = (
                "YouTube ID"
                if "YouTube ID" in target_df.columns
                else (
                    "YouTube_ID"
                    if "YouTube_ID" in target_df.columns
                    else ("Video ID" if "Video ID" in target_df.columns else None)
                )
            )
            yt_views_col = (
                "點閱率"
                if "點閱率" in target_df.columns
                else ("觀看次數" if "觀看次數" in target_df.columns else None)
            )

            if is_weekly_chart:
                target_df["榜單期數"] = target_df["抓取日期"].apply(
                    get_issue_label
                )

                agg_kwargs = {
                    "累積上榜期數": ("榜單期數", "nunique"),
                    "平均名次": (
                        ("排名", lambda x: round(x.mean(), 1))
                        if "排名" in target_df.columns
                        else ("榜單期數", "count")
                    ),
                }
            else:
                agg_kwargs = {
                    "累積上榜天數": ("抓取日期", "nunique"),
                    "平均名次": (
                        ("排名", lambda x: round(x.mean(), 1))
                        if "排名" in target_df.columns
                        else ("抓取日期", "count")
                    ),
                }

            if yt_id_col:
                agg_kwargs["YouTube ID"] = (yt_id_col, "first")
            if yt_views_col:
                agg_kwargs["點閱率"] = (yt_views_col, "first")

            if is_weekly_chart:
                evergreen = (
                    target_df.groupby([song_col, singer_col])
                    .agg(**agg_kwargs)
                    .reset_index()
                    .sort_values(
                        by=["累積上榜期數", "平均名次"],
                        ascending=[False, True],
                    )
                )
            else:
                evergreen = (
                    target_df.groupby([song_col, singer_col])
                    .agg(**agg_kwargs)
                    .reset_index()
                    .sort_values(
                        by=["累積上榜天數", "平均名次"],
                        ascending=[False, True],
                    )
                )

            if not evergreen.empty:
                def build_yt_url(val):
                    v = str(val).strip() if pd.notna(val) else ""
                    if v and v not in ["-", "nan", "None", ""]:
                        return f"https://www.youtube.com/watch?v={v}"
                    return None

                if "YouTube ID" in evergreen.columns:
                    evergreen["影片連結"] = evergreen["YouTube ID"].apply(
                        build_yt_url
                    )
                else:
                    evergreen["影片連結"] = None

                if "點閱率" in evergreen.columns:
                    evergreen["點閱率"] = (
                        evergreen["點閱率"]
                        .astype(str)
                        .str.replace(",", "", regex=False)
                    )
                    evergreen["點閱率"] = pd.to_numeric(
                        evergreen["點閱率"], errors="coerce"
                    )
                else:
                    evergreen["點閱率"] = None

                count_col_name = "累積上榜期數" if is_weekly_chart else "累積上榜天數"
                cols_order = [
                    song_col,
                    singer_col,
                    "點閱率",
                    count_col_name,
                    "平均名次",
                    "影片連結",
                ]
                evergreen = evergreen[cols_order]

            total_units = (
                target_df["榜單期數"].nunique()
                if is_weekly_chart
                else target_df["抓取日期"].nunique()
            )
            unit_name = "期" if is_weekly_chart else "天"

            st.success(
                f"📈【{chart_option_m3}（{'週榜' if is_weekly_chart else '日榜'}）】統計區間：{start_date} ～ {end_date}（涵蓋 {total_units} {unit_name}，共 {len(evergreen)} 首歌曲）："
            )

            column_config_dict = {
                "點閱率": st.column_config.NumberColumn(
                    "點閱率", format="%,d", width="small"
                ),
                count_col_name: st.column_config.NumberColumn(
                    count_col_name, format="%d", width="small"
                ),
                "平均名次": st.column_config.NumberColumn(
                    "平均名次", format="%.1f", width="small"
                ),
                "影片連結": st.column_config.LinkColumn(
                    "影片連結",
                    display_text="點此觀看",
                    help="點擊前往 YouTube 觀看 MV",
                    width="small",
                ),
            }

            st.dataframe(
                evergreen,
                column_config=column_config_dict,
                hide_index=True,
                use_container_width=True,
            )

            export_df = get_clean_export_df(target_df, evergreen)
            csv_data = export_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label=f"📥 匯出【{chart_option_m3}】常勝軍清單 (CSV)",
                data=csv_data,
                file_name=f"QQ音樂_榜單常勝軍_{chart_option_m3}_{start_date}_至_{end_date}.csv",
                mime="text/csv",
                key="m3_download",
            )
        else:
            st.info(
                f"在 {start_date} ～ {end_date} 區間內，尚無【{chart_option_m3}】的數據。"
            )
    else:
        st.info("選定日期區間內無數據。")

# ==========================================
# 📺 模組四：YouTube 點閱測繪（完整固定策略版）
# ==========================================
with main_tabs[3]:
  st.header("📺 模組四：YouTube 點閱測繪")
  st.markdown(
      "自動向 Git 數據源讀取最新榜單資料，並進行 YouTube"
      " 影片搜尋與點閱數據測繪。"
  )

  # --- 1. 輔助與清理函式 ---
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
    t = str(text).lower()
    
    # 1. 統一將特殊羅馬數字轉為半角英文字母
    t = t.replace('ⅱ', 'ii').replace('ⅰ', 'i').replace('ⅲ', 'iii').replace('ⅳ', 'iv')
    
    # 2. 清除空格、括號與引號符號 (包含 「」 《》)
    return re.sub(r"[\s\.\-\_\(\)（）「」《》【】『』""'']", "", t)

  def extract_artist_tokens(singer):
    if not singer or str(singer).lower() in ["-", "nan", "none"]:
      return []

    singer_str = str(singer).strip()

    # 1. 👈【關鍵修改】只用「真正的合唱分隔符」拆分不同歌手（移除括號 \(\)）
    raw_artists = re.split(
        r"[/&,\+\·\*\-\|\s]+|feat\.?|ft\.?|X|x",
        singer_str,
        flags=re.IGNORECASE,
    )

    artist_groups = []

    for raw in raw_artists:
      raw = raw.strip()
      if not raw:
        continue

      group_tokens = set()

      # 2. 提取整體（例如 "田園(小園)"）
      group_tokens.add(zhconv.convert(raw, "zh-hans"))
      group_tokens.add(zhconv.convert(raw, "zh-hant"))

      # 3. 提取去除括號後的主名字（例如 "田園"）
      clean_raw = re.sub(r"[\(\（][^\)\）]*[\)\）]", "", raw).strip()
      if clean_raw:
        group_tokens.add(zhconv.convert(clean_raw, "zh-hans"))
        group_tokens.add(zhconv.convert(clean_raw, "zh-hant"))

      # 4. 提取括號內的別名/綽號（例如 "小園"），全部放進「同一組」！
      bracket_content = re.findall(r"[\(\（]([^\)\）]+)[\)\）]", raw)
      for b in bracket_content:
        b = b.strip()
        if b:
          group_tokens.add(zhconv.convert(b, "zh-hans"))
          group_tokens.add(zhconv.convert(b, "zh-hant"))

      # 5. 拆解英文/單字片段
      sub_chunks = re.findall(
          r"[a-zA-Z0-9\.\-\']+|[\u4e00-\u9fa5]+|[\uAC00-\uD7A3]+", raw
      )
      if len(sub_chunks) > 1:
        for chunk in sub_chunks:
          chunk = chunk.strip()
          if len(chunk) >= 1:
            group_tokens.add(zhconv.convert(chunk, "zh-hans"))
            group_tokens.add(zhconv.convert(chunk, "zh-hant"))

      # 規格化
      norm_group = [normalize_text(t) for t in group_tokens if normalize_text(t)]
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
                    maxResults=max_results_val,  # 👈 viewCount: 30 / relevance: 5
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
                channel_title = item["snippet"].get("channelTitle", "")
                v_desc = item["snippet"].get("description", "") # 👈 抓取影片說明欄
                v_views = int(item["statistics"].get("viewCount", 0))

                duration_str = item.get("contentDetails", {}).get(
                    "duration", "PT0S"
                )
                duration_sec = parse_duration(duration_str)

                # 過濾短影音與過長影片 (1分5秒 ~ 8分鐘)
                if duration_sec <= 65 or duration_sec > 480:
                  continue

                v_title_lower = v_title.lower()
                v_title_norm = normalize_text(v_title)
                channel_lower = channel_title.lower()
                channel_norm = normalize_text(channel_title)
                v_desc_norm = normalize_text(v_desc)           # 👈 規格化說明欄

                is_topic = "topic" in channel_lower or "主題" in channel_lower

                has_noise = any(
                    nk in v_title_lower for nk in COMBINED_NOISE_KEYWORDS
                )
                if not is_topic and has_noise:
                  continue

                song_matched = (main_sim_norm in v_title_norm) or (
                    main_tra_norm in v_title_norm
                )
                if not song_matched:
                  continue
                v_full_text = f"{v_title_norm} {channel_norm} {v_desc_norm}"

                # 👈 歌手比對加入說明欄 v_desc_norm
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

                # 強制進行歌手檢驗，確保不張冠李戴
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
  m4_subtab1, m4_subtab2 = st.tabs(["📊 榜單批量測繪", "🔍 單首歌即時查詢"])

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
          ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜", "全部榜單"],
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
            "❌ 未找到有效的 API Key，請先在 Streamlit Cloud Secrets 設定"
            " `YOUTUBE_API_KEYS`！"
        )
        st.stop()

      df_curr = load_date_data(m4_date)

      if not df_curr.empty and m4_chart_type != "全部榜單":
        df_target = df_curr[df_curr["榜單類型"] == m4_chart_type].copy()
      else:
        df_target = df_curr.copy()

      if df_target is None or df_target.empty:
        st.error(
            f"❌ 無法讀取 `{m4_date}` 的榜單資料，請確認 GitHub Actions"
            " 是否已下載該日期之數據。"
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
              row.get("歌名", row.get("song", row.get("歌曲名稱", "Unknown")))
          ).strip()
          singer = str(
              row.get("歌手", row.get("singer", row.get("歌手名稱", "Unknown")))
          ).strip()

          # 👈 【改為以下安全寫法】
          try:
            rank = int(row.get("排名", 0))
            if rank <= 0:  # 若缺值填 0，自動補上 1-based 索引
              rank = idx + 1
          except (ValueError, TypeError):
            rank = idx + 1
              
          status_text.text(
              f"🔍 ({idx+1}/{test_limit}) 正在檢索點閱：{song} - {singer}"
              f" [Key {current_key_idx + 1}/{len(api_keys)}]"
          )
          progress_bar.progress((idx + 1) / test_limit)

          matched, current_key_idx, youtube_service = search_youtube_video(
              song, singer, api_keys, current_key_idx, youtube_service
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
                  if matched and matched.get("search_mode") == "viewCount"
                  else (
                      "相關性補救"
                      if matched and matched.get("search_mode") == "relevance"
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

        st.dataframe(df_display, use_container_width=True, hide_index=True)

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
              "❌ 未找到有效的 API Key，請先在 Streamlit Cloud Secrets 設定"
              " `YOUTUBE_API_KEYS`！"
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
            st.success(f"🎉 成功找到最佳匹配影片！（命中機制：{mode_desc}）")

            res_m1, res_m2, res_m3 = st.columns(3)
            res_m1.metric("Video ID", matched["id"])
            res_m2.metric("YT 觀看次數", f"{matched['views']:,}")
            res_m3.metric("頻道名稱", matched["channel"])

            st.write(f"**影片標題：** {matched['title']}")
            st.write(f"**影片連結：** [{matched['url']}]({matched['url']})")

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
                df_single_res, use_container_width=True, hide_index=True
            )
          else:
            st.error(
                "❌ 未找到符合過濾條件的 YouTube"
                " 影片，請嘗試微調歌名或歌手關鍵字。"
            )

# ==========================================
# 🌐 測試區：QQ 榜單 + Gemini AI 智慧語言檢測 & 對照表快搜
# ==========================================
with main_tabs[4]:
  st.header("🌐 Gemini AI 歌曲語言智慧檢測 & 本地對照表快搜")
  st.markdown(
      "整合 **Gemini 3.1 Flash Lite Preview** 語意模型與 **Key"
      " 輪詢池**，支援單筆測試、QQ 榜單批次分析及中央對照表"
      " (`yt_mapping.csv`) 快搜。"
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
  # 🤖 2. Gemini 3.1 Flash Lite API 呼叫函式 (含 Key 重試 & YT 連結)
  # ----------------------------------------------------
  def call_gemini_single_song(song_title, singer_name, yt_id=None):
    if not API_KEYS:
      return {"success": False, "error": "未設定 Gemini API Key", "attempts": 0}

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

  def batch_classify_with_gemini(song_items):
    if not API_KEYS:
      return None

    shuffled_keys = API_KEYS.copy()
    random.shuffle(shuffled_keys)

    prompt_data = [
        {"id": idx, "title": item["歌名"], "singer": item["歌手"]}
        for idx, item in enumerate(song_items, start=1)
    ]

    prompt = f"""
你是一個專業音樂榜單分析專家。請分析以下 10 首音樂歌曲，將每首歌嚴格歸類為【華語／韓語／日語／西洋／其它】之一。

歌曲清單 (JSON)：
{json.dumps(prompt_data, ensure_ascii=False)}

【重要規則】：
1. 亞洲歌手的全英文歌 (如張藝興 Crossfire、王嘉爾全英文歌)，請歸類為 "西洋"。
2. 僅歌名為英文但歌詞為中文者 (如周深中文歌)，歸類為 "華語"。
3. 請嚴格只輸出 JSON 陣列，格式如下：
[
  {{"id": 1, "category": "華語", "reason": "說明理由"}},
  ...
]
"""
    for current_key in shuffled_keys:
      try:
        genai.configure(api_key=current_key)
        model = genai.GenerativeModel("gemini-3.1-flash-lite-preview")
        response = model.generate_content(
            prompt, generation_config={"response_mime_type": "application/json"}
        )
        results_json = json.loads(response.text.strip())
        return {
            item["id"]: (item["category"], item.get("reason", "Gemini 判斷"))
            for item in results_json
        }
      except Exception:
        continue
    return None

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
            (
                c
                for c in ["YouTube ID", "Video ID", "YouTube_ID"]
                if c in df.columns
            ),
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
  # 🎛️ 4. UI 介面區塊
  # ----------------------------------------------------
  test_mode = st.radio(
      "📌 請選擇測試模式：",
      ["🧪 單筆歌曲測試", "🎵 QQ 榜單 Top 10 批次抓取"],
      horizontal=True,
  )

  # --- 模式一：單筆歌曲測試 ---
  if test_mode == "🧪 單筆歌曲測試":
    st.subheader("🧪 單筆資料分析與對照表檢索")

    c1, c2 = st.columns(2)
    with c1:
      input_song = st.text_input("歌名", value="crossfire", key="single_song_in")
    with c2:
      input_singer = st.text_input(
          "歌手 / 團體", value="张艺兴", key="single_singer_in"
      )

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
              "ℹ️ 本地對照庫無此歌名紀錄，將僅以歌名與歌手傳給 Gemini"
              " 判斷。"
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
            st.error(
              f"❌ API 判定失敗。原因：{res['error']}"
            )

  # --- 模式二：QQ 榜單 Top 10 批次抓取 ---
  else:
    st.subheader("🎵 QQ 官方榜單 Top 10 批次分析")

    chart_dict = {
        "QQ 熱歌榜 (綜合爆款)": 26,
        "QQ 飆升榜 (新歌快訊)": 62,
        "QQ 流行指數榜 (當前熱度)": 4,
    }
    selected_chart_name = st.selectbox(
        "選擇要測試的 QQ 官方榜單", options=list(chart_dict.keys())
    )
    selected_topid = chart_dict[selected_chart_name]

    if st.button(
        "🔍 抓取 Top 10 並進行 Gemini 分析",
        type="primary",
        key="btn_batch_run",
    ):
      with st.spinner("1/2 正在連線 QQ 音樂 API..."):
        url = "https://u.y.qq.com/cgi-bin/musicu.fcg"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://y.qq.com/"}
        payload = {
            "comm": {"ct": 24, "cv": 0},
            "toplist": {
                "module": "musicToplist.ToplistInfoServer",
                "method": "GetDetail",
                "param": {"topid": selected_topid, "num": 10, "period": ""},
            },
        }

        try:
          resp = requests.post(url, json=payload, headers=headers, timeout=10)
          song_list = (
              resp.json()
              .get("toplist", {})
              .get("data", {})
              .get("songInfoList", [])
          )

          raw_songs = []
          for idx, item in enumerate(song_list[:10], start=1):
            s_name = item.get("title", item.get("name", "Unknown"))
            singers = " / ".join(
                [s.get("name", "") for s in item.get("singer", [])]
            )
            raw_songs.append({"排名": idx, "歌名": s_name, "歌手": singers})
        except Exception as e:
          st.error(f"❌ 抓取 QQ API 失敗：{e}")
          raw_songs = []

      if raw_songs:
        with st.spinner(
            "2/2 正在透過 Gemini 3.1 Flash Lite 批次判定語言與檢索本地對照表..."
        ):
          gemini_res = batch_classify_with_gemini(raw_songs)

          final_rows = []
          for s in raw_songs:
            idx = s["排名"]

            if gemini_res and idx in gemini_res:
              cat, reason = gemini_res[idx]
            else:
              cat, reason = ("無法判定", "Gemini API 呼叫失敗")

            local_m = lookup_local_mapping(s["歌名"], s["歌手"])
            matched_yt = local_m["yt_id"] if local_m else "未命中"

            final_rows.append({
                "排名": idx,
                "歌名": s["歌名"],
                "歌手": s["歌手"],
                "AI 判定類別": cat,
                "判定說明": reason,
                "對照表 YT ID": matched_yt,
            })

          df_res = pd.DataFrame(final_rows)
          st.success("🎉 批次分析成功！")
          st.dataframe(df_res, hide_index=True, use_container_width=True)

# ==========================================
# 📊 原始榜單瀏覽
# ==========================================
with main_tabs[5]:
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
