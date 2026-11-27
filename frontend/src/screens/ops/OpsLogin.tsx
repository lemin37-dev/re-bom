'use client'

import { type FormEvent, useState } from 'react'
import { OpsError, opsApi } from '@/lib/ops-api'

export function OpsLogin({ onDone }: { onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    try {
      await opsApi.login(password)
      setPassword('')
      onDone()
    } catch (err) {
      const status = err instanceof OpsError ? err.status : 0
      setError(status === 401 ? '비밀번호가 달라요.' : status === 429 ? '여러 번 틀려서 잠시 막혔어요. 10분 뒤 다시 해 주세요.' : err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="ops-card ops-login" onSubmit={submit}>
      <h2>운영 화면 입장</h2>
      <p className="ops-muted">운영 비밀번호를 넣으면 이 브라우저에서 12시간 동안 입장이 유지돼요.</p>
      <label className="ops-field">
        <span>비밀번호</span>
        <input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} autoFocus />
      </label>
      {error && <p className="ops-error" role="alert">{error}</p>}
      <button type="submit" className="ops-btn ops-btn-primary" disabled={!password || busy}>{busy ? '확인 중…' : '입장'}</button>
    </form>
  )
}
