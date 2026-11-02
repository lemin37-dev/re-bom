def transform_images(raw):
    # 공고번호가 없으면 이미지를 어떤 공고와 연결할지 알 수 없다.
    desertion_no = raw.get("desertionNo")

    if not desertion_no or not str(desertion_no).strip():
        raise ValueError("desertionNo is required")

    images = []

    # API의 popfile1부터 popfile8까지 확인한다.
    for slot in range(1, 9):
        field = f"popfile{slot}"
        url = raw.get(field)

        # 필드가 없거나 빈 값이면 이미지 레코드를 만들지 않는다.
        if url is None or not str(url).strip():
            continue

        if not isinstance(url, str):
            raise ValueError(f"{field} must be a string")

        # 이미지 한 장을 Silver 이미지 레코드 한 건으로 변환한다.
        images.append({
            "desertion_no": str(desertion_no),
            "image_slot": slot,
            "source_url": url.strip(),
        })

    return images
