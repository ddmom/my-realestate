import requests
import json
import streamlit as st
import urllib.parse
import re

# =========================================================
# I. 기본 설정 (행정구역 DB, API URL 등)
# =========================================================

# 전국 행정구역 데이터 (원본 DB 역할)
FULL_ADMIN_DB = {
    "서울특별시": {
        "강남구": "11680",
        "서초구": "11650",
        "송파구": "11710",
        "영등포구": "11560",
        "마포구": "11440",
    },
    "부산광역시": {
        "해운대구": "26350",
        "부산진구": "26230",
        "동래구": "26260",
    },
    "인천광역시": {
        "연수구": "28185",
        "남동구": "28177",
        "서구": "28260",
    },
    "경기도": {
        "성남시 분당구": "41135",
        "수원시 영통구": "41113",
        "용인시 수지구": "41465",
    },
}

APART_PRICE_URL = "https://api.vworld.kr/ned/data/getApartHousingPriceAttr"
SEARCH_URL = "https://api.vworld.kr/req/search"

# ⚠ 브이월드 인증키에 등록한 도메인과 반드시 같아야 합니다.
#   (예: https://my-realestate.streamlit.app)
DOMAIN = "https://my-realestate.streamlit.app"


# =========================================================
# II. 설정 로딩 함수 (Streamlit Secrets 사용)
# =========================================================

def load_config():
    """
    config.ini 대신 Streamlit secrets에서 설정값을 읽어옵니다.

    .streamlit/secrets.toml 예시:

    [SETTINGS]
    VWORLD_KEY = "CB7D6C28-63D0-325F-9AD2-D5AD4755FC4D"

    [LOCATION]
    ALLOWED_CODES = "11680,11650,41465"

    [APP_DATA]
    MARKET_RATIO = "1.4"
    """
    try:
        settings = st.secrets["SETTINGS"]
        location = st.secrets["LOCATION"]
        app_data = st.secrets["APP_DATA"]

        v_key = settings["VWORLD_KEY"].strip()
        market_ratio = float(str(app_data["MARKET_RATIO"]).strip())

        allowed_raw = location["ALLOWED_CODES"]
        # 문자열이든 리스트든 모두 처리
        if isinstance(allowed_raw, str):
            allowed_codes = [
                c.strip() for c in allowed_raw.split(",") if c.strip()
            ]
        else:
            allowed_codes = [str(c).strip() for c in allowed_raw if str(c).strip()]

        if not v_key or not allowed_codes:
            raise ValueError("VWORLD_KEY 또는 ALLOWED_CODES가 비어 있습니다.")

        return v_key, market_ratio, allowed_codes

    except Exception as e:
        st.error(
            "❌ 오류: 설정 파일(secrets) 로딩 실패: "
            f"{e}\n\nStreamlit secrets에 다음 섹션이 정확히 있는지 확인해 주세요.\n"
            "[SETTINGS] VWORLD_KEY\n[LOCATION] ALLOWED_CODES\n[APP_DATA] MARKET_RATIO"
        )
        st.stop()


# =========================================================
# III. 모듈 함수 정의 (V-World PNU 안정화)
# =========================================================

def format_korean_money(amount: int) -> str:
    """숫자를 'X억 Y만 원' 형태로 변환"""
    if amount <= 0:
        return "0 원"
    amount = int(amount)
    eok = amount // 100_000_000
    remainder = amount % 100_000_000
    man = remainder // 10_000
    result = ""
    if eok > 0:
        result += f"{eok}억 "
    if man > 0:
        result += f"{man:,}만"
    return result.strip() + " 원"


def get_pnu_code(address: str, key: str, domain: str) -> str | None:
    """
    주소 문자열을 PNU 코드로 변환 (V-World Search API 사용)
    - 주소 문자열을 정제해서 검색 정확도를 높입니다.
    """
    clean_address = re.sub(r"\s+", " ", address).strip()

    try:
        params = {
            "service": "search",
            "request": "search",
            "version": "2.0",
            "query": clean_address,
            "type": "address",
            "category": "parcel",
            "format": "json",
            "key": key,
            "domain": domain,
        }
        response = requests.get(SEARCH_URL, params=params, timeout=5)
        data = response.json()

        if (
            data.get("response", {}).get("status") == "OK"
            and data["response"]["result"]["items"]
        ):
            pnu = data["response"]["result"]["items"][0]["id"]
            return pnu

        return None
    except Exception:
        return None


