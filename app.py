import streamlit as st
import pandas as pd
import os

# 1. 頁面基本設定
st.set_page_config(page_title="QQ音樂熱門歌曲挑選系統", page_icon="🎵", layout="wide")

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

# 側邊欄：選擇基準日期
st.sidebar.header("🔍 挑選條件設定")
selected_date = st.sidebar.selectbox("選擇主要基準日期", dates)

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
    st.markdown("自動比對當日 4 大榜單，篩選出**同時登上 2 個（含）以上榜單**的神曲，指標最硬不踩雷！")
    
    df_curr = load_date_data(selected_date)
    if not df_curr.empty:
        song_col = '歌名' if '歌名' in df_curr.columns else ('song' if 'song' in df_curr.columns else None)
        singer_col = '歌手' if '歌手' in df_curr.columns else ('singer' if 'singer' in df_curr.columns else None)
        
        if song_col and singer_col:
            grouped = df_curr.groupby([song_col, singer_col]).agg(
                登榜數量=('榜單類型', 'nunique'),
                登上榜單=('榜單類型', lambda x: "、".join(set(x))),
                最高名次=('排名', 'min') if '排名' in df_curr.columns else ('登榜數量', 'count')
            ).reset_index()
            
            multi_chart = grouped[grouped['登榜數量'] >= 2].sort_values(by=['登榜數量', '最高名次'], ascending=[False, True])
            
            if not multi_chart.empty:
                st.success(f"🎯 在 {selected_date} 共找到 {len(multi_chart)} 首跨榜爆款歌曲！")
                st.dataframe(multi_chart, hide_index=True, use_container_width=True)
            else:
                st.info(f"在 {selected_date} 當天，暫無同時登上 2 個以上榜單的歌曲。")
        else:
            st.warning("數據欄位解析異常，請確認 CSV 欄位是否包含『歌名』與『歌手』。")
    else:
        st.warning(f"{selected_date} 尚無榜單資料。")

# ==========================================
# 🚀 模組二：飆升與新進黑馬（新鮮潮流）
# ==========================================
with main_tabs[1]:
    st.header("🚀 模組二：飆升與新進黑馬")
    st.markdown("對比前後日期數據，找出名次大幅爬升或全新進榜（New Entry）的潛力黑馬歌曲！")
    
    curr_idx = dates.index(selected_date)
    prev_dates = dates[curr_idx + 1:]
    
    if prev_dates:
        compare_date = st.selectbox("選擇對比歷史日期", prev_dates, index=0)
        df_now = load_date_data(selected_date)
        df_prev = load_date_data(compare_date)
        
        if not df_now.empty and not df_prev.empty:
            song_col = '歌名' if '歌名' in df_now.columns else 'song'
            singer_col = '歌手' if '歌手' in df_now.columns else 'singer'
            rank_col = '排名' if '排名' in df_now.columns else 'rank'
            
            chart_option = st.radio("選擇要比對的榜單", ["新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"], horizontal=True, key="m2_radio")
            
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
                
                # 篩選全新進榜或爬升的歌曲
                rising = merged[merged['名次變動'].str.contains('全新進榜|爬升')].sort_values(by=f'{rank_col}_當前')
                
                st.success(f"📊 【{chart_option}】對比：{selected_date} vs {compare_date}（共找到 {len(rising)} 首上升或新進榜歌曲）")
                st.dataframe(rising[[song_col, singer_col, f'{rank_col}_當前', f'{rank_col}_前次', '名次變動']], hide_index=True, use_container_width=True)
            else:
                st.info(f"{selected_date} 當天無 {chart_option} 數據。")
        else:
            st.warning("數據載入不足，無法進行對比。")
    else:
        st.info("💡 這是目前最早的一天數據，待明日自動抓取更新後，即可開始比對變動！")

# ==========================================
# 👑 模組三：榜單常勝軍（長青熱歌）
# ==========================================
with main_tabs[2]:
    st.header("👑 模組三：榜單常勝軍（長青熱歌）")
    st.markdown("統計歷史所有抓取紀錄，算出現身頻率最高、最耐聽的長青熱歌[cite: 1]。")
    
    all_dfs = []
    for d in dates:
        d_df = load_date_data(d)
        if not d_df.empty:
            d_df['抓取日期'] = d
            all_dfs.append(d_df)
            
    if all_dfs:
        full_df = pd.concat(all_dfs, ignore_index=True)
        song_col = '歌名' if '歌名' in full_df.columns else 'song'
        singer_col = '歌手' if '歌手' in full_df.columns else 'singer'
        
        # 可切換獨立榜單或全榜綜合
        chart_option_m3 = st.radio(
            "選擇要統計常勝軍的榜單範圍",
            ["全榜綜合", "新歌榜", "影視金曲榜", "綜藝新歌榜", "抖音熱歌榜"],
            horizontal=True,
            key="m3_radio"
        )
        
        if chart_option_m3 != "全榜綜合":
            target_df = full_df[full_df['榜單類型'] == chart_option_m3]
        else:
            target_df = full_df
            
        if not target_df.empty:
            evergreen = target_df.groupby([song_col, singer_col]).agg(
                累積上榜天數=('抓取日期', 'nunique'),
                登上榜單類型=('榜單類型', lambda x: "、".join(set(x))),
                平均名次=('排名', lambda x: round(x.mean(), 1)) if '排名' in target_df.columns else ('抓取日期', 'count')
            ).reset_index().sort_values(by=['累積上榜天數', '平均名次'], ascending=[False, True])
            
            st.success(f"📈 【{chart_option_m3}】分析歷史累積共 {len(dates)} 天數據，產出常勝軍 Top 50：")
            st.dataframe(evergreen.head(50), hide_index=True, use_container_width=True)
        else:
            st.info(f"尚無【{chart_option_m3}】的歷史數據。")
    else:
        st.info("尚無足夠歷史數據進行統計。")

# ==========================================
# 📊 原始榜單瀏覽
# ==========================================
with main_tabs[3]:
    st.header("📊 原始各榜單數據瀏覽")
    
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
                st.success(f"📅 數據日期：{selected_date}｜共 {len(df)} 筆排名資料")
                
                search_term = st.text_input(f"🔍 在【{chart_name}】中搜尋歌名或歌手", key=f"raw_{chart_key}")
                if search_term:
                    mask = df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
                    df = df[mask]
                
                st.dataframe(df, hide_index=True, use_container_width=True)
            else:
                st.warning(f"⚠️ {selected_date} 尚未抓取到 {chart_name} 的 CSV 檔案 ({file_name})。")
