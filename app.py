import streamlit as st
import pandas as pd
import os
import datetime

# 1. 頁面基本設定
st.set_page_config(page_title="QQ音樂熱門歌曲挑選系統", page_icon="🎵", layout="wide")

# 2. 全域 CSS 設定：強制全站表格與內容統一靠左對齊
st.markdown("""
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
""", unsafe_allow_html=True)

st.title("🎵 QQ 音樂熱門歌曲挑選系統")
st.caption("少即是多：專注於全網霸榜爆款、飆升黑馬與長青熱歌的智慧選曲平台。")

data_dir = "data"

if not os.path.exists(data_dir):
    st.error("❌ 找不到 `data/` 資料夾，請確認 GitHub Actions 是否已成功抓取資料。")
    st.stop()

# 抓取所有日期資料夾（從最新到最舊）
dates = sorted([d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))], reverse=True)

if not dates:
    st.info("目前 `data/` 資料夾內尚無日期數據。")
    st.stop()

# 輔助函式：計算週榜期數標籤 (週四為起算點)
def get_issue_label(date_str):
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    offset = (dt.weekday() - 3) % 7 # 3 代表週四
    issue_start = dt - datetime.timedelta(days=offset)
    return issue_start.strftime("%Y-%m-%d 期")

# 讀取單日所有榜單資料的輔助函式
def load_date_data(date_str):
    day_path = os.path.join(data_dir, date_str)
    charts = {
        "new": "新歌榜",
        "film": "影視金曲榜",
        "show": "綜藝新歌榜",
        "tik": "抖音熱歌榜"
    }
    dfs = []
    for key, name in charts.items():
        fpath = os.path.join(day_path, f"{date_str}_{key}.csv")
        if os.path.exists(fpath):
            try:
                df = pd.read_csv(fpath)
                df['榜單類型'] = name
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

# 輔助函式：強行將 DataFrame 所有欄位轉字串以實現靠左對齊
def format_df_for_display(df):
    display_df = df.copy()
    for col in display_df.columns:
        display_df[col] = display_df[col].astype(str)
    return display_df

# 輔助函式：轉碼 CSV 匯出專用（純淨 4 直欄：歌名、歌手、專輯、發行日期）
def get_clean_export_df(source_df, filtered_songs_df):
    if source_df.empty or filtered_songs_df.empty:
        return pd.DataFrame(columns=['歌名', '歌手', '專輯', '發行日期'])
        
    song_col = '歌名' if '歌名' in source_df.columns else ('song' if 'song' in source_df.columns else None)
    singer_col = '歌手' if '歌手' in source_df.columns else ('singer' if 'singer' in source_df.columns else None)
    
    if not song_col or not singer_col:
        return filtered_songs_df

    target_order = ['歌名', '歌手', '專輯', '發行日期']
    col_map = {}
    for target in target_order:
        if target in source_df.columns:
            col_map[target] = target
        elif target == '歌名' and 'song' in source_df.columns:
            col_map['song'] = '歌名'
        elif target == '歌手' and 'singer' in source_df.columns:
            col_map['singer'] = '歌手'
        elif target == '專輯' and 'album' in source_df.columns:
            col_map['album'] = '專輯'
        elif target == '發行日期' and 'public_time' in source_df.columns:
            col_map['public_time'] = '發行日期'
            
    cols_to_extract = list(col_map.keys())
    keys = filtered_songs_df[[song_col, singer_col]].drop_duplicates()
    
    merged = pd.merge(
        keys, 
        source_df[cols_to_extract].drop_duplicates(subset=[song_col, singer_col]), 
        on=[song_col, singer_col], 
        how='left'
    )
    
    merged = merged.rename(columns=col_map)
    final_cols = [c for c in target_order if c in merged.columns]
    return merged[final_cols]

# 主介面四大分頁
main_tabs = st.tabs([
    "🔥 模組一：全網霸榜池",
    "🚀 模組二：黑馬雷達與動態追蹤",
    "👑 模組三：榜單常勝軍",
    "📊 原始榜單瀏覽"
])

