import streamlit as st

from utils.formatting import format_percent, format_won
from utils.kis_client import get_balance_snapshot, get_open_orders, get_order_history
from utils.navigation import render_sidebar_navigation
from utils.storage import init_session_state

st.set_page_config(
    page_title="Bloomberg | 주문/보유현황",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_session_state()
render_sidebar_navigation(active_page="operations")

st.title("주문/보유현황")

# ── 새로고침 버튼 ──────────────────────────────────────────
if st.button("↻ 새로고침", key="ops_refresh"):
    for key in ("ops_balance", "ops_orders", "ops_open_orders"):
        st.session_state.pop(key, None)
    st.rerun()

# ── 데이터 로드 (세션 캐시 활용) ──────────────────────────
if "ops_balance" not in st.session_state:
    with st.spinner("잔고를 조회하는 중..."):
        try:
            st.session_state["ops_balance"] = get_balance_snapshot()
        except Exception as exc:
            st.session_state["ops_balance"] = {"error": str(exc)}

if "ops_orders" not in st.session_state:
    with st.spinner("주문내역을 조회하는 중..."):
        try:
            st.session_state["ops_orders"] = get_order_history(ccld_dvsn="00")
        except Exception as exc:
            st.session_state["ops_orders"] = []
            st.warning(f"주문내역 조회 실패: {exc}")

if "ops_open_orders" not in st.session_state:
    with st.spinner("미체결 주문을 조회하는 중..."):
        try:
            st.session_state["ops_open_orders"] = get_open_orders()
        except Exception as exc:
            st.session_state["ops_open_orders"] = []

balance = st.session_state.get("ops_balance", {})
orders = st.session_state.get("ops_orders", [])
open_orders = st.session_state.get("ops_open_orders", [])

# ── 에러 표시 ──────────────────────────────────────────────
if balance.get("error"):
    st.error(f"잔고 조회 실패: {balance['error']}")
    st.stop()

summary = balance.get("summary", {})
holdings = balance.get("holdings", [])
source = balance.get("source", "-")

if source == "sample-placeholder":
    st.warning("⚠️ 샘플 데이터입니다. .env의 KIS API 키를 확인하세요.")

# ── 잔고 요약 메트릭 ───────────────────────────────────────
st.subheader("잔고 요약")
m1, m2, m3, m4 = st.columns(4)
m1.metric("예수금", format_won(summary.get("cash", 0)))
m2.metric("주식 평가금액", format_won(summary.get("evaluation_amount", 0)))
m3.metric("총 평가금액", format_won(summary.get("total_amount", summary.get("cash", 0))))
profit_loss = summary.get("profit_loss", 0)
m4.metric(
    "평가손익",
    format_won(abs(profit_loss)),
    delta=f"{'+' if profit_loss >= 0 else '-'}{format_won(abs(profit_loss))}",
    delta_color="normal" if profit_loss >= 0 else "inverse",
)

st.divider()

# ── 2열 레이아웃 ───────────────────────────────────────────
left_col, right_col = st.columns([1, 1])

# ── 좌: 주문내역 ───────────────────────────────────────────
with left_col:
    st.subheader("주문 내역 (당일)")

    tab_all, tab_open = st.tabs(["전체", f"미체결 ({len(open_orders)})"])

    with tab_all:
        if not orders:
            st.info("당일 주문내역이 없습니다.")
        else:
            import pandas as pd

            _STATUS_MAP = {
                "01": "접수", "02": "확인", "03": "취소",
                "04": "완료", "91": "거부", "": "-",
            }
            rows = []
            for o in orders:
                rows.append({
                    "종목명": o["company_name"] or o["symbol"],
                    "구분": o["side"],
                    "유형": o["order_type"],
                    "주문수량": o["qty"],
                    "주문가": format_won(o["price"]) if o["price"] else "시장가",
                    "체결수량": o["filled_qty"],
                    "체결가": format_won(o["avg_fill_price"]) if o["avg_fill_price"] else "-",
                    "상태": _STATUS_MAP.get(o["status"], o["status"]),
                    "시각": o["time"][:6] if len(o["time"]) >= 6 else o["time"],
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_open:
        if not open_orders:
            st.info("미체결 주문이 없습니다.")
        else:
            import pandas as pd

            rows = []
            for o in open_orders:
                rows.append({
                    "주문번호": o["order_no"],
                    "종목명": o["company_name"] or o["symbol"],
                    "구분": o["side"],
                    "잔량": o["qty"] - o["filled_qty"],
                    "주문가": format_won(o["price"]) if o["price"] else "시장가",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption("미체결 주문 취소는 증권사 앱/HTS에서 직접 진행하세요.")

# ── 우: 보유 현황 ──────────────────────────────────────────
with right_col:
    st.subheader("보유 종목")

    if not holdings:
        st.info("보유 종목이 없습니다.")
    else:
        import pandas as pd

        rows = []
        for h in holdings:
            pl = h["profit_loss"]
            pl_pct = h["profit_loss_pct"]
            rows.append({
                "종목명": h["company_name"] or h["symbol"],
                "보유수량": h["qty"],
                "평균단가": format_won(h["avg_price"]),
                "현재가": format_won(h["current_price"]),
                "평가금액": format_won(h["evaluation_amount"]),
                "평가손익": f"{'+' if pl >= 0 else ''}{format_won(pl)}",
                "수익률": f"{'+' if pl_pct >= 0 else ''}{pl_pct:.2f}%",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.caption(f"데이터 소스: {source}")
