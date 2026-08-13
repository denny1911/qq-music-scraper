import os
import re
from datetime import date, datetime, timedelta
from googleapiclient.discovery import build
import urllib.parse
import altair as alt
import pandas as pd
import streamlit as st
import time
import yt_dlp

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
                dfs.append(df)
            except Exception:
                pass
    if dfs:
        return pd.concat(dfs, ignore_index=True)
    return pd.DataFrame()


# 輔助函式：根據跨榜組合生成標籤
def generate_song_tags(charts_str):
    charts = set(charts_str.split("、"))
    tags = []
    if "新歌榜" in charts and "抖音熱歌榜" in charts:
        tags.append("🔥 社群爆款")
    if "影視金曲榜" in charts and "抖音熱歌榜" in charts:
        tags.append("🎬 大劇神曲")
    if "綜藝新歌榜" in charts and "新歌榜" in charts:
        tags.append("🎤 節目話題曲")
    if len(charts) >= 3:
        tags.append("🌟 跨榜超級爆款")
    if not tags:
        tags.append("⚡ 雙榜同登")
    return "｜".join(tags)


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
        "📊 原始榜單瀏覽",
    ]
)

# ==========================================
# 🏆 模組一：全網霸榜池（最猛爆款）
# ==========================================
with main_tabs[0]:
    st.header("🔥 模組一：全網跨榜霸榜池")
    st.markdown(
        "自動比對榜單數據，篩選出**登上 2 個（含）以上榜單**的神曲，指標最硬不踩雷！"
    )

    m1_preset = st.radio(
        "🗓️ 選擇分析時間範圍",
        [
            "⚡ 單日即時",
            "⚡ 近 7 天",
            "⚡ 近 30 天",
            "🌐 全部歷史區間",
            "📅 自訂月曆區間",
        ],
        horizontal=True,
        key="m1_preset_radio",
    )

    if m1_preset == "⚡ 單日即時":
        selected_date_obj = st.date_input(
            "📅 選擇基準日期 (預設為最新數據)",
            value=latest_date_obj,
            min_value=earliest_date_obj,
            max_value=latest_date_obj,
            key="m1_single_date_picker",
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
            df_curr = load_date_data(selected_date)
            if not df_curr.empty:
                song_col = (
                    "歌名"
                    if "歌名" in df_curr.columns
                    else ("song" if "song" in df_curr.columns else None)
                )
                singer_col = (
                    "歌手"
                    if "歌手" in df_curr.columns
                    else ("singer" if "singer" in df_curr.columns else None)
                )

                if song_col and singer_col:
                    yt_id_col = (
                        "YouTube ID"
                        if "YouTube ID" in df_curr.columns
                        else (
                            "YouTube_ID"
                            if "YouTube_ID" in df_curr.columns
                            else (
                                "Video ID"
                                if "Video ID" in df_curr.columns
                                else None
                            )
                        )
                    )
                    yt_views_col = (
                        "點閱率"
                        if "點閱率" in df_curr.columns
                        else (
                            "觀看次數"
                            if "觀看次數" in df_curr.columns
                            else None
                        )
                    )

                    agg_kwargs = {
                        "登榜數量": ("榜單類型", "nunique"),
                        "登上榜單": (
                            "榜單類型",
                            lambda x: "、".join(sorted(set(x))),
                        ),
                        "最高名次": (
                            ("排名", "min")
                            if "排名" in df_curr.columns
                            else ("登榜數量", "count")
                        ),
                    }
                    if yt_id_col:
                        agg_kwargs["YouTube ID"] = (yt_id_col, "first")
                    if yt_views_col:
                        agg_kwargs["點閱率"] = (yt_views_col, "first")

                    grouped = (
                        df_curr.groupby([song_col, singer_col])
                        .agg(**agg_kwargs)
                        .reset_index()
                    )

                    multi_chart = grouped[grouped["登榜數量"] >= 2].sort_values(
                        by=["登榜數量", "最高名次"], ascending=[False, True]
                    )

                    if not multi_chart.empty:

                        def build_yt_url(val):
                            v = str(val).strip() if pd.notna(val) else ""
                            if v and v not in ["-", "nan", "None", ""]:
                                return f"https://www.youtube.com/watch?v={v}"
                            return None

                        if "YouTube ID" in multi_chart.columns:
                            multi_chart["影片連結"] = multi_chart[
                                "YouTube ID"
                            ].apply(build_yt_url)
                        else:
                            multi_chart["影片連結"] = None

                        if "點閱率" in multi_chart.columns:
                            multi_chart["點閱率"] = (
                                multi_chart["點閱率"]
                                .astype(str)
                                .str.replace(",", "", regex=False)
                            )
                            multi_chart["點閱率"] = pd.to_numeric(
                                multi_chart["點閱率"], errors="coerce"
                            )
                        else:
                            multi_chart["點閱率"] = None

                        cols_order = [
                            song_col,
                            singer_col,
                            "點閱率",
                            "登榜數量",
                            "登上榜單",
                            "最高名次",
                            "影片連結",
                        ]
                        multi_chart = multi_chart[cols_order]

                        st.success(
                            f"🎯 在 {selected_date} 當天，共找到 {len(multi_chart)} 首跨榜爆款歌曲！"
                        )
                        st.dataframe(
                            multi_chart,
                            column_config={
                                "點閱率": st.column_config.NumberColumn(
                                    "點閱率", format="%,d", width="small"
                                ),
                                "登榜數量": st.column_config.NumberColumn(
                                    "登榜數量", format="%,d", width="small"
                                ),
                                "最高名次": st.column_config.NumberColumn(
                                    "最高名次", format="%,d", width="small"
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

                        export_df = get_clean_export_df(df_curr, multi_chart)
                        csv_data = export_df.to_csv(index=False).encode(
                            "utf-8-sig"
                        )
                        st.download_button(
                            label="📥 匯出單日霸榜池清單 (CSV)",
                            data=csv_data,
                            file_name=f"QQ音樂_單日霸榜池_{selected_date}.csv",
                            mime="text/csv",
                            key="m1_download_1d",
                        )
                    else:
                        st.info(
                            f"在 {selected_date} 當天，暫無同時登上 2 個以上榜單的歌曲。"
                        )
                else:
                    st.warning(
                        "數據欄位解析異常，請確認 CSV 欄位是否包含『歌名』與『歌手』。"
                    )
            else:
                st.warning(f"{selected_date} 尚無榜單資料。")
        else:
            st.info(
                "💡 **請先選擇『基準日期』**，即可開始進行單日霸榜池分析。"
            )

    else:
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
        elif m1_preset == "🌐 全部歷史區間":
            start_date_obj = earliest_date_obj
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

        selected_m1_dates = [d for d in dates if start_date <= d <= end_date]

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
                yt_views_col = (
                    "點閱率"
                    if "點閱率" in df_range.columns
                    else (
                        "觀看次數"
                        if "觀看次數" in df_range.columns
                        else None
                    )
                )

                agg_kwargs = {
                    "跨榜數量": ("榜單類型", "nunique"),
                    "涵蓋榜單": ("榜單類型", lambda x: "、".join(sorted(set(x)))),
                    "累積活躍天數": ("抓取日期", "nunique"),
                    "最高名次": (
                        ("排名", "min")
                        if "排名" in df_range.columns
                        else ("跨榜數量", "count")
                    ),
                }
                if yt_id_col:
                    agg_kwargs["YouTube ID"] = (yt_id_col, "first")
                if yt_views_col:
                    agg_kwargs["點閱率"] = (yt_views_col, "first")

                grouped = (
                    df_range.groupby([song_col, singer_col])
                    .agg(**agg_kwargs)
                    .reset_index()
                )

                multi_chart = grouped[grouped["跨榜數量"] >= 2].sort_values(
                    by=["跨榜數量", "累積活躍天數", "最高名次"],
                    ascending=[False, False, True],
                )

                if not multi_chart.empty:

                    def build_yt_url(val):
                        v = str(val).strip() if pd.notna(val) else ""
                        if v and v not in ["-", "nan", "None", ""]:
                            return f"https://www.youtube.com/watch?v={v}"
                        return None

                    if "YouTube ID" in multi_chart.columns:
                        multi_chart["影片連結"] = multi_chart[
                            "YouTube ID"
                        ].apply(build_yt_url)
                    else:
                        multi_chart["影片連結"] = None

                    if "點閱率" in multi_chart.columns:
                        multi_chart["點閱率"] = (
                            multi_chart["點閱率"]
                            .astype(str)
                            .str.replace(",", "", regex=False)
                        )
                        multi_chart["點閱率"] = pd.to_numeric(
                            multi_chart["點閱率"], errors="coerce"
                        )
                    else:
                        multi_chart["點閱率"] = None

                    cols_order = [
                        song_col,
                        singer_col,
                        "點閱率",
                        "跨榜數量",
                        "涵蓋榜單",
                        "累積活躍天數",
                        "最高名次",
                        "影片連結",
                    ]
                    multi_chart = multi_chart[cols_order]

                    num_days = len(selected_m1_dates)
                    num_issues = len(
                        set([get_issue_label(d) for d in selected_m1_dates])
                    )

                    st.success(
                        f"🎯 涵蓋區間：{start_date} ～ {end_date}（涵蓋 {num_days} 天數據 / {num_issues} 期週榜），共找到 {len(multi_chart)} 首跨榜爆款歌曲！"
                    )
                    st.dataframe(
                        multi_chart,
                        column_config={
                            "點閱率": st.column_config.NumberColumn(
                                "點閱率", format="%,d", width="small"
                            ),
                            "跨榜數量": st.column_config.NumberColumn(
                                "跨榜數量", format="%d", width="small"
                            ),
                            "累積活躍天數": st.column_config.NumberColumn(
                                "累積活躍天數", format="%d", width="small"
                            ),
                            "最高名次": st.column_config.NumberColumn(
                                "最高名次", format="%d", width="small"
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
                        label="📥 匯出跨榜霸榜池清單 (CSV)",
                        data=csv_data,
                        file_name=f"QQ音樂_跨榜霸榜池_{start_date}_至_{end_date}.csv",
                        mime="text/csv",
                        key="m1_download_range",
                    )
                else:
                    st.info(
                        f"在 {start_date} ～ {end_date} 區間內，暫無同時登上 2 個以上榜單的歌曲。"
                    )
            else:
                st.warning(
                    "數據欄位解析異常，請確認 CSV 欄位是否包含『歌名』與『歌手』。"
                )
        else:
            st.warning(f"在 {start_date} ～ {end_date} 區間內尚無榜單資料。")

# ==========================================
# 🚀 模組二：新進黑馬雷達（波段反彈修正版）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：新進黑馬雷達與動態追蹤")
    st.markdown(
        "偵測近期新進榜、尾盤持續上升，且具備「累積跌幅後能強勢反彈超車」的潛力黑馬！"
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
    if base_date not in dates:
        valid_dates = [d for d in dates if d <= base_date]
        base_date = valid_dates[0] if valid_dates else dates[0]

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
            label_text = "📊 七日連續追蹤"
        else:
            all_thursdays = [
                d
                for d in dates
                if datetime.strptime(d, "%Y-%m-%d").weekday() == 3
            ]
            if base_date in all_thursdays:
                base_idx = all_thursdays.index(base_date)
                range_dates = all_thursdays[
                    max(0, base_idx - 6) : base_idx + 1
                ]
            else:
                range_dates = [d for d in all_thursdays if d <= base_date][-7:]
            label_text = "📊 七期連續追蹤"

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
            song_col, singer_col, rank_col = "歌名", "歌手", "排名"
            pivot_df = df_all_range.pivot_table(
                index=[song_col, singer_col],
                columns="追蹤日期",
                values=rank_col,
                aggfunc="min",
            )

            if base_date in pivot_df.columns:
                base_active_songs = pivot_df[
                    pivot_df[base_date].notna()
                ].copy()
                processed_rows = []
                min_required = 3

                for idx, row in base_active_songs.iterrows():
                    song, singer = idx
                    valid_history = row[range_dates].dropna()

                    if len(valid_history) < min_required:
                        continue

                    first_date_idx = range_dates.index(valid_history.index[0])
                    if not (1 <= first_date_idx <= 4):
                        continue

                    if valid_history.iloc[-1] >= valid_history.iloc[-2]:
                        continue

                    ranks_seq = valid_history.values
                    has_valid_rebound = False

                    i = 0
                    while i < len(ranks_seq) - 1:
                        if ranks_seq[i + 1] > ranks_seq[i]:
                            start_drop_val = ranks_seq[i]
                            peak_idx = i + 1
                            while (
                                peak_idx < len(ranks_seq)
                                and ranks_seq[peak_idx] >= ranks_seq[peak_idx - 1]
                            ):
                                peak_idx += 1
                            peak_idx -= 1
                            max_bad_val = ranks_seq[peak_idx]

                            subsequent_surge = False
                            for j in range(peak_idx + 1, len(ranks_seq)):
                                if ranks_seq[j] < start_drop_val:
                                    subsequent_surge = True
                                    break
                            if subsequent_surge:
                                has_valid_rebound = True
                                break
                            i = peak_idx
                        else:
                            i += 1

                    has_any_drop = any(
                        ranks_seq[k + 1] > ranks_seq[k]
                        for k in range(len(ranks_seq) - 1)
                    )
                    if has_any_drop and not has_valid_rebound:
                        continue

                    curr_rank = int(row[base_date])
                    highest_rank = int(valid_history.min())
                    rise_count = 0
                    max_single_rise = 0
                    for k in range(1, len(valid_history)):
                        if valid_history.iloc[k] < valid_history.iloc[k - 1]:
                            rise_count += 1
                            max_single_rise = max(
                                max_single_rise,
                                int(
                                    valid_history.iloc[k - 1]
                                    - valid_history.iloc[k]
                                ),
                            )

                    sort_score = (
                        (100 - curr_rank) * 30
                        + (rise_count * 40)
                        + max_single_rise
                    )
                    processed_rows.append(
                        {
                            song_col: song,
                            singer_col: singer,
                            "歷史最高排名": highest_rank,
                            "區間上升次數": rise_count,
                            "單次最高爬升": max_single_rise,
                            "sort_score": sort_score,
                            "raw_song": song,
                            "raw_singer": singer,
                        }
                    )

                df_result = pd.DataFrame(processed_rows)
                if not df_result.empty:
                    df_result = (
                        df_result.sort_values(by="sort_score", ascending=False)
                        .head(10)
                        .reset_index(drop=True)
                    )
                    st.success(
                        "🎯 已鎖定具備波段反彈超車能力的黑馬！"
                    )
                    st.dataframe(
                        df_result.drop(
                            columns=["sort_score", "raw_song", "raw_singer"]
                        ),
                        hide_index=True,
                        use_container_width=True,
                    )

                    st.markdown("### 📈 黑馬反彈走勢")
                    top_keys = list(
                        zip(df_result["raw_song"], df_result["raw_singer"])
                    )
                    chart_data = pivot_df.loc[top_keys, range_dates].T

                    chart_data.columns = [s for s, si in top_keys]
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
                    csv = df_result.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "📥 匯出黑馬清單 (CSV)",
                        csv,
                        f"黑馬清單_{base_date}.csv",
                        "text/csv",
                    )
                else:
                    st.info("暫無符合條件的黑馬歌曲。")

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
            if is_weekly_chart:
                target_df["榜單期數"] = target_df["抓取日期"].apply(
                    get_issue_label
                )

                evergreen = (
                    target_df.groupby([song_col, singer_col])
                    .agg(
                        累積上榜期數=("榜單期數", "nunique"),
                        平均名次=(
                            ("排名", lambda x: round(x.mean(), 1))
                            if "排名" in target_df.columns
                            else ("榜單期數", "count")
                        ),
                    )
                    .reset_index()
                    .sort_values(
                        by=["累積上榜期數", "平均名次"],
                        ascending=[False, True],
                    )
                )
            else:
                evergreen = (
                    target_df.groupby([song_col, singer_col])
                    .agg(
                        累積上榜天數=("抓取日期", "nunique"),
                        平均名次=(
                            ("排名", lambda x: round(x.mean(), 1))
                            if "排名" in target_df.columns
                            else ("抓取日期", "count")
                        ),
                    )
                    .reset_index()
                    .sort_values(
                        by=["累積上榜天數", "平均名次"],
                        ascending=[False, True],
                    )
                )

            total_units = (
                target_df["榜單期數"].nunique()
                if is_weekly_chart
                else target_df["抓取日期"].nunique()
            )
            unit_name = "期" if is_weekly_chart else "天"

            st.success(
                f"📈 【{chart_option_m3}（{'週榜' if is_weekly_chart else '日榜'}）】統計區間：{start_date} ～ {end_date}（涵蓋 {total_units} {unit_name}，共 {len(evergreen)} 首歌曲）："
            )
            st.dataframe(
                evergreen,
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
# 📺 模組四：YouTube 點閱測繪
# ==========================================
import re
import time
from datetime import date
import pandas as pd
import streamlit as st
import zhconv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
    return re.sub(r"[\s\.\-\_\(\)（）]", "", str(text)).lower()

  def extract_artist_tokens(singer):
    if not singer or str(singer).lower() in ["-", "nan", "none"]:
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

  def fetch_api_keys():
    raw_keys = st.secrets.get(
        "YOUTUBE_API_KEYS", st.secrets.get("YOUTUBE_API_KEY", [])
    )
    if isinstance(raw_keys, str):
      return [k.strip() for k in raw_keys.split(",") if k.strip()]
    elif isinstance(raw_keys, list):
      return [str(k).strip() for k in raw_keys if str(k).strip()]
    return []

  # --- 2. 核心搜尋函式（包含觀看數 -> 相關性雙重備案） ---
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

    # 兩階段搜尋：先查 viewCount（高觀看量），查無結果再退回 relevance（相關性補救）
    order_strategies = ["viewCount", "relevance"]

    for order_mode in order_strategies:
      if matched_info:
        break

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
                    maxResults=30,
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
                v_views = int(item["statistics"].get("viewCount", 0))

                duration_str = item.get("contentDetails", {}).get(
                    "duration", "PT0S"
                )
                duration_sec = parse_duration(duration_str)

                # 過濾短影音與過長影片 (60秒 ~ 10分鐘)
                if duration_sec <= 60 or duration_sec > 600:
                  continue

                v_title_lower = v_title.lower()
                v_title_norm = normalize_text(v_title)
                channel_lower = channel_title.lower()
                channel_norm = normalize_text(channel_title)

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

                singer_matched = (
                    not artist_tokens
                    or any(tkn in v_title_norm for tkn in artist_tokens)
                    or any(tkn in channel_norm for tkn in artist_tokens)
                )

                cand = {
                    "id": v_id,
                    "title": v_title,
                    "channel": channel_title,
                    "views": v_views,
                    "url": f"https://www.youtube.com/watch?v={v_id}",
                    "search_mode": order_mode,
                }

                if is_topic or singer_matched:
                  candidates.append(cand)

              if candidates:
                # 若找到多個符合條件的，依然挑選觀看數最高者
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
              row.get(
                  "歌名", row.get("song", row.get("歌曲名稱", "Unknown"))
              )
          ).strip()
          singer = str(
              row.get(
                  "歌手",
                  row.get("singer", row.get("歌手名稱", "Unknown")),
              )
          ).strip()
          rank = int(row.get("排名", idx + 1))

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
                  "觀看量"
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
                "觀看量最高"
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
# 📊 原始榜單瀏覽
# ==========================================
with main_tabs[4]:
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