# ==========================================
# 🏆 模組一：全網霸榜池（最猛爆款）
# ==========================================
with main_tabs[0]:
    st.header("🔥 模組一：全網跨榜霸榜池")
    st.markdown("自動比對榜單數據，篩選出**登上 2 個（含）以上榜單**的神曲，指標最硬不踩雷！")
    
    m1_preset = st.radio(
        "🗓️ 選擇分析時間範圍",
        ["⚡ 單日即時", "⚡ 近 7 天", "⚡ 近 30 天", "🌐 全部歷史區間", "📅 自訂月曆區間"],
        horizontal=True,
        key="m1_preset_radio"
    )
    
    sorted_dates_asc = sorted(dates)
    earliest_date_obj = datetime.datetime.strptime(sorted_dates_asc[0], "%Y-%m-%d").date()
    latest_date_obj = datetime.datetime.strptime(sorted_dates_asc[-1], "%Y-%m-%d").date()
    
    if m1_preset == "⚡ 單日即時":
        selected_date = st.selectbox(
            "📅 選擇主要基準日期", 
            options=dates, 
            index=None, 
            placeholder="請選擇主要基準日期...", 
            key="m1_date"
        )
        if selected_date:
            df_curr = load_date_data(selected_date)
            if not df_curr.empty:
                song_col = '歌名' if '歌名' in df_curr.columns else ('song' if 'song' in df_curr.columns else None)
                singer_col = '歌手' if '歌手' in df_curr.columns else ('singer' if 'singer' in df_curr.columns else None)
                
                if song_col and singer_col:
                    grouped = df_curr.groupby([song_col, singer_col]).agg(
                        登榜數量=('榜單類型', 'nunique'),
                        登上榜單=('榜單類型', lambda x: "、".join(sorted(set(x)))),
                        最高名次=('排名', 'min') if '排名' in df_curr.columns else ('登榜數量', 'count')
                    ).reset_index()
                    
                    multi_chart = grouped[grouped['登榜數量'] >= 2].sort_values(by=['登榜數量', '最高名次'], ascending=[False, True])
                    
                    if not multi_chart.empty:
                        multi_chart['爆款屬性標籤'] = multi_chart['登上榜單'].apply(generate_song_tags)
                        
                        cols_order = [song_col, singer_col, '登榜數量', '爆款屬性標籤', '登上榜單', '最高名次']
                        multi_chart = multi_chart[cols_order]
                        
                        st.success(f"🎯 在 {selected_date} 當天，共找到 {len(multi_chart)} 首跨榜爆款歌曲！")
                        st.dataframe(format_df_for_display(multi_chart), hide_index=True, use_container_width=True)
                        
                        export_df = get_clean_export_df(df_curr, multi_chart)
                        csv_data = export_df.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            label="📥 匯出單日霸榜池清單 (CSV)",
                            data=csv_data,
                            file_name=f"QQ音樂_單日霸榜池_{selected_date}.csv",
                            mime="text/csv",
                            key="m1_download_1d"
                        )
                    else:
                        st.info(f"在 {selected_date} 當天，暫無同時登上 2 個以上榜單的歌曲。")
                else:
                    st.warning("數據欄位解析異常，請確認 CSV 欄位是否包含『歌名』與『歌手』。")
            else:
                st.warning(f"{selected_date} 尚無榜單資料。")
        else:
            st.info("💡 **請先選擇『主要基準日期』**，即可開始進行單日霸榜池分析。")

    else:
        if m1_preset == "⚡ 近 7 天":
            start_date_obj = max(earliest_date_obj, latest_date_obj - datetime.timedelta(days=6))
            end_date_obj = latest_date_obj
        elif m1_preset == "⚡ 近 30 天":
            start_date_obj = max(earliest_date_obj, latest_date_obj - datetime.timedelta(days=29))
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
                key="m1_date_picker"
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
                d_df['抓取日期'] = d
                all_dfs.append(d_df)
                
        if all_dfs:
            df_range = pd.concat(all_dfs, ignore_index=True)
            song_col = '歌名' if '歌名' in df_range.columns else ('song' if 'song' in df_range.columns else None)
            singer_col = '歌手' if '歌手' in df_range.columns else ('singer' if 'singer' in df_range.columns else None)
            
            if song_col and singer_col:
                grouped = df_range.groupby([song_col, singer_col]).agg(
                    跨榜數量=('榜單類型', 'nunique'),
                    涵蓋榜單=('榜單類型', lambda x: "、".join(sorted(set(x)))),
                    累積活躍天數=('抓取日期', 'nunique'),
                    最高名次=('排名', 'min') if '排名' in df_range.columns else ('跨榜數量', 'count')
                ).reset_index()
                
                multi_chart = grouped[grouped['跨榜數量'] >= 2].sort_values(
                    by=['跨榜數量', '累積活躍天數', '最高名次'],
                    ascending=[False, False, True]
                )
                
                if not multi_chart.empty:
                    multi_chart['爆款屬性標籤'] = multi_chart['涵蓋榜單'].apply(generate_song_tags)
                    
                    cols_order = [song_col, singer_col, '跨榜數量', '爆款屬性標籤', '涵蓋榜單', '累積活躍天數', '最高名次']
                    multi_chart = multi_chart[cols_order]
                    
                    num_days = len(selected_m1_dates)
                    num_issues = len(set([get_issue_label(d) for d in selected_m1_dates]))
                    
                    st.success(f"🎯 涵蓋區間：{start_date} ～ {end_date}（涵蓋 {num_days} 天數據 / {num_issues} 期週榜），共找到 {len(multi_chart)} 首跨榜爆款歌曲！")
                    st.dataframe(format_df_for_display(multi_chart), hide_index=True, use_container_width=True)
                    
                    export_df = get_clean_export_df(df_range, multi_chart)
                    csv_data = export_df.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label="📥 匯出跨榜霸榜池清單 (CSV)",
                        data=csv_data,
                        file_name=f"QQ音樂_跨榜霸榜池_{start_date}_至_{end_date}.csv",
                        mime="text/csv",
                        key="m1_download_range"
                    )
                else:
                    st.info(f"在 {start_date} ～ {end_date} 區間內，暫無同時登上 2 個以上榜單的歌曲。")
            else:
                st.warning("數據欄位解析異常，請確認 CSV 欄位是否包含『歌名』與『歌手』。")
        else:
            st.warning(f"在 {start_date} ～ {end_date} 區間內尚無榜單資料。")

