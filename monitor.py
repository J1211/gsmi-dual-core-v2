import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from fredapi import Fred
import akshare as ak

# ==============================================================================
# 1. 界面配置与视觉主题
# ==============================================================================
st.set_page_config(page_title="GSMI v2.2 | 全球宏观与本土行业", layout="wide")

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

st.title("🏹 GSMI Dual-Core v2.2 | 全球宏观与本土行业")

status_placeholder = st.empty()

# ==============================================================================
# 2. 侧边栏参数配置 (支持 0.0 全自动盘中动态折算)
# ==============================================================================
st.sidebar.header("🛠️ 本土与全球双核参数")
cn_turnover_manual = st.sidebar.number_input(
    "🌊 A股两市成交额 (万亿¥, 0为全自动每日测算)", 
    min_value=0.0, max_value=5.0, value=0.0, step=0.05,
    help="保持 0.00 即可！系统会自动抓取或根据核心宽基实时量能（含盘中分钟折算）动态推算当日两市全天预计成交额。"
)
cn_10y_bond = st.sidebar.number_input(
    "🏛️ 中国 10Y 国债收益率 (%)", 
    min_value=0.5, max_value=4.0, value=1.65, step=0.02,
    help="本土无风险重力标尺，用于与动态红利股息率对比计算本土股债利差（ERP）。"
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
# 3. 核心物理函数：盘中时钟折算、3日冷却记忆锁与三重装甲引擎
# ==============================================================================
# 核心行业 ETF 对应的第一权重龙头映射字典 (用于 Tab 3 龙头扳机实时验证)
LEADER_MAP = {
    "159566.SZ": ("300274.SZ", "阳光电源"),
    "159796.SZ": ("300750.SZ", "宁德时代"),
    "159326.SZ": ("002028.SZ", "思源电气"),
    "159558.SZ": ("002371.SZ", "北方华创"),
    "159516.SZ": ("002371.SZ", "北方华创"),
    "515880.SS": ("300308.SZ", "中际旭创"),
    "516620.SS": ("300251.SZ", "光线传媒"),
    "159855.SZ": ("300251.SZ", "光线传媒"),
    "512980.SS": ("002027.SZ", "分众传媒"),
    "159870.SZ": ("600309.SS", "万华化学"),
    "512070.SS": ("601318.SS", "中国平安"),
    "512880.SS": ("600030.SH", "中信证券"),
    "516310.SS": ("601288.SS", "农业银行"),
    "512800.SS": ("600036.SS", "招商银行"),
    "512890.SS": ("601009.SS", "南京银行"),
    "512010.SS": ("603259.SS", "药明康德"),
    "159992.SZ": ("600276.SS", "恒瑞医药"),
    "512690.SS": ("600519.SS", "贵州茅台"),
    "512400.SS": ("601899.SS", "紫金矿业"),
    "515220.SS": ("601088.SS", "中国神华"),
    "159732.SZ": ("002475.SZ", "立讯精密")
}

def get_beijing_now():
    """获取当前精确北京时间 (UTC+8)"""
    return datetime.now(timezone.utc) + timedelta(hours=8)

def get_intraday_multiplier(last_bar_date_obj, is_cn_market=True):
    """
    计算 A 股盘中交易时段 (09:30 - 15:00) 的全天成交量折算系数。
    同时用于个股/ETF 的智能 VR 以及顶部宏观两市总成交额的盘中校准。
    """
    bj_now = get_beijing_now()
    last_date = pd.to_datetime(last_bar_date_obj).date()
    if is_cn_market and last_date == bj_now.date() and bj_now.weekday() < 5:
        cur_mins = bj_now.hour * 60 + bj_now.minute
        open_mins = 9 * 60 + 30    # 09:30
        lunch_start = 11 * 60 + 30 # 11:30
        lunch_end = 13 * 60        # 13:00
        close_mins = 15 * 60       # 15:00

        if open_mins <= cur_mins < close_mins:
            if cur_mins <= lunch_start:
                elapsed = cur_mins - open_mins
            elif cur_mins <= lunch_end:
                elapsed = 120.0
            else:
                elapsed = 120.0 + (cur_mins - lunch_end)
            eff_elapsed = max(float(elapsed), 20.0)
            return 240.0 / eff_elapsed, True
    return 1.0, False

def calc_smart_vr(vol_series, is_cn_market=True):
    """
    智能量能倍率 (VR) 计算器：
    采用过去5日均量与中位数的稳健均值作为分母（防止前一日单日天量过度压低次日VR），并支持盘中动态折算。
    """
    if len(vol_series) < 6:
        return 1.0, False
    prev_5_vols = vol_series.iloc[-6:-1]
    base_vol = 0.5 * prev_5_vols.mean() + 0.5 * prev_5_vols.median()
    if base_vol <= 0:
        return 1.0, False

    mult, is_extrap = get_intraday_multiplier(vol_series.index[-1], is_cn_market=is_cn_market)
    proj_vol = float(vol_series.iloc[-1]) * mult
    vr_val = proj_vol / (base_vol + 1e-9)
    return vr_val, is_extrap

def is_bar_distribution(close_p, prev_close_p, high_p, low_p, vr_val):
    """
    判定单根 K 线是否构成【真实的放量长上影/阴线出逃】：
    增加物理绝对幅度门槛：上影线真实回落幅度必须 > 1.2% 且全天振幅 > 1.5%（或单日实跌 < -1.5%），
    彻底消除银行等低波资产全天仅波动 0.4% (几厘钱) 被显微镜放大误报为长上影出逃的问题。
    """
    ret_1d = (close_p / (prev_close_p + 1e-9)) - 1.0
    c_range = max(high_p - low_p, 1e-6)
    c_pos = (close_p - low_p) / c_range
    upper_shadow_pct = (high_p - close_p) / (close_p + 1e-9)
    day_amp_pct = (high_p - low_p) / (low_p + 1e-9)

    is_real_long_shadow = (c_pos < 0.35) and (upper_shadow_pct > 0.012) and (day_amp_pct > 0.015)
    is_real_big_drop = (ret_1d < -0.015)

    return (vr_val > 1.38) and (is_real_big_drop or is_real_long_shadow)

def check_cooldown_lock(close_s, high_s, low_s, vol_s, is_cn_market=True, lookback=3):
    """
    3日出逃冷却记忆锁 (Cooldown Lock)：
    检查过去 lookback (3) 个交易日内是否发生过真实的放量派发/出逃 K 线，
    若发生过，且当前最新价尚未向上突破那根出逃 K 线的最高价 (未完成反包确认)，则触发冷却锁！
    """
    n = len(close_s)
    if n < 10:
        return False
    # 检查今日 (-1) 以及前两日 (-2, -3)
    for offset in range(1, lookback + 1):
        idx = n - offset
        c_p = float(close_s.iloc[idx])
        prev_c = float(close_s.iloc[idx - 1])
        h_p = float(high_s.iloc[idx])
        l_p = float(low_s.iloc[idx])

        prev_5_v = vol_s.iloc[idx - 5:idx]
        b_vol = 0.5 * prev_5_v.mean() + 0.5 * prev_5_v.median()
        if idx == n - 1:
            mult, _ = get_intraday_multiplier(vol_s.index[idx], is_cn_market=is_cn_market)
            v_bar = float(vol_s.iloc[idx]) * mult / (b_vol + 1e-9)
        else:
            v_bar = float(vol_s.iloc[idx]) / (b_vol + 1e-9)

        if is_bar_distribution(c_p, prev_c, h_p, l_p, v_bar):
            # 如果是过去2天发生的出逃，且当前最新价仍低于出逃日最高价，则处于冷却锁定期
            if offset > 1 and float(close_s.iloc[-1]) < h_p:
                return True
    return False

@st.cache_data(ttl=900)
def get_etf_data_v2(ticker, start_date, end_date):
    """三重装甲 ETF/个股前复权抓取引擎"""
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

    yf_ticker = ticker.replace(".SH", ".SS")
    try:
        tkr = yf.Ticker(yf_ticker)
        df_yf = tkr.history(start=start_date.strftime('%Y-%m-%d'), end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d'))
        if df_yf is not None and not df_yf.empty:
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Open', 'High', 'Low', 'Close', 'Volume']].sort_index()
    except Exception:
        pass

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

@st.cache_data(ttl=1200)
def fetch_and_sync_data():
    end = datetime.now()
    start = end - timedelta(days=500)
    status_report = {}
    
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

    def safe_get_yf(ticker, name, return_vol=False):
        try:
            df_y = yf.download(ticker, start=start - timedelta(days=15), end=end + timedelta(days=1), progress=False)
            if df_y is None or df_y.empty: 
                status_report[name] = "❌"
                return (pd.Series(dtype='float64'), pd.Series(dtype='float64')) if return_vol else pd.Series(dtype='float64')
            close_s = df_y['Close'].iloc[:, 0] if isinstance(df_y.columns, pd.MultiIndex) else df_y['Close']
            close_s.index = pd.to_datetime(close_s.index).tz_localize(None)
            status_report[name] = "✅"
            if return_vol:
                vol_s = df_y['Volume'].iloc[:, 0] if isinstance(df_y.columns, pd.MultiIndex) else df_y['Volume']
                vol_s.index = pd.to_datetime(vol_s.index).tz_localize(None)
                return close_s, vol_s
            return close_s
        except Exception:
            status_report[name] = "❌"
            return (pd.Series(dtype='float64'), pd.Series(dtype='float64')) if return_vol else pd.Series(dtype='float64')

    as300_c, as300_v = safe_get_yf("510300.SS", "AS300", return_vol=True)
    chinext_c, chinext_v = safe_get_yf("159915.SZ", "ChiNext", return_vol=True)

    # 盘中成交量动态时间折算：防止盘中半截成交量导致顶部两市成交额与本土活水总分误降 9 分
    if not as300_v.empty:
        mult_a, is_ext_a = get_intraday_multiplier(as300_v.index[-1], is_cn_market=True)
        if is_ext_a:
            as300_v.iloc[-1] = as300_v.iloc[-1] * mult_a
    if not chinext_v.empty:
        mult_c, is_ext_c = get_intraday_multiplier(chinext_v.index[-1], is_cn_market=True)
        if is_ext_c:
            chinext_v.iloc[-1] = chinext_v.iloc[-1] * mult_c

    yf_dict = {
        'dxy': safe_get_yf("DX-Y.NYB", "DXY"),
        'copper': safe_get_yf("HG=F", "Copper"),
        'gold': safe_get_yf("GC=F", "Gold"),
        'hkd': safe_get_yf("HKD=X", "HKD"),
        'cny': safe_get_yf("CNY=X", "CNY"),
        'hsi': safe_get_yf("^HSI", "HSI"),
        'as300': as300_c,
        'as300_vol': as300_v,
        'chinext': chinext_c,
        'chinext_vol': chinext_v,
        'div_low': safe_get_yf("512890.SS", "DivLowVol"),
        'btc': safe_get_yf("BTC-USD", "BTC"),
        'qqq': safe_get_yf("QQQ", "QQQ"),
        'move': safe_get_yf("^MOVE", "MOVE"),
        'vix': safe_get_yf("^VIX", "VIX")
    }

    cn_real_amt = pd.Series(dtype='float64')
    try:
        sh_idx = ak.stock_zh_index_daily_em(symbol="sh000001")
        sz_idx = ak.stock_zh_index_daily_em(symbol="sz399001")
        if not sh_idx.empty and not sz_idx.empty:
            sh_s = pd.Series(pd.to_numeric(sh_idx['amount'], errors='coerce').values, index=pd.to_datetime(sh_idx['date']))
            sz_s = pd.Series(pd.to_numeric(sz_idx['amount'], errors='coerce').values, index=pd.to_datetime(sz_idx['date']))
            cn_real_amt = ((sh_s + sz_s) / 1e12).sort_index()
            if not cn_real_amt.empty:
                m_idx, is_ext_idx = get_intraday_multiplier(cn_real_amt.index[-1], is_cn_market=True)
                if is_ext_idx:
                    cn_real_amt.iloc[-1] = cn_real_amt.iloc[-1] * m_idx
            status_report["CN_Turnover"] = "✅(Real)"
    except Exception:
        status_report["CN_Turnover"] = "⚠️(Proxy)"

    all_series = {**data_dict, **yf_dict}
    if not cn_real_amt.empty:
        all_series['cn_amt_real'] = cn_real_amt

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

        vol_proxy_ratio = (
            (df['as300_vol'] / (df['as300_vol'].rolling(20).median() + 1e-9)) * 0.6 +
            (df['chinext_vol'] / (df['chinext_vol'].rolling(20).median() + 1e-9)) * 0.4
        ).clip(0.65, 2.2).fillna(1.0)

        if 'cn_amt_real' in df.columns and df['cn_amt_real'].notna().sum() > 10:
            df['cn_turnover_series'] = df['cn_amt_real'].ffill().fillna(1.58 * vol_proxy_ratio)
        else:
            df['cn_turnover_series'] = (1.58 * vol_proxy_ratio).round(2)

    return df, status_report

def calculate_dual_scores(df, fms_val, cn_turnover_input, cn_bond):
    if df.empty:
        return df, 50.0, 1.55, {}
    if 'spread' in df.columns and df['spread'].max() < 50:
        df['spread'] = df['spread'] * 100

    gsmi_history = []
    cn_score_history = []

    nl_ma20 = df['nl'].rolling(20).mean()
    cg_ma200 = df['cg_ratio'].rolling(200).mean()
    as300_ma20 = df['as300'].rolling(20).mean()
    div_ma60 = df['div_low'].rolling(60).mean()

    for i in range(len(df)):
        if i < 20:
            gsmi_history.append(np.nan)
            cn_score_history.append(np.nan)
            continue

        # 1. 全球美元 GSMI 评分
        s_nl = (15 if df['nl'].iloc[i] > nl_ma20.iloc[i] else 0) + (10 if df['nl'].iloc[i] > df['nl'].iloc[i-5] else 0)
        s_tips = score_linear(df['tips'].iloc[i], 0.5, 2.5, 20, reverse=True)
        s_dxy = score_linear(df['dxy'].iloc[i], 98, 108, 15, reverse=True)
        s_fms = score_linear(fms_val, 3.5, 6.0, 15, reverse=False)
        s_cg = (10 if df['cg_ratio'].iloc[i] > cg_ma200.iloc[i] else 0) + (5 if df['cg_ratio'].iloc[i] > df['cg_ratio'].iloc[i-10:i-5].mean() else 0)
        s_spread = score_linear(df['spread'].iloc[i], 300, 600, 10, reverse=True)
        gsmi_history.append(s_nl + s_tips + s_dxy + s_fms + s_cg + s_spread)

        # 2. 中国本土活水评分 (含盘中折算量能保护)
        day_to = df['cn_turnover_series'].iloc[i]
        if i == len(df) - 1 and cn_turnover_input > 0:
            day_to = cn_turnover_input

        s_vol_abs = score_linear(day_to, 1.0, 2.1, 25, reverse=False)
        v_ratio_5d = df['as300_vol'].iloc[i-4:i+1].mean() / (df['as300_vol'].iloc[i-19:i+1].median() + 1e-9)
        s_vol_mom = score_linear(v_ratio_5d, 0.7, 1.35, 10, reverse=False)
        s_cn_vol = s_vol_abs + s_vol_mom

        as300_bias20 = (df['as300'].iloc[i] / (as300_ma20.iloc[i] + 1e-9)) - 1
        chinext_mom10 = (df['chinext'].iloc[i] / (df['chinext'].iloc[i-10] + 1e-9)) - 1
        s_cn_trend = score_linear(as300_bias20, -0.05, 0.05, 15, reverse=False) + score_linear(chinext_mom10, -0.08, 0.08, 15, reverse=False)

        div_price_ratio = (div_ma60.iloc[i] / (df['div_low'].iloc[i] + 1e-9)) if not pd.isna(div_ma60.iloc[i]) else 1.0
        dyn_div_yield = 4.66 * np.clip(div_price_ratio, 0.85, 1.15)
        erp_val = dyn_div_yield - cn_bond
        s_cn_erp = score_linear(erp_val, 1.6, 3.3, 20, reverse=False)

        cny_chg10 = (df['cny'].iloc[i] / (df['cny'].iloc[i-10] + 1e-9)) - 1
        s_cny = score_linear(cny_chg10, -0.015, 0.015, 9, reverse=True)
        s_hkd = score_linear(df['hkd'].iloc[i], 7.76, 7.85, 6, reverse=True)
        s_cn_fx = s_cny + s_hkd

        cn_score_history.append(round(s_cn_vol + s_cn_trend + s_cn_erp + s_cn_fx, 1))

    df['gsmi_score'] = gsmi_history
    df['cn_score'] = cn_score_history

    effective_turnover = cn_turnover_input if cn_turnover_input > 0 else float(df['cn_turnover_series'].iloc[-1])
    latest_cn_score = float(df['cn_score'].iloc[-1])
    latest_div_ratio = (div_ma60.iloc[-1] / df['div_low'].iloc[-1]) if not pd.isna(div_ma60.iloc[-1]) else 1.0
    latest_erp_bps = round((4.66 * np.clip(latest_div_ratio, 0.85, 1.15) - cn_bond) * 100, 0)

    cn_details = {
        "erp_bps": latest_erp_bps,
        "eff_turnover": round(effective_turnover, 2)
    }
    return df, latest_cn_score, round(effective_turnover, 2), cn_details

# ==============================================================================
# 4. 主程序执行与顶部双核战时状态机
# ==============================================================================
try:
    df_raw, report = fetch_and_sync_data()
    if df_raw.empty:
        st.error("🚨 核心数据链路断裂，请检查 FRED API Key 或网络连接。")
        st.stop()

    df, cn_score, eff_turnover, cn_details = calculate_dual_scores(df_raw, fms_cash, cn_turnover_manual, cn_10y_bond)
    latest = df.iloc[-1]
    prev_cn_score = df['cn_score'].iloc[-2] if len(df) > 2 and not pd.isna(df['cn_score'].iloc[-2]) else cn_score
    cn_score_delta = cn_score - prev_cn_score

    latest_date = df.index[-1].strftime('%Y.%m.%d')
    us_gsmi = latest['gsmi_score']
    sofr_val = latest.get('sofr_spread', -1.0)
    vix_val = latest.get('vix', 18.0)

    if sofr_val > 5.0 or vix_val > 32.0:
        banner_icon, banner_zone = "🚨", "海外流动性休克警报 (跨洋提款风险)"
        banner_action = f"SOFR息差({sofr_val:+.1f}bps)或VIX({vix_val:.1f})触发极值！严防外资抛售A股核心资产换汇，暂停右侧追高，收缩至现金与低波防线。"
        border_col = "#FF3131"
    elif cn_score >= 65:
        banner_icon, banner_zone = "🟢", "本土活水扩张区 (内生做多窗口)"
        banner_action = f"A股预计量能({eff_turnover:.2f}万亿)与动量充沛，本土总分({cn_score:.1f})走强！优先围绕『🎯水上回踩10MA』与硬核制造主线积极吸气。"
        border_col = "#00ffcc"
    elif cn_score >= 45:
        banner_icon, banner_zone = "🟡", "结构性存量博弈区 (买阴不买阳 / 严禁追高)"
        banner_action = f"本土总分({cn_score:.1f}，较前日{cn_score_delta:+.1f})处于存量轮动，全球GSMI({us_gsmi:.1f})。严禁在偏离10MA >5%时追高，坚持10MA缩量回踩分批低吸。"
        border_col = "#FFD700"
    else:
        banner_icon, banner_zone = "❄️", "本土枯水窒息区 (现金为王防御模式)"
        banner_action = f"A股量能萎缩且趋势承压(本土得分{cn_score:.1f})。严控仓位上限，保持高比例现金冗余等待缩量冰点过去。"
        border_col = "#ff6b6b"

    status_placeholder.markdown(f"""
    <div style='padding: 12px 18px; border-radius: 6px; border-left: 6px solid {border_col}; border-top: 1px solid #333; border-right: 1px solid #333; border-bottom: 1px solid #333; background-color: #161922; margin-bottom: 15px;'>
        <div style='display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;'>
            <h4 style='margin:0; color: #fff;'>{banner_icon} {latest_date} 双核状态：{banner_zone}</h4>
            <span style='color: #00ffcc; font-weight: bold; font-size: 15px;'>
                🌊 本土活水总分: {cn_score:.1f}/100 ({cn_score_delta:+.1f}) &nbsp;|&nbsp; 🌎 全球美元 GSMI: {us_gsmi:.1f}/100 &nbsp;|&nbsp; 🩸 SOFR-IORB: {sofr_val:+.1f} bps
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
    # TAB 1: 全球与本土宏观总闸
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
            vol_tag = "🔥 增量博弈" if eff_turnover >= 1.8 else ("⚖️ 存量轮动" if eff_turnover >= 1.3 else "❄️ 缩量防守")
            cny_val = latest.get('cny', 6.70)
            mode_tag = "手动" if cn_turnover_manual > 0 else "自动折算"
            st.markdown(f"<div class='card-box'><div class='card-title'>🌊 A股成交({mode_tag}) & 本土ERP</div><div class='card-val'>{eff_turnover:.2f}万亿 <span style='font-size:15px;color:#00ffcc;'>| CNY {cny_val:.4f}</span></div><div class='card-sub'>{vol_tag} | 动态股债利差: +{cn_details['erp_bps']:.0f}bps</div></div>", unsafe_allow_html=True)

        st.markdown("---")
        chart_choice = st.radio(
            "📈 选择下方深度历史监控视图 (一屏切换)：",
            [
                "1. 💧 美联储净流动性 (NL) vs 10Y TIPS 重力轨迹",
                "2. 🩸 系统血压 (SOFR-IORB) 心电图与 90天休克黑匣子",
                "3. 🏛️ 债市熊陡压差 (2s10s) 与 VIX/MOVE 双核波动率",
                "4. 🪙 法币脱钩显影 (黄金 vs TIPS) & 铜金比 200MA",
                "5. 📊 双核历史轨迹：本土活水总分 vs 全球 GSMI & 沪深300"
            ],
            horizontal=True
        )

        if "1." in chart_choice:
            valid_df = df[df['nl'] > 1.0].copy()
            if not valid_df.empty:
                st.markdown("##### 💧 美联储净流动性 (NL) vs 10Y TIPS 实际利率重力轨迹")
                nl_min, nl_max = valid_df['nl'].min() - 0.1, valid_df['nl'].max() + 0.1
                fig_nl = go.Figure()
                fig_nl.add_trace(go.Scatter(x=valid_df.index, y=valid_df['nl'], name="净流动性 NL (T$)", line=dict(color='#00ffcc', width=3)))
                fig_nl.add_trace(go.Scatter(x=valid_df.index, y=valid_df['tips'], name="10Y TIPS (%)", line=dict(color='#FF3131', dash='dot', width=2), yaxis="y2"))
                fig_nl.update_layout(height=360, template="plotly_dark", yaxis=dict(title="NL (T$)", range=[nl_min, nl_max], gridcolor='#333'), yaxis2=dict(overlaying="y", side="right", showgrid=False, title="TIPS (%)"), legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=10, t=35, b=10))
                st.plotly_chart(fig_nl, use_container_width=True)

        elif "2." in chart_choice:
            c_ekg, c_log = st.columns([2.2, 1])
            with c_ekg:
                st.markdown("##### 🩸 美元回购管网血压心电图 (SOFR - IORB)")
                fig_bp = go.Figure()
                fig_bp.add_trace(go.Scatter(x=df.index, y=df['sofr_spread'], name="SOFR-IORB (bps)", line=dict(color='#00ffcc', width=2), fill='tozeroy', fillcolor='rgba(0, 255, 204, 0.1)'))
                fig_bp.add_hline(y=0, line_dash="dash", line_color="orange", annotation_text="0 bps 基准线")
                fig_bp.add_hline(y=5, line_dash="dot", line_color="#FF3131", annotation_text="+5 bps 真实休克高通滤波线")
                shock_pts = df[df['sofr_spread'] > 0]
                if not shock_pts.empty:
                    fig_bp.add_trace(go.Scatter(x=shock_pts.index, y=shock_pts['sofr_spread'], mode='markers', name="正压报警点", marker=dict(color='#FF3131', size=7, symbol='x')))
                fig_bp.update_layout(height=340, template="plotly_dark", yaxis_title="息差 (bps)", legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=10, t=35, b=10))
                st.plotly_chart(fig_bp, use_container_width=True)
            with c_log:
                st.markdown("##### 🚨 近 90 天正压事件黑匣子")
                recent_shocks = shock_pts[shock_pts.index > (datetime.now() - timedelta(days=90))]
                if not recent_shocks.empty:
                    log_df = recent_shocks[['sofr_spread']].copy()
                    log_df.index = log_df.index.strftime('%Y-%m-%d')
                    log_df.columns = ['读数(bps)']
                    log_df['定性'] = log_df['读数(bps)'].apply(lambda x: "💥 真实休克" if x >= 5.0 else "⚠️ 微观摩擦")
                    st.dataframe(log_df.sort_index(ascending=False), height=300, use_container_width=True)
                else:
                    st.success("过去 90 天内管网无正压异常。")

        elif "3." in chart_choice:
            st.markdown("##### 🏛️ 美债收益率曲线压差 (2s10s) 与 VIX/MOVE 双核波动率共振")
            fig_bond = go.Figure()
            if 'yield_curve' in df.columns:
                fig_bond.add_trace(go.Scatter(x=df.index[-180:], y=df['yield_curve'].tail(180), name="2s10s 压差 (左轴)", line=dict(color='#FF3131', width=3)))
                fig_bond.add_hline(y=0, line_dash="dash", line_color="white", annotation_text="解除倒挂 (熊陡警戒线)")
            if 'move' in df.columns and 'vix' in df.columns:
                fig_bond.add_trace(go.Scatter(x=df.index[-180:], y=df['move'].tail(180), name="MOVE 债市波动 (右轴)", line=dict(color='#00ffcc', width=2, dash='dot'), yaxis="y2"))
                fig_bond.add_trace(go.Scatter(x=df.index[-180:], y=df['vix'].tail(180), name="VIX 股市恐慌 (右轴)", line=dict(color='orange', width=2), yaxis="y2"))
            fig_bond.update_layout(height=360, template="plotly_dark", yaxis2=dict(overlaying="y", side="right", showgrid=False), legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=10, t=35, b=10))
            st.plotly_chart(fig_bond, use_container_width=True)

        elif "4." in chart_choice:
            f1, f2 = st.columns(2)
            with f1:
                st.markdown("##### 🪙 法币脱钩显影：黄金 (实物原子) vs 10Y TIPS")
                fig_fiat = go.Figure()
                fig_fiat.add_trace(go.Scatter(x=df.index[-250:], y=df['gold'].tail(250), name="黄金", line=dict(color='#FFD700', width=3)))
                fig_fiat.add_trace(go.Scatter(x=df.index[-250:], y=df['tips'].tail(250), name="10Y TIPS (右轴)", line=dict(color='#FF3131', width=2, dash='dash'), yaxis="y2"))
                fig_fiat.update_layout(height=340, template="plotly_dark", yaxis2=dict(overlaying="y", side="right", showgrid=False), legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=10, t=35, b=10))
                st.plotly_chart(fig_fiat, use_container_width=True)
            with f2:
                st.markdown("##### 🏗️ 实体经济温度计：铜金比 vs 200MA")
                fig_cg = go.Figure()
                fig_cg.add_trace(go.Scatter(x=df.index[-250:], y=df['cg_ratio'].tail(250), name="铜金比", line=dict(color='#00ffcc', width=3)))
                fig_cg.add_trace(go.Scatter(x=df.index[-250:], y=df['cg_ratio'].rolling(200).mean().tail(250), name="200MA", line=dict(color='orange', width=2, dash='dash')))
                fig_cg.update_layout(height=340, template="plotly_dark", yaxis=dict(tickformat=".4f"), legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=10, t=35, b=10))
                st.plotly_chart(fig_cg, use_container_width=True)

        elif "5." in chart_choice:
            st.markdown("##### 📊 双核历史轨迹：本土活水总分 (CN-Water) vs 全球 GSMI & 沪深300")
            df_v = df.tail(180).dropna(subset=['gsmi_score', 'cn_score', 'as300'])
            fig_v = go.Figure()
            fig_v.add_trace(go.Scatter(x=df_v.index, y=df_v['cn_score'], name="🌊 本土活水总分 (左轴)", line=dict(color='#00ffcc', width=3)))
            fig_v.add_trace(go.Scatter(x=df_v.index, y=df_v['gsmi_score'], name="🌎 全球美元 GSMI (左轴)", line=dict(color='#aaa', width=1.5, dash='dot')))
            fig_v.add_trace(go.Scatter(x=df_v.index, y=(df_v['as300']/df_v['as300'].iloc[0])*100, name="沪深300 (归一化, 右轴)", line=dict(color='#FFD700', width=2.5), yaxis="y2"))
            fig_v.update_layout(height=360, template="plotly_dark", yaxis=dict(title="评分 (0-100)"), yaxis2=dict(overlaying="y", side="right", showgrid=False, title="沪深300归一化"), legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=10, t=35, b=10))
            st.plotly_chart(fig_v, use_container_width=True)

    # ------------------------------------------------------------------------------
    # TAB 2: 行业 ETF 全息 X光 (v2.2 含3日冷却锁、假长上影过滤、避险动量衰减与深坑强反抽识别)
    # ------------------------------------------------------------------------------
    with tabs[1]:
        st.subheader("🔭 行业板块 ETF 全息 X光扫描 (v2.2 冷却记忆锁与动量衰减升级版)")
        st.markdown("""
        **v2.2 新增防骗机制**：① **3日出逃冷却锁**（近3日发生放量派发未反包者，禁止跳回『最佳甜点』）；② **真实上影线过滤**（上影线回落 `<1.2%` 的低波平盘震荡不再误报出逃）；③ 自动识别 **`⏳ 避险动量衰减 (前期老本)`** 与 **`🌱 深坑强反抽 (5日点火)`**。
        """)

        sector_map = {
            "🔋 储能电池 (集体提价+AIDC配储)": "159566.SZ",
            "🔬 半导体设备 (地缘刚需+国产扩产)": "159558.SZ",
            "⚡ 电网设备 (十五五5万亿+出海)": "159326.SZ",
            "🎬 影视主题 (贺岁春节档+AI漫剧)": "516620.SS",
            "🧪 细分化工 (制冷剂+锂电材料涨价)": "159870.SZ",
            "🛡️ 大金融/300非银 (券商+保险龙头)": "512070.SS",
            "⚔️ 纯证券公司 (高Beta交易收税机)": "512880.SS",
            "🏦 银行低费率 (息差企稳收租盾)": "516310.SS",
            "⚓ 红利低波 (城商行+实物垄断底座)": "512890.SS",
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
            run_xray = st.button("📡 启动全市场 18 大行业 ETF 全息扫描", use_container_width=True)

        if run_xray:
            with st.spinner("正在穿透 18 大核心行业 ETF 的流体力学、冷却记忆锁与量价底稿..."):
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
                            if s_data.empty or len(s_data) < 55:
                                sector_results.append({"行业板块": sector_name, "代码": ticker, "二维资金状态": "❌ 数据不足", "微观量价照妖镜": "-", "RS 20日(中期)": -999, "RS 5日(短期)": "-", "偏离10MA": "-", "偏离50MA": "-", "智能VR": "-"})
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
                            abs_ret_20d = (s_close.iloc[-1] / s_close.iloc[-20]) - 1

                            ma10 = s_close.rolling(10).mean().iloc[-1]
                            ma50_s = s_close.rolling(50).mean()
                            ma50 = ma50_s.iloc[-1]
                            ma50_up = ma50 >= ma50_s.iloc[-5]

                            bias_10 = (s_close.iloc[-1] / ma10) - 1
                            bias_50 = (s_close.iloc[-1] / ma50) - 1

                            is_cn_tkr = (".SS" in ticker or ".SZ" in ticker or ".SH" in ticker)
                            vr, is_extrap = calc_smart_vr(s_vol, is_cn_market=is_cn_tkr)
                            ret_1d = (s_close.iloc[-1] / s_close.iloc[-2]) - 1
                            candle_range = max(s_high.iloc[-1] - s_low.iloc[-1], 1e-6)
                            candle_pos = (s_close.iloc[-1] - s_low.iloc[-1]) / candle_range

                            today_dist = is_bar_distribution(s_close.iloc[-1], s_close.iloc[-2], s_high.iloc[-1], s_low.iloc[-1], vr)
                            in_cooldown = check_cooldown_lock(s_close, s_high, s_low, s_vol, is_cn_market=is_cn_tkr, lookback=3)

                            # 1. 二维 RS 状态判定 (加入『避险动量衰减』与『深坑强反抽』识别)
                            if rs_20d > 0.03:
                                if abs_ret_20d <= 0:
                                    state = "🛡️ 逆势抗跌 (相对跑赢/绝对未涨)"
                                elif rs_5d > 0.05:
                                    state = "🔥 强势吸筹 & 极速逼空"
                                elif rs_20d > 0.04 and rs_5d < 0.008 and (rs_5d < rs_20d * 0.15):
                                    state = "⏳ 避险动量衰减 (前期老本/防抽血)"
                                else:
                                    state = "🌊 稳健吸筹 (中期真涨)"
                            elif rs_20d < -0.03:
                                if rs_5d >= 0.04:
                                    state = "🌱 深坑强反抽 (5日点火/中期修复)"
                                elif rs_5d < -0.05:
                                    state = "🩸 持续失血 & 恐慌抛售"
                                else:
                                    state = "📉 阴跌失血 (机构撤离)"
                            else:
                                if rs_5d > 0.05:
                                    state = "🚀 暴力点火 (短期极速反转)"
                                elif rs_5d < -0.05:
                                    state = "⚠️ 短期砸盘 (动能向下突变)"
                                else:
                                    state = "⚖️ 随波逐流 (纯 Beta)"

                            # 2. 微观量价照妖镜 (含真实振幅过滤 + 3日出逃冷却记忆锁)
                            if bias_10 > 0.08 and vr > 1.5:
                                micro_mirror = "🚨 乖离过热巨量 (防机构赎回派发)"
                            elif today_dist:
                                micro_mirror = "🩸 放量长上影/阴线 (主力出逃)"
                            elif in_cooldown:
                                micro_mirror = "⚠️ 分歧冷却期 (近3日派发未反包)"
                            elif vr > 1.3 and ret_1d > 0.015 and candle_pos >= 0.6 and bias_10 <= 0.06:
                                micro_mirror = "💎 放量真阳起跑 (主动扫货)"
                            elif -0.022 <= bias_10 <= 0.025 and vr < 0.92 and (rs_20d > 0 or rs_5d > 0.01):
                                if bias_50 >= 0:
                                    micro_mirror = "🎯 水上回踩10MA (右侧最佳甜点)"
                                elif bias_50 >= -0.038:
                                    micro_mirror = "🚧 逼近50MA天花板 (防冲高回落)"
                                else:
                                    micro_mirror = "🌱 水下回踩10MA (反弹中继)"
                            elif -0.038 <= bias_50 < 0 and not ma50_up:
                                micro_mirror = "🚧 逼近50MA天花板 (上方套牢区)"
                            elif bias_50 < -0.05 and vr < 0.75:
                                micro_mirror = "❄️ 低位地量冰点 (抛压衰竭)"
                            else:
                                micro_mirror = "⚖️ 量价常态博弈"

                            vr_disp = f"{vr:.2f} (盘中折算)" if is_extrap else f"{vr:.2f}"

                            sector_results.append({
                                "行业板块": sector_name,
                                "代码": ticker,
                                "二维资金状态": state,
                                "微观量价照妖镜": micro_mirror,
                                "RS 20日(中期)": rs_20d,
                                "RS 5日(短期)": f"{rs_5d*100:+.2f}%",
                                "偏离10MA": f"{bias_10*100:+.2f}%",
                                "偏离50MA": f"{bias_50*100:+.2f}%",
                                "智能VR": vr_disp
                            })
                        except Exception as e:
                            sector_results.append({"行业板块": sector_name, "代码": ticker, "二维资金状态": f"❌ 断裂:{str(e)[:10]}", "微观量价照妖镜": "-", "RS 20日(中期)": -999, "RS 5日(短期)": "-", "偏离10MA": "-", "偏离50MA": "-", "智能VR": "-"})

                    if sector_results:
                        res_df = pd.DataFrame(sector_results)
                        res_df['sort_key'] = pd.to_numeric(res_df['RS 20日(中期)'], errors='coerce').fillna(-999)
                        res_df = res_df.sort_values(by="sort_key", ascending=False).drop(columns=['sort_key'])
                        res_df['RS 20日(中期)'] = res_df['RS 20日(中期)'].apply(lambda x: f"{x*100:+.2f}%" if isinstance(x, float) and x != -999 else "-")

                        def color_xray(val):
                            s = str(val)
                            if "稳健吸筹" in s or "强势吸筹" in s or "真阳" in s or "水上回踩" in s:
                                return 'color: #00ffcc; font-weight: bold;'
                            elif "点火" in s or "强反抽" in s or "水下回踩" in s:
                                return 'color: #FFD700; font-weight: bold;'
                            elif "过热" in s or "砸盘" in s or "天花板" in s or "冷却期" in s or "动量衰减" in s or "逆势抗跌" in s:
                                return 'color: #FF8C00; font-weight: bold;'
                            elif "失血" in s or "出逃" in s:
                                return 'color: #FF3131; font-weight: bold;'
                            return 'color: #ccc;'

                        st.dataframe(res_df.style.map(color_xray, subset=['二维资金状态', '微观量价照妖镜']), use_container_width=True, height=620)

    # ------------------------------------------------------------------------------
    # TAB 3: 猎杀雷达与五步买卖时机状态机 (新增🔔核心龙头扳机 + 3日冷却记忆锁)
    # ------------------------------------------------------------------------------
    with tabs[2]:
        st.subheader("🎯 猎杀雷达：五步买卖时机精确制导系统 (含🔔核心龙头扳机)")
        st.markdown("""
        自动测算 **均线拓扑(水上/水下)、3日出逃冷却记忆锁、10日乖离率与第一权重龙头实时表现**：
        `🎯 1.水上回踩10MA(最佳甜点)` | `🔥 2.起跑突破买点(放量真阳)` | `🌱 3.水下回踩10MA(反弹中继)` | `⚠️ 分歧冷却期(观察3日)` | `🚨 4.乖离过热止盈` | `🩸 5.放量派发/破位`
        """)

        ma_period = st.radio(
            "🔭 选择雷达主引力刻度 (MA焦距)", 
            options=[20, 50, 60, 120, 200, 250], 
            index=4, 
            horizontal=True
        )

        default_snipers = ['159566.SZ', '159558.SZ', '516620.SS', '159326.SZ', '159870.SZ', '516310.SS']
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
                if t_data.empty or len(t_data) < 55:
                    sniper_results.append({"标的": t, "当前价": "-", "10MA低吸价": "-", "偏离10MA": "-", "偏离50MA": "-", "智能VR": "-", "🔔 龙头扳机状态": "-", "五步买卖时机指令": "❌ 数据为空"})
                    continue

                t_close = t_data['Close'].ffill().dropna()
                t_vol = t_data['Volume'].ffill().fillna(0)
                t_high = t_data['High'].ffill().dropna()
                t_low = t_data['Low'].ffill().dropna()

                curr_p = t_close.iloc[-1]
                ma10_val = t_close.rolling(10).mean().iloc[-1]
                ma20_val = t_close.rolling(20).mean().iloc[-1]
                ma50_val = t_close.rolling(50).mean().iloc[-1]

                bias_10 = (curr_p / ma10_val - 1) * 100
                bias_20 = (curr_p / ma20_val - 1) * 100
                bias_50 = (curr_p / ma50_val - 1) * 100
                ret_1d = (curr_p / t_close.iloc[-2] - 1) * 100
                ret_3d = (curr_p / t_close.iloc[-4] - 1) * 100 if len(t_close) >= 4 else 0.0

                c_range = max(t_high.iloc[-1] - t_low.iloc[-1], 1e-6)
                c_pos = (curr_p - t_low.iloc[-1]) / c_range

                if t in manual_ma:
                    ma_main = manual_ma[t]
                    above_main = curr_p >= ma_main
                else:
                    ma_main = t_close.rolling(ma_period).mean().iloc[-1] if len(t_close) >= ma_period else ma20_val
                    above_main = curr_p >= ma_main

                is_cn_t = (".SS" in t or ".SZ" in t or ".SH" in t)
                vr, is_ext = calc_smart_vr(t_vol, is_cn_market=is_cn_t)
                vr_str = f"{vr:.2f}*" if is_ext else f"{vr:.2f}"

                today_dist_t = is_bar_distribution(curr_p, t_close.iloc[-2], t_high.iloc[-1], t_low.iloc[-1], vr)
                cooldown_t = check_cooldown_lock(t_close, t_high, t_low, t_vol, is_cn_market=is_cn_t, lookback=3)

                rs_df = pd.DataFrame({'target': t_close, 'base': df['as300']}).ffill().dropna()
                rs_curve = rs_df['target'] / rs_df['base']
                rs_10d = (rs_curve.iloc[-1] / rs_curve.iloc[-10] - 1) * 100 if len(rs_curve) >= 10 else 0.0
                rs_5d = (rs_curve.iloc[-1] / rs_curve.iloc[-5] - 1) * 100 if len(rs_curve) >= 5 else 0.0

                # 穿透抓取第一权重龙头股实时扳机表现
                leader_str = "个股/自选"
                if t in LEADER_MAP:
                    l_code, l_name = LEADER_MAP[t]
                    try:
                        l_df = get_etf_data_v2(l_code, datetime.now() - timedelta(days=40), datetime.now())
                        if not l_df.empty and len(l_df) >= 12:
                            l_c = l_df['Close'].ffill().dropna()
                            l_ret = (l_c.iloc[-1] / l_c.iloc[-2] - 1) * 100
                            l_ma10 = l_c.rolling(10).mean().iloc[-1]
                            if l_ret >= 0.5:
                                l_tag = "✅翻红领涨"
                            elif l_ret >= -0.5 and l_c.iloc[-1] >= l_ma10 * 0.99:
                                l_tag = "✅缩量企稳"
                            else:
                                l_tag = "⏳回调消化"
                            leader_str = f"{l_name} {l_ret:+.2f}% ({l_tag})"
                    except Exception:
                        leader_str = f"{l_name} (读取中)"

                # 升级后的五步买卖状态机 (含真长上影过滤 + 3日冷却锁 + 水上/水下分流)
                if bias_10 > 8.0 or ret_3d > 14.5:
                    timing_cmd = "🚨 4.乖离过热止盈 (禁追高/逢高呼气减半)"
                elif today_dist_t:
                    timing_cmd = "🩸 5.放量长上影/阴线 (主力派发警惕)"
                elif cooldown_t:
                    timing_cmd = "⚠️ 分歧冷却期 (近3日放量派发未反包)"
                elif (-2.2 <= bias_10 <= 2.5 or -1.8 <= bias_20 <= 2.0) and vr < 0.92 and (rs_10d > -2.5 or rs_5d > 0.5):
                    if bias_50 >= 0:
                        timing_cmd = "🎯 1.水上回踩10MA (右侧最佳甜点)"
                    elif bias_50 >= -3.8:
                        timing_cmd = "🚧 逼近50MA天花板 (谨防受阻回落)"
                    else:
                        timing_cmd = "🌱 3.水下回踩10MA (反弹蓄势中继)"
                elif -3.5 <= bias_50 < 0 and curr_p >= ma10_val:
                    timing_cmd = "🚧 逼近50MA天花板 (等待放量突破50MA)"
                elif above_main and bias_10 <= 7.5 and vr >= 1.30 and c_pos >= 0.55 and (rs_5d > 0 or rs_10d > 0):
                    timing_cmd = "🔥 2.起跑突破买点 (放量真阳右侧主攻)"
                elif above_main and rs_10d > 0:
                    timing_cmd = "🌊 多头趋势持仓 (等回踩10MA加仓)"
                elif not above_main and (rs_5d > 1.5 or (vr < 0.72 and bias_20 > -4.0)):
                    timing_cmd = f"🌱 3.左侧极寒筑底 (RS苏醒/等10MA贴合)"
                else:
                    timing_cmd = "❄️ 5.重力压制/破位 (观望蛰伏)"

                sniper_results.append({
                    "标的": t,
                    "当前价": f"{curr_p:.3f}",
                    "10MA低吸价": f"{ma10_val:.3f}",
                    "偏离10MA": f"{bias_10:+.2f}%",
                    "偏离50MA": f"{bias_50:+.2f}%",
                    "智能VR": vr_str,
                    "🔔 龙头扳机状态": leader_str,
                    "五步买卖时机指令": timing_cmd
                })
            except Exception as e:
                sniper_results.append({"标的": t, "当前价": "-", "10MA低吸价": "-", "偏离10MA": "-", "偏离50MA": "-", "智能VR": "-", "🔔 龙头扳机状态": "-", "五步买卖时机指令": f"❌ 异常:{str(e)[:12]}"})

        if sniper_results:
            st.table(pd.DataFrame(sniper_results))
            st.caption("注：智能VR带 `*` 号代表盘中自动按开盘分钟折算；当『五步买卖时机指令』处于回踩10MA且『🔔 龙头扳机状态』显示 ✅企稳/翻红时，为最佳开火瞬间。")

        st.write("---")
        st.markdown("#### 🔍 单项标的价格均线 (10/20/50/200MA) 与 RS 相对强度双轨透视")
        audit_ticker = st.text_input("输入要深度透视买点的 ETF 或个股代码", "159566.SZ", key="single_v2")
        if audit_ticker:
            clean_tkr = audit_ticker.strip()
            a_data = get_etf_data_v2(clean_tkr, datetime.now() - timedelta(days=400), datetime.now())
            if not a_data.empty and len(a_data) > 30:
                a_close = a_data['Close'].ffill().dropna()
                a_vol = a_data['Volume'].ffill().fillna(0)
                is_cn_s = (".SS" in clean_tkr or ".SZ" in clean_tkr or ".SH" in clean_tkr)
                vr_s, is_ext_s = calc_smart_vr(a_vol, is_cn_market=is_cn_s)
                vr_tag = f"{vr_s:.2f} (盘中折算)" if is_ext_s else f"{vr_s:.2f}"

                rs_df_s = pd.DataFrame({'target': a_close, 'base': df['as300']}).ffill().dropna()
                rs_ratio = rs_df_s['target'] / rs_df_s['base']

                p_col1, p_col2 = st.columns(2)
                with p_col1:
                    st.markdown(f"##### 📈 {clean_tkr} 价格与 10/20/50/200MA 买点透视图 (智能VR: {vr_tag})")
                    fig_p = go.Figure()
                    fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.tail(180), name="收盘价", line=dict(color='#00ffcc', width=2.5)))
                    fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.rolling(10).mean().tail(180), name="10MA (回踩线)", line=dict(color='#FFD700', width=1.5, dash='dot')))
                    fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.rolling(20).mean().tail(180), name="20MA (生命线)", line=dict(color='white', width=1.5, dash='dash')))
                    if len(a_close) >= 50:
                        fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.rolling(50).mean().tail(180), name="50MA (中期颈线)", line=dict(color='#ff9f43', width=2)))
                    if len(a_close) >= 200:
                        fig_p.add_trace(go.Scatter(x=a_close.index[-180:], y=a_close.rolling(200).mean().tail(180), name="200MA (牛熊线)", line=dict(color='#FF3131', width=2)))
                    fig_p.update_layout(height=360, template="plotly_dark", legend=dict(orientation="h", y=1.12, x=0), margin=dict(l=10, r=10, t=45, b=10))
                    st.plotly_chart(fig_p, use_container_width=True)
                with p_col2:
                    st.markdown(f"##### 🚀 {clean_tkr} 相对沪深300 Alpha 强度 (RS 曲线)")
                    fig_rs = go.Figure()
                    fig_rs.add_trace(go.Scatter(x=rs_ratio.index[-180:], y=rs_ratio.tail(180), name="相对沪深300 RS曲线", line=dict(color='#00ffcc', width=2.5)))
                    fig_rs.add_trace(go.Scatter(x=rs_ratio.index[-180:], y=rs_ratio.rolling(20).mean().tail(180), name="RS 20MA", line=dict(color='white', dash='dot')))
                    if len(rs_ratio) >= 200:
                        fig_rs.add_trace(go.Scatter(x=rs_ratio.index[-180:], y=rs_ratio.rolling(200).mean().tail(180), name="RS 200MA", line=dict(color='orange', width=2, dash='dash')))
                    fig_rs.update_layout(height=360, template="plotly_dark", legend=dict(orientation="h", y=1.12, x=0), margin=dict(l=10, r=10, t=45, b=10))
                    st.plotly_chart(fig_rs, use_container_width=True)

    # ------------------------------------------------------------------------------
    # TAB 4: 通用型 AB 双雄对决罗盘与三三制正交沙盘
    # ------------------------------------------------------------------------------
    with tabs[3]:
        st.subheader("⚖️ 通用型【AB 双雄强弱对决罗盘】与【组合正交实验室】")
        st.markdown("100% 参数化设计：随时对比任意两个板块的**资金抽血方向（A/B 比值曲线）**，并检验持仓是否发生同向踩踏。")

        preset_mode = st.radio(
            "⚡ 快捷对决模式切换 (或选第5项自由输入任意代码)：",
            [
                "1. 算电基建内战：储能电池 (159566.SZ) vs 电网设备 (159326.SZ)",
                "2. 大盘风格总开关：创业板指 (159915.SZ) vs 银行低费率 (516310.SS)",
                "3. 科技内外循环：半导体设备 (159558.SZ) vs 通信光模块 (515880.SS)",
                "4. 跨界轮动对比：储能电池 (159566.SZ) vs 影视主题 (516620.SS)",
                "5. 自由定制对决：手动输入任意两个 ETF/股票代码"
            ],
            horizontal=False
        )

        preset_dict = {
            "1.": ("159566.SZ", "159326.SZ", "储能电池(高弹矛)", "电网设备(稳健盾)"),
            "2.": ("159915.SZ", "516310.SS", "创业板(成长重启)", "银行ETF(防御抱团)"),
            "3.": ("159558.SZ", "515880.SS", "半导体设备(内循环)", "通信光模块(外循环)"),
            "4.": ("159566.SZ", "516620.SS", "储能电池(算电制造)", "影视ETF(AI漫剧/贺岁)")
        }

        key_prefix = preset_mode[:2]
        if key_prefix in preset_dict:
            def_a, def_b, name_a, name_b = preset_dict[key_prefix]
        else:
            def_a, def_b, name_a, name_b = "159566.SZ", "159796.SZ", "标的 A", "标的 B"

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
                        verdict = f"🛡️ 【{ticker_b}】全面胜出！A/B比值跌破20日均线({r_5d:+.2f}%)，市场处于切换态势，配置重心应果断向【{ticker_b}】倾斜！"
                        v_col = "#FFD700"
                    else:
                        verdict = f"⚖️ 双雄处于风格切换临界期（20日相对动量 {r_20d:+.2f}%，5日相对动量 {r_5d:+.2f}%），建议采取均衡分批策略。"
                        v_col = "#ccc"

                    st.markdown(f"<div class='quadrant-box' style='border-color:{v_col};'><h4 style='color:{v_col};margin:0;'>{verdict}</h4></div>", unsafe_allow_html=True)
                    st.markdown(f"##### 📈 双雄强弱比值曲线 (向上代表 {ticker_a} 跑赢 {ticker_b})")
                    fig_ab = go.Figure()
                    fig_ab.add_trace(go.Scatter(x=ratio_ab.index[-180:], y=ratio_ab.tail(180), name=f"{ticker_a} / {ticker_b} 强弱比值", line=dict(color='#00ffcc', width=3)))
                    fig_ab.add_trace(go.Scatter(x=ratio_ma20.index[-180:], y=ratio_ma20.tail(180), name="比值 20MA (风格分水岭)", line=dict(color='orange', width=2, dash='dash')))
                    fig_ab.update_layout(height=330, template="plotly_dark", legend=dict(orientation="h", y=1.12, x=0), margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_ab, use_container_width=True)

        st.write("---")
        st.subheader("🕸️ 跨周期正交沙盘：进攻舱 vs 轮动/防守舱相关性防坍塌检验")
        col_p1, col_p2 = st.columns(2)
        off_input = col_p1.text_input("⚔️ 第一舱（核心科技与制造舱，逗号分隔）", value="159566.SZ, 159558.SZ")
        def_input = col_p2.text_input("🛡️ 第二舱（低相关轮动与防守舱，逗号分隔）", value="516620.SS, 516310.SS, 518880.SS")

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

                cross_corrs = []
                for o_t in off_list:
                    for d_t in def_list:
                        if o_t in corr_m.columns and d_t in corr_m.columns and o_t != d_t:
                            cross_corrs.append(corr_m.loc[o_t, d_t])
                avg_cross = np.mean(cross_corrs) if cross_corrs else 0.0

                if avg_cross > 0.70:
                    st.error(f"🚨 跨舱维度坍塌警告：你的【第一舱】与【第二舱】平均相关系数高达 {avg_cross:.2f} (>0.70)！属于同一条产业链重复下注。")
                else:
                    st.success(f"✅ 跨舱正交化极佳：【第一舱】与【第二舱】平均相关系数仅为 {avg_cross:.2f}，形成了真正独立的风险分散保护。")

                st.markdown("##### 🕸️ 持仓组合近 60 日底层相关系数热力图")
                fig_corr = go.Figure(data=go.Heatmap(
                    z=corr_m.values, x=corr_m.columns, y=corr_m.columns,
                    colorscale='RdBu', zmin=-1, zmax=1,
                    text=np.round(corr_m.values, 2), texttemplate="%{text}"
                ))
                fig_corr.update_layout(height=360, template="plotly_dark", margin=dict(l=10, r=10, t=20, b=10))
                st.plotly_chart(fig_corr, use_container_width=True)

    # ------------------------------------------------------------------------------
    # TAB 5: 三三制 ATR 仓位分配与挂单生成器
    # ------------------------------------------------------------------------------
    with tabs[4]:
        st.subheader("🛡️ 三三制 ATR 仓位几何与自动挂单表")
        st.markdown("输入你的账户总资金与舱位比例，系统自动读取你在 Tab 4 设定的【第一舱】与【第二舱】标的，批量生成 **10MA 低吸挂单价、20MA 生命线、50MA 颈线与 2倍 ATR 硬止损线**。")

        p1, p2, p3, p4 = st.columns(4)
        total_cap = p1.number_input("💰 账户总本金 (¥)", value=1000000, step=100000)
        off_pct = p2.number_input("⚔️ 第一舱总配额 (%)", value=33.0, step=1.0, max_value=60.0)
        def_pct = p3.number_input("🛡️ 第二舱总配额 (%)", value=25.0, step=1.0, max_value=60.0)
        atr_mult = p4.number_input("📏 止损宽度 (倍数 ATR)", value=2.0, step=0.1)

        cash_pct = max(0.0, 100.0 - off_pct - def_pct)
        st.info(f"📊 **当前资金划分**：⚔️ 第一舱配额 **¥{total_cap*off_pct/100:,.0f} ({off_pct:.0f}%)** &nbsp;|&nbsp; 🛡️ 第二舱配额 **¥{total_cap*def_pct/100:,.0f} ({def_pct:.0f}%)** &nbsp;|&nbsp; 💧 现金冗余 **¥{total_cap*cash_pct/100:,.0f} ({cash_pct:.0f}%)**")

        batch_rows = []
        for bucket_name, t_list, bucket_pct in [("⚔️ 第一舱", off_list, off_pct), ("🛡️ 第二舱", def_list, def_pct)]:
            if not t_list:
                continue
            per_slot_cap = (total_cap * (bucket_pct / 100.0)) / len(t_list)
            for tkr in t_list:
                p_df = get_etf_data_v2(tkr, datetime.now() - timedelta(days=120), datetime.now())
                if p_df.empty or len(p_df) < 50:
                    continue
                p_df = p_df.ffill().dropna()
                c_col, h_col, l_col = p_df['Close'], p_df['High'], p_df['Low']
                prev_c = c_col.shift(1)
                tr = pd.concat([h_col - l_col, (h_col - prev_c).abs(), (l_col - prev_c).abs()], axis=1).max(axis=1)
                atr_val = tr.rolling(14).mean().iloc[-1]
                curr_p = c_col.iloc[-1]
                ma10_p = c_col.rolling(10).mean().iloc[-1]
                ma20_p = c_col.rolling(20).mean().iloc[-1]
                ma50_p = c_col.rolling(50).mean().iloc[-1]

                stop_dist = atr_val * atr_mult
                stop_p = curr_p - stop_dist

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
                    "🎯 10MA低吸价": f"{ma10_p:.3f}",
                    "📏 20MA生命线": f"{ma20_p:.3f}",
                    "🚧 50MA颈线": f"{ma50_p:.3f}",
                    "14日ATR波动": f"{atr_val:.3f}",
                    "🚨 硬编码止损价": f"{stop_p:.3f}",
                    "建议股数(整百)": f"{shares:,} 股",
                    "分配资金 (¥)": f"¥{actual_cost:,.0f}",
                    "触损占总本金风险": f"-{risk_pct_total:.2f}%"
                })

        if batch_rows:
            st.table(pd.DataFrame(batch_rows))
            st.caption("💡 实战执行纪律：处于『🚨 乖离过热』、『⚠️ 分歧冷却期』或『🚧 逼近50MA天花板』的标的，严禁按现价盲目追高，耐心挂单在【🎯 10MA低吸价】或【📏 20MA生命线】附近分批吸气。")

except Exception as e:
    st.error(f"系统运行中发生错误: {e}")

st.markdown("---")
st.caption("🏹 GSMI Dual-Core v2.2 | 全球宏观与本土行业 | 三三制正交风控体系")
```!]
