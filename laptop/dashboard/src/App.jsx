import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, useMap } from 'react-leaflet'
import './App.css'

const BACKEND_URL = `${window.location.protocol}//${window.location.host}`
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
const JETSON_MJPEG_URL = 'http://172.20.10.10:8080'

/* ── helpers ─────────────────────────────────────────────────── */

function fmtNum(v, digits = 2) {
  if (typeof v !== 'number' || Number.isNaN(v)) return '--'
  return v.toFixed(digits)
}

function fmtCm(v) {
  if (typeof v !== 'number' || v < 0) return '--'
  return String(Math.round(v))
}

function timeAgo(ts) {
  if (!ts) return 'never'
  const diff = Math.floor((Date.now() - ts) / 1000)
  if (diff < 2) return 'just now'
  if (diff < 60) return `${diff}s ago`
  return `${Math.floor(diff / 60)}m ago`
}

/* ── map helper: recenter on sub position ────────────────────── */

function MapRecenter({ lat, lon }) {
  const map = useMap()
  const initialized = useRef(false)

  useEffect(() => {
    if (!initialized.current && lat && lon) {
      map.setView([lat, lon], 17)
      initialized.current = true
    }
    // Invalidate size after mount to fix leaflet rendering in flex layouts
    setTimeout(() => map.invalidateSize(), 200)
  }, [lat, lon, map])

  return null
}

/* ── connection status indicator ─────────────────────────────── */

function ConnectionBadge({ connected, lastUpdate }) {
  return (
    <div className={`conn-badge ${connected ? 'conn-ok' : 'conn-lost'}`}>
      <span className="conn-dot" />
      <span className="conn-label">{connected ? 'CONNECTED' : 'DISCONNECTED'}</span>
      {lastUpdate > 0 && (
        <span className="conn-ts">{timeAgo(lastUpdate)}</span>
      )}
    </div>
  )
}

/* ── gauges ───────────────────────────────────────────────────── */

function HGauge({ label, value = 0, min, max, unit = '%', accent }) {
  const clamped = Math.max(min, Math.min(max, value))
  const pct = ((clamped - min) / (max - min)) * 100
  const left = Math.min(50, pct)
  const right = Math.max(50, pct)

  return (
    <div className="hgauge">
      <span className="hgauge-label">{label}</span>
      <div className="hgauge-track">
        <div className="hgauge-center" />
        <div
          className="hgauge-fill"
          style={{
            left: `${left}%`,
            width: `${right - left}%`,
            background: accent || 'var(--accent)',
          }}
        />
      </div>
      <span className="hgauge-val">
        {Math.round(clamped)}
        <small>{unit}</small>
      </span>
    </div>
  )
}

