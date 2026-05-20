import hashlib
import json
import math
import os
import random
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - 초기 설치 전 보호
    requests = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 초기 설치 전 보호
    def load_dotenv(*args, **kwargs) -> None:
        return None


# CWD와 무관하게 이 파일 기준으로 프로젝트 루트 .env를 찾는다
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH, override=True)

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
TOKEN_ENDPOINT = "/oauth2/tokenP"
PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
PRICE_TR_ID = "FHKST01010100"
DAILY_CHART_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
DAILY_CHART_TR_ID = "FHKST03010100"
ORDER_ENDPOINT = "/uapi/domestic-stock/v1/trading/order-cash"
BALANCE_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-balance"
ORDER_HISTORY_ENDPOINT = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
KIS_MARKET_CODE = "J"

# 모의투자 TR ID (실전: TTTC0802U/TTTC0801U — 이번 구현에서는 demo 전용)
_TR_ORDER_BUY = "VTTC0802U"
_TR_ORDER_SELL = "VTTC0801U"
_TR_BALANCE = "VTTC8434R"
_TR_ORDER_HISTORY = "VTTC8001R"

_TOKEN_CACHE: dict[str, dict[str, Any]] = {}
_LAST_REQUEST_TS = 0.0


def _company_name(symbol: str) -> str:
    return f"{symbol} 종목" if symbol else "종목"


def _base_price(symbol: str) -> int:
    numeric = sum(ord(char) for char in symbol)
    return 50000 + (numeric % 70000)


def _missing_env() -> list[str]:
    # Streamlit은 모듈을 캐시하므로, 매 호출마다 .env를 재로드해 최신 값을 반영한다.
    load_dotenv(_ENV_PATH, override=True)
    required = [
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_ACCOUNT_NO",
        "KIS_ACCOUNT_PRODUCT_CODE",
    ]
    return [name for name in required if not os.getenv(name, "").strip()]


def _env_name() -> str:
    return "real" if os.getenv("KIS_ENV", "demo").strip().lower() == "real" else "demo"


def _base_url(env_name: str) -> str:
    return REAL_BASE_URL if env_name == "real" else PAPER_BASE_URL