# ==========================================
# 🚀 模組二：黑馬雷達與動態追蹤（全新淨爬升演算法）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：黑馬雷達與動態追蹤")
    st.markdown("採用**『淨爬升名次（Net Rank Change）』**演算法，自動比對基準日與歷史對比日，精準鎖定 Top 10 爆發黑馬與新進黑馬！")

    m2_chart_option = st.radio(
        "選擇要分析的榜單",
        ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"],
        horizontal=True,
        key="m2_chart_radio"
    )

    m2_preset = st.radio(
        "🗓️ 選擇黑馬分析週期",
        ["⚡ 近 7 天短期爆發黑馬", "📈 近 30 天中長期逆襲黑馬"],
        horizontal=True,
        key="m2_preset_radio"
    )

    base_date = st.selectbox(
        "📅 選擇基準日期 (預設為最新數據)", 
        options=dates, 
        index=0, 
        key="m2_base_date"
    )

    if base_date:
        base_dt = datetime.datetime.strptime(base_date, "%Y-%m-%d")
        delta_days = 7 if "7 天" in m2_preset else 30
        target_past_dt = base_dt - datetime.timedelta(days=delta_days)

        available_past_dates = [d for d in dates if datetime.datetime.strptime(d, "%Y-%m-%d") <= target_past_dt]

        if not available_past_dates:
            past_date = sorted(dates)[0]
            st.caption(f"ℹ️ 系統內歷史數據不足 {delta_days} 天，已自動改用最早可得日期：`{past_date}` 進行對比。")
        else:
            past_date = max(available_past_dates)
            st.caption(f"📊 數據對比基準：`{base_date}` 🆚 歷史對比日：`{past_date}`（跨度約 {delta_days} 天）")

        df_base_full = load_date_data(base_date)
        df_past_full = load_date_data(past_date)

        if not df_base_full.empty:
            song_col = '歌名' if '歌名' in df_base_full.columns else ('song' if 'song' in df_base_full.columns else None)
            singer_col = '歌手' if '歌手' in df_base_full.columns else ('singer' if 'singer' in df_base_full.columns else None)
            rank_col = '排名' if '排名' in df_base_full.columns else ('rank' if 'rank' in df_base_full.columns else None)

            if song_col and singer_col and rank_col:
                base_chart = df_base_full[df_base_full['榜單類型'] == m2_chart_option]
                past_chart = df_past_full[df_past_full['榜單類型'] == m2_chart_option] if not df_past_full.empty else pd.DataFrame()

                if not base_chart.empty:
                    max_rank_val = base_chart[rank_col].max() if not base_chart.empty else 100
                    
                    if not past_chart.empty and rank_col in past_chart.columns:
                        merged = pd.merge(
                            base_chart,
                            past_chart[[song_col, singer_col, rank_col]],
                            on=[song_col, singer_col],
                            how='left',
                            suffixes=('_基準', '_過去')
                        )
                        merged['過去排名_temp'] = merged[f'{rank_col}_過去'].fillna(max_rank_val + 20)
                        merged['淨爬升名次'] = merged['過去排名_temp'] - merged[f'{rank_col}_基準']
                        merged['對比歷史排名'] = merged[f'{rank_col}_過去'].apply(lambda x: "🆕 全新進榜" if pd.isna(x) else str(int(x)))
                        merged['基準日排名'] = merged[f'{rank_col}_基準'].astype(int).astype(str)
                        
                        black_horses = merged[merged['淨爬升名次'] > 0].sort_values(by='淨爬升名次', ascending=False)
                        
                        if not black_horses.empty:
                            display_top = black_horses.head(10).copy()
                            
                            display_df = display_top[[song_col, singer_col, '對比歷史排名', '基準日排名', '淨爬升名次']].copy()
                            display_df.columns = ['歌名', '歌手', '對比歷史排名', '基準日排名', '淨爬升名次']
                            display_df['淨爬升名次'] = display_df['淨爬升名次'].apply(lambda x: f"⬆️ 暴增進步 {int(x)} 名")
                            
                            st.success(f"🎯 在【{m2_chart_option}】中，成功鎖定以下 Top 10 潛力黑馬！")
                            st.dataframe(format_df_for_display(display_df), hide_index=True, use_container_width=True)
                            
                            export_df = get_clean_export_df(base_chart, display_top)
                            csv_data = export_df.to_csv(index=False).encode('utf-8-sig')
                            st.download_button(
                                label=f"📥 匯出【{m2_chart_option}】Top 10 黑馬清單 (CSV)",
                                data=csv_data,
                                file_name=f"QQ音樂_黑馬雷達_{m2_chart_option}_{base_date}.csv",
                                mime="text/csv",
                                key="m2_download_btn"
                            )
                        else:
                            st.info(f"在所選區間內，【{m2_chart_option}】暫無名次正成長的黑馬歌曲。")
                    else:
                        st.warning("歷史對比數據不足或欄位解析異常，無法進行跨期淨爬升計算。")
                else:
                    st.warning(f"在 {base_date} 找不到【{m2_chart_option}】的資料。")
            else:
                st.warning("資料欄位解析異常（找不到歌名、歌手或排名欄位）。")
        else:
            st.warning(f"無法載入 {base_date} 的資料。")

