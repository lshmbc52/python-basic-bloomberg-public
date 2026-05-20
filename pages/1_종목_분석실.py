import streamlit as st

from utils.analysis import build_analysis_summary, build_order_gap_summary, compute_indicators, normalize_ohlcv
from utils.formatting import format_percent, format_won
from utils.kis_client import get_current_price, get_price_history, submit_paper_order
from utils.navigation import render_sidebar_navigation
from utils.storage import init_session_state
from utils.ai_assistant import ask_analysis_copilot, get_ai_runtime_status, summarize_news_briefing
from utils.news_client import fetch_company_news

st.set_page_config(
    page_title="Bloomberg | 종목 분석실",
    layout="wide",
    initial_sidebar_state="expanded",
)
init_session_state()
render_sidebar_navigation(active_page="analysis")

st.title("종목 분석실")

with st.sidebar:
    st.subheader("주문 패널")

    account_mode = st.selectbox(
        "계좌 모드",
        options=["demo", "real"],
        key="order_ticket_account_mode",
        format_func=lambda value: "모의투자" if value == "demo" else "실전투자",
    )
    order_symbol = st.text_input("종목코드", key="order_ticket_symbol")
    side = st.selectbox(
        "주문 구분",
        options=["buy", "sell"],
        key="order_ticket_side",
        format_func=lambda value: "매수" if value == "buy" else "매도",
    )
    order_type = st.selectbox(
        "주문 유형",
        options=["market", "limit"],
        key="order_ticket_type",
        format_func=lambda value: "시장가" if value == "market" else "지정가",
    )
    qty = st.number_input("수량", min_value=1, step=1, key="order_ticket_qty")

    if order_type == "market":
        refresh_columns = st.columns([3, 1], vertical_alignment="bottom")
        with refresh_columns[1]:
            refresh_clicked = st.button(
                "↻",
                key="refresh_market_price",
                help="현재 시장가 새로고침",
                width="stretch",
            )
            if refresh_clicked:
                target = (order_symbol or st.session_state.get("selected_symbol", "")).strip()
                if target:
                    with st.spinner("현재가 조회 중..."):
                        try:
                            fresh = get_current_price(target)
                            st.session_state.order_ticket_market_price = fresh["current_price"]
                            st.session_state.order_ticket_market_as_of = fresh["as_of"]
                            st.rerun()
                        except Exception as exc:
                            st.error(f"조회 실패: {exc}")

        with refresh_columns[0]:
            st.text_input(
                "가격",
                value=format_won(st.session_state.order_ticket_market_price),
                disabled=True,
            )

        st.caption(f"기준 시각: {st.session_state.order_ticket_market_as_of}")
        price = st.session_state.order_ticket_market_price
    else:
        market_price = st.session_state.order_ticket_market_price
        st.caption(f"현재가 기준 빠른 선택: {format_won(market_price)}")

        adjust_columns = st.columns(4)
        if adjust_columns[0].button("-1,000원", width="stretch"):
            st.session_state.order_ticket_price = max(0, st.session_state.order_ticket_price - 1000)
            st.rerun()
        if adjust_columns[1].button("-500원", width="stretch"):
            st.session_state.order_ticket_price = max(0, st.session_state.order_ticket_price - 500)
            st.rerun()
        if adjust_columns[2].button("+500원", width="stretch"):
            st.session_state.order_ticket_price = st.session_state.order_ticket_price + 500
            st.rerun()
        if adjust_columns[3].button("+1,000원", width="stretch"):
            st.session_state.order_ticket_price = st.session_state.order_ticket_price + 1000
            st.rerun()

        # 지정가 초기값을 현재가로 맞춰두기 (최초 진입 또는 종목 변경 시)
        _limit_init_key = f"_limit_price_init_{order_symbol or st.session_state.get('selected_symbol', '')}"
        if _limit_init_key not in st.session_state:
            st.session_state.order_ticket_price = market_price
            st.session_state[_limit_init_key] = True

        price = st.number_input(
            "가격",
            min_value=0,
            step=500,
            key="order_ticket_price",
        )

    preview_clicked = st.button("주문 미리보기", width="stretch")
    if preview_clicked:
        current_price = st.session_state.order_ticket_market_price
        gap = build_order_gap_summary(
            current_price=float(current_price),
            side=side,
            order_type=order_type,
            qty=int(qty),
            price=float(price),
        )
        st.session_state["order_preview"] = {
            "gap": gap,
            "symbol": (order_symbol or st.session_state.get("selected_symbol", "")).strip(),
            "account_mode": account_mode,
        }
        st.session_state.pop("order_result", None)

    preview = st.session_state.get("order_preview")
    if preview:
        gap = preview["gap"]
        expected_amount = gap["qty"] * gap["price"]
        side_label = "매수" if gap["side"] == "buy" else "매도"
        type_label = "시장가" if gap["order_type"] == "market" else "지정가"
        st.markdown(f"**{side_label} · {type_label} 미리보기**")
        st.markdown(f"- 예상 주문금액: **{format_won(expected_amount)}**")
        st.markdown(f"- 주문가격: {format_won(gap['price'])}")
        st.markdown(f"- 현재가 대비: {gap['gap_price']:+,.0f}원 ({gap['gap_pct_vs_current']:+.2f}%)")
        if gap["warning"] == "정상 범위":
            st.success(f"✔ {gap['warning']}")
        elif gap["warning"] == "주문 가격을 다시 확인하세요":
            st.warning(f"⚠ {gap['warning']}")
        else:
            st.error(f"✖ {gap['warning']}")

        if account_mode == "real":
            st.error("실전 주문은 지원되지 않습니다. 모의투자 모드로 변경하세요.")
        else:
            if st.button("✅ 주문 제출", width="stretch", type="primary"):
                with st.spinner("주문을 전송하는 중..."):
                    try:
                        result = submit_paper_order({
                            "symbol": preview["symbol"],
                            "side": gap["side"],
                            "order_type": gap["order_type"],
                            "qty": gap["qty"],
                            "price": gap["price"],
                        })
                        st.session_state["order_result"] = result
                        st.session_state.pop("order_preview", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(f"주문 실패: {exc}")

    order_result = st.session_state.get("order_result")
    if order_result:
        if order_result.get("ok"):
            st.success(f"주문 접수 완료\n{order_result.get('message', '')}")
            if order_result.get("order_no"):
                st.caption(f"주문번호: {order_result['order_no']}")
        else:
            st.error(order_result.get("message", "주문 실패"))

st.write(
    "이 페이지는 10-3장부터 10-8장까지 가장 많이 다루게 될 핵심 화면입니다. "
    "지금은 자리만 잡아 두고, 이후 입력 폼, 차트와 지표, AI Copilot, 뉴스 브리핑, 주문 패널, 주문 결과 확인 구현."
)

left_col, right_col = st.columns([2, 1])

with left_col:
    st.subheader("분석 입력")
    if "analysis_symbol" not in st.session_state:
        st.session_state["analysis_symbol"] = st.session_state.selected_symbol
    form_symbol = st.text_input(
        "종목코드",
        key="analysis_symbol",
        placeholder="예: 005930",
    )
    if "analysis_start" not in st.session_state:
        st.session_state["analysis_start"] = st.session_state.start_date
    if "analysis_end" not in st.session_state:
        st.session_state["analysis_end"] = st.session_state.end_date
    date_col1, date_col2 = st.columns(2)
    with date_col1:
        form_start = st.date_input("시작일", key="analysis_start")
    with date_col2:
        form_end = st.date_input("종료일", key="analysis_end")
    submitted = st.button("저장", use_container_width=True)

    if submitted:
        if not form_symbol.strip():
            st.error("종목코드를 입력해 주세요.")
        elif form_start > form_end:
            st.error("시작일이 종료일보다 늦을 수 없습니다.")
        else:
            st.session_state.selected_symbol = form_symbol.strip()
            st.session_state.start_date = form_start
            st.session_state.end_date = form_end
            with st.spinner("데이터를 불러오는 중..."):
                try:
                    quote = get_current_price(st.session_state.selected_symbol)
                    records = get_price_history(
                        st.session_state.selected_symbol,
                        st.session_state.start_date,
                        st.session_state.end_date,
                    )
                    price_df = normalize_ohlcv(records)
                    price_df = compute_indicators(price_df)
                    summary = build_analysis_summary(
                        st.session_state.selected_symbol,
                        quote["company_name"],
                        price_df,
                        quote,
                    )
                    st.session_state.price_df = price_df
                    st.session_state.analysis_summary = summary
                    st.session_state["current_quote"] = quote
                    st.session_state.order_ticket_market_price = quote["current_price"]
                    st.session_state.order_ticket_market_as_of = quote["as_of"]
                    st.success(
                        f"{quote['company_name']} ({st.session_state.selected_symbol}) | "
                        f"{st.session_state.start_date} ~ {st.session_state.end_date} 저장 완료"
                    )
                except Exception as exc:
                    st.error(f"데이터 조회 실패: {exc}")

    st.subheader("차트와 지표")

    price_df = st.session_state.price_df
    summary = st.session_state.analysis_summary
    quote = st.session_state.get("current_quote")

    if price_df is None or (hasattr(price_df, "empty") and price_df.empty):
        st.info("종목코드와 기간을 입력한 뒤 저장하면 차트와 지표가 표시됩니다.")
    else:
        # 현재가
        if quote:
            if quote.get("source") == "sample-placeholder":
                st.warning(
                    "⚠️ 샘플 데이터입니다. .env의 KIS API 키를 확인하고 저장 버튼을 다시 누르세요.",
                    icon=None,
                )
            change_pct = quote.get("change_pct", 0.0)
            symbol_label = st.session_state.selected_symbol
            q_col1, q_col2, q_col3 = st.columns(3)
            q_col1.metric(
                label=f"현재가 · {symbol_label}",
                value=format_won(quote["current_price"]),
                delta=f"{change_pct:+.2f}%",
            )
            q_col2.metric(label="기준 시각", value=quote.get("as_of", "-"))
            q_col3.metric(label="데이터 소스", value=quote.get("source", "-"))

        st.divider()

        # 기간 지표 카드
        if summary:
            i_col1, i_col2, i_col3, i_col4 = st.columns(4)
            i_col1.metric(label="MA5", value=format_won(summary["trend"]["ma5"]))
            i_col2.metric(label="MA20", value=format_won(summary["trend"]["ma20"]))
            i_col3.metric(
                label="기간 수익률",
                value=format_percent(summary["period_return_pct"]),
            )
            i_col4.metric(
                label="변동성 (20일)",
                value=format_percent(summary["risk"]["volatility_pct"]),
            )

        st.divider()

        # 종가 차트 (datetime → 문자열로 변환해 x축 렌더링 안정화)
        chart_df = price_df.copy()
        chart_df["date"] = chart_df["date"].dt.strftime("%Y-%m-%d")
        chart_df = chart_df.set_index("date")[["close", "ma5", "ma20"]].rename(
            columns={"close": "종가", "ma5": "MA5", "ma20": "MA20"}
        )
        st.line_chart(chart_df)

        # 기간 시세 표 (최근 10거래일)
        with st.expander("기간 시세 (최근 10거래일)"):
            recent = price_df.tail(10).copy()
            recent["date"] = recent["date"].dt.strftime("%Y-%m-%d")
            recent = (
                recent[["date", "open", "high", "low", "close", "volume"]]
                .rename(
                    columns={
                        "date": "날짜",
                        "open": "시가",
                        "high": "고가",
                        "low": "저가",
                        "close": "종가",
                        "volume": "거래량",
                    }
                )
                .reset_index(drop=True)
            )
            st.dataframe(recent, use_container_width=True)

with right_col:
    st.subheader("ClaudeCode")

    runtime = get_ai_runtime_status()
    st.caption(f"AI: {runtime.get('provider')} · model={runtime.get('model')}")

    data_ready = price_df is not None and not (hasattr(price_df, "empty") and price_df.empty)

    if not data_ready:
        st.info("종목코드와 기간을 저장하면 AI에게 질문할 수 있습니다.")
    else:
        ai_question = st.text_area(
            "질문 입력",
            placeholder="예: 최근 추세와 주요 지표를 요약해줘",
            key="ai_copilot_question",
            height=100,
        )

        if st.button("질문하기", key="ai_copilot_ask", use_container_width=True):
            if not ai_question.strip():
                st.warning("질문을 입력해 주세요.")
            else:
                ctx = {
                    "symbol": st.session_state.get("selected_symbol"),
                    "company_name": quote.get("company_name") if quote else None,
                    "summary": summary,
                    "quote": quote,
                }
                recent = price_df.tail(5)[
                    ["date", "open", "high", "low", "close", "volume", "ma5", "ma20"]
                ].copy()
                recent["date"] = recent["date"].dt.strftime("%Y-%m-%d")
                try:
                    ctx["recent_prices"] = recent.to_dict(orient="records")
                except Exception:
                    ctx["recent_prices"] = []

                with st.spinner("AI가 답변을 생성하는 중..."):
                    ai_response = ask_analysis_copilot(ctx, ai_question)
                st.session_state["ai_last_response"] = ai_response

        if st.session_state.get("ai_last_response"):
            resp = st.session_state["ai_last_response"]
            st.divider()
            st.markdown("**AI 답변**")
            st.write(resp.get("answer"))
            if resp.get("error"):
                with st.expander("오류 상세"):
                    st.json({"source": resp.get("source"), "error": resp.get("error")})

    st.subheader("뉴스 브리핑")
    if not data_ready:
        st.info("종목코드와 기간을 저장하면 뉴스를 조회할 수 있습니다.")
    else:
        current_symbol = st.session_state.get("selected_symbol", "")
        company_name = quote.get("company_name", "") if quote else current_symbol

        if st.session_state.get("_news_symbol") != current_symbol:
            with st.spinner("뉴스를 불러오는 중..."):
                articles = fetch_company_news(
                    company_name=company_name,
                    symbol=current_symbol,
                    max_items=10,
                )
                briefing = summarize_news_briefing(company_name, articles)
            st.session_state["news_items"] = articles
            st.session_state["news_briefing"] = briefing
            st.session_state["_news_symbol"] = current_symbol

        articles = st.session_state.get("news_items", [])
        briefing = st.session_state.get("news_briefing") or {}

        if briefing.get("summary"):
            st.markdown("**AI 브리핑**")
            st.write(briefing["summary"])
            st.divider()

        if articles:
            for article in articles:
                st.markdown(f"**[{article['title']}]({article['link']})**")
                st.caption(f"{article['source_name']} · {article['published_at']}")
                st.write("")
        else:
            display_name = company_name if "종목" not in company_name else current_symbol
            st.info(f"현재 '{display_name}' 관련 뉴스가 없습니다.")

        if st.button("뉴스 새로고침", key="news_refresh"):
            st.session_state.pop("_news_symbol", None)
            st.rerun()
