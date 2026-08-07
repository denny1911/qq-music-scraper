已將程式碼中原本使用下拉選單（st.selectbox）的單日日期選擇部分，全部改為如圖二之直覺式月曆選擇器（st.date_input）。同時加入了日期邊界限制與防呆對應，確保使用者點選月曆時能自動對應到最接近的有效數據日。

以下為修改後的完整程式碼：

Python
import streamlit as st
import pandas as pd
import os
import datetime
import altair as alt

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

# 全域共用日期邊界物件
sorted_dates_asc = sorted(dates)
earliest_date_obj = datetime.datetime.strptime(sorted_dates_asc[0], "%Y-%m-%d").date()
latest_date_obj = datetime.datetime.strptime(sorted_dates_asc[-1], "%Y-%m-%d").date()

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
    
    if m1_preset == "⚡ 單日即時":
        selected_date_obj = st.date_input(
            "📅 選擇基準日期", 
            value=latest_date_obj,
            min_value=earliest_date_obj,
            max_value=latest_date_obj,
            key="m1_single_date_picker"
        )
        selected_date = selected_date_obj.strftime("%Y-%m-%d") if isinstance(selected_date_obj, datetime.date) else None
        if selected_date and selected_date not in dates:
            valid_dates = [d for d in dates if d <= selected_date]
            selected_date = valid_dates[0] if valid_dates else dates[0]

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
            st.info("💡 **請先選擇『基準日期』**，即可開始進行單日霸榜池分析。")

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
# 🚀 模組二：新進黑馬雷達（波段反彈修正版）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：新進黑馬雷達與動態追蹤")
    st.markdown("偵測近期新進榜、尾盤持續上升，且具備「累積跌幅後能強勢反彈超車」的潛力黑馬！")

    # 1. UI 陳列
    m2_chart_option = st.radio("選擇要分析的榜單", ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"], horizontal=True, key="m2_chart_radio")
    
    base_date_obj = st.date_input(
        "📅 選擇基準日期 (預設為最新數據)",
        value=latest_date_obj,
        min_value=earliest_date_obj,
        max_value=latest_date_obj,
        key="m2_base_date_picker"
    )
    base_date = base_date_obj.strftime("%Y-%m-%d") if isinstance(base_date_obj, datetime.date) else dates[0]
    if base_date not in dates:
        valid_dates = [d for d in dates if d <= base_date]
        base_date = valid_dates[0] if valid_dates else dates[0]
    
    if base_date:
        base_dt = datetime.datetime.strptime(base_date, "%Y-%m-%d")
        
        # 邏輯區分：新歌榜抓 7 天，其餘三榜抓 7 期
        if m2_chart_option == "新歌榜":
            target_past_dt = base_dt - datetime.timedelta(days=7)
            range_dates = sorted([d for d in dates if target_past_dt <= datetime.datetime.strptime(d, "%Y-%m-%d") <= base_dt])
            label_text = "📊 七日連續追蹤"
        else:
            all_thursdays = [d for d in dates if datetime.datetime.strptime(d, "%Y-%m-%d").weekday() == 3]
            if base_date in all_thursdays:
                base_idx = all_thursdays.index(base_date)
                range_dates = all_thursdays[max(0, base_idx - 6) : base_idx + 1]
            else:
                range_dates = [d for d in all_thursdays if d <= base_date][-7:]
            label_text = "📊 七期連續追蹤"

        st.caption(f"{label_text}：`{min(range_dates)}` ➡️ `{max(range_dates)}`")

        # 讀取資料
        range_dfs = []
        for d in range_dates:
            d_full = load_date_data(d)
            if not d_full.empty:
                d_chart = d_full[d_full['榜單類型'] == m2_chart_option].copy()
                if not d_chart.empty:
                    d_chart['追蹤日期'] = d
                    range_dfs.append(d_chart)

        if range_dfs:
            df_all_range = pd.concat(range_dfs, ignore_index=True)
            song_col, singer_col, rank_col = '歌名', '歌手', '排名'
            pivot_df = df_all_range.pivot_table(index=[song_col, singer_col], columns='追蹤日期', values=rank_col, aggfunc='min')

            if base_date in pivot_df.columns:
                base_active_songs = pivot_df[pivot_df[base_date].notna()].copy()
                processed_rows = []
                min_required = 3

                for idx, row in base_active_songs.iterrows():
                    song, singer = idx
                    valid_history = row[range_dates].dropna()
                    
                    if len(valid_history) < min_required: continue
                    
                    # 1. 檢查首期/首日不在榜 (第 2~5 個點才進榜)
                    first_date_idx = range_dates.index(valid_history.index[0])
                    if not (1 <= first_date_idx <= 4): continue
                    
                    # 2. 檢查趨勢：最後兩期必須上升
                    if valid_history.iloc[-1] >= valid_history.iloc[-2]: continue

                    # 3. 波段反彈邏輯：檢查是否有發生過「下跌後被強勢反彈超車」的現象
                    ranks_seq = valid_history.values
                    has_valid_rebound = False
                    
                    i = 0
                    while i < len(ranks_seq) - 1:
                        if ranks_seq[i+1] > ranks_seq[i]:
                            start_drop_val = ranks_seq[i]
                            peak_idx = i + 1
                            while peak_idx < len(ranks_seq) and ranks_seq[peak_idx] >= ranks_seq[peak_idx-1]:
                                peak_idx += 1
                            peak_idx -= 1
                            max_bad_val = ranks_seq[peak_idx]
                            cumulative_drop = max_bad_val - start_drop_val
                            
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
                    
                    has_any_drop = any(ranks_seq[k+1] > ranks_seq[k] for k in range(len(ranks_seq)-1))
                    if has_any_drop and not has_valid_rebound:
                        continue

                    # 計算統計
                    curr_rank = int(row[base_date])
                    highest_rank = int(valid_history.min())
                    rise_count = 0
                    max_single_rise = 0
                    for k in range(1, len(valid_history)):
                        if valid_history.iloc[k] < valid_history.iloc[k-1]:
                            rise_count += 1
                            max_single_rise = max(max_single_rise, int(valid_history.iloc[k-1] - valid_history.iloc[k]))
                    
                    sort_score = (100 - curr_rank) * 30 + (rise_count * 40) + max_single_rise
                    processed_rows.append({
                        song_col: song, singer_col: singer, '歷史最高排名': str(highest_rank),
                        '區間上升次數': f"📈 {rise_count} 次", '單次最高爬升': f"🆕 {max_single_rise} 名", 'sort_score': sort_score,
                        'raw_song': song, 'raw_singer': singer
                    })

                # 顯示結果
                df_result = pd.DataFrame(processed_rows)
                if not df_result.empty:
                    df_result = df_result.sort_values(by='sort_score', ascending=False).head(10).reset_index(drop=True)
                    st.success("🎯 已鎖定具備波段反彈超車能力的黑馬！")
                    st.dataframe(df_result.drop(columns=['sort_score', 'raw_song', 'raw_singer']), hide_index=True, use_container_width=True)
                    
                    # 📊 繪圖 (無編號、鎖定圖表)
                    st.markdown("### 📈 黑馬反彈走勢")
                    top_keys = list(zip(df_result['raw_song'], df_result['raw_singer']))
                    chart_data = pivot_df.loc[top_keys, range_dates].T
                    
                    chart_data.columns = [s for s, si in top_keys]
                    chart_data.index = [f"第 {i+1} {'天' if m2_chart_option == '新歌榜' else '期'}" for i in range(len(range_dates))]
                    chart_data = chart_data.reset_index().rename(columns={'index': '追蹤時間'})
                    
                    df_melted = chart_data.melt(id_vars='追蹤時間', var_name='歌曲', value_name='名次')
                    
                    c = alt.Chart(df_melted).mark_line(point=True, strokeWidth=2.5).encode(
                        x=alt.X('追蹤時間:N', sort=None, title='追蹤時間', axis=alt.Axis(labelAngle=0)),
                        y=alt.Y('名次:Q', scale=alt.Scale(domain=[1, 100], reverse=True, clamp=True, zero=False), title='名次', axis=alt.Axis(titleAngle=0)),
                        color=alt.Color('歌曲:N', title='黑馬清單'),
                        tooltip=['追蹤時間', '歌曲', '名次']
                    ).properties(width='container', height=450)
                    
                    st.altair_chart(c, use_container_width=True)
                    csv = df_result.to_csv(index=False).encode('utf-8-sig')
                    st.download_button("📥 匯出黑馬清單 (CSV)", csv, f"黑馬清單_{base_date}.csv", "text/csv")
                else:
                    st.info("暫無符合條件的黑馬歌曲。")
                    
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
    
    selected_date_obj = st.date_input(
        "📅 選擇基準日期", 
        value=latest_date_obj,
        min_value=earliest_date_obj,
        max_value=latest_date_obj,
        key="m4_date_picker"
    )
    selected_date = selected_date_obj.strftime("%Y-%m-%d") if isinstance(selected_date_obj, datetime.date) else None
    if selected_date and selected_date not in dates:
        valid_dates = [d for d in dates if d <= selected_date]
        selected_date = valid_dates[0] if valid_dates else dates[0]
    
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
        st.info("💡 **請先選擇『基準日期』**，即可開始瀏覽原始榜單資料。")
