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
    "🚀 模組二：飆升與新進黑馬",
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
        # 區間分析模式
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
# 🚀 模組二：飆升與新進黑馬（新鮮潮流）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：飆升與新進黑馬")
    st.markdown("對比前後數據，找出名次大幅爬升或全新進榜（New Entry）的潛力黑馬歌曲！")
    
    def reset_m2_selections():
        for key in ["m2_curr_issue", "m2_comp_issue", "m2_date", "m2_compare_date"]:
            if key in st.session_state:
                st.session_state[key] = None

    chart_option = st.radio(
        "選擇要比對的榜單", 
        ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"], 
        horizontal=True, 
        key="m2_radio",
        on_change=reset_m2_selections
    )
    is_weekly_chart = chart_option != "新歌榜"

    if is_weekly_chart:
        all_issues = sorted(list(set([get_issue_label(d) for d in dates])), reverse=True)
        
        if len(all_issues) >= 2:
            col1, col2 = st.columns(2)
            
            with col1:
                curr_issue = st.selectbox(
                    "📅 第一步：選擇當期週榜", 
                    options=all_issues, 
                    index=None, 
                    placeholder="請選擇當期週榜...", 
                    key="m2_curr_issue"
                )
            
            history_issues = []
            with col2:
                if curr_issue:
                    curr_idx = all_issues.index(curr_issue)
                    history_issues = all_issues[curr_idx + 1:]
                    
                    if history_issues:
                        compare_issue = st.selectbox(
                            "🔍 第二步：選擇對比歷史週榜", 
                            options=history_issues, 
                            index=None, 
                            placeholder="請選擇對比歷史週榜...", 
                            key="m2_comp_issue"
                        )
                    else:
                        st.selectbox("🔍 第二步：選擇對比歷史週榜", ["無更早的歷史週榜"], index=0, disabled=True, key="m2_comp_issue_disabled")
                        compare_issue = None
                else:
                    st.selectbox("🔍 第二步：選擇對比歷史週榜", ["請先選擇第一步當期週榜"], index=0, disabled=True, key="m2_comp_issue_waiting")
                    compare_issue = None

            if compare_issue:
                curr_dates = [d for d in dates if get_issue_label(d) == curr_issue]
                comp_dates = [d for d in dates if get_issue_label(d) == compare_issue]
                
                df_now = load_date_data(curr_dates[0])
                df_prev = load_date_data(comp_dates[0])
                
                now_chart = df_now[df_now['榜單類型'] == chart_option]
                prev_chart = df_prev[df_prev['榜單類型'] == chart_option]
                
                song_col = '歌名' if '歌名' in now_chart.columns else 'song'
                singer_col = '歌手' if '歌手' in now_chart.columns else 'singer'
                rank_col = '排名' if '排名' in now_chart.columns else 'rank'

                merged = pd.merge(
                    now_chart[[song_col, singer_col, rank_col]],
                    prev_chart[[song_col, singer_col, rank_col]],
                    on=[song_col, singer_col],
                    how='left',
                    suffixes=('_當前', '_前次')
                )
                
                def calc_status(row):
                    if pd.isna(row[f'{rank_col}_前次']):
                        return "🆕 全新進榜"
                    diff = row[f'{rank_col}_前次'] - row[f'{rank_col}_當前']
                    if diff > 0:
                        return f"⬆️ 爬升 {int(diff)} 名"
                    elif diff < 0:
                        return f"⬇️ 下降 {int(abs(diff))} 名"
                    else:
                        return "➡️ 持平"
                
                merged['名次變動'] = merged.apply(calc_status, axis=1)
                merged['當期週榜排名'] = merged[f'{rank_col}_當前'].apply(lambda x: str(int(x)) if pd.notna(x) else "")
                merged['對比歷史週榜排名'] = merged[f'{rank_col}_前次'].apply(lambda x: "未入榜" if pd.isna(x) else str(int(x)))
                
                rising = merged[merged['名次變動'].str.contains('全新進榜|爬升')].sort_values(by=f'{rank_col}_當前')
                
                st.success(f"📊 【{chart_option}】跨期對比：{curr_issue} vs {compare_issue}（共找到 {len(rising)} 首上升或新進榜歌曲）")
                st.dataframe(format_df_for_display(rising[[song_col, singer_col, '對比歷史週榜排名', '當期週榜排名', '名次變動']]), hide_index=True, use_container_width=True)
                
                export_df = get_clean_export_df(now_chart, rising)
                csv_data = export_df.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label=f"📥 匯出【{chart_option}】飆升與新進黑馬清單 (CSV)",
                    data=csv_data,
                    file_name=f"QQ音樂_飆升與新進黑馬_{chart_option}_{curr_issue}.csv",
                    mime="text/csv",
                    key="m2_download_weekly"
                )
            elif history_issues:
                st.info("💡 **請在右上角選取『對比歷史週榜』**，即可開始進行名次變動分析。")
            elif not curr_issue:
                st.info("💡 **請先選擇第一步『當期週榜』**。")
            else:
                st.warning("⚠️ 您選擇的『當期週榜』為系統內最早的一期，無更早的歷史週榜可供對比。")
        else:
            st.info(f"💡 **週榜比對說明**：【{chart_option}】為週榜。目前資料區間皆屬於同一期，尚無歷史週榜可供跨期比對。")

    else:
        if len(dates) >= 2:
            col1, col2 = st.columns(2)
            
            with col1:
                selected_date = st.selectbox(
                    "📅 第一步：選擇主要基準日期", 
                    options=dates, 
                    index=None, 
                    placeholder="請選擇主要基準日期...", 
                    key="m2_date"
                )
            
            history_dates = []
            with col2:
                if selected_date:
                    curr_idx = dates.index(selected_date)
                    history_dates = dates[curr_idx + 1:]
                    
                    if history_dates:
                        compare_date = st.selectbox(
                            "🔍 第二步：選擇對比歷史日期", 
                            options=history_dates, 
                            index=None, 
                            placeholder="請選擇對比歷史日期...", 
                            key="m2_compare_date"
                        )
                    else:
                        st.selectbox("🔍 第二步：選擇對比歷史日期", ["無更早的歷史日期"], index=0, disabled=True, key="m2_comp_date_disabled")
                        compare_date = None
                else:
                    st.selectbox("🔍 第二步：選擇對比歷史日期", ["請先選擇第一步主要基準日期"], index=0, disabled=True, key="m2_comp_date_waiting")
                    compare_date = None

            if compare_date:
                df_now = load_date_data(selected_date)
                df_prev = load_date_data(compare_date)
                
                song_col = '歌名' if '歌名' in df_now.columns else 'song'
                singer_col = '歌手' if '歌手' in df_now.columns else 'singer'
                rank_col = '排名' if '排名' in df_now.columns else 'rank'
                
                now_chart = df_now[df_now['榜單類型'] == chart_option]
                prev_chart = df_prev[df_prev['榜單類型'] == chart_option]
                
                if not now_chart.empty:
                    merged = pd.merge(
                        now_chart[[song_col, singer_col, rank_col]],
                        prev_chart[[song_col, singer_col, rank_col]],
                        on=[song_col, singer_col],
                        how='left',
                        suffixes=('_當前', '_前次')
                    )
                    
                    def calc_status(row):
                        if pd.isna(row[f'{rank_col}_前次']):
                            return "🆕 全新進榜"
                        diff = row[f'{rank_col}_前次'] - row[f'{rank_col}_當前']
                        if diff > 0:
                            return f"⬆️ 爬升 {int(diff)} 名"
                        elif diff < 0:
                            return f"⬇️ 下降 {int(abs(diff))} 名"
                        else:
                            return "➡️ 持平"
                    
                    merged['名次變動'] = merged.apply(calc_status, axis=1)
                    merged['當前日榜排名'] = merged[f'{rank_col}_當前'].apply(lambda x: str(int(x)) if pd.notna(x) else "")
                    merged['對比歷史日榜排名'] = merged[f'{rank_col}_前次'].apply(lambda x: "未入榜" if pd.isna(x) else str(int(x)))
                    
                    rising = merged[merged['名次變動'].str.contains('全新進榜|爬升')].sort_values(by=f'{rank_col}_當前')
                    
                    st.success(f"📊 【{chart_option}】對比：{selected_date} vs {compare_date}（共找到 {len(rising)} 首上升或新進榜歌曲）")
                    st.dataframe(format_df_for_display(rising[[song_col, singer_col, '對比歷史日榜排名', '當前日榜排名', '名次變動']]), hide_index=True, use_container_width=True)
                    
                    export_df = get_clean_export_df(now_chart, rising)
                    csv_data = export_df.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label=f"📥 匯出【{chart_option}】飆升與新進黑馬清單 (CSV)",
                        data=csv_data,
                        file_name=f"QQ音樂_飆升與新進黑馬_{chart_option}_{selected_date}.csv",
                        mime="text/csv",
                        key="m2_download_daily"
                    )
            elif history_dates:
                st.info("💡 **請在右上角選取『對比歷史日期』**，即可開始進行名次變動分析。")
            elif not selected_date:
                st.info("💡 **請先選擇第一步『主要基準日期』**。")
            else:
                st.warning("⚠️ 您選擇的『基準日期』為系統內最早的一天，無更早的歷史日期可供對比。")
        else:
            st.info("目前系統內只有單日數據，尚無法進行日榜跨期對比。")

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
