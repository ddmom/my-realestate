import requests
import json
import sys
import urllib.parse

# =========================================================
# I. 설정 및 환경 변수 (인증키 및 기본값)
# =========================================================
# 💡 선생님의 유효한 V-World 인증키
VWORLD_KEY = "8C1C6095-657F-3CFD-808D-6A944FE091DA"
DOMAIN = "http://localhost"
APART_PRICE_URL = "https://api.vworld.kr/ned/data/getApartHousingPriceAttr"
SEARCH_URL = "http://api.vworld.kr/req/search"
# [공인중개사 전문 지식] 공시가 대비 추정 시세 배율 (130%~150% 사이가 안전마진)
MARKET_RATIO = 1.4 

# =========================================================
# II. 모듈 함수 정의 (핵심 로직)
# =========================================================

def get_pnu_code(address):
    """주소 문자열을 PNU 코드로 변환합니다. (V-World Search API 사용)"""
    try:
        params = {
            "service": "search",
            "request": "search",
            "version": "2.0",
            "query": address,
            "type": "address",
            "category": "parcel",
            "format": "json",
            "key": VWORLD_KEY,
            "domain": DOMAIN
        }
        response = requests.get(SEARCH_URL, params=params)
        data = response.json()

        if data['response']['status'] == 'OK' and data['response']['result']['items']:
            pnu = data['response']['result']['items'][0]['id']
            print(f"🔑 PNU 변환 성공: {pnu}")
            return pnu
        
        print(f"⚠️ PNU 변환 실패: 주소를 찾을 수 없습니다.")
        return None
    except Exception as e:
        print(f"⚠️ PNU 검색 중 에러 발생: {e}")
        return None

def get_latest_official_price(pnu, target_year="2024"):
    """PNU 코드를 사용하여 최신 공시가격과 면적을 조회합니다. (V-World Price API 사용)"""
    params = {
        "key": VWORLD_KEY,
        "pnu": pnu,
        "stdrYear": target_year,
        "format": "json",
        "numOfRows": "100",
        "domain": DOMAIN
    }
    headers = {'Referer': DOMAIN}
    response = requests.get(APART_PRICE_URL, params=params, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        
        # 실제 데이터 구조에 맞춰 파싱 (가장 성공했던 구조)
        if 'apartHousingPrices' in data and 'field' in data['apartHousingPrices']:
            items = data['apartHousingPrices']['field']
            
            if items:
                # 모든 호수 중 최고 공시가격을 대표값으로 추출
                latest_price = max([int(item.get('pblntfPc', 0)) for item in items])
                area = items[0].get('prvuseAr', '0')
                name = items[0].get('aphusNm', '아파트명 불명')
                
                return name, latest_price, float(area)
            
    print("⚠️ 가격 조회 실패: 해당 PNU에 최신년도 가격 데이터가 없거나 서비스에 문제가 있습니다.")
    return None, 0, 0

def calculate_risk(official_price, market_price_ratio, jeonse_deposit, loan_amount):
    """
    공시가격, 전세금, 대출금을 바탕으로 깡통전세 위험도를 계산하고 판정합니다.
    (공인중개사 전문 지식이 반영된 핵심 로직)
    """
    estimated_market_price = int(official_price * market_price_ratio)
    total_burden = jeonse_deposit + loan_amount

    if estimated_market_price == 0:
        risk_percent = 100.0
    else:
        risk_percent = (total_burden / estimated_market_price) * 100

    # 판정 로직 적용
    if risk_percent < 70:
        judgment = "✅ 안전 (70% 미만)"
    elif risk_percent <= 80:
        judgment = "⚠️ 주의 (80% 이하 - 보증보험 가입 고려)"
    else:
        judgment = "❌ 위험 (80% 초과 - 깡통전세 가능성 높음)"

    return risk_percent, judgment, estimated_market_price

# =========================================================
# III. 사용자 입력 (시뮬레이션 데이터)
# =========================================================
# 실제 앱에서는 사용자가 입력하는 값입니다.
TARGET_ADDRESS = "서울특별시 강남구 개포동 12"
JEONSE_DEPOSIT = 1_800_000_000   # 목표 전세금 (18억 원)
LOAN_AMOUNT = 300_000_000         # 선순위 대출금 (3억 원)

# =========================================================
# IV. 메인 실행 함수 (모든 모듈 호출 및 통합 결과 출력)
# =========================================================

def main():
    print("=============================================")
    print(f"🏠 [전세 안전 진단 시작] 대상 주소: {TARGET_ADDRESS}")
    print("=============================================")

    # 1. 주소 -> PNU 코드 변환
    pnu_code = get_pnu_code(TARGET_ADDRESS)
    if not pnu_code:
        print("❌ ERROR: PNU 코드 변환에 실패했습니다. 프로그램을 종료합니다.")
        return

    # 2. PNU 코드로 최신 공시가격 조회
    name, official_price, area = get_latest_official_price(pnu_code, target_year="2024")
    
    if official_price <= 0:
        print("❌ ERROR: 공시가격 데이터를 가져오는 데 실패했습니다.")
        return

    print(f"✅ 데이터 추출 성공: {name} ({area}㎡)")
    print(f"💰 공시가격(API): {official_price:,}원")
    
    # 3. 위험도 계산 로직 실행
    risk_pct, judgment, estimated_market_price = calculate_risk(
        official_price, 
        MARKET_RATIO, 
        JEONSE_DEPOSIT, 
        LOAN_AMOUNT
    )

    # 4. 최종 결과 출력
    print("\n=============================================")
    print("🛡️ [최종 깡통전세 위험도 판정 결과]")
    print(f"   - 공인중개사 추정 시세: {estimated_market_price:,}원 (공시가 * {MARKET_RATIO})")
    print(f"   - 총 부채 (전세금 + 대출): {(JEONSE_DEPOSIT + LOAN_AMOUNT):,}원")
    print(f"   - 최종 위험도 (전세가율): {risk_pct:.2f}%")
    print(f"   - **판정 결과:** {judgment}")
    print("=============================================")
    
# 파이썬 파일을 실행했을 때 main() 함수가 실행되도록 하는 표준 구문
if __name__ == "__main__":
    main()