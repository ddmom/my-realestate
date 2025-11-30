import requests
import json
import streamlit as st
import configparser
import sys
import urllib.parse # requests 사용 시 필요

# =========================================================
# I. 설정 및 환경 변수 (CONFIG.INI에서 로드)
# =========================================================
# 전국 행정구역 데이터 (원본 DB 역할) - config.ini에 없는 지역이 들어올 경우 대비
FULL_ADMIN_DB = {
    "서울특별시": {
        "강남구": "11680", "서초구": "11650", "송파구": "11710",
        "영등포구": "11560", "마포구": "11440"
    },
    "부산광역시": {
        "해운대구": "26350", "부산진구": "26230", "동래구": "26260"
    },
    "인천광역시": {
        "연수구": "28185", "남동구": "28177", "서구": "28260"
    },
    "대구광역시": {
        "수성구": "27260", "달서구": "27290", "중구": "27110"
    },
    "경기도": {
        "성남시 분당구": "41135", "수원시 영통구": "41113", "용인시 수지구": "41465"
    }
}

# --- config.ini 파일 로딩 함수 ---
def load_config():
    """config.ini 파일을 읽고 유효성 검사를 수행합니다."""
    config = configparser.ConfigParser()
    try:
        config.read('config.ini', encoding='utf-8')
        
        # 설정값 로드
        v_key = config.get('SETTINGS', 'VWORLD_KEY').strip()
        ratio = float(config.get('APP_DATA', 'MARKET_RATIO'))
        
        # 허용된 지역 코드 로드 및 정리
        allowed_codes_str = config.get('LOCATION', 'ALLOWED_CODES')
        allowed_codes = [c.strip() for c in allowed_codes_str.split(',') if c.strip()]

        if not v_key or not allowed_codes:
            raise ValueError("인증키 또는 담당 지역 코드가 누락되었습니다.")

        return v_key, ratio, allowed_codes
    
    except FileNotFoundError:
        st.error("❌ 오류: 'config.ini' 파일을 찾을 수 없습니다. 앱 폴더에 파일을 생성했는지 확인하세요.")
        st.stop()
    except Exception as e:
        st.error(f"❌ 오류: config.ini 파일 내용이 잘못되었습니다. 포맷 또는 코드를 확인하세요. ({e})")
        st.stop()


# =========================================================
# II. 모듈 함수 정의 (통합 핵심 로직)
# =========================================================

def format_korean_money(amount):
    """숫자를 'X억 Y천만 Z만원' 형태로 변환합니다."""
    if amount <= 0:
        return "0 원"
    
    amount = int(amount)
    eok = amount // 100000000
    remainder = amount % 100000000
    man = remainder // 10000
    
    result = ""
    
    if eok > 0:
        result += f"{eok}억 "
        
    if man > 0:
        result += f"{man:,}만"
        
    return result.strip() + " 원"

def get_pnu_code(address, key, domain):
    """주소 문자열을 PNU 코드로 변환합니다. (V-World Search API)"""
    try:
        params = {
            "service": "search", "request": "search", "version": "2.0",
            "query": address, "type": "address", "category": "parcel",
            "format": "json", "key": key, "domain": domain
        }
        # URL은 http://api.vworld.kr/req/search 입니다.
        response = requests.get("http://api.vworld.kr/req/search", params=params, timeout=5)
        data = response.json()

        if data['response']['status'] == 'OK' and data['response']['result']['items']:
            pnu = data['response']['result']['items'][0]['id']
            return pnu
        return None
    except Exception as e:
        return None

def get_latest_official_price(pnu, key, target_year="2024", url="https://api.vworld.kr/ned/data/getApartHousingPriceAttr", domain="http://localhost"):
    """PNU 코드를 사용하여 최신 공시가격, 면적, 단지명을 조회합니다. (V-World Price API)"""
    params = {
        "key": key, "pnu": pnu, "stdrYear": target_year,
        "format": "json", "numOfRows": "100", "domain": domain
    }
    headers = {'Referer': domain}
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            if 'apartHousingPrices' in data and 'field' in data['apartHousingPrices']:
                items = data['apartHousingPrices']['field']
                
                if items:
                    latest_price = max([int(item.get('pblntfPc', 0)) for item in items])
                    area = items[0].get('prvuseAr', '0')
                    name = items[0].get('aphusNm', '주택명 미상')
                    return name, latest_price, float(area)
            
        return "데이터 없음", 0, 0.0
    except Exception as e:
        return "통신 오류", 0, 0.0