# ==========================================
# 👑 模組三：榜單常勝軍（長青熱歌）
# ==========================================
with main_tabs[2]:
    st.header("👑 模組三：榜單常勝軍（長青熱歌）")
    st.markdown("統計**指定日期區間**內，在個別榜單的累積表現（**新歌榜統計天數，其餘三榜依官方週四更新期數統計**）。")
    
    chart_option_m3 = st.radio(
        "選擇要統計常勝軍的榜單",
        ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"],
        horizontal=True,
        key="m3_radio"
    )
    
    is_weekly_chart = chart_option_m3 != "新歌榜"
    
    m3_preset = st.radio(
        "🗓️ 選擇統計時間範圍",
        ["⚡ 近 7 天", "⚡ 近 30 天", "🌐 全部歷史區間", "📅 自訂月曆區間"],
        horizontal=True,
        key="m3_preset_radio"
    )
    
    sorted_dates_asc = sorted(dates)
    earliest_date_obj = datetime.datetime.strptime(sorted_dates_asc[0], "%Y-%m-%d").date()
    latest_date_obj = datetime.datetime.strptime(sorted_dates_asc[-1], "%Y-%m-%d").date()
    
    if m3_preset == "⚡ 近 7 天":
        start_date_obj = max(earliest_date_obj, latest_date_obj - datetime.timedelta(days=6))
        end_date_obj = latest_date_obj
    elif m3_preset == "⚡ 近 30 天":
        start_date_obj = max(earliest_date_obj, latest_date_obj - datetime.timedelta(days=29))
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
            key="m3_date_picker"
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
            d_df['抓取日期'] = d
            all_dfs.append(d_df)
            
    if all_dfs:
        full_df = pd.concat(all_dfs, ignore_index=True)
        song_col = '歌名' if '歌名' in full_df.columns else 'song'
        singer_col = '歌手' if '歌手' in full_df.columns else 'singer'
        
        target_df = full_df[full_df['榜單類型'] == chart_option_m3].copy()
            
        if not target_df.empty:
            if is_weekly_chart:
                target_df['榜單期數'] = target_df['抓取日期'].apply(get_issue_label)
                
                evergreen = target_df.groupby([song_col, singer_col]).agg(
                    累積上榜期數=('榜單期數', 'nunique'),
                    平均名次=('排名', lambda x: round(x.mean(), 1)) if '排名' in target_df.columns else ('榜單期數', 'count')
                ).reset_index().sort_values(by=['累積上榜期數', '平均名次'], ascending=[False, True])
            else:
                evergreen = target_df.groupby([song_col, singer_col]).agg(
                    累積上榜天數=('抓取日期', 'nunique'),
                    平均名次=('排名', lambda x: round(x.mean(), 1)) if '排名' in target_df.columns else ('抓取日期', 'count')
                ).reset_index().sort_values(by=['累積上榜天數', '平均名次'], ascending=[False, True])
            
            total_units = target_df['榜單期數'].nunique() if is_weekly_chart else target_df['抓取日期'].nunique()
            unit_name = "期" if is_weekly_chart else "天"

            st.success(f"📈 【{chart_option_m3}（{ '週榜' if is_weekly_chart else '日榜' }）】統計區間：{start_date} ～ {end_date}（涵蓋 {total_units} {unit_name}，共 {len(evergreen)} 首歌曲）：")
            st.dataframe(format_df_for_display(evergreen), hide_index=True, use_container_width=True)
            
            export_df = get_clean_export_df(target_df, evergreen)
            csv_data = export_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label=f"📥 匯出【{chart_option_m3}】常勝軍清單 (CSV)",
                data=csv_data,
                file_name=f"QQ音樂_榜單常勝軍_{chart_option_m3}_{start_date}_至_{end_date}.csv",
                mime="text/csv",
                key="m3_download"
            )
        else:
            st.info(f"在 {start_date} ～ {end_date} 區間內，尚無【{chart_option_m3}】的數據。")
    else:
        st.info("選定日期區間內無數據。")

