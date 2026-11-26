def parse_breed(raw):
    # 품종 코드는 앞자리 0이 중요하므로 문자열로 보존한다.
    # 기타 축종은 kindNm만으로 실제 동물을 구분하기 어려울 수 있다.
    return {
        "species_code": raw.get("upKindCd"),
        "species_name": raw.get("upKindNm"),
        "breed_code": raw.get("kindCd"),
        "breed_name_raw": raw.get("kindNm"),
        "breed_full_name_raw": raw.get("kindFullNm"),
    }