def get_latest_official_price(
    pnu: str,
    key: str,
    target_year: str = "2024",
    url: str = APART_PRICE_URL,
) -> tuple[str, int, float]:
    """PNU 코드를 사용하여 최신 공시가격, 면적, 단지명을 조회합니다."""
    params = {
        "key": key,
        "pnu": pnu,
        "stdrYear": target_year,
        "format": "json",
        "numOfRows": "100",
    }
    headers = {"Referer": DOMAIN}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            ahp = data.get("apartHousingPrices", {})
            items = ahp.get("field", [])
            if items:
                latest_price = max(
                    int(item.get("pblntfPc", 0)) for item in items
                )
                area = float(items[0].get("prvuseAr", "0") or 0)
                name = items[0].get("aphusNm", "주택명 미상")
                return name, latest_price, area

        return "데이터 없음", 0, 0.0
    except Exception:
        return "통신 오류", 0, 0.0


def calculate_risk(
    official_price: int,
    market_price_ratio: float,
    jeonse_deposit: int,
    loan_amount: int,
) -> tuple[float, str, int]:
    """깡통전세 위험도 계산 및 판정"""
    estimated_market_price = int(official_price * market_price_ratio)
    total_burden = jeonse_deposit + loan_amount
    risk_percent = (
        (total_burden / estimated_market_price) * 100
        if estimated_market_price > 0
        else 100.0
    )

    if risk_percent < 70:
        judgment = "✅ 안전 (70% 미만)"
    elif risk_percent <= 80:
        judgment = "⚠️ 주의 (80% 이하 - 보증보험 필요)"
    else:
        judgment = "❌ 위험 (80% 초과 - 깡통전세 가능성 높음)"

    return risk_percent, judgment, estimated_market_price


def calculate_safe_jeonse(
    estimated_market_price: int,
    loan_amount: int,
) -> tuple[int, int]:
    """적정 안전 전세금과 최대 경고 전세금 계산"""
    MAX_SAFE_RATIO = 0.70
    MAX_WARNING_RATIO = 0.80

    max_safe_jeonse = int(estimated_market_price * MAX_SAFE_RATIO - loan_amount)
    max_warning_jeonse = int(estimated_market_price * MAX_WARNING_RATIO - loan_amount)

    return max(0, max_safe_jeonse), max(0, max_warning_jeonse)


# =========================================================
# IV. Streamlit UI & 메인 실행 함수
# =========================================================

