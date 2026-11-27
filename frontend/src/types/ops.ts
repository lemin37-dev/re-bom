// 운영 모니터링 화면(/ops) API 응답 모양 (src/rebom/serving/ops_api.py, docs/data_contract.md 16.4)

export type OpsEnv = 'test' | 'prod'
export type StageStatus = 'ok' | 'warn' | 'failed' | 'skipped' | string

export type OpsStage = {
  stage: string
  status: StageStatus
  runId: string | null
  publishId: string | null
  goldRunId: string | null
  startedAt: string | null
  finishedAt: string | null
  inputCount: number | null
  outputCount: number | null
  failedCount: number | null
  stats: Record<string, unknown>
  codeVersion: string | null
}

export type OpsReasonCount = {
  stage: string
  reasonCode: string
  count: number
  blocking: number
  /** 알려진 예외(예: API null 레코드 날짜) */
  knownException: number
  /** 재시도로 결국 받아낸 건 */
  recovered: number
  /** 실제로 봐야 할 건 = count - knownException - recovered */
  actionable: number
}

export type OpsCollection = {
  status: 'ok' | 'aborted' | 'not_run'
  today: string
  lastRunId: string | null
  lastRunAt: string | null
  lastRunStatus: string | null
  warn: boolean
  lastSuccessRunId: string | null
  lastSuccessAt: string | null
}

export type OpsOverview = {
  env: OpsEnv
  collection: OpsCollection
  /** "오늘" = 마지막 수집일 */
  date: string | null
  runId: string | null
  cards: {
    received: number | null
    inserted: number | null
    updated: number | null
    transitions: Record<string, number>
    collectFailures: number
    cleanBlocking: number
    modelFailures: number
    syncFailures: number
  }
  stages: OpsStage[]
  reasons: OpsReasonCount[]
}

export type OpsRunStage = { status: StageStatus; inputCount: number | null; outputCount: number | null; failedCount: number | null }
export type OpsRun = { runId: string; date: string | null; stages: Record<string, OpsRunStage> }
export type OpsRuns = { env: OpsEnv; days: number; runs: OpsRun[] }

export type OpsFailure = {
  id: number | null
  runId: string | null
  publishId: string | null
  stage: string
  subjectType: string
  subjectKey: string
  reasonCode: string
  message: string | null
  attempts: number | null
  blocking: boolean
  knownException: boolean
  recovered: boolean
  evidence: Record<string, unknown>
  evidenceRef: string | null
  observedAt: string | null
}
export type OpsFailures = { env: OpsEnv; runId: string; total: number; items: OpsFailure[] }

export type OpsAnimal = {
  env: OpsEnv
  desertionNo: string
  /** RDS service_notice 한 행 (없으면 서비스에 없음) */
  service: Record<string, string | number | null> | null
  failures: OpsFailure[]
}

// ── 탭2: 문서(DBML) 흐름 ERD ──
export type ErdValue = { value: string; note: string | null }
export type ErdColumn = {
  name: string
  type: string
  note: string | null
  example: string | null
  pk: boolean
  notNull: boolean
  /** 값이 정해진 칼럼(Enum)의 값 목록 */
  values: ErdValue[] | null
}
export type ErdTable = { table: string; layer: string; group: string | null; source: string; note: string | null; columns: ErdColumn[] }
export type ErdGroup = { group: string; layer: string; color: string | null; note: string | null }
export type ErdEnd = { table: string; columns: string[] }
export type ErdRef = {
  from: ErdEnd
  to: ErdEnd
  /** link = 같은 값으로 잇는 처리 연결(Iceberg·S3), fk = RDS 실제 외래키 */
  kind: 'link' | 'fk'
  key: string
  how: string | null
  color: string | null
}
export type OpsErd = { layers: string[]; groups: ErdGroup[]; tables: ErdTable[]; refs: ErdRef[] }

// ── 탭2: 문서 vs 실제 ──
export type OpsSchemaTable = {
  table: string
  layer: string
  source: string
  docColumns: number
  docFingerprint: string
  /** true = 실제 값을 봄, false = 아직 관측 없음, null = 관측 대상 아님(Bronze 파일) */
  observed: boolean | null
  physicalName?: string | null
  schemaId?: number | null
  fingerprint?: string
  versions?: number | null
  observedAt?: string | null
  missingInReal?: string[]
  extraInReal?: string[]
  matches: boolean
}
export type OpsMigration = {
  filename: string
  applied: boolean
  shaMatches: boolean | null
  appliedAt: string | null
  appliedBy: string | null
  how: string | null
}
export type OpsSchema = {
  env: OpsEnv
  rdsVersion: string | null
  tables: OpsSchemaTable[]
  migrations: OpsMigration[]
  pendingMigrations: string[]
}