def calculate_risk(official_price, market_price_ratio, jeonse_deposit, loan_amount):
    """깡통전세 위험도를 계산하고 판정합니다."""
    estimated_market_price = int(official_price * market_price_ratio)
    total_burden = jeonse_deposit + loan_amount
    risk_percent = (total_burden / estimated_market_price) * 100 if estimated_market_price > 0 else 100.0

    if risk_percent < 70:
        judgment = "✅ 안전 (70% 미만)"
    elif risk_percent <= 80:
        judgment = "⚠️ 주의 (80% 이하 - 보증보험 필요)"
    else:
        judgment = "❌ 위험 (80% 초과 - 깡통전세 가능성 높음)"

    return risk_percent, judgment, estimated_market_price

def calculate_safe_jeonse(estimated_market_price, loan_amount):
    """적정 안전 전세금과 최대 경고 전세금을 계산합니다."""
    MAX_SAFE_RATIO = 0.70 
    MAX_WARNING_RATIO = 0.80
    max_safe_jeonse = int((estimated_market_price * MAX_SAFE_RATIO) - loan_amount)
    max_warning_jeonse = int((estimated_market_price * MAX_WARNING_RATIO) - loan_amount)
    return max(0, max_safe_jeonse), max(0, max_warning_jeonse)

# =========================================================
# III. 사용자 입력 및 메인 실행 함수 (Streamlit UI)
# =========================================================

