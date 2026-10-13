// Schematic illustrations for the Gmail steps. They imitate the layout of the Google pages in plain black and white.
// To use real screenshots instead, drop images in web/public/guide/ named step1.png, step2.png, step3.png.
import { useState } from 'react'

const W = 520, H = 300
const ink = 'var(--fg)', paper = 'var(--bg)', soft = 'var(--g2)'

function Window({ url, children, label }) {
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <rect x="1.5" y="1.5" width={W - 3} height={H - 3} rx="14" fill={paper} stroke={ink} strokeWidth="3" />
      <path d={`M1.5 38 V16 a14.5 14.5 0 0 1 14.5 -14.5 H${W - 16} a14.5 14.5 0 0 1 14.5 14.5 V38 Z`} fill={ink} />
      {[22, 40, 58].map((x) => <circle key={x} cx={x} cy="20" r="5" fill="none" stroke={paper} strokeWidth="2" />)}
      <rect x="84" y="9" width={W - 110} height="22" rx="11" fill="none" stroke={paper} strokeWidth="1.5" />
      <text x="100" y="25" fontSize="12" fontFamily="ui-monospace,Menlo,monospace" fill={paper}>{url}</text>
      {children}
    </svg>
  )
}

const Line = ({ x, y, w, bold }) => <rect x={x} y={y} width={w} height={bold ? 9 : 6} rx="3" fill={bold ? ink : soft} />
const Badge = ({ x, y, n }) => (<g><circle cx={x} cy={y} r="13" fill={ink} /><text x={x} y={Number(y) + 5} textAnchor="middle" fontSize="14" fontWeight="800" fill={paper} fontFamily="ui-sans-serif,system-ui,sans-serif">{n}</text></g>)
const Arrow = ({ x1, y1, x2, y2 }) => (
  <g stroke={ink} strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round">
    <path d={`M${x1} ${y1} L${x2} ${y2}`} /><path d={`M${x2 - 9} ${y2 - 2} L${x2} ${y2} L${x2 - 3} ${y2 - 10}`} />
  </g>
)

export function Step1Art() {
  return (
    <Window url="myaccount.google.com/security" label="Google Account security page with 2-Step Verification set to On">
      {[0, 1, 2, 3, 4].map((i) => <Line key={i} x="24" y={66 + i * 26} w={i === 1 ? 92 : 70} bold={i === 1} />)}
      <rect x="150" y="62" width="352" height="64" rx="10" fill="none" stroke={soft} strokeWidth="2" />
      <Line x="168" y="80" w="150" /><Line x="168" y="98" w="100" />
      <rect x="150" y="142" width="352" height="74" rx="10" fill="none" stroke={ink} strokeWidth="3.5" />
      <text x="170" y="170" fontSize="15" fontWeight="700" fontFamily="ui-sans-serif,system-ui,sans-serif" fill={ink}>2-Step Verification</text>
      <Line x="170" y="184" w="160" />
      <rect x="418" y="152" width="70" height="30" rx="15" fill={ink} />
      <text x="453" y="172" textAnchor="middle" fontSize="13" fontWeight="700" fontFamily="ui-sans-serif,system-ui,sans-serif" fill={paper}>On</text>
      <rect x="150" y="232" width="352" height="46" rx="10" fill="none" stroke={soft} strokeWidth="2" />
      <Line x="168" y="248" w="120" />
      <Badge x="136" y="146" n="1" />
    </Window>
  )
}

export function Step2Art() {
  return (
    <Window url="myaccount.google.com/apppasswords" label="Google App passwords page: type the name WatchFox and press Create">
      <text x="40" y="76" fontSize="17" fontWeight="800" fontFamily="ui-sans-serif,system-ui,sans-serif" fill={ink}>App passwords</text>
      <Line x="40" y="90" w="300" /><Line x="40" y="104" w="240" />
      <text x="40" y="146" fontSize="12" fontWeight="600" fontFamily="ui-sans-serif,system-ui,sans-serif" fill={ink}>App name</text>
      <rect x="40" y="154" width="300" height="42" rx="9" fill="none" stroke={ink} strokeWidth="3.5" />
      <text x="56" y="181" fontSize="16" fontFamily="ui-monospace,Menlo,monospace" fill={ink}>WatchFox</text>
      <rect x="352" y="154" width="128" height="42" rx="21" fill={ink} />
      <text x="416" y="181" textAnchor="middle" fontSize="15" fontWeight="700" fontFamily="ui-sans-serif,system-ui,sans-serif" fill={paper}>Create</text>
      <Line x="40" y="226" w="200" /><Line x="40" y="244" w="150" />
      <Badge x="28" y="175" n="2" />
      <Arrow x1="440" y1="236" x2="428" y2="204" />
    </Window>
  )
}

export function Step3Art() {
  return (
    <Window url="myaccount.google.com/apppasswords" label="Google shows a 16-letter app password once. Copy it.">
      <rect x="70" y="62" width="380" height="206" rx="14" fill={paper} stroke={ink} strokeWidth="3.5" />
      <text x="96" y="94" fontSize="15" fontWeight="800" fontFamily="ui-sans-serif,system-ui,sans-serif" fill={ink}>Your app password</text>
      <rect x="96" y="112" width="328" height="52" rx="9" fill={soft} />
      <text x="260" y="146" textAnchor="middle" fontSize="21" fontWeight="700" letterSpacing="2" fontFamily="ui-monospace,Menlo,monospace" fill={ink}>abcd efgh ijkl mnop</text>
      <rect x="96" y="112" width="328" height="52" rx="9" fill="none" stroke={ink} strokeWidth="3.5" />
      <Line x="96" y="184" w="250" /><Line x="96" y="200" w="190" />
      <rect x="318" y="224" width="106" height="30" rx="15" fill="none" stroke={ink} strokeWidth="2" />
      <text x="371" y="244" textAnchor="middle" fontSize="13" fontWeight="700" fontFamily="ui-sans-serif,system-ui,sans-serif" fill={ink}>Done</text>
      <Badge x="82" y="138" n="3" />
    </Window>
  )
}

// If you add real screenshots in web/public/guide/, they replace the illustration automatically.
export function GuideImage({ n, Art }) {
  const [broken, setBroken] = useState(false)
  if (broken) return <Art />
  return <img src={`/guide/step${n}.png`} alt="" onError={() => setBroken(true)} style={{ width: '100%', border: '1.5px solid var(--fg)', borderRadius: 14 }} />
}