# ==========================================
# 📊 原始榜單瀏覽
# ==========================================
with main_tabs[3]:
    st.header("📊 原始各榜單數據瀏覽")
    
    selected_date = st.selectbox(
        "📅 選擇主要基準日期", 
        options=dates, 
        index=None, 
        placeholder="請選擇主要基準日期...", 
        key="m4_date"
    )
    
    if selected_date:
        charts = {
            "新歌榜 (日榜)": "new",
            "影視金曲榜 (週榜)": "film",
            "綜藝新歌榜 (週榜)": "show",
            "抖音熱歌榜 (週榜)": "tik"
        }
        
        sub_tabs = st.tabs(list(charts.keys()))
        day_path = os.path.join(data_dir, selected_date)
        
        for tab, (chart_name, chart_key) in zip(sub_tabs, charts.items()):
            with tab:
                file_name = f"{selected_date}_{chart_key}.csv"
                file_path = os.path.join(day_path, file_name)
                
                if os.path.exists(file_path):
                    df = pd.read_csv(file_path)
                    
                    cols_to_drop = [c for c in ['抓取日期', '榜單類型', '榜單種類'] if c in df.columns]
                    if cols_to_drop:
                        df = df.drop(columns=cols_to_drop)

                    st.success(f"📅 數據日期：{selected_date}｜共 {len(df)} 筆排名資料")
                    
                    search_term = st.text_input(f"🔍 在【{chart_name}】中搜尋歌名或歌手", key=f"raw_{chart_key}")
                    if search_term:
                        mask = df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
                        df = df[mask]
                    
                    st.dataframe(format_df_for_display(df), hide_index=True, use_container_width=True)
                    
                    csv_data = df.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label=f"📥 匯出【{chart_name}】原始資料 (CSV)",
                        data=csv_data,
                        file_name=f"{selected_date}_{chart_key}.csv",
                        mime="text/csv",
                        key=f"m4_download_{chart_key}"
                    )
                else:
                    st.warning(f"⚠️ {selected_date} 尚未抓取到 {chart_name} 的 CSV 檔案 ({file_name})。")
    else:
        st.info("💡 **請先選擇『主要基準日期』**，即可開始瀏覽原始榜單資料。")