def main():
    # --- 설정 파일 로드 (가장 먼저 실행) ---
    VWORLD_KEY, MARKET_RATIO, ALLOWED_CODES = load_config()

    # --- Streamlit 페이지 설정 및 로고 적용 ---
    st.set_page_config(
        layout="wide",
        page_title="깡통전세 위험도 판독기",
        page_icon="logo.png" # ⬅️ 로고 파일명
    )
    st.title("🛡️ 깡통전세 위험도 판독기 (공인중개사 버전)")
    st.sidebar.image("logo.png", width=100) # 사이드바 로고
    st.sidebar.markdown("---")
    
    col_city, col_district, col_detail_input = st.columns(3)

    # 1. 주소 입력 (담당 지역 필터링)
    st.subheader("1. 담당 지역 및 주소 정보 입력")
    
    # --- 드롭다운 메뉴 필터링 로직 ---
    filtered_districts = {}
    for city, districts in FULL_ADMIN_DB.items():
        for district, code in districts.items():
            if code in ALLOWED_CODES:
                if city not in filtered_districts:
                    filtered_districts[city] = []
                filtered_districts[city].append(district)

    if not filtered_districts:
        st.error("❌ 오류: 'config.ini'에 유효한 지역 코드가 없습니다. 관리자에게 문의하세요.")
        return

    # 1-1. 시/도 선택
    city_options = list(filtered_districts.keys())
    selected_city = col_city.selectbox("① 시/도 선택", city_options, index=0, key="city_select")
    
    # 1-2. 시/군/구 선택 (Cascading Filter)
    district_options = filtered_districts.get(selected_city, [])
    selected_district = col_district.selectbox("② 시/군/구 선택", district_options, index=0, key="district_select")

    # 1-3. 상세 주소 입력 (동, 번지)
    detail_address = col_detail_input.text_input(
        "③ 상세 주소 (동, 번지 예: 개포동 12)", 
        value="개포동 12" if "강남구" in selected_district else "", # 강남구 선택 시 초기값 제공
        key="detail_addr_input",
        help="정확한 PNU 코드를 위해 반드시 '동 이름 + 번지' 포맷으로 입력해야 합니다."
    )

    # 최종 목표 주소 문자열 조합
    TARGET_ADDRESS = f"{selected_city} {selected_district} {detail_address}"
    st.info(f"🔍 최종 검색 주소: {TARGET_ADDRESS}")
    
    # ------------------ 2. 전세 및 부채 정보 ------------------
    st.subheader("2. 전세 및 선순위 부채 정보")
    
    # ⚠️ 콤마 표시를 위해 format="%i" 적용
    JEONSE_DEPOSIT = st.number_input(
        "목표 전세 보증금 (원)", min_value=0, value=1800000000, step=10000000, format="%i"
    )
    
    LOAN_AMOUNT = st.number_input(
        "선순위 대출 금액 (원)", min_value=0, value=300000000, step=10000000, format="%i"
    )
    
    st.markdown("---")
    
    # 4. 진단 시작 버튼
    if st.button("🚨 위험도 진단 시작", use_container_width=True, type="primary"):
        
        # ------------------ 입력 포맷 검증 로직 ------------------
        if len(detail_address.strip().split()) < 2:
            st.error("❌ 오류: 상세 주소를 '동 이름 번지' 포맷으로 정확히 입력해 주세요. (예: 개포동 12)")
            return
        # --------------------------------------------------------

        with st.spinner('데이터를 조회하고 위험도를 판정 중입니다...'):
            
            # 1. PNU 코드 변환
            pnu_code = get_pnu_code(TARGET_ADDRESS, VWORLD_KEY, DOMAIN)
            if not pnu_code:
                st.error("❌ 오류: V-World에서 PNU 코드 변환에 실패했습니다. 입력 주소 조합을 확인해 주세요.")
                return

            # 2. 최신 공시가격 조회
            name, official_price, area = get_latest_official_price(pnu_code, VWORLD_KEY, target_year="2024")
            
            if official_price <= 0:
                st.warning("⚠️ 해당 주소의 최신 공시가격(2024년) 데이터를 찾을 수 없습니다.")
                return

            # 3. 위험도 및 적정 전세금 계산
            risk_pct, judgment, estimated_market_price = calculate_risk(
                official_price, MARKET_RATIO, int(JEONSE_DEPOSIT), int(LOAN_AMOUNT)
            )
            
            max_safe_jeonse, max_warning_jeonse = calculate_safe_jeonse(
                estimated_market_price, int(LOAN_AMOUNT)
            )

        # ------------------ 최종 결과 출력 ------------------
        
        if "❌ 위험" in judgment:
            color = "red"
            emoji = "🚨"
        elif "⚠️ 주의" in judgment:
            color = "orange"
            emoji = "🟡"
        else:
            color = "green"
            emoji = "✅"
            
        st.subheader(f"{emoji} 최종 판정 결과: {judgment}")
        st.markdown("---")
        
        # 시세 및 부채 정보 출력
        col_price1, col_price2, col_price3 = st.columns(3)
        col_price1.metric("API 조회 공시가격 (대표값)", format_korean_money(official_price), delta_color="off")
        col_price2.metric("추정 시장가치 (공시가 x 140%)", format_korean_money(estimated_market_price), delta_color="off")
        col_price3.metric("총 부담액 (전세금 + 대출)", format_korean_money(JEONSE_DEPOSIT + LOAN_AMOUNT), delta_color="off")

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(f"## 최종 위험 전세가율: <span style='color:{color}; font-size: 32px;'>{risk_pct:.2f}%</span>", unsafe_allow_html=True)
        st.markdown("---")
        
        # 적정 전세금 정보 출력
        st.header("✨ 적정 전세금 제안 (공인중개사 추천)")
        col_safe1, col_safe2 = st.columns(2)
        
        col_safe1.metric("최대 안전 전세금 (70% 기준)", format_korean_money(max_safe_jeonse), 
                         help="전세가율 70% 이하를 유지하는 최대 금액입니다. (HUG 보증보험 가입에 가장 안전한 기준)")
        
        col_safe2.metric("최대 주의 전세금 (80% 기준)", format_korean_money(max_warning_jeonse),
                         help="전세가율 80% 이하를 유지하는 최대 금액입니다.")

        st.info(f"💡 진단 대상: **{name}** | 전용면적: {area}㎡. 판정 기준은 공시가 {MARKET_RATIO}배율을 적용한 공인중개사 룰에 따릅니다.")

# 파이썬 파일을 실행했을 때 main() 함수가 실행되도록 하는 표준 구문
if __name__ == "__main__":
    main()