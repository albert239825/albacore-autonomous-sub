import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet'
import './App.css'

const BACKEND_URL = `${window.location.protocol}//${window.location.host}`
const WS_URL = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`
const JETSON_MJPEG_URL = 'http://192.168.0.204:8080'

function fmtNum(v, digits = 2) {
  if (typeof v !== 'number' || Number.isNaN(v)) {
    return '--'
  }
  return v.toFixed(digits)
}

function fmtCm(v) {
  if (typeof v !== 'number' || v < 0) {
    return '--'
  }
  return String(Math.round(v))
}

function GaugeBar({ label, value = 0, min, max, unit = '%' }) {
  const clamped = Math.max(min, Math.min(max, value))
  const pct = ((clamped - min) / (max - min)) * 100
  const left = Math.min(50, pct)
  const right = Math.max(50, pct)

  return (
    <div className="gauge-row">
      <div className="gauge-label">{label}</div>
      <div className="gauge-track">
        <div className="gauge-center" />
        <div className="gauge-fill" style={{ left: `${left}%`, width: `${right - left}%` }} />
      </div>
      <div className="gauge-value">
        {Math.round(clamped)}
        {unit}
      </div>
    </div>
  )
}

function BatteryBar({ voltage = 0 }) {
  const pct = Math.max(0, Math.min(100, ((voltage - 9.5) / (12.6 - 9.5)) * 100))
  const cls = voltage > 11 ? 'good' : voltage > 10 ? 'warn' : 'bad'
  return (
    <div className="gauge-row">
      <div className="gauge-label">BAT</div>
      <div className="gauge-track battery">
        <div className={`gauge-fill ${cls}`} style={{ left: '0%', width: `${pct}%` }} />
      </div>
      <div className="gauge-value">{fmtNum(voltage, 1)}V</div>
    </div>
  )
}

function LiveFeed({ telemetry }) {
  return (
    <section className="panel">
      <div className="panel-header">LIVE FEED</div>
      <div className="panel-body">
        <img
          src={JETSON_MJPEG_URL}
          alt="Sub camera"
          className="live-feed"
          onError={(event) => {
            event.currentTarget.style.display = 'none'
          }}
        />
      </div>
      <div className="panel-footer mono">
        MODE: {telemetry.mode || 'MANUAL'}
        {telemetry?.detection ? ` | TARGET: ${telemetry.detection}` : ''}
      </div>
    </section>
  )
}

function TacticalMap({ contacts, sub, selectedId, pulseIds, onSelectContact }) {
  const statusColor = {
    suspected: '#f59e0b',
    confirmed: '#ff4444',
    tracking: '#ff8c00',
    neutralized: '#22c55e',
  }
  return (
    <section className="panel map-panel">
      <div className="panel-header">TACTICAL MAP</div>
      <div className="map-wrap">
        <MapContainer center={[sub.lat, sub.lon]} zoom={15} className="leaflet-map">
          <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
          <CircleMarker
            center={[sub.lat, sub.lon]}
            radius={8}
            pathOptions={{ color: '#00d4ff', fillColor: '#00d4ff', fillOpacity: 0.8 }}
          >
            <Popup>ALBACORE-1</Popup>
          </CircleMarker>
          {Object.entries(contacts).map(([id, contact]) => (
            <CircleMarker
              key={id}
              center={[contact.lat, contact.lon]}
              radius={id === selectedId ? 14 : 10}
              pathOptions={{
                color: statusColor[contact.status] || '#94a3b8',
                fillColor: statusColor[contact.status] || '#94a3b8',
                fillOpacity: 0.65,
                className: pulseIds[id] ? 'pulse-new' : '',
              }}
              eventHandlers={{
                click: () => onSelectContact(id),
              }}
            >
              <Popup>
                {id}: {contact.label}
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
    </section>
  )
}

function Telemetry({ data }) {
  const cmd = data.cmd || {}
  const bat = data.bat || {}
  const imu = data.imu || {}
  const uss = data.uss || {}
  const dep = data.dep || {}

  return (
    <section className="panel">
      <div className="panel-header">TELEMETRY</div>
      <div className="panel-body">
        <GaugeBar label="THR" value={cmd.thruster || 0} min={-100} max={100} />
        <GaugeBar label="BOW" value={cmd.bow || 0} min={-100} max={100} />
        <GaugeBar label="RUD" value={cmd.rudder || 0} min={-45} max={45} unit="°" />
        <BatteryBar voltage={bat.voltage || 0} />
        <div className="data-row">DEP {fmtNum(dep.depth_m || 0, 1)}m</div>
        <div className="data-row mono">
          IMU ax:{fmtNum(imu.ax)} ay:{fmtNum(imu.ay)} az:{fmtNum(imu.az)}
        </div>
        <div className="data-row mono">
          USS T:{fmtCm(uss.top)} L:{fmtCm(uss.left)} R:{fmtCm(uss.right)} F:{fmtCm(uss.front)}
        </div>
      </div>
    </section>
  )
}

function ThreatDetails({ contact, contactId, onCommand }) {
  const statusColors = {
    suspected: 'var(--caution)',
    confirmed: 'var(--warning)',
    tracking: '#ff8c00',
    neutralized: 'var(--success)',
  }

  if (!contact) {
    return (
      <section className="panel">
        <div className="panel-header">THREAT DETAILS</div>
        <div className="empty-state">Select a contact on the tactical map</div>
      </section>
    )
  }

  return (
    <section className="panel">
      <div className="panel-header">THREAT DETAILS</div>
      <div className="threat-content">
        {contact.image ? (
          <img
            src={`${BACKEND_URL}/api/captures/${contact.image}`}
            alt={contactId}
            className="threat-photo"
          />
        ) : (
          <div className="no-photo">NO VISUAL</div>
        )}
        <div className="threat-meta">
          <div className="threat-id">{contactId}</div>
          <div className="threat-label">{contact.label}</div>
          {contact.confidence > 0 ? (
            <div>Confidence: {(contact.confidence * 100).toFixed(0)}%</div>
          ) : null}
          <div>
            Position: {fmtNum(contact.lat, 4)}, {fmtNum(contact.lon, 4)}
          </div>
          <div>
            Status:{' '}
            <span style={{ color: statusColors[contact.status] || '#94a3b8' }}>
              ● {String(contact.status).toUpperCase()}
            </span>
          </div>
          {contact.notes ? <div className="threat-notes">{contact.notes}</div> : null}
        </div>
      </div>
      <div className="threat-actions">
        {contact.status !== 'neutralized' && contact.status !== 'tracking' ? (
          <button className="btn-deploy" onClick={() => onCommand({ action: 'deploy', target_id: contactId })}>
            DEPLOY UUV
          </button>
        ) : null}
        {contact.status === 'tracking' ? (
          <button
            className="btn-neutralize"
            onClick={() => onCommand({ action: 'neutralize', target_id: contactId })}
          >
            MARK NEUTRALIZED
          </button>
        ) : null}
      </div>
    </section>
  )
}

function Toast({ id, contact, onClose, onClick }) {
  useEffect(() => {
    const timer = setTimeout(onClose, 5000)
    return () => clearTimeout(timer)
  }, [onClose])

  return (
    <button className="toast" onClick={onClick}>
      <div className="toast-icon">!</div>
      <div className="toast-text">
        <div className="toast-title">NEW CONTACT DETECTED</div>
        <div className="toast-body">
          {id} | {contact.label} | {(contact.confidence * 100).toFixed(0)}%
        </div>
      </div>
    </button>
  )
}

function App() {
  const [subs, setSubs] = useState({})
  const [contacts, setContacts] = useState({})
  const [telemetry, setTelemetry] = useState({})
  const [selectedContactId, setSelectedContactId] = useState(null)
  const [toasts, setToasts] = useState([])
  const [pulseIds, setPulseIds] = useState({})

  const sub = useMemo(
    () => subs['ALBACORE-1'] || { lat: 39.89, lon: -75.17, mode: telemetry.mode || 'MANUAL' },
    [subs, telemetry.mode]
  )

  useEffect(() => {
    fetch(`${BACKEND_URL}/api/state`)
      .then((response) => response.json())
      .then((data) => {
        setSubs(data.subs || {})
        setContacts(data.contacts || {})
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    let ws = null
    let reconnectTimer = null

    const connect = () => {
      ws = new WebSocket(WS_URL)
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data)
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
          setToasts((prev) => [...prev, { toastId: `${id}-${Date.now()}`, id, contact }])
          setPulseIds((prev) => ({ ...prev, [id]: true }))
          setTimeout(() => {
            setPulseIds((prev) => {
              const next = { ...prev }
              delete next[id]
              return next
            })
          }, 3000)
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
        reconnectTimer = setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
      }
      if (ws) {
        ws.close()
      }
    }
  }, [])

  const sendCommand = async (cmd) => {
    await fetch(`${BACKEND_URL}/api/command`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cmd),
    })
  }

  return (
    <div className="dashboard">
      <header className="top-bar">
        <div className="top-title">ALBACORE C2 — MARITIME THREAT RESPONSE</div>
        <div className="top-actions">
          <button className="mode-btn" onClick={() => sendCommand({ action: 'manual' })}>
            {telemetry.mode || sub.mode || 'MANUAL'}
          </button>
          <button className="estop-btn" onClick={() => sendCommand({ action: 'estop' })}>
            ESTOP
          </button>
        </div>
      </header>

      <main className="grid">
        <LiveFeed telemetry={telemetry} />
        <TacticalMap
          contacts={contacts}
          sub={sub}
          selectedId={selectedContactId}
          pulseIds={pulseIds}
          onSelectContact={setSelectedContactId}
        />
        <Telemetry data={telemetry} />
        <ThreatDetails
          contact={selectedContactId ? contacts[selectedContactId] : null}
          contactId={selectedContactId}
          onCommand={sendCommand}
        />
      </main>

      <div className="toast-stack">
        {toasts.map((toast) => (
          <Toast
            key={toast.toastId}
            id={toast.id}
            contact={toast.contact}
            onClose={() => setToasts((prev) => prev.filter((t) => t.toastId !== toast.toastId))}
            onClick={() => {
              setSelectedContactId(toast.id)
              setToasts((prev) => prev.filter((t) => t.toastId !== toast.toastId))
            }}
          />
        ))}
      </div>
    </div>
  )
}

export default App
