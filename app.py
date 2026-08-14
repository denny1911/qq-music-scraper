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
# 🚀 模組二：新進黑馬雷達（週區間窗口整合版）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：新進黑馬雷達與動態追蹤")
    st.markdown("偵測近期新進榜、名次持續爬升且點閱率大幅增長的潛力黑馬！")

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
    
    # 確保基準日是有效的
    if base_date not in dates:
        valid_dates = [d for d in dates if d <= base_date]
        base_date = valid_dates[-1] if valid_dates else dates[0]

    if base_date:
        base_dt = datetime.strptime(base_date, "%Y-%m-%d")
        
        # 建立週區間對應表 (Window Mapping)
        # 我們要把每一期視為一個「週資料桶」
        days_since_thu = (base_dt.weekday() - 3) % 7
        base_thu = base_dt - timedelta(days=days_since_thu)
        
        # 找出要追蹤的幾個期別 (例如最近 4 期)
        periods = []
        for k in range(4): # 追蹤最近 4 期
            target_thu = base_thu - timedelta(weeks=k)
            periods.append(target_thu.strftime("%Y-%m-%d"))
        
        range_dates = sorted(periods) # 這邊的 range_dates 現在代表的是「期別」
        label_text = f"📊 {len(range_dates)}期窗口動態追蹤"

        st.caption(f"{label_text}：{range_dates[0]} ➡️ {range_dates[-1]}")

        # 🎯 核心邏輯：建立「週區間」資料桶
        # 結構: { '2026-07-30': DataFrame_of_the_week }
        weekly_data_map = {}
        
        for p_date in range_dates:
            p_dt = datetime.strptime(p_date, "%Y-%m-%d")
            # 該期的 7 天窗口
            window_days = [(p_dt + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
            
            # 蒐集這 7 天內所有的檔案
            week_dfs = []
            for d in window_days:
                if d in dates:
                    d_full = load_date_data(d)
                    if not d_full.empty:
                        d_chart = d_full[d_full["榜單類型"] == m2_chart_option].copy()
                        if not d_chart.empty:
                            d_chart["實際採集日"] = d
                            week_dfs.append(d_chart)
            
            if week_dfs:
                # 合併這 7 天的資料，取「最後一天」的資料作為該週代表
                df_week = pd.concat(week_dfs, ignore_index=True)
                # 依歌名、歌手排序，取實際採集日最新的那筆資料
                df_week = df_week.sort_values("實際採集日").groupby(["歌名", "歌手"], as_index=False).last()
                weekly_data_map[p_date] = df_week

        if weekly_data_map:
            # 準備進行 pivot 分析
            all_dfs = []
            for p, df in weekly_data_map.items():
                df_copy = df.copy()
                df_copy["期別"] = p
                all_dfs.append(df_copy)
            
            df_all = pd.concat(all_dfs, ignore_index=True)
            
            song_col = "歌名" if "歌名" in df_all.columns else "song"
            singer_col = "歌手" if "歌手" in df_all.columns else "singer"
            rank_col = "排名" if "排名" in df_all.columns else "rank"

            # 萬用點閱率解析器
            def parse_views_num(val):
                if pd.isna(val) or val is None: return float("nan")
                v_str = str(val).strip().replace(",", "")
                if v_str in ["", "nan", "None", "-", "null"]: return float("nan")
                try:
                    if "萬" in v_str or "万" in v_str: return float(v_str.replace("萬", "").replace("万", "")) * 10000
                    if "k" in v_str.lower(): return float(v_str.lower().replace("k", "")) * 1000
                    if "m" in v_str.lower(): return float(v_str.lower().replace("m", "")) * 1000000
                    return float(v_str)
                except: return float("nan")

            # 統一計算點閱欄位
            view_cols = [c for c in df_all.columns if any(kw in str(c) for kw in ["點閱", "觀看", "views", "view", "播放"])]
            df_all["__unified_views__"] = float("nan")
            for vc in view_cols:
                df_all["__unified_views__"] = df_all["__unified_views__"].fillna(df_all[vc].apply(parse_views_num))

            # Pivot Table: 名次與點閱
            pivot_rank = df_all.pivot_table(index=[song_col, singer_col], columns="期別", values=rank_col, aggfunc="min")
            pivot_views = df_all.pivot_table(index=[song_col, singer_col], columns="期別", values="__unified_views__", aggfunc="last")

            # 計算結果
            processed_rows = []
            latest_period = range_dates[-1]

            for idx, row in pivot_rank.iterrows():
                song, singer = idx
                # 必須在最新一期有排名
                if pd.isna(row[latest_period]): continue
                
                # 必須有足夠的期數 (至少 2 期)
                valid_periods = row.dropna().index
                if len(valid_periods) < 2: continue
                
                initial_period = valid_periods[0]
                
                # 名次變化
                rank_surge = int(row[initial_period] - row[latest_period])
                if rank_surge <= 0: continue # 只看爬升的
                
                # 點閱變化
                view_growth = None
                if idx in pivot_views.index:
                    v_row = pivot_views.loc[idx]
                    if pd.notna(v_row[initial_period]) and pd.notna(v_row[latest_period]):
                        view_growth = int(v_row[latest_period] - v_row[initial_period])

                processed_rows.append({
                    song_col: song, singer_col: singer,
                    "點閱淨增量": view_growth,
                    "名次總爬升幅": rank_surge,
                    "追蹤期初名次": int(row[initial_period]),
                    "基準日名次": int(row[latest_period]),
                    "期別": latest_period
                })

            df_result = pd.DataFrame(processed_rows)
            
            if not df_result.empty:
                df_result = df_result.sort_values(by=["點閱淨增量", "名次總爬升幅"], ascending=[False, False], na_position="last").head(10)
                
                # 格式化顯示
                df_display = df_result.copy()
                df_display["點閱淨增量"] = df_display["點閱淨增量"].apply(lambda x: f"+{int(x):,}" if pd.notna(x) else "-")
                
                st.success(f"🎯 已鎖定 {latest_period} 期的潛力黑馬！")
                st.dataframe(df_display, hide_index=True, use_container_width=True)
            else:
                st.info("該區間內暫無符合爬升條件的黑馬。")
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