function BatteryGauge({ voltage = 0 }) {
  const pct = Math.max(0, Math.min(100, ((voltage - 9.5) / (12.6 - 9.5)) * 100))
  const cls = voltage > 11 ? 'bat-good' : voltage > 10 ? 'bat-warn' : 'bat-crit'
  return (
    <div className="hgauge">
      <span className="hgauge-label">BAT</span>
      <div className="hgauge-track">
        <div className={`hgauge-fill-abs ${cls}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="hgauge-val">
        {fmtNum(voltage, 1)}
        <small>V</small>
      </span>
    </div>
  )
}

/* ── depth gauge (vertical) ──────────────────────────────────── */

function DepthGauge({ depth = 0, maxDepth = 5 }) {
  const pct = Math.min(100, Math.max(0, (depth / maxDepth) * 100))
  const cls = depth > 4 ? 'dep-crit' : depth > 2.5 ? 'dep-warn' : 'dep-ok'
  return (
    <div className="depth-gauge">
      <div className="depth-label">DEPTH</div>
      <div className="depth-track">
        <div className={`depth-fill ${cls}`} style={{ height: `${pct}%` }} />
        <div className="depth-marks">
          {[0, 1, 2, 3, 4, 5].map((m) => (
            <span key={m} style={{ bottom: `${(m / maxDepth) * 100}%` }}>
              {m}m
            </span>
          ))}
        </div>
      </div>
      <div className="depth-readout">{fmtNum(depth, 1)}m</div>
    </div>
  )
}

/* ── proximity SVG (top-down) ────────────────────────────────── */

function ProximitySvg({ uss = {} }) {
  const maxRange = 200 // cm
  const dirs = [
    { key: 'front', label: 'F', angle: -90, val: uss.front },
    { key: 'right', label: 'R', angle: 0, val: uss.right },
    { key: 'left', label: 'L', angle: 180, val: uss.left },
    { key: 'top', label: 'T', angle: 90, val: uss.top },
  ]

  const barLen = (v) => {
    if (typeof v !== 'number' || v < 0) return 0
    return Math.min(1, v / maxRange) * 52
  }
  const barColor = (v) => {
    if (typeof v !== 'number' || v < 0) return '#334155'
    if (v < 20) return '#ef4444'
    if (v < 50) return '#f59e0b'
    return '#22d3ee'
  }

  return (
    <div className="prox-wrap">
      <div className="prox-title">PROXIMITY</div>
      <svg viewBox="0 0 160 160" className="prox-svg">
        {/* range rings */}
        <circle cx="80" cy="80" r="55" fill="none" stroke="#1e293b" strokeWidth="1" />
        <circle cx="80" cy="80" r="35" fill="none" stroke="#1e293b" strokeWidth="0.5" />
        {/* sub icon */}
        <ellipse cx="80" cy="80" rx="8" ry="12" fill="#0e7490" stroke="#22d3ee" strokeWidth="1.5" />
        <circle cx="80" cy="72" r="2" fill="#22d3ee" />
        {/* distance bars */}
        {dirs.map((d) => {
          const len = barLen(d.val)
          const rad = (d.angle * Math.PI) / 180
          const x1 = 80 + Math.cos(rad) * 14
          const y1 = 80 + Math.sin(rad) * 14
          const x2 = 80 + Math.cos(rad) * (14 + len)
          const y2 = 80 + Math.sin(rad) * (14 + len)
          const lx = 80 + Math.cos(rad) * 72
          const ly = 80 + Math.sin(rad) * 72
          return (
            <g key={d.key}>
              <line
                x1={x1}
                y1={y1}
                x2={x2}
                y2={y2}
                stroke={barColor(d.val)}
                strokeWidth="5"
                strokeLinecap="round"
              />
              <text x={lx} y={ly} textAnchor="middle" dominantBaseline="central" className="prox-lbl">
                {d.label}:{fmtCm(d.val)}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

/* ── attitude indicator (pitch/roll from IMU) ────────────────── */

function AttitudeIndicator({ imu = {} }) {
  // Derive approximate pitch/roll from accelerometer (static orientation)
  const ax = imu.ax || 0
  const ay = imu.ay || 0
  const az = imu.az || 0
  const pitch = Math.atan2(ax, Math.sqrt(ay * ay + az * az)) * (180 / Math.PI)
  const roll = Math.atan2(ay, Math.sqrt(ax * ax + az * az)) * (180 / Math.PI)

  const horizonY = 50 + pitch * 1.2 // 1.2 px per degree
  return (
    <div className="atti-wrap">
      <div className="atti-title">ATTITUDE</div>
      <svg viewBox="0 0 100 100" className="atti-svg">
        <defs>
          <clipPath id="atti-clip">
            <circle cx="50" cy="50" r="42" />
          </clipPath>
        </defs>
        <g clipPath="url(#atti-clip)" transform={`rotate(${-roll}, 50, 50)`}>
          {/* sky */}
          <rect x="0" y="0" width="100" height={horizonY} fill="#1a3a5c" />
          {/* ground */}
          <rect x="0" y={horizonY} width="100" height={100 - horizonY} fill="#4a3728" />
          {/* horizon line */}
          <line x1="0" y1={horizonY} x2="100" y2={horizonY} stroke="#e2e8f0" strokeWidth="1" />
          {/* pitch lines */}
          {[-20, -10, 10, 20].map((p) => {
            const y = horizonY - p * 1.2
            return (
              <line key={p} x1="35" y1={y} x2="65" y2={y} stroke="#94a3b8" strokeWidth="0.5" />
            )
          })}
        </g>
        {/* fixed aircraft symbol */}
        <line x1="20" y1="50" x2="42" y2="50" stroke="#22d3ee" strokeWidth="2" />
        <line x1="58" y1="50" x2="80" y2="50" stroke="#22d3ee" strokeWidth="2" />
        <circle cx="50" cy="50" r="3" fill="none" stroke="#22d3ee" strokeWidth="1.5" />
        {/* border */}
        <circle cx="50" cy="50" r="42" fill="none" stroke="#334155" strokeWidth="2" />
      </svg>
      <div className="atti-readout">
        P:{fmtNum(pitch, 1)}° R:{fmtNum(roll, 1)}°
      </div>
    </div>
  )
}

/* ── live feed (compact) ─────────────────────────────────────── */

function LiveFeed({ telemetry }) {
  const [feedError, setFeedError] = useState(false)
  return (
    <div className="panel feed-panel">
      <div className="panel-hdr">LIVE FEED</div>
      <div className="feed-body">
        {!feedError ? (
          <img
            src={JETSON_MJPEG_URL}
            alt="Sub camera"
            className="feed-img"
            onError={() => setFeedError(true)}
          />
        ) : (
          <div className="feed-offline">NO SIGNAL</div>
        )}
      </div>
      <div className="feed-footer">
        MODE: {telemetry.mode || 'MANUAL'}
        {telemetry?.detection ? ` | TARGET: ${telemetry.detection}` : ''}
      </div>
    </div>
  )
}

/* ── tactical map ────────────────────────────────────────────── */

function TacticalMap({ contacts, sub, selectedId, pulseIds, onSelectContact }) {
  const statusColor = {
    suspected: '#f59e0b',
    confirmed: '#ff4444',
    tracking: '#ff8c00',
    neutralized: '#22c55e',
  }

  return (
    <div className="panel map-panel">
      <div className="panel-hdr">TACTICAL MAP</div>
      <div className="map-body">
        <MapContainer center={[sub.lat, sub.lon]} zoom={17} className="leaflet-map" zoomControl={false}>
          <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
          <MapRecenter lat={sub.lat} lon={sub.lon} />

          {/* Sub marker with heading line */}
          <CircleMarker
            center={[sub.lat, sub.lon]}
            radius={7}
            pathOptions={{ color: '#22d3ee', fillColor: '#22d3ee', fillOpacity: 0.9, weight: 2 }}
          >
            <Popup>ALBACORE-1</Popup>
          </CircleMarker>

          {/* Contacts */}
          {Object.entries(contacts).map(([id, c]) => (
            <CircleMarker
              key={id}
              center={[c.lat, c.lon]}
              radius={id === selectedId ? 12 : 8}
              pathOptions={{
                color: statusColor[c.status] || '#94a3b8',
                fillColor: statusColor[c.status] || '#94a3b8',
                fillOpacity: 0.7,
                weight: id === selectedId ? 3 : 1.5,
                className: pulseIds[id] ? 'pulse-new' : '',
              }}
              eventHandlers={{ click: () => onSelectContact(id) }}
            >
              <Popup>
                {id}: {c.label}
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
    </div>
  )
}

/* ── threat details ──────────────────────────────────────────── */

function ThreatDetails({ contact, contactId, onCommand }) {
  const statusColors = {
    suspected: 'var(--caution)',
    confirmed: 'var(--danger)',
    tracking: '#ff8c00',
    neutralized: 'var(--success)',
  }

  if (!contact) {
    return (
      <div className="panel threat-panel">
        <div className="panel-hdr">THREAT DETAILS</div>
        <div className="empty-state">Select a contact on the map</div>
      </div>
    )
  }

  return (
    <div className="panel threat-panel">
      <div className="panel-hdr">THREAT DETAILS</div>
      <div className="threat-body">
        {contact.image ? (
          <img
            src={`${BACKEND_URL}/api/captures/${contact.image}`}
            alt={contactId}
            className="threat-img"
          />
        ) : (
          <div className="threat-no-img">NO VISUAL</div>
        )}
        <div className="threat-meta">
          <div className="threat-id">{contactId}</div>
          <div className="threat-label">{contact.label}</div>
          {contact.confidence > 0 && (
            <div className="threat-conf">CONF {(contact.confidence * 100).toFixed(0)}%</div>
          )}
          <div className="threat-pos">
            {fmtNum(contact.lat, 4)}, {fmtNum(contact.lon, 4)}
          </div>
          <div className="threat-status">
            <span className="status-dot" style={{ background: statusColors[contact.status] || '#94a3b8' }} />
            {String(contact.status).toUpperCase()}
          </div>
          {contact.notes && <div className="threat-notes">{contact.notes}</div>}
        </div>
      </div>
      <div className="threat-actions">
        {contact.status !== 'neutralized' && contact.status !== 'tracking' && (
          <button className="btn-action btn-deploy" onClick={() => onCommand({ action: 'deploy', target_id: contactId })}>
            DEPLOY UUV
          </button>
        )}
        {contact.status === 'tracking' && (
          <button className="btn-action btn-neutral" onClick={() => onCommand({ action: 'neutralize', target_id: contactId })}>
            MARK NEUTRALIZED
          </button>
        )}
      </div>
    </div>
  )
}

/* ── alert history ───────────────────────────────────────────── */

function AlertHistory({ alerts }) {
  const listRef = useRef(null)

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = 0
    }
  }, [alerts.length])

  return (
    <div className="panel alert-panel">
      <div className="panel-hdr">
        ALERT LOG
        {alerts.length > 0 && <span className="alert-count">{alerts.length}</span>}
      </div>
      <div className="alert-list" ref={listRef}>
        {alerts.length === 0 && <div className="empty-state">No alerts</div>}
        {alerts.map((a) => (
          <div key={a.toastId} className={`alert-item status-${a.contact.status}`}>
            <span className="alert-time">{new Date(a.ts).toLocaleTimeString()}</span>
            <span className="alert-id">{a.id}</span>
            <span className="alert-label">{a.contact.label}</span>
            <span className="alert-conf">{(a.contact.confidence * 100).toFixed(0)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── toast ────────────────────────────────────────────────────── */

function Toast({ id, contact, onClose, onClick }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 5000)
    return () => clearTimeout(timer)
  }, [onClose])

  return (
    <button className="toast" onClick={onClick}>
      <div className="toast-icon">⚠</div>
      <div className="toast-text">
        <div className="toast-title">NEW CONTACT</div>
        <div className="toast-body">
          {id} — {contact.label} — {(contact.confidence * 100).toFixed(0)}%
        </div>
      </div>
    </button>
  )
}

/* ── main app ────────────────────────────────────────────────── */

function App() {
  const [subs, setSubs] = useState({})
  const [contacts, setContacts] = useState({})
  const [telemetry, setTelemetry] = useState({})
  const [selectedContactId, setSelectedContactId] = useState(null)
  const [toasts, setToasts] = useState([])
  const [alertLog, setAlertLog] = useState([])
  const [pulseIds, setPulseIds] = useState({})
  const [wsConnected, setWsConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(0)

  const MODES = ['MANUAL', 'AUTO', 'WAYPOINT']

  const sub = useMemo(
    () => subs['ALBACORE-1'] || { lat: 39.89, lon: -75.17, mode: telemetry.mode || 'MANUAL' },
    [subs, telemetry.mode],
  )

  const currentMode = telemetry.mode || sub.mode || 'MANUAL'

  /* initial state fetch */
  useEffect(() => {
    fetch(`${BACKEND_URL}/api/state`)
      .then((r) => r.json())
      .then((data) => {
        setSubs(data.subs || {})
        setContacts(data.contacts || {})
      })
      .catch(() => {})
  }, [])

  /* websocket */
  useEffect(() => {
    let ws = null
    let reconnectTimer = null

    const connect = () => {
      ws = new WebSocket(WS_URL)

      ws.onopen = () => setWsConnected(true)

      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data)
        setLastUpdate(Date.now())

        if (msg.type === 'telemetry') {
          setTelemetry(msg)
          setSubs((prev) => {
            const old = prev['ALBACORE-1'] || {}
            return {
              ...prev,
              'ALBACORE-1': {
                ...old,
                telemetry: msg,
                battery_v: msg?.bat?.voltage ?? old.battery_v ?? 0,
                mode: msg.mode || old.mode || 'MANUAL',
              },
            }
          })
        } else if (msg.type === 'new_contact') {
          const { type, id, ...contact } = msg
          setContacts((prev) => ({ ...prev, [id]: contact }))
          const entry = { toastId: crypto.randomUUID(), id, contact, ts: Date.now() }
          setToasts((prev) => (prev.some((t) => t.id === id) ? prev : [...prev, entry]))
          setAlertLog((prev) =>
            prev.some((a) => a.id === id) ? prev : [entry, ...prev].slice(0, 50),
          )
          setPulseIds((prev) => ({ ...prev, [id]: true }))
          setTimeout(() => setPulseIds((prev) => { const n = { ...prev }; delete n[id]; return n }), 3000)
        } else if (msg.type === 'status_change') {
          setContacts((prev) => ({
            ...prev,
            [msg.id]: { ...(prev[msg.id] || {}), status: msg.status },
          }))
        } else if (msg.type === 'mode_change') {
          setTelemetry((prev) => ({ ...prev, mode: msg.mode }))
        }
      }

      ws.onclose = () => {
        setWsConnected(false)
        reconnectTimer = setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (ws) ws.close()
    }
  }, [])

  /* commands */
  const sendCommand = useCallback(async (cmd) => {
    await fetch(`${BACKEND_URL}/api/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cmd),
    })
  }, [])

  const cycleMode = useCallback(() => {
    const idx = MODES.indexOf(currentMode)
    const next = MODES[(idx + 1) % MODES.length]
    sendCommand({ action: 'mode', mode: next.toLowerCase() })
  }, [currentMode, sendCommand])

  const estop = useCallback(() => sendCommand({ action: 'estop' }), [sendCommand])

  /* keyboard shortcuts */
  useEffect(() => {
    const handler = (e) => {
      // Spacebar → ESTOP (unless typing in an input)
      if (e.code === 'Space' && e.target.tagName !== 'INPUT') {
        e.preventDefault()
        estop()
      }
      // M → cycle mode
      if (e.key === 'm' && e.target.tagName !== 'INPUT') {
        cycleMode()
      }
      // Escape → deselect contact
      if (e.key === 'Escape') {
        setSelectedContactId(null)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [estop, cycleMode])

  /* low battery warning */
  const batV = telemetry?.bat?.voltage || 0
  useEffect(() => {
    if (batV > 0 && batV < 10) {
      const entry = {
        toastId: crypto.randomUUID(),
        id: 'SYSTEM',
        contact: { label: `LOW BATTERY: ${fmtNum(batV, 1)}V`, confidence: 0, status: 'confirmed' },
        ts: Date.now(),
      }
      setAlertLog((prev) => [entry, ...prev].slice(0, 50))
    }
  }, [batV < 10])

  return (
    <div className="c2">
      {/* ── top bar ─────────────────────────────────────────── */}
      <header className="topbar">
        <div className="topbar-left">
          <span className="topbar-title">ALBACORE C2</span>
          <span className="topbar-subtitle">MARITIME THREAT RESPONSE</span>
        </div>
        <div className="topbar-right">
          <ConnectionBadge connected={wsConnected} lastUpdate={lastUpdate} />
          <button className="mode-btn" onClick={cycleMode} title="Press M to cycle modes">
            {currentMode}
          </button>
          <button className="estop-btn" onClick={estop} title="Press SPACE for emergency stop">
            E-STOP
          </button>
        </div>
      </header>

      {/* ── main grid ───────────────────────────────────────── */}
      <main className="main-grid">
        {/* LEFT COLUMN: feed + telemetry */}
        <div className="col-left">
          <LiveFeed telemetry={telemetry} />

          <div className="panel telem-panel">
            <div className="panel-hdr">TELEMETRY</div>
            <div className="telem-body">
              <div className="telem-gauges">
                <HGauge label="THR" value={telemetry?.cmd?.thruster || 0} min={-100} max={100} />
                <HGauge label="BOW" value={telemetry?.cmd?.bow || 0} min={-100} max={100} />
                <HGauge label="RUD" value={telemetry?.cmd?.rudder || 0} min={-45} max={45} unit="°" accent="var(--caution)" />
                <BatteryGauge voltage={telemetry?.bat?.voltage || 0} />
              </div>
              <div className="telem-spatial">
                <DepthGauge depth={telemetry?.dep?.depth_m || 0} />
                <ProximitySvg uss={telemetry?.uss || {}} />
                <AttitudeIndicator imu={telemetry?.imu || {}} />
              </div>
            </div>
          </div>
        </div>

        {/* CENTER: map */}
        <div className="col-center">
          <TacticalMap
            contacts={contacts}
            sub={sub}
            selectedId={selectedContactId}
            pulseIds={pulseIds}
            onSelectContact={setSelectedContactId}
          />
        </div>

        {/* RIGHT COLUMN: threat + alerts */}
        <div className="col-right">
          <ThreatDetails
            contact={selectedContactId ? contacts[selectedContactId] : null}
            contactId={selectedContactId}
            onCommand={sendCommand}
          />
          <AlertHistory alerts={alertLog} />
        </div>
      </main>

      {/* ── keyboard hints ──────────────────────────────────── */}
      <footer className="hotkey-bar">
        <span><kbd>SPACE</kbd> E-STOP</span>
        <span><kbd>M</kbd> MODE</span>
        <span><kbd>ESC</kbd> DESELECT</span>
      </footer>

      {/* ── toasts ──────────────────────────────────────────── */}
      <div className="toast-stack">
        {toasts.map((t) => (
          <Toast
            key={t.toastId}
            id={t.id}
            contact={t.contact}
            onClose={() => setToasts((prev) => prev.filter((x) => x.toastId !== t.toastId))}
            onClick={() => {
              setSelectedContactId(t.id)
              setToasts((prev) => prev.filter((x) => x.toastId !== t.toastId))
            }}
          />
        ))}
      </div>
    </div>
  )
}

export default App