def main():
    # --- 1. 설정 로드 (secrets 사용) ---
    VWORLD_KEY, MARKET_RATIO, ALLOWED_CODES = load_config()

    # --- Streamlit 페이지 기본 설정 ---
    st.set_page_config(
        layout="wide",
        page_title="깡통전세 위험도 판독기",
        page_icon="logo.png",
    )

    st.title("🛡️ 깡통전세 위험도 판독기 (공인중개사 버전)")
    st.sidebar.image("logo.png", width=100)
    st.sidebar.markdown("---")

    col_city, col_district, col_detail_input = st.columns(3)

    # 1. 주소 입력 (담당 지역 필터링)
    st.subheader("1. 담당 지역 및 주소 정보 입력")

    # --- 드롭다운 메뉴 필터링 (허용 코드만) ---
    filtered_districts: dict[str, list[str]] = {}
    for city, districts in FULL_ADMIN_DB.items():
        for district, code in districts.items():
            if code in ALLOWED_CODES:
                filtered_districts.setdefault(city, []).append(district)

    if not filtered_districts:
        st.error(
            "❌ 오류: secrets의 ALLOWED_CODES에 해당하는 담당 지역이 없습니다. "
            "브이월드 지역 코드와 일치하는지 확인해 주세요."
        )
        return

    # 1-1. 시/도 선택
    city_options = list(filtered_districts.keys())
    selected_city = col_city.selectbox(
        "① 시/도 선택", city_options, index=0, key="city_select"
    )

    # 1-2. 시/군/구 선택
    district_options = filtered_districts.get(selected_city, [])
    selected_district = col_district.selectbox(
        "② 시/군/구 선택", district_options, index=0, key="district_select"
    )

    # 1-3. 상세 주소 입력
    detail_address = col_detail_input.text_input(
        "③ 상세 주소 (동, 번지 예: 개포동 12)",
        value="개포동 12",
        key="detail_addr_input",
        help="정확한 PNU 코드를 위해 반드시 '동 이름 + 번지' 포맷으로 입력해야 합니다.",
    )

    TARGET_ADDRESS = f"{selected_city} {selected_district} {detail_address}"
    st.info(f"🔍 최종 검색 주소: {TARGET_ADDRESS}")

    # ------------------ 2. 전세 및 부채 정보 ------------------
    st.subheader("2. 전세 및 선순위 부채 정보")

    JEONSE_DEPOSIT = st.number_input(
        "목표 전세 보증금 (원)",
        min_value=0,
        value=1_800_000_000,
        step=10_000_000,
        format="%i",
    )
    LOAN_AMOUNT = st.number_input(
        "선순위 대출 금액 (원)",
        min_value=0,
        value=300_000_000,
        step=10_000_000,
        format="%i",
    )

    st.markdown("---")

    # 3. 진단 시작 버튼
    if st.button("🚨 위험도 진단 시작", use_container_width=True, type="primary"):

        # 상세 주소 포맷 간단 검증
        if len(detail_address.strip().split()) < 2:
            st.error(
                "❌ 오류: 상세 주소를 '동 이름 번지' 포맷으로 정확히 입력해 주세요. (예: 개포동 12)"
            )
            return

        with st.spinner("데이터를 조회하고 위험도를 판정 중입니다..."):

            # 1) PNU 코드 변환
            pnu_code = get_pnu_code(TARGET_ADDRESS, VWORLD_KEY, DOMAIN)
            if not pnu_code:
                st.error(
                    "❌ 오류: V-World에서 PNU 코드 변환에 실패했습니다.\n"
                    "- 주소 오타 여부\n"
                    "- 브이월드 인증키의 활용 API(지오코딩) 설정\n"
                    "- 도메인(URL) 설정\n"
                    "을 다시 확인해 주세요."
                )
                return

            # 2) 최신 공시가격 조회
            name, official_price, area = get_latest_official_price(
                pnu_code, VWORLD_KEY
            )

            if official_price <= 0:
                st.warning(
                    "⚠️ 해당 주소의 최신 공시가격(2024년) 데이터를 찾을 수 없습니다."
                )
                return

            # 3) 위험도 및 적정 전세금 계산
            risk_pct, judgment, estimated_market_price = calculate_risk(
                official_price,
                MARKET_RATIO,
                int(JEONSE_DEPOSIT),
                int(LOAN_AMOUNT),
            )

            max_safe_jeonse, max_warning_jeonse = calculate_safe_jeonse(
                estimated_market_price,
                int(LOAN_AMOUNT),
            )

        # ------------------ 결과 출력 ------------------
        if "❌ 위험" in judgment:
            color = "red"
        elif "⚠️ 주의" in judgment:
            color = "orange"
        else:
            color = "green"

        st.subheader(f"🏠 {name} ({area}㎡) 최종 판정 결과: {judgment}")
        st.markdown("---")

        col_price1, col_price2, col_price3 = st.columns(3)
        col_price1.metric(
            "API 조회 공시가격 (대표값)",
            format_korean_money(official_price),
            delta_color="off",
        )
        col_price2.metric(
            f"추정 시장가치 (공시가 x {MARKET_RATIO:.2f})",
            format_korean_money(estimated_market_price),
            delta_color="off",
        )
        col_price3.metric(
            "총 부담액 (전세금 + 대출)",
            format_korean_money(JEONSE_DEPOSIT + LOAN_AMOUNT),
            delta_color="off",
        )

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"## 최종 위험 전세가율: "
            f"<span style='color:{color}; font-size: 32px;'>{risk_pct:.2f}%</span>",
            unsafe_allow_html=True,
        )
        st.markdown("---")

        # 적정 전세금 정보
        st.header("✨ 적정 전세금 제안 (공인중개사 추천)")
        col_safe1, col_safe2 = st.columns(2)

        col_safe1.metric(
            "최대 안전 전세금 (70% 기준)",
            format_korean_money(max_safe_jeonse),
            help="전세가율 70% 이하를 유지하는 최대 금액입니다. "
            "(HUG 보증보험 가입에 가장 안전한 기준)",
        )

        col_safe2.metric(
            "최대 주의 전세금 (80% 기준)",
            format_korean_money(max_warning_jeonse),
            help="전세가율 80% 이하를 유지하는 최대 금액입니다.",
        )

        st.info(
            f"💡 진단 대상: **{name}** | 전용면적: {area}㎡. "
            f"판정 기준은 공시가에 {MARKET_RATIO:.2f}배율을 적용한 공인중개사 룰에 따릅니다."
        )


# =========================================================
# V. 엔트리 포인트
# =========================================================

if __name__ == "__main__":
    main()
