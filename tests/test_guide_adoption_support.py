"""scripts/guide/build_adoption_support.py 의 분류 규칙 테스트. 외부 API 는 부르지 않는다."""

import importlib.util
import pathlib

PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "guide" / "build_adoption_support.py"
spec = importlib.util.spec_from_file_location("build_adoption_support", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_split_region():
    assert mod.split_region("서울특별시 영등포구") == ("서울특별시", "영등포구")
    assert mod.split_region("경상남도") == ("경상남도", None)
    assert mod.split_region("(구)광주광역시") == (None, None)
    assert mod.split_region(None) == (None, None)


def test_program_kinds_animal_adoption_only():
    cost = {"서비스명": "유실·유기동물 입양비 지원", "서비스목적요약": "입양시 소유자 부담비용 지원(최대 15만원)"}
    insurance = {"서비스명": "입양동물 펫보험 가입 지원", "서비스목적요약": "보호동물 입양 시 펫보험 가입비 지원"}
    typo = {"서비스명": "펫 보헙 가입비 지원사업", "서비스목적요약": "펫보험 가입비 지원", "지원대상": "동물보호소 내 유실ㆍ유기동물을 입양 후"}
    child = {"서비스명": "입양축하금", "서비스목적요약": "1회에 한하여 200만원 입양축하금 지급"}
    shelter = {"서비스명": "유기유실동물 관리수준 개선지원", "서비스목적요약": "동물보호센터에 유기동물 보호 및 입양 처리, 유기견 포획 등 지원"}
    assert mod.program_kinds(cost) == ["cost"]
    assert mod.program_kinds(insurance) == ["insurance"]
    assert mod.program_kinds(typo) == ["insurance"]
    assert mod.program_kinds(child) == []  # 아이 입양 지원은 뺌
    assert mod.program_kinds(shelter) == []  # 보호센터 운영 지원은 뺌


def test_classify_articles():
    guri = "제18조(유실ㆍ유기동물의 입양 지원) 시장은 유실ㆍ유기동물을 분양받는 자에게 마리당 한 번만 예산의 범위에서 필요한 비용을 지원할 수 있다."
    busan = "제19조(유실ㆍ유기동물의 입양) ① 시장은 유실ㆍ유기동물을 입양하려는 자에게 예산의 범위에서 동물보험 납입비용의 전부 또는 일부를 지원할 수 있다."
    fee = "제12조(수수료) ① 도지사는 등록 수수료를 감면할 수 있다. 2. 유실&#11825;유기 동물을 입양 또는 기증받아 등록하는 경우: 전액 면제"
    basic = "제3조(군수의 책무) ① 군수는 유기동물의 보호 및 입양 등 동물복지 정책을 적극적으로 추진하여야 한다."
    assert mod.classify_articles([guri]) == {"cost": ["제18조"]}
    assert mod.classify_articles([busan])["insurance"] == ["제19조"]
    assert mod.classify_articles([fee]) == {"fee": ["제12조"]}
    assert mod.classify_articles([basic]) == {"basic": ["제3조"]}


def test_pet_festival_and_address():
    assert mod.is_pet_festival("2026 경산 반려문화축제")
    assert mod.is_pet_festival("광주 펫크닉")
    assert not mod.is_pet_festival("광주서창억새축제")
    assert mod.region_of_address("경기도 성남시 중원구 성남대로 997") == ("경기도", "성남시")
    assert mod.region_of_address("") == (None, None)


def test_clean_text():
    assert mod.clean("1. 입양비 신청서\r\n\r\n2.  입양자   신분증 ") == "1. 입양비 신청서\n2. 입양자 신분증"


def test_clean_drops_email_address():
    text = mod.clean("- 방문: 보건소\n- 이메일: abc.1@example.go.kr")
    assert text == "- 방문: 보건소\n- 이메일: 담당 부서에 주소 문의"
    assert mod.benefit_facts({"method": text}, ["cost"])["channels"] == ["방문", "이메일"]


def test_benefit_facts_plain_summary():
    ydp = {
        "summary": "영등포구 지정 동물보호센터의 유실·유기동물 입양시 소유자 부담비용 지원(최대 15만원)",
        "target": "영등포구 지정 동물보호센터에서 입양하려는 자로서\n- 동물등록(내장형) 완료\n- 동물사랑배움터 '입양예정자 교육'을 이수한 자",
        "content": "- 소유자 부담비용의 60%, 최대 15만원 지원\n- 지원범위: 질병진단비, 치료비, 예방접종, 중성화수술비, 내장형 동물등록비, 미용비, 펫보험 가입비",
        "deadline": "입양 후 1년 이내. 예산 소진시까지",
        "method": "- 방문: 영등포구보건소\n- 이메일: 예시@ydp.go.kr",
    }
    f = mod.benefit_facts(ydp, ["cost"])
    assert f["headline"] == "입양 비용 지원 최대 15만원"
    assert f["percent"] == 60
    assert f["items"][:4] == ["질병 진단", "치료", "예방접종", "중성화 수술"]
    assert f["conditions"] == ["이 지역 보호센터에서 입양", "내장형 동물등록 완료", "입양 전 교육 이수"]
    assert f["applyWithin"] == "입양 후 1년 안에 신청"
    assert f["channels"] == ["방문", "이메일"]
    assert f["budgetLimited"] is True


def test_benefit_facts_insurance_and_no_amount():
    jeongeup = {"summary": "펫보험 가입비 및 보험료 15만원 지원", "target": "정읍시 동물보호소 내 유기동물을 입양 후 펫 보험에 가입한 자", "content": "", "deadline": "상시신청", "method": "방문신청 및 등기우편"}
    f = mod.benefit_facts(jeongeup, ["insurance"])
    assert f["headline"] == "펫보험 가입비 지원 15만원"
    assert f["items"] == ["펫보험 가입"]
    assert "펫보험 가입 후 신청" in f["conditions"] and f["applyWithin"] == "언제든 신청"
    gyeongnam = {"name": "유기동물 입양장려금 지원", "summary": "시군 동물보호센터의 보호동물 입양 시 입양장려금 제공", "target": "도내 시군 동물보호센터에서 입양하려는 도민으로 동물등록(내장형)을 완료한 입양자", "content": "", "deadline": "입양 후 6개월 이내(시군별 사업량 소진 시까지)", "method": ""}
    g = mod.benefit_facts(gyeongnam, ["cost"])
    assert g["headline"] == "입양장려금" and g["amountMan"] is None
    assert g["conditions"] == ["이 지역 보호센터에서 입양", "내장형 동물등록 완료", "이 지역 주민"]
    assert g["applyWithin"] == "입양 후 6개월 안에 신청"
