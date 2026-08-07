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
# 🚀 模組二：黑馬雷達與動態追蹤（含每日走勢折線圖）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：黑馬雷達與動態追蹤")
    st.markdown("連線期間內**每日數據**，完整計算**「區間內上升次數」**與**「新進榜優先排序」**，並透過互動折線圖透視完整成長軌跡！")

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
            st.caption(f"ℹ️ 系統內歷史數據不足 {delta_days} 天, 已自動改用最早可得日期：`{past_date}` 進行連續軌跡追蹤。")
        else:
            past_date = max(available_past_dates)
            st.caption(f"📊 多日連續追蹤區間：`{past_date}` ➡️ `{base_date}`（跨度約 {delta_days} 天）")

        # 取得該區間內所有的實際存在日期（按時間排序由舊到新）
        range_dates = sorted([d for d in dates if past_date <= d <= base_date])

        if len(range_dates) < 2:
            st.warning("⚠️ 區間內可比對的歷史天數不足兩天，無法計算上升軌跡。")
        else:
            # 載入區間內每一天的資料
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
                song_col = '歌名' if '歌名' in df_all_range.columns else ('song' if 'song' in df_all_range.columns else None)
                singer_col = '歌手' if '歌手' in df_all_range.columns else ('singer' if 'singer' in df_all_range.columns else None)
                rank_col = '排名' if '排名' in df_all_range.columns else ('rank' if 'rank' in df_all_range.columns else None)

                if song_col and singer_col and rank_col:
                    # 建立透視表：列是 (歌名, 歌手)，行是日期，值是排名
                    pivot_df = df_all_range.pivot_table(
                        index=[song_col, singer_col],
                        columns='追蹤日期',
                        values=rank_col,
                        aggfunc='min'
                    )

                    # 篩選在「基準日」(base_date) 有上榜的歌曲
                    if base_date in pivot_df.columns:
                        base_active_songs = pivot_df[pivot_df[base_date].notna()].copy()
                        
                        processed_rows = []
                        for idx, row in base_active_songs.iterrows():
                            song, singer = idx
                            
                            song_history = row[range_dates]
                            valid_history = song_history.dropna()
                            if valid_history.empty:
                                continue
                            
                            # 1. 歷史最高排名
                            highest_rank = int(valid_history.min())
                            curr_rank = int(row[base_date])  # 基準日（最後一天）的名次
                            
                            # 🚨 關鍵過濾一：基準日名次離歷史最高名次不能掉超過 15 名（過濾衝高後大崩盤的歌曲）
                            if (curr_rank - highest_rank) > 15:
                                continue
                            
                            # 🚨 關鍵過濾二：最後一天（基準日）不能處於急劇下滑狀態（比前一天跌超過 5 名視為退燒）
                            if len(range_dates) >= 2:
                                prev_date = range_dates[-2]
                                prev_rank = row.get(prev_date, float('nan'))
                                if pd.notna(prev_rank) and (curr_rank - int(prev_rank)) > 5:
                                    continue
                            
                            # 歷史最低排名顯示
                            if song_history.isna().any():
                                lowest_rank_display = "排行榜外"
                            else:
                                lowest_rank_display = str(int(song_history.max()))
                            
                            rise_count = 0
                            max_single_rise = 0
                            
                            for i in range(1, len(range_dates)):
                                d_prev = range_dates[i-1]
                                d_curr = range_dates[i]
                                r_prev = row.get(d_prev, float('nan'))
                                r_curr = row.get(d_curr, float('nan'))
                                
                                if pd.notna(r_prev) and pd.notna(r_curr):
                                    if r_curr < r_prev:
                                        rise_count += 1
                                        jump = int(r_prev - r_curr)
                                        if jump > max_single_rise:
                                            max_single_rise = jump
                                elif pd.notna(r_curr) and pd.isna(r_prev):
                                    rise_count += 1
                                    target_x = int(r_curr)
                                    jump = int(100 - target_x)
                                    if jump > max_single_rise:
                                        max_single_rise = jump

                            past_rank_val = row.get(past_date, float('nan'))
                            
                            # 🎯 新的合理計分公式：綜合考慮「目前名次高低」、「離頂峰近不近」與「暴衝力道」
                            peak_closeness = 15 - (curr_rank - highest_rank)  # 越接近最高名次分數越高
                            current_rank_score = 100 - curr_rank              # 目前名次越靠前分數越高
                            
                            if pd.isna(past_rank_val):
                                sort_score = 10000 + (current_rank_score * 10) + (peak_closeness * 5) + max_single_rise
                                display_text = f"🆕 新進榜 (單次最高衝 {max_single_rise} 名)"
                            else:
                                past_rank = int(past_rank_val)
                                net_change = past_rank - curr_rank
                                if net_change > 0:
                                    sort_score = (current_rank_score * 10) + (peak_closeness * 5) + net_change
                                    display_text = f"🚀 單次最高衝 {max_single_rise} 名"
                                else:
                                    continue 

                            processed_rows.append({
                                song_col: song,
                                singer_col: singer,
                                '歷史最低排名': lowest_rank_display,
                                '歷史最高排名': highest_rank,
                                '區間上升次數': f"📈 {rise_count} 次",
                                '單次最高爬升': display_text,
                                'sort_score': sort_score,
                                'raw_song': song,
                                'raw_singer': singer
                            })

                        df_result = pd.DataFrame(processed_rows)

                        if not df_result.empty:
                            df_result = df_result.sort_values(by='sort_score', ascending=False).head(10).reset_index(drop=True)
                            
                            df_result['黑馬綜合排名'] = range(1, len(df_result) + 1)
                            
                            display_df = df_result[['黑馬綜合排名', song_col, singer_col, '歷史最低排名', '歷史最高排名', '區間上升次數', '單次最高爬升']].copy()
                            display_df.columns = ['黑馬綜合排名', '歌名', '歌手', '歷史最低排名', '歷史最高排名', '區間上升次數', '單次最高爬升']
                            
                            st.success(f"🎯 在【{m2_chart_option}】中，已成功鎖定 Top 10 潛力黑馬！")
                            st.dataframe(format_df_for_display(display_df), hide_index=True, use_container_width=True)
                            
                            # 📊 Top 10 黑馬每日名次走勢圖
                            st.markdown("### 📈 Top 10 黑馬每日名次走勢圖")
                            st.caption("💡 註：X 軸為追蹤序列天數，Y 軸已固定範圍（1 在最上方，100 在最下方）。")
                            
                            top_keys = list(zip(df_result['raw_song'], df_result['raw_singer']))
                            chart_subset = pivot_df.loc[top_keys, range_dates].T
                            
                            chart_subset.index = [f"第 {i+1} 天" for i in range(len(chart_subset))]
                            
                            column_names = []
                            for rank, (s, si) in enumerate(top_keys, start=1):
                                column_names.append(f"{rank}. {s} ({si})")
                            chart_subset.columns = column_names
                            
                            df_melted = chart_subset.reset_index().melt(
                                id_vars=['index'],
                                var_name='黑馬綜合排名',
                                value_name='名次'
                            ).rename(columns={'index': '追蹤天數'})
                            
                            c = alt.Chart(df_melted).mark_line(point=True, strokeWidth=2).encode(
                                x=alt.X('追蹤天數:N', sort=None, title='追蹤天數'),
                                y=alt.Y(
                                    '名次:Q', 
                                    scale=alt.Scale(domain=[1, 100], reverse=True, nice=False), 
                                    title='名次 (1 在最上方)'
                                ),
                                color=alt.Color(
                                    '黑馬綜合排名:N', 
                                    title='Top 10 黑馬排行',
                                    sort=column_names
                                ),
                                tooltip=['黑馬綜合排名', '追蹤天數', '名次']
                            ).properties(
                                width='container',
                                height=450
                            ).interactive()
                            
                            st.altair_chart(c, use_container_width=True)
                            
                            # 準備匯出資料
                            base_chart = df_all_range[df_all_range['追蹤日期'] == base_date]
                            keys_to_export = df_result[['raw_song', 'raw_singer']].rename(columns={'raw_song': song_col, 'raw_singer': singer_col})
                            display_top_raw = pd.merge(keys_to_export, base_chart, on=[song_col, singer_col], how='inner')
                            
                            export_df = get_clean_export_df(base_chart, display_top_raw)
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
                        st.warning(f"在基準日 {base_date} 找不到對應的榜單資料。")
                else:
                    st.warning("資料欄位解析異常。")
            else:
                st.warning("區間內找不到可用的榜單檔案。")

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