def _token_cache_path(env_name: str) -> Path:
    app_key = os.getenv("KIS_APP_KEY", "").strip()
    digest = hashlib.sha256(f"{env_name}:{app_key}".encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"kis_token_cache_{env_name}_{digest}.json"


def _throttle_requests(min_interval_seconds: float = 0.35) -> None:
    global _LAST_REQUEST_TS

    now = time.monotonic()
    wait_seconds = min_interval_seconds - (now - _LAST_REQUEST_TS)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _LAST_REQUEST_TS = time.monotonic()


def _force_sample_data() -> bool:
    value = os.getenv("KIS_FORCE_SAMPLE", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _sample_current_price(symbol: str, source: str) -> dict[str, Any]:
    base_price = _base_price(symbol)
    return {
        "stock_code": symbol,
        "company_name": _company_name(symbol),
        "current_price": base_price,
        "change_pct": 0.0,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": source,
    }


def _sample_price_history(symbol: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
    rng = random.Random(symbol)
    records: list[dict[str, Any]] = []
    base_price = float(_base_price(symbol))
    current_date = start_date
    index = 0

    while current_date <= end_date:
        if current_date.weekday() < 5:
            drift = math.sin(index / 4) * 0.01
            noise = (rng.random() - 0.5) * 0.02
            close_price = max(1000.0, base_price * (1 + drift + noise))
            open_price = max(1000.0, close_price * (1 + (rng.random() - 0.5) * 0.01))
            high_price = max(open_price, close_price) * 1.01
            low_price = min(open_price, close_price) * 0.99

            records.append(
                {
                    "date": current_date.isoformat(),
                    "open": round(open_price, 2),
                    "high": round(high_price, 2),
                    "low": round(low_price, 2),
                    "close": round(close_price, 2),
                    "volume": 1000000 + index * 5000,
                }
            )
            base_price = close_price
            index += 1
        current_date = current_date.fromordinal(current_date.toordinal() + 1)

    return records


def _read_cached_token(env_name: str) -> str | None:
    cache_file = _token_cache_path(env_name)
    if not cache_file.exists():
        return None

    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        expires_at = datetime.fromisoformat(payload["expires_at"])
        if expires_at <= datetime.now():
            return None
        access_token = str(payload.get("access_token") or "").strip()
        return access_token or None
    except Exception:
        return None


def _write_cached_token(env_name: str, access_token: str, expires_at: datetime) -> None:
    cache_file = _token_cache_path(env_name)
    try:
        cache_file.write_text(
            json.dumps(
                {
                    "access_token": access_token,
                    "expires_at": expires_at.isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        return None


def _safe_json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_rate_limited(payload: dict[str, Any]) -> bool:
    msg_cd = str(payload.get("msg_cd") or "")
    message = " ".join(
        str(payload.get(key) or "")
        for key in ("msg1", "message", "error_description")
    )
    return msg_cd == "EGW00201" or "초당 거래건수를 초과" in message


def _get_access_token() -> str:
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 KIS API를 호출할 수 없습니다.")

    env_name = _env_name()
    cached = _TOKEN_CACHE.get(env_name)
    if cached and cached["expires_at"] > datetime.now():
        return cached["access_token"]

    disk_cached_token = _read_cached_token(env_name)
    if disk_cached_token:
        expires_at = datetime.now() + timedelta(minutes=30)
        _TOKEN_CACHE[env_name] = {
            "access_token": disk_cached_token,
            "expires_at": expires_at,
        }
        return disk_cached_token

    app_key = os.getenv("KIS_APP_KEY", "").strip()
    app_secret = os.getenv("KIS_APP_SECRET", "").strip()
    url = f"{_base_url(env_name)}{TOKEN_ENDPOINT}"

    _throttle_requests()
    response = requests.post(
        url,
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        },
        timeout=15,
    )
    data = _safe_json(response)
    response.raise_for_status()
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"KIS 토큰 응답에 access_token이 없습니다: {data}")

    ttl_seconds = max(_to_int(data.get("expires_in"), 0), 300)
    expires_at = datetime.now() + timedelta(seconds=max(ttl_seconds - 60, 60))
    _TOKEN_CACHE[env_name] = {
        "access_token": access_token,
        "expires_at": expires_at,
    }
    _write_cached_token(env_name, access_token, expires_at)
    return access_token


def _account_params() -> tuple[str, str]:
    """(계좌번호, 상품코드) 반환."""
    return os.getenv("KIS_ACCOUNT_NO", "").strip(), os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "").strip()


def _request_kis_json(
    *,
    endpoint: str,
    tr_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 KIS API를 호출할 수 없습니다.")

    env_name = _env_name()
    app_key = os.getenv("KIS_APP_KEY", "").strip()
    app_secret = os.getenv("KIS_APP_SECRET", "").strip()
    access_token = _get_access_token()

    for attempt in range(4):
        _throttle_requests()
        response = requests.get(
            f"{_base_url(env_name)}{endpoint}",
            headers={
                "Content-Type": "application/json",
                "authorization": f"Bearer {access_token}",
                "appkey": app_key,
                "appsecret": app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            },
            params=params,
            timeout=15,
        )
        data = _safe_json(response)

        if response.ok and data.get("rt_cd") in (None, "", "0"):
            return data

        if _is_rate_limited(data) and attempt < 3:
            time.sleep(0.5 * (attempt + 1))
            continue

        if not response.ok:
            message = data.get("msg1") or data.get("message") or response.text
            raise RuntimeError(f"KIS API 요청 실패: {message}")

        message = data.get("msg1") or data
        raise RuntimeError(f"KIS API 요청 실패: {message}")

    raise RuntimeError("KIS API 요청이 반복적으로 제한되었습니다. 잠시 후 다시 시도하세요.")


def _post_kis_json(
    *,
    endpoint: str,
    tr_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """KIS API POST 요청 (주문 전용)."""
    if requests is None:
        raise RuntimeError("requests 패키지가 없어 KIS API를 호출할 수 없습니다.")

    env_name = _env_name()
    app_key = os.getenv("KIS_APP_KEY", "").strip()
    app_secret = os.getenv("KIS_APP_SECRET", "").strip()
    access_token = _get_access_token()

    _throttle_requests()
    response = requests.post(
        f"{_base_url(env_name)}{endpoint}",
        headers={
            "Content-Type": "application/json",
            "authorization": f"Bearer {access_token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "hashkey": "",
        },
        json=body,
        timeout=15,
    )
    data = _safe_json(response)
    if response.ok and data.get("rt_cd") in (None, "", "0"):
        return data
    message = data.get("msg1") or data.get("message") or response.text
    raise RuntimeError(f"KIS 주문 요청 실패: {message}")


def _format_as_of(output: dict[str, Any]) -> str:
    trade_date = str(output.get("stck_bsop_date") or "").strip()
    trade_time = str(output.get("stck_cntg_hour") or "").strip()
    if len(trade_date) == 8 and len(trade_time) >= 6:
        return (
            f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]} "
            f"{trade_time[:2]}:{trade_time[2:4]}"
        )
    if len(trade_date) == 8:
        return f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def using_sample_data() -> bool:
    return _force_sample_data() or requests is None or len(_missing_env()) > 0


def get_runtime_status() -> dict[str, Any]:
    sample_mode = using_sample_data()
    return {
        "provider": "sample-placeholder" if sample_mode else "kis-openapi",
        "kis_env": _env_name(),
        "configured": len(_missing_env()) == 0,
        "missing_env": _missing_env(),
        "force_sample": _force_sample_data(),
        "requests_available": requests is not None,
    }


def get_current_price(symbol: str) -> dict[str, Any]:
    clean_symbol = symbol.strip()
    if not clean_symbol:
        return _sample_current_price(clean_symbol, source="sample-placeholder")

    if using_sample_data():
        return _sample_current_price(clean_symbol, source="sample-placeholder")

    data = _request_kis_json(
        endpoint=PRICE_ENDPOINT,
        tr_id=PRICE_TR_ID,
        params={
            "FID_COND_MRKT_DIV_CODE": KIS_MARKET_CODE,
            "FID_INPUT_ISCD": clean_symbol,
        },
    )
    output = data.get("output", {}) or {}
    current_price = _to_int(output.get("stck_prpr"))
    if current_price <= 0:
        raise RuntimeError(f"현재가 응답이 비어 있습니다: {output}")

    company_name = (
        output.get("hts_kor_isnm")
        or output.get("prdt_name")
        or _company_name(clean_symbol)
    )
    return {
        "stock_code": output.get("stck_shrn_iscd") or clean_symbol,
        "company_name": company_name,
        "current_price": current_price,
        "change_pct": _to_float(output.get("prdy_ctrt")),
        "as_of": _format_as_of(output),
        "source": "kis-openapi",
    }


def get_price_history(symbol: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
    if start_date > end_date:
        raise ValueError("시작일은 종료일보다 앞서야 합니다.")

    clean_symbol = symbol.strip()
    if not clean_symbol:
        return []

    if using_sample_data():
        return _sample_price_history(clean_symbol, start_date, end_date)

    data = _request_kis_json(
        endpoint=DAILY_CHART_ENDPOINT,
        tr_id=DAILY_CHART_TR_ID,
        params={
            "FID_COND_MRKT_DIV_CODE": KIS_MARKET_CODE,
            "FID_INPUT_ISCD": clean_symbol,
            "FID_INPUT_DATE_1": start_date.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end_date.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "1",
        },
    )
    records: list[dict[str, Any]] = []
    for row in data.get("output2", []) or []:
        trade_date = str(row.get("stck_bsop_date") or "").strip()
        if len(trade_date) != 8:
            continue
        records.append(
            {
                "date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
                "open": _to_float(row.get("stck_oprc")),
                "high": _to_float(row.get("stck_hgpr")),
                "low": _to_float(row.get("stck_lwpr")),
                "close": _to_float(row.get("stck_clpr")),
                "volume": _to_int(row.get("acml_vol")),
            }
        )

    if not records:
        raise RuntimeError("기간 시세 응답이 비어 있습니다.")
    return records


def get_balance_snapshot() -> dict[str, Any]:
    """잔고 조회 (VTTC8434R). 샘플 모드에서는 더미 데이터 반환."""
    if using_sample_data():
        return {
            "summary": {"cash": 10_000_000, "evaluation_amount": 0, "profit_loss": 0},
            "holdings": [],
            "source": "sample-placeholder",
        }

    account_no, product_code = _account_params()
    data = _request_kis_json(
        endpoint=BALANCE_ENDPOINT,
        tr_id=_TR_BALANCE,
        params={
            "CANO": account_no,
            "ACNT_PRDT_CD": product_code,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
    )
    summary_list = data.get("output2") or [{}]
    raw = summary_list[0] if summary_list else {}
    holdings = []
    for h in data.get("output1") or []:
        qty = _to_int(h.get("hldg_qty"))
        if qty <= 0:
            continue
        holdings.append({
            "symbol": h.get("pdno", ""),
            "company_name": h.get("prdt_name", ""),
            "qty": qty,
            "avg_price": _to_float(h.get("pchs_avg_pric")),
            "current_price": _to_int(h.get("prpr")),
            "evaluation_amount": _to_int(h.get("evlu_amt")),
            "profit_loss": _to_int(h.get("evlu_pfls_amt")),
            "profit_loss_pct": _to_float(h.get("evlu_erng_rt")),
        })
    return {
        "summary": {
            "cash": _to_int(raw.get("dnca_tot_amt")),
            "evaluation_amount": _to_int(raw.get("scts_evlu_amt")),
            "total_amount": _to_int(raw.get("tot_evlu_amt")),
            "profit_loss": _to_int(raw.get("evlu_pfls_smtl_amt")),
        },
        "holdings": holdings,
        "source": "kis-openapi",
    }


def get_order_history(*, ccld_dvsn: str = "00") -> list[dict[str, Any]]:
    """당일 주문내역 조회 (VTTC8001R).

    ccld_dvsn: "00"=전체, "01"=체결, "02"=미체결
    """
    if using_sample_data():
        return []

    account_no, product_code = _account_params()
    today = datetime.now().strftime("%Y%m%d")
    data = _request_kis_json(
        endpoint=ORDER_HISTORY_ENDPOINT,
        tr_id=_TR_ORDER_HISTORY,
        params={
            "CANO": account_no,
            "ACNT_PRDT_CD": product_code,
            "INQR_STRT_DT": today,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "01",
            "PDNO": "",
            "CCLD_DVSN": ccld_dvsn,
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        },
    )
    result = []
    for o in data.get("output1") or []:
        side_code = o.get("sll_buy_dvsn_cd", "")
        result.append({
            "order_no": o.get("odno", ""),
            "symbol": o.get("pdno", ""),
            "company_name": o.get("prdt_name", ""),
            "side": "매수" if side_code == "02" else "매도",
            "order_type": "시장가" if o.get("ord_dvsn_cd") == "01" else "지정가",
            "qty": _to_int(o.get("ord_qty")),
            "price": _to_int(o.get("ord_unpr")),
            "filled_qty": _to_int(o.get("tot_ccld_qty")),
            "avg_fill_price": _to_int(o.get("avg_prvs")),
            "status": o.get("ord_stts", ""),
            "time": o.get("ord_tmd", ""),
        })
    return result


def get_open_orders() -> list[dict[str, Any]]:
    """미체결 주문 조회 (VTTC8001R CCLD_DVSN=02)."""
    return get_order_history(ccld_dvsn="02")


def submit_paper_order(order_payload: dict[str, Any]) -> dict[str, Any]:
    """모의투자 주문 제출. real 환경에서는 호출 자체를 차단한다."""
    if _env_name() == "real":
        raise RuntimeError("실전 주문은 이 앱에서 지원하지 않습니다. KIS_ENV=demo로 설정하세요.")

    if using_sample_data():
        return {
            "ok": False,
            "message": "샘플 데이터 모드에서는 주문을 보낼 수 없습니다. .env의 KIS API 키를 확인하세요.",
            "order_no": "",
        }

    account_no, product_code = _account_params()
    side = order_payload.get("side", "buy")
    order_type = order_payload.get("order_type", "market")
    symbol = str(order_payload.get("symbol", "")).strip()
    qty = int(order_payload.get("qty", 1))
    price = int(order_payload.get("price", 0))

    tr_id = _TR_ORDER_BUY if side == "buy" else _TR_ORDER_SELL
    # KIS 주문구분코드: 00=지정가, 01=시장가
    ord_dvsn = "01" if order_type == "market" else "00"
    ord_unpr = "0" if order_type == "market" else str(price)

    data = _post_kis_json(
        endpoint=ORDER_ENDPOINT,
        tr_id=tr_id,
        body={
            "CANO": account_no,
            "ACNT_PRDT_CD": product_code,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": ord_unpr,
        },
    )
    output = data.get("output") or {}
    return {
        "ok": True,
        "message": (data.get("msg1") or "주문이 접수되었습니다.").strip(),
        "order_no": output.get("odno", ""),
        "order_time": output.get("ord_tmd", ""),
    }
