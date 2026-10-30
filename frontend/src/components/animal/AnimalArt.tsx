import type { Animal } from '@/types/animal'

/** 실제 사진 대신 사용하는 동물 일러스트 */
export function AnimalArt({ animal: a, small = false }: { animal: Animal; small?: boolean }) {
  const pointed = a.id === 3 || a.cat
  const dark = a.id === 4
  return <svg viewBox="0 0 320 200" className={`animal-art ${small ? 'small-art' : ''}`} role="img" aria-label={`${a.color} ${a.kind} 일러스트`}>
    {!small && <><ellipse cx="160" cy="193" rx="79" ry="6" fill={a.ear} opacity=".09" /><path d="M35 165c-7-10-6-18-4-24 7 4 11 12 4 24Zm0 0c2-12 8-16 14-17 0 9-5 15-14 17" fill={a.ear} opacity=".13" /><circle cx="265" cy="66" r="3" fill="white" opacity=".65" /><circle cx="59" cy="67" r="2" fill="white" opacity=".7" /><path d="m252 147 3 3 3-3-3-3Z" fill={a.ear} opacity=".15" /></>}
    <path d="M105 201v-17c0-28 22-46 55-46s55 18 55 46v17" fill={a.coat} />
    <path d="M147 158c-9 10-14 24-13 43h52c1-19-4-33-13-43Z" fill={dark ? '#BE8B58' : '#FFF9EA'} opacity=".36" />
    {pointed ? <><path d="m113 94 5-61 34 38m56 23-6-61-33 38" fill={a.coat} /><path d="m121 70 2-27 18 26m44 0 13-26 3 28" fill="#EBC3AE" /></> : <><ellipse cx="115" cy="100" rx="22" ry="39" transform="rotate(13 115 100)" fill={a.ear} /><ellipse cx="205" cy="100" rx="22" ry="39" transform="rotate(-13 205 100)" fill={a.ear} /></>}
    <ellipse cx="160" cy="106" rx="47" ry="52" fill={a.coat} />
    {dark && <><ellipse cx="141" cy="91" rx="5" ry="4" fill="#BE8B58" /><ellipse cx="179" cy="91" rx="5" ry="4" fill="#BE8B58" /></>}
    {a.cat && <path d="m149 58 3 18m8-19v18m11-16-3 17" stroke={a.ear} strokeWidth="5" strokeLinecap="round" />}
    <ellipse cx="160" cy="127" rx="23" ry="19" fill={dark ? '#BB8957' : '#FFF4DE'} />
    <circle cx="143" cy="105" r="5.3" fill="#302E27" /><circle cx="177" cy="105" r="5.3" fill="#302E27" /><circle cx="144.5" cy="103.5" r="1.7" fill="white" /><circle cx="178.5" cy="103.5" r="1.7" fill="white" />
    <ellipse cx="132" cy="120" rx="6" ry="4" fill="#E2A98D" opacity=".5" /><ellipse cx="188" cy="120" rx="6" ry="4" fill="#E2A98D" opacity=".5" />
    <path d="M153 119c0-5 14-5 14 0 0 4-7 7-7 7s-7-3-7-7Z" fill="#302E27" /><path d="M160 125v7m0-1c-3 5-7 5-10 2m10-2c3 5 7 5 10 2" stroke="#302E27" strokeWidth="1.8" fill="none" strokeLinecap="round" /><path d="M156 136q4 8 8 0" fill="#D98E81" />
    {!small && <><path d="M123 158q37 13 74-1" fill="none" stroke="#95A26F" strokeWidth="5" /><circle cx="160" cy="166" r="5" fill="#D8B16C" /><path d="M160 163v5" stroke="#F8E9BD" strokeWidth="1.3" /></>}
  </svg>
}
