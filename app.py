import streamlit as st
import pandas as pd
import os

# 1. 頁面基本設定
st.set_page_config(page_title="QQ音樂熱門歌曲挑選系統", page_icon="🎵", layout="wide")

st.title("🎵 QQ 音樂熱門歌曲挑選系統")
st.caption("每日自動同步榜單數據，幫你快速篩選爆款熱門歌曲。")

# 2. 數據目錄設定
data_dir = "data"

if os.path.exists(data_dir):
    # 抓取所有日期資料夾（從最新到最舊排序）
    dates = sorted([d for d in os.listdir(data_dir) if os.path.isdir(os.path.join(data_dir, d))], reverse=True)
    
    if dates:
        st.sidebar.header("🔍 篩選與設定")
        selected_date = st.sidebar.selectbox("選擇數據日期", dates)
        
        day_path = os.path.join(data_dir, selected_date)
        
        # 定義榜單檔案對照
        charts = {
            "新歌榜 (日榜)": "new.csv",
            "影視金曲榜 (週榜)": "film.csv",
            "綜藝新歌榜 (週榜)": "show.csv",
            "抖音熱歌榜 (週榜)": "tik.csv"
        }
        
        # 建立分頁
        tabs = st.tabs(list(charts.keys()))
        
        for tab, (chart_name, file_name) in zip(tabs, charts.items()):
            with tab:
                file_path = os.path.join(day_path, file_name)
                if os.path.exists(file_path):
                    df = pd.read_csv(file_path)
                    st.success(f"📅 讀取成功｜數據日期：{selected_date}｜共 {len(df)} 筆排名資料")
                    
                    # 搜尋過濾功能
                    search_term = st.text_input(f"🔍 在【{chart_name}】中搜尋歌名或歌手", key=chart_name)
                    if search_term:
                        # 假設 CSV 內欄位為 'song' 與 'singer'（若不同系統會自動相容）
                        mask = df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
                        df = df[mask]
                    
                    st.dataframe(df, use_container_width=True)
                else:
                    st.warning(f"⚠️ {selected_date} 尚未抓取到 {chart_name} 的 CSV 檔案。")
    else:
        st.info("目前 `data/` 資料夾內尚無日期數據。")
else:
    st.error("❌ 找不到 `data/` 資料夾，請確認 GitHub Actions 是否已成功抓取資料。")
