# Gemini 모델별 비용 리포트

Cloud Billing 의 "표준 사용 비용" BigQuery 내보내기 테이블에서 Gemini API 금액을 모델별·일별로 모아 HTML 로 만든다.
결제 계정 단위 데이터라 팀원 누가 어느 PC 에서 호출했든 같은 결과를 본다. 반영은 몇 시간 늦다.

## 1. 한 번만: 결제 내보내기 설정 (결제 계정 관리자)
1. 권한: 결제 계정에 Billing Account Administrator 또는 Billing Account Costs Manager, 데이터셋 프로젝트에 BigQuery User.
2. GEMINI_API_KEY 프로젝트와 같은 결제 계정에 연결된 프로젝트에 BigQuery 데이터셋 생성 (예: `billing_export`).
   - 위치는 **US 멀티리전**: 켠 시점의 지난달 1일부터 소급된다. 단일 리전(서울 등)은 소급 없음. 위치는 나중에 못 바꾼다.
   - 테이블 만료는 끈다.
3. 콘솔 → 결제 → 결제 내보내기 → BigQuery 내보내기 → "표준 사용 비용" 사용 설정 → 프로젝트·데이터셋 지정 → 저장.
4. 몇 시간 뒤 `gcp_billing_export_v1_<결제계정ID>` 테이블이 생긴다. 소급분은 최대 5일.
5. 팀원에게 데이터셋 BigQuery Data Viewer + 프로젝트 BigQuery Job User 를 준다.

무료 등급 키로 쓴 분은 금액이 0 이라 의미가 없다.

## 2. 각자 PC
```bash
pip install google-cloud-bigquery python-dotenv
gcloud auth application-default login
```
`.env` 에 테이블 이름을 넣는다 (키 아님, `.env.example` 참고):
```
REBOM_BILLING_TABLE=<project>.<dataset>.gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX
```

## 3. 실행
```bash
python scripts/billing/gemini_cost_report.py            # 이번 달 1일 ~ 오늘 (KST)
python scripts/billing/gemini_cost_report.py --days 7 --open
python scripts/billing/gemini_cost_report.py --start 2026-11-01 --end 2026-11-15 --json
python scripts/billing/gemini_cost_report.py --rows tests/fixtures/billing/gemini_cost_rows.json   # BigQuery 없이 샘플로
```
결과는 `data/billing/gemini_cost_<시작>_<끝>.html` (커밋 대상 아님). 콘솔에도 모델별 합계를 출력한다.

## 참고
- 모델 이름은 SKU 설명에서 뽑는다 (예: `Gemini 3.1 Flash Lite Input Text Tokens` → `Gemini 3.1 Flash Lite`). 형식을 모르는 SKU 는 설명 그대로 한 줄로 나온다.
- 서비스 필터는 `service.description` 에 `Gemini` 가 들어가거나 `Generative Language API` 인 행. 실제 데이터가 들어오면 이름을 한 번 확인한다.
- 쿼리 비용은 테이블 프로젝트로 청구된다. 다른 프로젝트로 내려면 `--project` 또는 `REBOM_BILLING_QUERY_PROJECT`.
