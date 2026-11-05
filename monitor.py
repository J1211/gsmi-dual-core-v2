import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta
from fredapi import Fred
import akshare as ak

# ==============================================================================
# 1. 界面配置与视觉主题
# ==============================================================================
st.set_page_config(page_title="GSMI v2.0 | 全球宏观与本土行业", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    div[data-testid="stMetricValue"] { font-size: 24px; font-weight: bold; color: #00ffcc; }
    .standard-text { color: #aaa; font-size: 14px; margin-top: -10px; margin-bottom: 10px; font-weight: bold; }
    .quadrant-box { padding: 12px; border-radius: 6px; border: 1px solid #333; background-color: #1a1c24; text-align: center; min-height: 60px; }
    .card-box { padding: 12px 15px; border-radius: 6px; border: 1px solid #2d313e; background-color: #151820; margin-bottom: 10px; }
    .card-title { color: #9aa0a6; font-size: 13px; font-weight: 600; margin-bottom: 4px; }
    .card-val { color: #ffffff; font-size: 22px; font-weight: bold; }
    .card-sub { font-size: 12px; margin-top: 4px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🏹 GSMI Dual-Core v2.0 | 全球宏观与本土行业")

# 顶部双核状态机空间锚点
status_placeholder = st.empty()

# ==============================================================================
# 2. 侧边栏参数配置
# ==============================================================================
st.sidebar.header("🛠️ 本土与全球双核参数")
cn_turnover_manual = st.sidebar.number_input(
    "🌊 A股两市成交额 (万亿¥, 0为自动估算)", 
    min_value=0.0, max_value=5.0, value=2.14, step=0.05,
    help="A股最核心的内生水源指标。>1.8万亿为活水扩张区（利好券商与高弹性科技）；<1.2万亿为存量枯水区（退守红利与银行）。"
)
cn_10y_bond = st.sidebar.number_input(
    "🏛️ 中国 10Y 国债收益率 (%)", 
    min_value=0.5, max_value=4.0, value=1.65, step=0.05,
    help="本土无风险重力标尺。用于与红利/银行股息率对比计算本土股债利差（ERP）。"
)

st.sidebar.markdown("---")
st.sidebar.header("🗳️ BofA FMS 与 TGA 配置")
DEFAULT_FMS_CASH = 3.9
fms_cash = st.sidebar.slider("FMS 机构现金水平 (%)", 3.0, 6.5, DEFAULT_FMS_CASH, 0.1)
tga_target = st.sidebar.number_input("本季末 TGA 余额目标 (十亿$)", value=950, step=50)

if "fred_api_key" in st.secrets:
    fred_key = st.secrets["fred_api_key"]
else:
    fred_key = st.sidebar.text_input("FRED API Key", type="password")

if not fred_key:
    st.warning("⚠️ 请在 Streamlit Secrets 或左侧边栏配置 FRED API Key 以启动系统。")
    st.stop()

fred = Fred(api_key=fred_key)

# ==============================================================================
# 3. 核心数据抓取与物理引擎
# ==============================================================================
@st.cache_data(ttl=1800)
def get_etf_data_v2(ticker, start_date, end_date):
    """
    三重装甲 ETF/个股前复权抓取引擎：
    1. 优先尝试 AkShare ETF 前复权接口
    2. 备用尝试 AkShare A股前复权接口
    3. 海外节点被墙时，自动降级调用 yf.Ticker().history() (默认 auto_adjust=True 前复权)
    """
    if ".SS" in ticker or ".SZ" in ticker or ".SH" in ticker:
        try:
            symbol = ticker.split('.')[0]
            start_str = start_date.strftime('%Y%m%d')
            end_str = end_date.strftime('%Y%m%d')
            df = pd.DataFrame()
            try:
                df = ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
            except Exception:
                pass
            if df is None or df.empty:
                try:
                    df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_str, end_date=end_str, adjust="qfq")
                except Exception:
                    pass
            if df is not None and not df.empty:
                df = df.rename(columns={'日期': 'Date', '收盘': 'Close', '成交量': 'Volume', '最高': 'High', '最低': 'Low', '开盘': 'Open'})
                if 'Date' in df.columns and 'Close' in df.columns:
                    df['Date'] = pd.to_datetime(df['Date'])
                    df.set_index('Date', inplace=True)
                    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors='coerce')
                    return df[['Open', 'High', 'Low', 'Close', 'Volume']].sort_index()
        except Exception:
            pass

    # 海外云端防线：yf.Ticker().history()
    yf_ticker = ticker.replace(".SH", ".SS")
    try:
        tkr = yf.Ticker(yf_ticker)
        df_yf = tkr.history(start=start_date.strftime('%Y-%m-%d'), end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d'))
        if df_yf is not None and not df_yf.empty:
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Open', 'High', 'Low', 'Close', 'Volume']].sort_index()
    except Exception:
        pass

    # 终极保底：yf.download
    try:
        df_dl = yf.download(yf_ticker, start=start_date, end=end_date + timedelta(days=1), progress=False)
        if df_dl is not None and not df_dl.empty:
            res = pd.DataFrame(index=pd.to_datetime(df_dl.index).tz_localize(None))
            price_col = 'Adj Close' if 'Adj Close' in df_dl.columns else 'Close'
            for col_name, target_col in [('Open', 'Open'), ('High', 'High'), ('Low', 'Low'), (price_col, 'Close'), ('Volume', 'Volume')]:
                if col_name in df_dl.columns:
                    s = df_dl[col_name]
                    res[target_col] = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
            return res.sort_index()
    except Exception:
        pass

    return pd.DataFrame()

def score_linear(val, min_val, max_val, max_score, reverse=False):
    if pd.isna(val):
        return max_score * 0.5
    if not reverse:
        score = (val - min_val) / (max_val - min_val) * max_score
    else:
        score = (max_val - val) / (max_val - min_val) * max_score
    return max(0.0, min(float(max_score), float(score)))

def get_tga_forecast(curr_tga_billion, target_val):
    today = datetime.now()
    m, d = today.month, today.day
    msg, risk = "⚪ 【平稳周期】关注目标回归", "Normal"
    if m == 4 and 10 <= d <= 22: msg, risk = "🚨 【年度吸水期】4.15个人税高峰，NL面临强压", "High"
    elif m in [6, 9, 10, 12] and 12 <= d <= 20: msg, risk = "🟠 【季中吸水期】企业缴税/结算窗口，NL承压", "High"
    elif 13 <= d <= 18: msg, risk = "🟢 【利息释放期】国债付息窗口，利好流动性修复", "Low"
    elif 1 <= d <= 5: msg, risk = "🔵 【财政支出期】月初支出高峰，水源偏暖", "Low"
    gap = target_val - curr_tga_billion
    return msg, gap, risk

@st.cache_data(ttl=3600)
def fetch_and_sync_data():
    end = datetime.now()
    start = end - timedelta(days=500)
    status_report = {}
    
    # 1. 抓取 FRED 美元宏观管网数据
    fred_map = {
        'tips': 'DFII10', 'spread': 'BAMLH0A0HYM2', 'assets': 'WALCL',
        'tga': 'WTREGEN', 'rrp': 'RRPONTSYD', 'sofr': 'SOFR',
        'iorb': 'IORB', 'us2y': 'DGS2', 'us10y': 'DGS10',
        'term_premium': 'THREEFYTP10'
    }
    
    data_dict = {}
    for key, fid in fred_map.items():
        try:
            s = fred.get_series(fid, start - timedelta(days=30), end)
            if not s.empty:
                data_dict[key] = s
                status_report[f"FRED:{key}"] = "✅"
            else:
                status_report[f"FRED:{key}"] = "❌"
        except Exception:
            if key == 'term_premium':
                try:
                    s_fb = fred.get_series('ACMTP10', start - timedelta(days=30), end)
                    if not s_fb.empty:
                        data_dict[key] = s_fb
                        status_report[f"FRED:{key}"] = "✅(ACM)"
                        continue
                except Exception:
                    pass
            status_report[f"FRED:{key}"] = "❌"

    # 2. 抓取全球与本土核心市场跨资产价格
    def safe_get_yf(ticker, name):
        try:
            df_y = yf.download(ticker, start=start - timedelta(days=15), end=end + timedelta(days=1), progress=False)
            if df_y is None or df_y.empty: 
                status_report[name] = "❌"
                return pd.Series(dtype='float64')
            data = df_y['Close'].iloc[:, 0] if isinstance(df_y.columns, pd.MultiIndex) else df_y['Close']
            data.index = pd.to_datetime(data.index).tz_localize(None)
            status_report[name] = "✅"
            return data
        except Exception:
            status_report[name] = "❌"
            return pd.Series(dtype='float64')

    yf_dict = {
        'dxy': safe_get_yf("DX-Y.NYB", "DXY"),
        'copper': safe_get_yf("HG=F", "Copper"),
        'gold': safe_get_yf("GC=F", "Gold"),
        'hkd': safe_get_yf("HKD=X", "HKD"),
        'cny': safe_get_yf("CNY=X", "CNY"),
        'hsi': safe_get_yf("^HSI", "HSI"),
        'as300': safe_get_yf("510300.SS", "AS300"),
        'chinext': safe_get_yf("159915.SZ", "ChiNext"),
        'div_low': safe_get_yf("512890.SS", "DivLowVol"),
        'btc': safe_get_yf("BTC-USD", "BTC"),
        'qqq': safe_get_yf("QQQ", "QQQ"),
        'move': safe_get_yf("^MOVE", "MOVE"),
        'vix': safe_get_yf("^VIX", "VIX")
    }

    all_series = {**data_dict, **yf_dict}
    df = pd.DataFrame(all_series)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.sort_index()
    df = df.loc[start.replace(tzinfo=None):end.replace(tzinfo=None)]
    df = df.ffill()
    df = df.dropna(subset=['qqq', 'btc'], how='all')
    
    if not df.empty:
        assets_s = df.get('assets', pd.Series(0, index=df.index)).ffill().fillna(0)
        tga_s = df.get('tga', pd.Series(0, index=df.index)).ffill().fillna(0)
        rrp_s = df.get('rrp', pd.Series(0, index=df.index)).ffill().fillna(0)
        
        df['nl'] = (assets_s / 1000000) - (tga_s / 1000000) - (rrp_s / 1000)
        if 'copper' in df.columns and 'gold' in df.columns:
            df['cg_ratio'] = df['copper'] / df['gold']
        if 'sofr' in df.columns and 'iorb' in df.columns:
            df['sofr_spread'] = (df['sofr'] - df['iorb']) * 100
        if 'us10y' in df.columns and 'us2y' in df.columns:
            df['yield_curve'] = df['us10y'] - df['us2y']
            
    return df, status_report

def calculate_dual_scores(df, fms_val, cn_turnover, cn_bond):
    if df.empty:
        return df, 50.0, {}
    if 'spread' in df.columns and df['spread'].max() < 50:
        df['spread'] = df['spread'] * 100

    # 1. 计算全球美元 GSMI 历史序列
    gsmi_history = []
    nl_ma20 = df['nl'].rolling(20).mean()
    cg_ma200 = df['cg_ratio'].rolling(200).mean()
    
    for i in range(len(df)):
        if i < 20:
            gsmi_history.append(np.nan)
            continue
        s_nl = (15 if df['nl'].iloc[i] > nl_ma20.iloc[i] else 0) + (10 if df['nl'].iloc[i] > df['nl'].iloc[i-5] else 0)
        s_tips = score_linear(df['tips'].iloc[i], 0.5, 2.5, 20, reverse=True)
        s_dxy = score_linear(df['dxy'].iloc[i], 98, 108, 15, reverse=True)
        s_fms = score_linear(fms_val, 3.5, 6.0, 15, reverse=False)
        s_cg = (10 if df['cg_ratio'].iloc[i] > cg_ma200.iloc[i] else 0) + (5 if df['cg_ratio'].iloc[i] > df['cg_ratio'].iloc[i-10:i-5].mean() else 0)
        s_spread = score_linear(df['spread'].iloc[i], 300, 600, 10, reverse=True)
        gsmi_history.append(s_nl + s_tips + s_dxy + s_fms + s_cg + s_spread)
    
    df['gsmi_score'] = gsmi_history

    # 2. 计算中国本土水源与动量总分 (CN-Water Score: 0-100)
    # (a) 两市成交额水位 (40分)：1.0万亿(0分) ~ 2.0万亿(40分)
    s_cn_vol = score_linear(cn_turnover, 1.0, 2.0, 40, reverse=False)
    # (b) 本土大盘趋势 (25分)：沪深300(510300) vs 20MA (15分) + 创业板(159915) 5日动量 (10分)
    as300_ma20 = df['as300'].rolling(20).mean().iloc[-1]
    s_cn_trend = (15.0 if df['as300'].iloc[-1] >= as300_ma20 else 5.0) + (10.0 if df['chinext'].iloc[-1] >= df['chinext'].iloc[-6] else 0.0)
    # (c) 本土重力抗性 ERP (20分)：红利股息率基准(约4.66%) - 中债10Y收益率，利差在1.5%~3.2%间打分
    erp_spread = 4.66 - cn_bond
    s_cn_erp = score_linear(erp_spread, 1.5, 3.2, 20, reverse=False)
    # (d) 人民币与离岸汇率环境 (15分)：USD/CNY 稳健或结汇升值 + 港元流动性
    cny_change_10d = (df['cny'].iloc[-1] / df['cny'].iloc[-10] - 1) if 'cny' in df.columns and len(df) > 10 else 0
    s_cn_fx = (10.0 if cny_change_10d <= 0.005 else 3.0) + (5.0 if df['hkd'].iloc[-1] < 7.82 else 1.0)

    cn_score = round(s_cn_vol + s_cn_trend + s_cn_erp + s_cn_fx, 1)
    cn_details = {
        "vol_score": round(s_cn_vol, 1),
        "trend_score": round(s_cn_trend, 1),
        "erp_score": round(s_cn_erp, 1),
        "fx_score": round(s_cn_fx, 1),
        "erp_bps": round(erp_spread * 100, 0)
    }
    return df, cn_score, cn_details

# ==============================================================================
# 4. 主程序执行与顶部双核战时状态机
# ==============================================================================
try:
    df_raw, report = fetch_and_sync_data()
    if df_raw.empty:
        st.error("🚨 核心数据链路断裂，请检查 FRED API Key 或网络连接。")
        st.stop()

    df, cn_score, cn_details = calculate_dual_scores(df_raw, fms_cash, cn_turnover_manual, cn_10y_bond)
    latest = df.iloc[-1]
    latest_date = df.index[-1].strftime('%Y.%m.%d')
    us_gsmi = latest['gsmi_score']
    sofr_val = latest.get('sofr_spread', -1.0)
    vix_val = latest.get('vix', 18.0)

    # 双核综合战术判定逻辑
    if sofr_val > 5.0 or vix_val > 32.0:
        banner_icon, banner_zone = "🚨", "海外流动性休克警报 (跨洋提款风险)"
        banner_action = f"SOFR息差({sofr_val:+.1f}bps)或VIX({vix_val:.1f})触发极值！严防外资抛售A股核心资产换汇，暂停右侧追高，收缩至红利与现金防线。"
        border_col = "#FF3131"
    elif cn_score >= 65:
        banner_icon, banner_zone = "🟢", "本土活水扩张区 (内生做多窗口)"
        banner_action = f"A股成交({cn_turnover_manual:.2f}万亿)充沛，本土得分({cn_score})强劲！即使美元重力偏高，A股具备独立行情基础。大金融倾向【券商/非银】，硬科技逢回踩10MA积极吸气。"
        border_col = "#00ffcc"
    elif cn_score >= 45:
        banner_icon, banner_zone = "🟡", "结构性存量博弈区 (三三制均衡防守反击)"
        banner_action = f"本土得分({cn_score})中性，全球GSMI({us_gsmi:.1f})。严禁在暴涨>15%后追高，严格执行【1/3硬核制造 + 1/3金融红利 + 1/3现金】，只做缩量回踩买点。"
        border_col = "#FFD700"
    else:
        banner_icon, banner_zone = "❄️", "本土枯水窒息区 (全面防御模式)"
        banner_action = f"A股量能萎缩且趋势承压(本土得分{cn_score})。砍掉高Beta科技与券商敞口，将仓位锁定在【银行/红利低波】与【高比例现金】中等待冰点过去。"
        border_col = "#ff6b6b"

    status_placeholder.markdown(f"""
    <div style='padding: 12px 18px; border-radius: 6px; border-left: 6px solid {border_col}; border-top: 1px solid #333; border-right: 1px solid #333; border-bottom: 1px solid #333; background-color: #161922; margin-bottom: 15px;'>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;'>
            <h4 style='margin:0; color: #fff;'>{banner_icon} {latest_date} 双核状态：{banner_zone}</h4>
            <span style='color: #00ffcc; font-weight: bold; font-size: 15px;'>
                🌊 本土活水总分: {cn_score:.1f}/100 &nbsp;|&nbsp; 🌎 全球美元 GSMI: {us_gsmi:.1f}/100 &nbsp;|&nbsp; 🩸 SOFR-IORB: {sofr_val:+.1f} bps
            </span>
        </div>
        <p style='margin: 6px 0 0 0; font-size: 14px; color: #ccc;'>⚡ <strong>首席风险官执行指令：</strong>{banner_action}</p>
    </div>
    """, unsafe_allow_html=True)

    with st.expander("🛠️ 系统底层数据源健康审计 (点击展开)", expanded=False):
        cols = st.columns(5)
        for i, (name, status) in enumerate(report.items()):
            cols[i % 5].write(f"{name}: {status}")

    # ==============================================================================
    # 5. 五大核心作战室 (5 Tabs)
    # ==============================================================================
    tabs = st.tabs([
        "🌊 1. 全球与本土宏观总闸", 
        "🔭 2. 行业 ETF 全息 X光", 
        "🎯 3. 猎杀雷达与买卖时机", 
        "⚖️ 4. 双雄对决罗盘与正交沙盘", 
        "🛡️ 5. 三三制 ATR 仓位与挂单器"
    ])

    # ------------------------------------------------------------------------------
    # TAB 1: 全球与本土宏观总闸 (一屏流高密度设计)
    # ------------------------------------------------------------------------------
    with tabs[0]:
        nl_ma_last = df['nl'].rolling(20).mean().iloc[-1]
        s_nl_latest = (15 if latest['nl'] > nl_ma_last else 0) + (10 if latest['nl'] > df['nl'].iloc[-6] else 0)
        s_cg_latest = (10 if latest['cg_ratio'] > df['cg_ratio'].rolling(200).mean().iloc[-1] else 0) + (5 if latest['cg_ratio'] > df['cg_ratio'].iloc[-10:-5].mean() else 0)
        t_msg, t_gap, t_risk = get_tga_forecast(latest['tga']/1000, tga_target)

        st.markdown("##### 🌐 第一轨：美元水源、重力与系统血压 (华尔街底盘)")
        r1c1, r1c2, r1c3, r1c4 = st.columns(4)
        with r1c1:
            nl_dir = "🟢 扩张 (>20MA)" if latest['nl'] > nl_ma_last else "🔴 收缩 (<20MA)"
            st.markdown(f"<div class='card-box'><div class='card-title'>💧 美联储净流动性 (NL)</div><div class='card-val'>${latest['nl']:.2f}T</div><div class='card-sub'>{nl_dir} | 得分: {s_nl_latest}/25</div></div>", unsafe_allow_html=True)
        with r1c2:
            tips_status = "🔴 高重力压制 (>2.1%)" if latest['tips'] > 2.1 else "🟢 重力温和"
            st.markdown(f"<div class='card-box'><div class='card-title'>🪐 10Y TIPS 实际利率</div><div class='card-val'>{latest['tips']:.2f}%</div><div class='card-sub'>{tips_status} | 期限溢价: {latest.get('term_premium',0):.2f}</div></div>", unsafe_allow_html=True)
        with r1c3:
            hkd_status = "🟢 港股吸金" if latest['hkd'] < 7.80 else "🔴 离岸失血"
            st.markdown(f"<div class='card-box'><div class='card-title'>💵 美元 (DXY) & 港元阀门</div><div class='card-val'>{latest['dxy']:.2f} <span style='font-size:15px;color:#aaa;'>/ HKD {latest['hkd']:.4f}</span></div><div class='card-sub'>{hkd_status} | TGA: {t_msg[:12]}</div></div>", unsafe_allow_html=True)
        with r1c4:
            if sofr_val > 5.0:
                bp_str = "🚨 管道破裂 (>+5bps 休克)"
            elif sofr_val > 0:
                bp_str = "🟡 月末/结算微颤 (0~5bps)"
            else:
                bp_str = "🟢 负压安全区 (<0bps)"
            st.markdown(f"<div class='card-box'><div class='card-title'>🩸 系统血压 (SOFR-IORB)</div><div class='card-val'>{sofr_val:+.1f} bps</div><div class='card-sub'>{bp_str}</div></div>", unsafe_allow_html=True)

        st.markdown("##### 🏮 第二轨：全球风险情绪与中国本土水源 (A股作战温床)")
        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        with r2c1:
            yc_val = latest.get('yield_curve', 0)
            vix_str = "🚨 恐慌" if vix_val > 30 else ("🟡 警戒" if latest.get('move',0) > 100 else "🟢 平稳")
            st.markdown(f"<div class='card-box'><div class='card-title'>📉 恐慌双核 (VIX / MOVE)</div><div class='card-val'>{vix_val:.1f} <span style='font-size:15px;color:#aaa;'>/ {latest.get('move',0):.1f}</span></div><div class='card-sub'>{vix_str} | 美债2s10s压差: {yc_val:+.2f}%</div></div>", unsafe_allow_html=True)
        with r2c2:
            btc_ret = df['btc'].pct_change().dropna() * 100
            pos_days = len(btc_ret.tail(28)[btc_ret.tail(28) > 0])
            btc_emo = "🔥 投机活跃" if pos_days >= 18 else ("❄️ 情绪冰点" if pos_days <= 10 else "⚖️ 情绪震荡")
            st.markdown(f"<div class='card-box'><div class='card-title'>⚡ BTC 28日情绪 & FMS现金</div><div class='card-val'>{pos_days}/28天涨 <span style='font-size:15px;color:#aaa;'>| FMS {fms_cash}%</span></div><div class='card-sub'>{btc_emo} | 信用利差: {latest['spread']:.0f}bps</div></div>", unsafe_allow_html=True)
        with r2c3:
            cg_ma = df['cg_ratio'].rolling(200).mean().iloc[-1]
            cg_str = "🟢 站上200MA (实体复苏)" if latest['cg_ratio'] > cg_ma else "🟡 低于200MA (需求承压)"
            st.markdown(f"<div class='card-box'><div class='card-title'>🪙 铜金比 (实体防线)</div><div class='card-val'>{latest['cg_ratio']:.4f}</div><div class='card-sub'>{cg_str} | 得分: {s_cg_latest}/15</div></div>", unsafe_allow_html=True)
        with r2c4:
            vol_tag = "🔥 增量博弈" if cn_turnover_manual >= 1.8 else ("⚖️ 存量轮动" if cn_turnover_manual >= 1.3 else "❄️ 缩量防守")
            cny_val = latest.get('cny', 6.70)
            st.markdown(f"<div class='card-box'><div class='card-title'>🌊 A股两市成交 & 本土ERP</div><div class='card-val'>{cn_turnover_manual:.2f}万亿 <span style='font-size:15px;color:#00ffcc;'>| USD/CNY {cny_val:.4f}</span></div><div class='card-sub'>{vol_tag} | 红利股债利差: +{cn_details['erp_bps']:.0f}bps</div></div>", unsafe_allow_html=True)

        st.markdown("---")
        chart_choice = st.radio(
            "📈 选择下方深度历史监控视图 (一屏切换)：",
            [
                "1. 💧 美联储净流动性 (NL) vs 10Y TIPS 重力轨迹",
                "2. 🩸 系统血压 (SOFR-IORB) 心电图与 90天休克黑匣子",
                "3. 🏛️ 债市熊陡压差 (2s10s) 与 VIX/MOVE 双核波动率",
                "4. 🪙 法币脱钩显影 (黄金 vs TIPS) & 铜金比 200MA",
                "5. 📊 双核验证：全球 GSMI 评分 vs QQQ & 沪深300 走势"
            ],
            horizontal=True
        )

        if "1." in chart_choice:
            valid_df = df[df['nl'] > 1.0].copy()
            if not valid_df.empty:
                nl_min, nl_max = valid_df['nl'].min() - 0.1, valid_df['nl'].max() + 0.1
                fig_nl = go.Figure()
                fig_nl.add_trace(go.Scatter(x=valid_df.index, y=valid_df['nl'], name="净流动性 NL (T$)", line=dict(color='#00ffcc', width=3)))
                fig_nl.add_trace(go.Scatter(x=valid_df.index, y=valid_df['tips'], name="10Y TIPS (%)", line=dict(color='#FF3131', dash='dot', width=2), yaxis="y2"))
                fig_nl.update_layout(height=360, template="plotly_dark", yaxis=dict(title="NL (T$)", range=[nl_min, nl_max], gridcolor='#333'), yaxis2=dict(overlaying="y", side="right", showgrid=False, title="TIPS (%)"), legend=dict(orientation="h", y=1.1, x=0), margin=dict(l=10, r=10, t=30, b=10))
                st.plotly_chart(fig_nl, use_container_width=True)

        elif "2." in chart_choice:
            c_ekg, c_log = st.columns([2.2, 1])
            with c_ekg:
                fig_bp = go.Figure()
                fig_bp.add_trace(go.Scatter(x=df.index, y=df['sofr_spread'], name="SOFR-IORB (bps)", line=dict(color='#00ffcc', width=2), fill='tozeroy', fillcolor='rgba(0, 255, 204, 0.1)'))
                fig_bp.add_hline(y=0, line_dash="dash", line_color="orange", annotation_text="0 bps 基准线")
                fig_bp.add_hline(y=5, line_dash="dot", line_color="#FF3131", annotation_text="+5 bps 真实休克高通滤波线")
                shock_pts = df[df['sofr_spread'] > 0]
                if not shock_pts.empty:
                    fig_bp.add_trace(go.Scatter(x=shock_pts.index, y=shock_pts['sofr_spread'], mode='markers', name="正压报警点", marker=dict(color='#FF3131', size=7, symbol='x')))
                fig_bp.update_layout(height=340, template="plotly_dark", yaxis_title="息差 (bps)", margin=dict(l=10, r=10, t=25, b=10))
                st.plotly_chart(fig_bp, use_container_width=True)
            with c_log:
                st.markdown("**🚨 近 90 天正压事件黑匣子**")
                recent_shocks = shock_pts[shock_pts.index > (datetime.now() - timedelta(days=90))]
                if not recent_shocks.empty:
                    log_df = recent_shocks[['sofr_spread']].copy()
                    log_df.index = log_df.index.strftime('%Y-%m-%d')
                    log_df.columns = ['读数(bps)']
                    log_df['定性'] = log_df['读数(bps)'].apply(lambda x: "💥 真实休克" if x >= 5.0 else "⚠️ 微观摩擦")
                    st.dataframe(log_df.sort_index(ascending=False), height=280, use_container_width=True)
                else:
                    st.success("过去 90 天内管网无正压异常。")

        elif "3." in chart_choice:
            fig_bond = go.Figure()
            if 'yield_curve' in df.columns:
                fig_bond.add_trace(go.Scatter(x=df.index[-180:], y=df['yield_curve'].tail(180), name="2s10s 压差 (左轴)", line=dict(color='#FF3131', width=3)))
                fig_bond.add_hline(y=0, line_dash="dash", line_color="white", annotation_text="解除倒挂 (熊陡警戒线)")
            if 'move' in df.columns and 'vix' in df.columns:
                fig_bond.add_trace(go.Scatter(x=df.index[-180:], y=df['move'].tail(180), name="MOVE 债市波动 (右轴)", line=dict(color='#00ffcc', width=2, dash='dot'), yaxis="y2"))
                fig_bond.add_trace(go.Scatter(x=df.index[-180:], y=df['vix'].tail(180), name="VIX 股市恐慌 (右轴)", line=dict(color='orange', width=2), yaxis="y2"))
            fig_bond.update_layout(height=360, template="plotly_dark", yaxis2=dict(overlaying="y", side="right", showgrid=False), legend=dict(orientation="h", y=1.1, x=0), margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_bond, use_container_width=True)

        elif "4." in chart_choice:
            f1, f2 = st.columns(2)
            with f1:
                fig_fiat = go.Figure()
                fig_fiat.add_trace(go.Scatter(x=df.index[-250:], y=df['gold'].tail(250), name="黄金 (实物原子)", line=dict(color='#FFD700', width=3)))
                fig_fiat.add_trace(go.Scatter(x=df.index[-250:], y=df['tips'].tail(250), name="10Y TIPS (右轴)", line=dict(color='#FF3131', width=2, dash='dash'), yaxis="y2"))
                fig_fiat.update_layout(height=340, template="plotly_dark", title="法币脱钩显影：黄金 vs TIPS", yaxis2=dict(overlaying="y", side="right", showgrid=False), margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_fiat, use_container_width=True)
            with f2:
                fig_cg = go.Figure()
                fig_cg.add_trace(go.Scatter(x=df.index[-250:], y=df['cg_ratio'].tail(250), name="铜金比", line=dict(color='#00ffcc', width=3)))
                fig_cg.add_trace(go.Scatter(x=df.index[-250:], y=df['cg_ratio'].rolling(200).mean().tail(250), name="200MA", line=dict(color='orange', width=2, dash='dash')))
                fig_cg.update_layout(height=340, template="plotly_dark", title="实体经济温度计：铜金比 vs 200MA", yaxis=dict(tickformat=".4f"), margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_cg, use_container_width=True)

        elif "5." in chart_choice:
            df_w = df.resample('W-FRI').last().dropna(subset=['gsmi_score', 'qqq', 'as300'])
            fig_v = go.Figure()
            fig_v.add_trace(go.Scatter(x=df_w.index, y=df_w['gsmi_score'], name="全球 GSMI 评分", line=dict(color='#00ffcc', width=3)))
            fig_v.add_trace(go.Scatter(x=df_w.index, y=(df_w['qqq']/df_w['qqq'].iloc[0])*100, name="美股 QQQ (归一化, 右轴)", line=dict(color='#FFD700', dash='dot', width=2), yaxis="y2"))
            fig_v.add_trace(go.Scatter(x=df_w.index, y=(df_w['as300']/df_w['as300'].iloc[0])*100, name="沪深300 (归一化, 右轴)", line=dict(color='#ff6b6b', width=2), yaxis="y2"))
            fig_v.update_layout(height=360, template="plotly_dark", yaxis2=dict(overlaying="y", side="right", showgrid=False), legend=dict(orientation="h", y=1.1, x=0), margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_v, use_container_width=True)

    # ------------------------------------------------------------------------------
    # TAB 2: 行业 ETF 全息 X光 (二维RS + 量价派发/吸筹照妖镜)
    # ------------------------------------------------------------------------------
    with tabs[1]:
        st.subheader("🔭 行业板块 ETF 全息 X光扫描 (二维RS + 主力量价照妖镜)")
        st.markdown("同时扫描 **中期洋流 (20日RS)**、**短期爆发 (5日RS)**、**均线乖离拥挤度** 与 **主力放量吸筹/派发特征**。")

        sector_map = {
            "🛡️ 大金融/300非银 (券商+保险龙头)": "512070.SS",
            "⚔️ 纯证券公司 (高Beta交易收税机)": "512880.SS",
            "🏦 银行低费率 (息差企稳收租盾)": "516310.SS",
            "⚓ 红利低波 (城商行+实物垄断底座)": "512890.SS",
            "⚡ 电网设备 (十五五5万亿+出海)": "159326.SZ",
            "🔋 储能电池 (集体提价+AIDC配储)": "159566.SZ",
            "🔬 半导体设备 (地缘刚需+国产扩产)": "159558.SZ",
            "📡 通信/光模块 (算力管道)": "515880.SS",
            "⛏️ 有色金属 (铜铝原子源头)": "512400.SS",
            "🔥 能源/煤炭 (高股息基载能源)": "515220.SS",
            "🪙 黄金 ETF (主权信用脱钩代理)": "518880.SS",
            "💊 医药医疗 (长久期超跌探头)": "512010.SS",
            "🍶 消费白酒 (内需资产负债表探头)": "512690.SS",
            "🌐 中概互联 (离岸流动性温度计)": "513050.SS",
            "🤖 机器人 (纯叙事游资温度计)": "159530.SZ",
            "🌎 纳斯达克 100 (全球科技 Beta)": "QQQ"
        }

        c_base1, c_base2 = st.columns([1, 2])
        with c_base1:
            baseline = st.selectbox("选择对标的宏观重力基准", ["沪深300 (510300.SS)", "纳斯达克 (QQQ)"])
            baseline_ticker = "510300.SS" if "沪深300" in baseline else "QQQ"
        with c_base2:
            st.write("")
            run_xray = st.button("📡 启动全市场行业 ETF 扫描", use_container_width=True)

        if run_xray:
            with st.spinner("正在穿透 16 大核心行业 ETF 的流体力学与量价底稿..."):
                sector_results = []
                fetch_start = datetime.now() - timedelta(days=200)
                base_raw = get_etf_data_v2(baseline_ticker, fetch_start, datetime.now())
                if base_raw.empty:
                    st.error(f"基准 {baseline_ticker} 数据抓取失败。")
                else:
                    base_data = base_raw['Close'].ffill().dropna()
                    for sector_name, ticker in sector_map.items():
                        try:
                            s_data = get_etf_data_v2(ticker, fetch_start, datetime.now())
                            if s_data.empty or len(s_data) < 50:
                                sector_results.append({"行业板块": sector_name, "代码": ticker, "二维资金状态": "❌ 数据不足", "微观量价照妖镜": "-", "RS 20日(中期)": -999, "RS 5日(短期)": "-", "偏离10MA": "-", "偏离50MA": "-", "量能VR": "-"})
                                continue
                            s_close = s_data['Close'].ffill().dropna()
                            s_vol = s_data['Volume'].ffill().fillna(0)
                            s_high = s_data['High'].ffill().dropna()
                            s_low = s_data['Low'].ffill().dropna()

                            df_merge = pd.DataFrame({'Target': s_close, 'Base': base_data}).ffill().dropna()
                            if len(df_merge) < 35:
                                continue

                            rs_curve = df_merge['Target'] / df_merge['Base']
                            rs_20d = (rs_curve.iloc[-1] / rs_curve.iloc[-20]) - 1
                            rs_5d = (rs_curve.iloc[-1] / rs_curve.iloc[-5]) - 1

                            ma10 = s_close.rolling(10).mean().iloc[-1]
                            ma50 = s_close.rolling(50).mean().iloc[-1]
                            bias_10 = (s_close.iloc[-1] / ma10) - 1
                            bias_50 = (s_close.iloc[-1] / ma50) - 1

                            vr = s_vol.iloc[-1] / (s_vol.iloc[-6:-1].mean() + 1e-9)
                            ret_1d = (s_close.iloc[-1] / s_close.iloc[-2]) - 1
                            candle_range = max(s_high.iloc[-1] - s_low.iloc[-1], 1e-6)
                            candle_pos = (s_close.iloc[-1] - s_low.iloc[-1]) / candle_range

                            # 1. 二维 RS 状态判定
                            if rs_20d > 0.03:
                                state = "🔥 强势吸筹 & 极速逼空" if rs_5d > 0.05 else "🌊 稳健吸筹 (中期走强)"
                            elif rs_20d < -0.03:
                                state = "🩸 持续失血 & 恐慌抛售" if rs_5d < -0.05 else "📉 阴跌失血 (机构撤离)"
                            else:
                                if rs_5d > 0.05:
                                    state = "🚀 暴力点火 (短期极速反转)"
                                elif rs_5d < -0.05:
                                    state = "⚠️ 短期砸盘 (动能向下突变)"
                                else:
                                    state = "⚖️ 随波逐流 (纯 Beta)"

                            # 2. 微观量价照妖镜 (识别暗中吸筹 vs 暴涨赎回派发 vs 缩量回踩)
                            if bias_10 > 0.08 and vr > 1.5:
                                micro_mirror = "🚨 乖离过热巨量 (防机构赎回派发)"
                            elif vr > 1.4 and (ret_1d < -0.015 or candle_pos < 0.35):
                                micro_mirror = "🩸 放量长上影/阴线 (主力出逃)"
                            elif vr > 1.3 and ret_1d > 0.015 and candle_pos >= 0.6 and bias_10 <= 0.06:
                                micro_mirror = "💎 放量真阳起跑 (主动扫货)"
                            elif -0.02 <= bias_10 <= 0.025 and vr < 0.85 and rs_20d > 0:
                                micro_mirror = "🎯 缩量回踩10MA (洗盘到位)"
                            elif bias_50 < -0.05 and vr < 0.7:
                                micro_mirror = "❄️ 低位地量冰点 (抛压衰竭)"
                            else:
                                micro_mirror = "⚖️ 量价常态博弈"

                            sector_results.append({
                                "行业板块": sector_name,
                                "代码": ticker,
                                "二维资金状态": state,
                                "微观量价照妖镜": micro_mirror,
                                "RS 20日(中期)": rs_20d,
                                "RS 5日(短期)": f"{rs_5d*100:+.2f}%",
                                "偏离10MA": f"{bias_10*100:+.2f}%",
                                "偏离50MA": f"{bias_50*100:+.2f}%",
                                "量能VR": f"{vr:.2f}"
                            })
                        except Exception as e:
                            sector_results.append({"行业板块": sector_name, "代码": ticker, "二维资金状态": f"❌ 断裂:{str(e)[:10]}", "微观量价照妖镜": "-", "RS 20日(中期)": -999, "RS 5日(短期)": "-", "偏离10MA": "-", "偏离50MA": "-", "量能VR": "-"})

                    if sector_results:
                        res_df = pd.DataFrame(sector_results)
                        res_df['sort_key'] = pd.to_numeric(res_df['RS 20日(中期)'], errors='coerce').fillna(-999)
                        res_df = res_df.sort_values(by="sort_key", ascending=False).drop(columns=['sort_key'])
                        res_df['RS 20日(中期)'] = res_df['RS 20日(中期)'].apply(lambda x: f"{x*100:+.2f}%" if isinstance(x, float) and x != -999 else "-")

                        def color_xray(val):
                            s = str(val)
                            if "吸筹" in s or "真阳" in s or "黄金" in s or "缩量回踩" in s:
                                return 'color: #00ffcc; font-weight: bold;'
                            elif "点火" in s:
                                return 'color: #FFD700; font-weight: bold;'
                            elif "过热" in s or "砸盘" in s:
                                return 'color: #FF8C00; font-weight: bold;'
                            elif "失血" in s or "出逃" in s:
                                return 'color: #FF3131; font-weight: bold;'
                            return 'color: #ccc;'

                        st.dataframe(res_df.style.map(color_xray, subset=['二维资金状态', '微观量价照妖镜']), use_container_width=True, height=580)

    # ------------------------------------------------------------------------------
    # TAB 3: 猎杀雷达与五步买卖时机状态机
    # ------------------------------------------------------------------------------
    with tabs[2]:
        st.subheader("🎯 猎杀雷达：五步买卖时机精确制导系统")
        st.markdown("""
        告别单一的“突破追高”。系统自动测算 **均线位置、10日乖离率(BIAS)、K线实体饱满度与量能(VR)**，将标的精准定位至 **五大实战买卖档位**：
        `🎯 1.黄金回踩买点(缩量贴10/20MA)` | `🔥 2.起跑突破买点(放量真阳)` | `🌱 3.左侧极寒筑底` | `🚨 4.乖离过热止盈(禁追高/呼气)` | `🩸 5.放量派发/破位清仓`
        """)

        ma_period = st.radio(
            "🔭 选择雷达主引力刻度 (MA焦距)", 
            options=[20, 50, 60, 120, 200, 250], 
            index=4, 
            horizontal=True,
            help="200MA 适合确认长期牛熊分界；50/60MA 适合捕捉中期主升浪；若 API 复权异常支持直接输入 `代码:均线值` 手动接管。"
        )

        default_snipers = ['159326.SZ', '159558.SZ', '159566.SZ', '512070.SS', '516310.SS', '512890.SS']
        sniper_cols = st.columns(6)
        sniper_tickers = []
        manual_ma = {}

        for i in range(6):
            val = sniper_cols[i].text_input(f"核心槽位 {i+1}", default_snipers[i])
            if val:
                if ":" in val:
                    parts = val.split(":")
                    tkr = parts[0].strip()
                    sniper_tickers.append(tkr)
                    try:
                        manual_ma[tkr] = float(parts[1].strip())
                    except Exception:
                        pass
                else:
                    sniper_tickers.append(val.strip())

        sniper_results = []
        for t in sniper_tickers:
            try:
                t_data = get_etf_data_v2(t, datetime.now() - timedelta(days=400), datetime.now())
                if t_data.empty or len(t_data) < 30:
                    sniper_results.append({"标的": t, "五步买卖时机指令": "❌ 数据为空", "当前价": "-", "10MA低吸参考": "-", f"价/{ma_period}MA": "-", "10日乖离(BIAS)": "-", "RS 10日/5日": "-", "量能VR": "-"})
                    continue

                t_close = t_data['Close'].ffill().dropna()
                t_vol = t_data['Volume'].ffill().fillna(0)
                t_high = t_data['High'].ffill().dropna()
                t_low = t_data['Low'].ffill().dropna()

                curr_p = t_close.iloc[-1]
                ma10_val = t_close.rolling(10).mean().iloc[-1]
                ma20_val = t_close.rolling(20).mean().iloc[-1]
                bias_10 = (curr_p / ma10_val - 1) * 100
                bias_20 = (curr_p / ma20_val - 1) * 100
                ret_1d = (curr_p / t_close.iloc[-2] - 1) * 100
                ret_3d = (curr_p / t_close.iloc[-4] - 1) * 100 if len(t_close) >= 4 else 0.0

                c_range = max(t_high.iloc[-1] - t_low.iloc[-1], 1e-6)
                c_pos = (curr_p - t_low.iloc[-1]) / c_range

                if t in manual_ma:
                    ma_main = manual_ma[t]
                    above_main = curr_p >= ma_main
                    ma_str = f"{(curr_p/ma_main):.2f} (手动)"
                else:
                    if len(t_close) < ma_period:
                        ma_main = ma20_val
                        above_main = curr_p >= ma_main
                        ma_str = f"不足{ma_period}天"
                    else:
                        ma_main = t_close.rolling(ma_period).mean().iloc[-1]
                        above_main = curr_p >= ma_main
                        ma_str = f"{(curr_p/ma_main):.2f}"

                vr = t_vol.iloc[-1] / (t_vol.iloc[-6:-1].mean() + 1e-9)
                rs_df = pd.DataFrame({'target': t_close, 'base': df['as300']}).ffill().dropna()
                rs_curve = rs_df['target'] / rs_df['base']
                rs_10d = (rs_curve.iloc[-1] / rs_curve.iloc[-10] - 1) * 100 if len(rs_curve) >= 10 else 0.0
                rs_5d = (rs_curve.iloc[-1] / rs_curve.iloc[-5] - 1) * 100 if len(rs_curve) >= 5 else 0.0

                # 五步买卖状态机核心裁决树
                if bias_10 > 8.0 or ret_3d > 14.5:
                    timing_cmd = "🚨 4.乖离过热止盈 (禁追高/逢高呼气减半)"
                elif vr > 1.45 and (ret_1d < -1.8 or c_pos < 0.35):
                    timing_cmd = "🩸 5.放量长上影/阴线 (主力派发警惕)"
                elif (above_main or ma10_val >= ma20_val * 0.99) and (-2.2 <= bias_10 <= 2.5 or -1.8 <= bias_20 <= 2.0) and vr < 0.92 and rs_10d > -2.5:
                    timing_cmd = "🎯 1.黄金回踩买点 (缩量贴10/20MA吸气)"
                elif above_main and bias_10 <= 7.5 and vr >= 1.30 and c_pos >= 0.55 and (rs_5d > 0 or rs_10d > 0):
                    timing_cmd = "🔥 2.起跑突破买点 (放量真阳右侧主攻)"
                elif above_main and rs_10d > 0:
                    timing_cmd = "🌊 多头趋势持仓 (等回踩10MA加仓)"
                elif not above_main and (rs_5d > 1.5 or (vr < 0.65 and bias_20 > -4.0)):
                    timing_cmd = f"🌱 3.左侧极寒筑底 (RS苏醒/等突破{ma_period}MA)"
                else:
                    timing_cmd = "❄️ 5.重力压制/破位 (观望蛰伏)"

                sniper_results.append({
                    "标的": t,
                    "当前价": f"{curr_p:.3f}",
                    "10MA低吸参考": f"{ma10_val:.3f}",
                    f"价/{ma_period}MA": ma_str,
                    "10日乖离(BIAS)": f"{bias_10:+.2f}%",
                    "RS 10日/5日": f"{rs_10d:+.1f}% / {rs_5d:+.1f}%",
                    "量能VR": f"{vr:.2f}",
                    "五步买卖时机指令": timing_cmd
                })
            except Exception as e:
                sniper_results.append({"标的": t, "当前价": "-", "10MA低吸参考": "-", f"价/{ma_period}MA": "-", "10日乖离(BIAS)": "-", "RS 10日/5日": "-", "量能VR": "-", "五步买卖时机指令": f"❌ 异常:{str(e)[:12]}"})

        if sniper_results:
            st.table(pd.DataFrame(sniper_results))

        st.write("---")
        st.markdown("#### 🔍 单项标的价格均线 (10/20/200MA) 与 RS 相对强度双轨透视")
        audit_ticker = st.text_input("输入要深度透视买点的 ETF 或个股代码", "159558.SZ", key="single_v2")
        if audit_ticker:
            a_data = get_etf_data_v2(audit_ticker.strip(), datetime.now() - timedelta(days=400), datetime.now())
            if not a_data.empty and len(a_data) > 30:
                a_close = a_data['Close'].ffill().dropna()
                a_vol = a_data['Volume'].ffill().fillna(0)
                vr_s = a_vol.iloc[-1] / (a_vol.iloc[-6:-1].mean() + 1e-9)
                rs_df_s = pd.DataFrame({'target': a_close, 'base': df['as300']}).ffill().dropna()
                rs_ratio = rs_df_s['target'] / rs_df_s['base']

                p_col1, p_col2 = st.columns(2)
                with p_col1:
                    fig_p = go.Figure()
                    fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.tail(180), name="收盘价", line=dict(color='#00ffcc', width=2.5)))
                    fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.rolling(10).mean().tail(180), name="10MA (回踩低吸线)", line=dict(color='#FFD700', width=1.5, dash='dot')))
                    fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.rolling(20).mean().tail(180), name="20MA (波段生命线)", line=dict(color='white', width=1.5, dash='dash')))
                    if len(a_close) >= 200:
                        fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.rolling(200).mean().tail(180), name="200MA (牛熊边界)", line=dict(color='#FF3131', width=2)))
                    fig_p.update_layout(height=360, template="plotly_dark", title=f"{audit_ticker} 价格与 10/20/200MA 回踩买点图 (当日VR: {vr_s:.2f})", legend=dict(orientation="h", y=1.1, x=0), margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_p, use_container_width=True)
                with p_col2:
                    fig_rs = go.Figure()
                    fig_rs.add_trace(go.Scatter(x=rs_ratio.index[-180:], y=rs_ratio.tail(180), name="相对沪深300 RS曲线", line=dict(color='#00ffcc', width=2.5)))
                    fig_rs.add_trace(go.Scatter(x=rs_ratio.index[-180:], y=rs_ratio.rolling(20).mean().tail(180), name="RS 20MA", line=dict(color='white', dash='dot')))
                    if len(rs_ratio) >= 200:
                        fig_rs.add_trace(go.Scatter(x=rs_ratio.index[-180:], y=rs_ratio.rolling(200).mean().tail(180), name="RS 200MA", line=dict(color='orange', width=2, dash='dash')))
                    fig_rs.update_layout(height=360, template="plotly_dark", title=f"{audit_ticker} 相对大盘 Alpha 强度 (RS 曲线)", legend=dict(orientation="h", y=1.1, x=0), margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_rs, use_container_width=True)

    # ------------------------------------------------------------------------------
    # TAB 4: 通用型 AB 双雄对决罗盘与三三制正交沙盘 (跨周期通用架构)
    # ------------------------------------------------------------------------------
    with tabs[3]:
        st.subheader("⚖️ 通用型【AB 双雄强弱对决罗盘】与【组合正交实验室】")
        st.markdown("100% 参数化设计：无论市场主线如何变迁，随时对比任意两个板块的**资金抽血方向（A/B 比值曲线）**，并检验持仓是否发生同向踩踏。")

        preset_mode = st.radio(
            "⚡ 快捷对决模式切换 (或选第5项自由输入任意代码)：",
            [
                "1. 金融攻守内战：300非银 (512070.SS) vs 银行 (516310.SS)",
                "2. 大盘风格总开关：创业板指 (159915.SZ) vs 红利低波 (512890.SS)",
                "3. 科技内外循环：半导体设备 (159558.SZ) vs 通信光模块 (515880.SS)",
                "4. 电力基建双雄：电网设备 (159326.SZ) vs 储能电池 (159566.SZ)",
                "5. 自由定制对决：手动输入任意两个 ETF/股票代码"
            ],
            horizontal=False
        )

        preset_dict = {
            "1.": ("512070.SS", "516310.SS", "300非银(进攻)", "银行ETF(防守)"),
            "2.": ("159915.SZ", "512890.SS", "创业板(成长矛)", "红利低波(收租盾)"),
            "3.": ("159558.SZ", "515880.SS", "半导体设备(内循环)", "通信光模块(出海链)"),
            "4.": ("159326.SZ", "159566.SZ", "电网设备(特高压)", "储能电池(AIDC配储)")
        }

        key_prefix = preset_mode[:2]
        if key_prefix in preset_dict:
            def_a, def_b, name_a, name_b = preset_dict[key_prefix]
        else:
            def_a, def_b, name_a, name_b = "512880.SS", "512890.SS", "标的 A", "标的 B"

        col_ab1, col_ab2 = st.columns(2)
        ticker_a = col_ab1.text_input(f"⚔️ 对决方 A ({name_a})", value=def_a)
        ticker_b = col_ab2.text_input(f"🛡️ 对决方 B ({name_b})", value=def_b)

        if ticker_a and ticker_b:
            df_a = get_etf_data_v2(ticker_a.strip(), datetime.now() - timedelta(days=300), datetime.now())
            df_b = get_etf_data_v2(ticker_b.strip(), datetime.now() - timedelta(days=300), datetime.now())
            if not df_a.empty and not df_b.empty:
                ab_df = pd.DataFrame({'A': df_a['Close'], 'B': df_b['Close']}).ffill().dropna()
                if len(ab_df) > 25:
                    ratio_ab = ab_df['A'] / ab_df['B']
                    ratio_ma20 = ratio_ab.rolling(20).mean()
                    r_5d = (ratio_ab.iloc[-1] / ratio_ab.iloc[-5] - 1) * 100
                    r_20d = (ratio_ab.iloc[-1] / ratio_ab.iloc[-20] - 1) * 100
                    above_ma20 = ratio_ab.iloc[-1] >= ratio_ma20.iloc[-1]

                    if above_ma20 and r_5d > 0:
                        verdict = f"🔥 【{ticker_a}】全面胜出！A/B比值站上20日均线且短期加速({r_5d:+.2f}%)，资金正从 {ticker_b} 抽血流向 {ticker_a}，配置重心应果断向【{ticker_a}】倾斜！"
                        v_col = "#00ffcc"
                    elif not above_ma20 and r_5d < 0:
                        verdict = f"🛡️ 【{ticker_b}】全面胜出！A/B比值跌破20日均线({r_5d:+.2f}%)，市场处于防御/切换态势，配置重心应果断向【{ticker_b}】倾斜！"
                        v_col = "#FFD700"
                    else:
                        verdict = f"⚖️ 双雄处于风格切换临界期（20日相对动量 {r_20d:+.2f}%，5日相对动量 {r_5d:+.2f}%），建议采取均衡配比。"
                        v_col = "#ccc"

                    st.markdown(f"<div class='quadrant-box' style='border-color:{v_col};'><h4 style='color:{v_col};margin:0;'>{verdict}</h4></div>", unsafe_allow_html=True)

                    fig_ab = go.Figure()
                    fig_ab.add_trace(go.Scatter(x=ratio_ab.index[-180:], y=ratio_ab.tail(180), name=f"{ticker_a} / {ticker_b} 强弱比值", line=dict(color='#00ffcc', width=3)))
                    fig_ab.add_trace(go.Scatter(x=ratio_ma20.index[-180:], y=ratio_ma20.tail(180), name="比值 20MA (风格分水岭)", line=dict(color='orange', width=2, dash='dash')))
                    fig_ab.update_layout(height=330, template="plotly_dark", title=f"双雄强弱比值曲线 (向上代表 {ticker_a} 跑赢 {ticker_b})", margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_ab, use_container_width=True)

        st.write("---")
        st.subheader("🕸️ 跨周期正交沙盘：进攻舱 vs 防守舱相关性防坍塌检验")
        col_p1, col_p2 = st.columns(2)
        off_input = col_p1.text_input("⚔️ 第一舱（进攻与成长舱，逗号分隔）", value="159326.SZ, 159558.SZ, 159566.SZ")
        def_input = col_p2.text_input("🛡️ 第二舱（防守与赔率舱，逗号分隔）", value="512070.SS, 516310.SS, 512890.SS")

        off_list = [x.strip() for x in off_input.split(",") if x.strip()]
        def_list = [x.strip() for x in def_input.split(",") if x.strip()]
        all_port = list(dict.fromkeys(off_list + def_list))

        if len(all_port) > 1:
            c_dict = {}
            for ct in all_port:
                t_df = get_etf_data_v2(ct, datetime.now() - timedelta(days=100), datetime.now())
                if not t_df.empty:
                    c_dict[ct] = t_df['Close']
            if len(c_dict) > 1:
                c_df = pd.DataFrame(c_dict).ffill().dropna()
                corr_m = c_df.pct_change().dropna().corr()

                # 计算进攻舱与防守舱之间的跨舱平均相关性
                cross_corrs = []
                for o_t in off_list:
                    for d_t in def_list:
                        if o_t in corr_m.columns and d_t in corr_m.columns and o_t != d_t:
                            cross_corrs.append(corr_m.loc[o_t, d_t])
                avg_cross = np.mean(cross_corrs) if cross_corrs else 0.0

                if avg_cross > 0.70:
                    st.error(f"🚨 跨舱维度坍塌警告：你的【进攻舱】与【防守舱】平均相关系数高达 {avg_cross:.2f} (>0.70)！大盘暴跌时无法起到对冲保护作用。")
                else:
                    st.success(f"✅ 跨舱正交化极佳：【进攻舱】与【防守舱】平均相关系数仅为 {avg_cross:.2f}，攻守双肺形成了坚固的对冲保护。")

                fig_corr = go.Figure(data=go.Heatmap(
                    z=corr_m.values, x=corr_m.columns, y=corr_m.columns,
                    colorscale='RdBu', zmin=-1, zmax=1,
                    text=np.round(corr_m.values, 2), texttemplate="%{text}"
                ))
                fig_corr.update_layout(height=380, template="plotly_dark", title="持仓组合近 60 日底层相关系数热力图", margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_corr, use_container_width=True)

    # ------------------------------------------------------------------------------
    # TAB 5: 三三制 ATR 仓位分配与挂单生成器
    # ------------------------------------------------------------------------------
    with tabs[4]:
        st.subheader("🛡️ 三三制 ATR 仓位几何与自动挂单表")
        st.markdown("输入你的账户总资金与三三制舱位比例，系统自动读取你在 Tab 4 设定的【进攻舱】与【防守舱】标的，批量生成 **10MA 低吸挂单价、2倍 ATR 硬止损线与建议买入股数**。")

        p1, p2, p3, p4 = st.columns(4)
        total_cap = p1.number_input("💰 账户总本金 (¥)", value=1000000, step=100000)
        off_pct = p2.number_input("⚔️ 进攻舱总配额 (%)", value=33.0, step=1.0, max_value=60.0)
        def_pct = p3.number_input("🛡️ 防守舱总配额 (%)", value=35.0, step=1.0, max_value=60.0)
        atr_mult = p4.number_input("📏 止损宽度 (倍数 ATR)", value=2.0, step=0.1)

        cash_pct = max(0.0, 100.0 - off_pct - def_pct)
        st.info(f"📊 **当前三三制资金划分**：⚔️ 进攻舱配额 **¥{total_cap*off_pct/100:,.0f} ({off_pct:.0f}%)** &nbsp;|&nbsp; 🛡️ 防守舱配额 **¥{total_cap*def_pct/100:,.0f} ({def_pct:.0f}%)** &nbsp;|&nbsp; 💧 现金冗余 **¥{total_cap*cash_pct/100:,.0f} ({cash_pct:.0f}%)**")

        batch_rows = []
        for bucket_name, t_list, bucket_pct in [("⚔️ 进攻舱", off_list, off_pct), ("🛡️ 防守舱", def_list, def_pct)]:
            if not t_list:
                continue
            per_slot_cap = (total_cap * (bucket_pct / 100.0)) / len(t_list)
            for tkr in t_list:
                p_df = get_etf_data_v2(tkr, datetime.now() - timedelta(days=120), datetime.now())
                if p_df.empty or len(p_df) < 20:
                    continue
                p_df = p_df.ffill().dropna()
                c_col, h_col, l_col = p_df['Close'], p_df['High'], p_df['Low']
                prev_c = c_col.shift(1)
                tr = pd.concat([h_col - l_col, (h_col - prev_c).abs(), (l_col - prev_c).abs()], axis=1).max(axis=1)
                atr_val = tr.rolling(14).mean().iloc[-1]
                curr_p = c_col.iloc[-1]
                ma10_p = c_col.rolling(10).mean().iloc[-1]
                ma20_p = c_col.rolling(20).mean().iloc[-1]

                stop_dist = atr_val * atr_mult
                stop_p = curr_p - stop_dist

                # 按槽位配额计算整百股数
                shares = int(per_slot_cap / curr_p)
                if ".SZ" in tkr or ".SS" in tkr or ".SH" in tkr:
                    shares = (shares // 100) * 100
                actual_cost = shares * curr_p
                risk_loss = shares * stop_dist
                risk_pct_total = (risk_loss / total_cap) * 100

                batch_rows.append({
                    "所属舱位": bucket_name,
                    "标的代码": tkr,
                    "当前现价": f"{curr_p:.3f}",
                    "🎯 10MA低吸参考价": f"{ma10_p:.3f}",
                    "📏 20MA生命线": f"{ma20_p:.3f}",
                    "14日ATR波动": f"{atr_val:.3f}",
                    "🚨 硬编码止损价": f"{stop_p:.3f}",
                    "建议买入股数(整百)": f"{shares:,} 股",
                    "分配资金 (¥)": f"¥{actual_cost:,.0f}",
                    "触损占总本金风险": f"-{risk_pct_total:.2f}%"
                })

        if batch_rows:
            st.table(pd.DataFrame(batch_rows))
            st.caption("💡 实战执行纪律：处于『🚨 乖离过热』的标的，严禁按当前现价直接追高，应耐心挂单在【🎯 10MA低吸参考价】附近分批吸气；买入后立刻将【🚨 硬编码止损价】输入券商条件单。")

except Exception as e:
    st.error(f"系统运行中发生错误: {e}")

st.markdown("---")
st.caption("🏹 GSMI Dual-Core v2.0 | 全球宏观与本土行业 | 三三制正交风控体系")
```$]
