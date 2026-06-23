import React, { useState, useEffect, useRef, useCallback } from "react";

/* =================================================================
   RideFleet — Front-end (SIN 142 / Sistemas Distribuidos UFV)
   - Conecta ao back-end real em API_BASE (localhost:8000 via Nginx)
   - Abas: Passageiro (solicitar + acompanhar) e Operador (painel)
   - Status em tempo real por polling
   - Destaque de delegacao (delegated_from / delegated_to)
   - Mapa estilizado (SVG, sem libs externas) + ETA
   - Modo demonstracao embutido (quando o back-end nao responde)
   ================================================================= */

const API_BASE = "http://localhost:8000";
const POLL_MS = 2500;

const STATUS = {
  request:    { label: "Aguardando",          short: "Procurando motorista",     color: "amber",  step: 0, prog: 0.0 },
  match:      { label: "Motorista encontrado", short: "Corrida atribuida",        color: "blue",   step: 1, prog: 0.10 },
  confirm:    { label: "A caminho",            short: "Motorista indo ate voce",  color: "blue",   step: 2, prog: 0.22 },
  in_transit: { label: "Em transito",          short: "Viagem em andamento",      color: "cyan",   step: 3, prog: 0.62 },
  complete:   { label: "Concluida",            short: "Voce chegou ao destino",   color: "green",  step: 4, prog: 1.0 },
  cancelled:  { label: "Cancelada",            short: "Corrida cancelada",        color: "red",    step: -1, prog: 0.0 },
};
const STEPS = ["request", "match", "confirm", "in_transit", "complete"];
const ETA = { request: null, match: 8, confirm: 5, in_transit: 3, complete: 0, cancelled: null };

/* ---------- estilos (control-room dark) ---------- */
const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@500;600;700;800&family=DM+Sans:opsz,wght@9..40,400;9..40,500&family=JetBrains+Mono:wght@400;500;700&display=swap');

.rf * { box-sizing: border-box; }
.rf {
  --bg:#0a0e16; --bg2:#0e1420; --panel:#131b2a; --panel2:#172131;
  --line:#243044; --line2:#2d3b52;
  --txt:#e7eef8; --muted:#8595ad; --faint:#5a6b85;
  --amber:#f6b73c; --blue:#5aa2ff; --cyan:#36d4c4; --green:#3ddc97; --red:#ff5f6e;
  --magenta:#f06ecb;
  font-family:'DM Sans', ui-sans-serif, system-ui, sans-serif;
  color:var(--txt);
  min-height:100vh;
  background:
    radial-gradient(900px 500px at 88% -8%, rgba(246,183,60,.10), transparent 60%),
    radial-gradient(800px 600px at 0% 110%, rgba(90,162,255,.10), transparent 55%),
    linear-gradient(180deg, var(--bg), var(--bg2));
  background-attachment: fixed;
  position:relative;
}
.rf::before{
  content:""; position:fixed; inset:0; pointer-events:none; opacity:.5;
  background-image:linear-gradient(rgba(255,255,255,.022) 1px, transparent 1px),
                   linear-gradient(90deg, rgba(255,255,255,.022) 1px, transparent 1px);
  background-size:34px 34px; mask-image:radial-gradient(circle at 50% 30%, #000 30%, transparent 85%);
}
.rf .wrap{ max-width:1120px; margin:0 auto; padding:22px 20px 64px; position:relative; }

.rf h1,.rf h2,.rf h3,.rf .display{ font-family:'Archivo', sans-serif; letter-spacing:-.02em; }
.rf .mono{ font-family:'JetBrains Mono', ui-monospace, monospace; }

/* topbar */
.rf .top{ display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:18px; }
.rf .brand{ display:flex; align-items:center; gap:13px; }
.rf .logo{ width:42px; height:42px; border-radius:12px; display:grid; place-items:center;
  background:linear-gradient(145deg,#f6b73c,#f0903c); box-shadow:0 6px 22px rgba(246,183,60,.35); }
.rf .logo svg{ width:24px; height:24px; }
.rf .brand b{ font-family:'Archivo'; font-weight:800; font-size:21px; display:block; line-height:1; }
.rf .brand span{ color:var(--faint); font-size:11.5px; letter-spacing:.14em; text-transform:uppercase; }

/* health pill */
.rf .health{ display:flex; align-items:center; gap:14px; background:var(--panel);
  border:1px solid var(--line); border-radius:13px; padding:8px 14px; }
.rf .hstat{ display:flex; align-items:center; gap:8px; font-weight:600; font-size:13.5px; }
.rf .dot{ width:9px; height:9px; border-radius:50%; box-shadow:0 0 0 4px rgba(255,255,255,.04); }
.rf .dot.live{ background:var(--green); animation:pulse 1.8s infinite; }
.rf .dot.warn{ background:var(--amber); }
.rf .dot.down{ background:var(--red); }
@keyframes pulse{ 0%,100%{ box-shadow:0 0 0 0 rgba(61,220,151,.5);} 50%{ box-shadow:0 0 0 7px rgba(61,220,151,0);} }
.rf .hsep{ width:1px; height:22px; background:var(--line2); }
.rf .hmetric{ font-size:11.5px; color:var(--muted); display:flex; flex-direction:column; line-height:1.25; }
.rf .hmetric b{ font-family:'JetBrains Mono'; color:var(--txt); font-size:14px; }

/* tabs */
.rf .tabs{ display:inline-flex; background:var(--panel); border:1px solid var(--line); border-radius:13px; padding:5px; gap:4px; margin-bottom:20px; }
.rf .tab{ border:0; background:transparent; color:var(--muted); font-family:'Archivo'; font-weight:600; font-size:14px;
  padding:9px 20px; border-radius:9px; cursor:pointer; transition:.18s; }
.rf .tab:hover{ color:var(--txt); }
.rf .tab.on{ background:linear-gradient(160deg,var(--panel2),#0f1726); color:var(--txt);
  box-shadow:inset 0 0 0 1px var(--line2), 0 4px 14px rgba(0,0,0,.3); }

/* layout */
.rf .grid{ display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.15fr); gap:18px; }
@media(max-width:840px){ .rf .grid{ grid-template-columns:1fr; } }

.rf .card{ background:linear-gradient(180deg,var(--panel),var(--bg2)); border:1px solid var(--line);
  border-radius:18px; padding:22px; position:relative; overflow:hidden; }
.rf .card .corner{ position:absolute; top:0; right:0; width:64px; height:64px;
  background:radial-gradient(circle at 100% 0, rgba(246,183,60,.16), transparent 70%); }
.rf .ctitle{ font-family:'Archivo'; font-weight:700; font-size:13px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--faint); margin:0 0 16px; display:flex; align-items:center; gap:9px; }
.rf .ctitle::before{ content:""; width:18px; height:2px; background:var(--amber); border-radius:2px; }

/* form */
.rf label{ display:block; font-size:12px; color:var(--muted); margin:0 0 6px; font-weight:500; letter-spacing:.02em; }
.rf .field{ margin-bottom:14px; }
.rf input, .rf select{ width:100%; background:#0c121d; border:1px solid var(--line2); color:var(--txt);
  border-radius:11px; padding:12px 14px; font-size:14.5px; font-family:'DM Sans'; outline:none; transition:.16s; }
.rf input:focus, .rf select:focus{ border-color:var(--amber); box-shadow:0 0 0 3px rgba(246,183,60,.15); }
.rf input::placeholder{ color:var(--faint); }
.rf .row2{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }

.rf .btn{ width:100%; border:0; cursor:pointer; font-family:'Archivo'; font-weight:700; font-size:15px;
  padding:14px; border-radius:12px; color:#1a1206; background:linear-gradient(145deg,#f6b73c,#f0973c);
  box-shadow:0 8px 24px rgba(246,183,60,.3); transition:.16s; letter-spacing:.01em; }
.rf .btn:hover{ transform:translateY(-1px); box-shadow:0 12px 30px rgba(246,183,60,.42); }
.rf .btn:active{ transform:translateY(0); }
.rf .btn:disabled{ opacity:.55; cursor:not-allowed; transform:none; box-shadow:none; }
.rf .btn.ghost{ background:transparent; color:var(--muted); box-shadow:inset 0 0 0 1px var(--line2); }
.rf .btn.ghost:hover{ color:var(--txt); box-shadow:inset 0 0 0 1px var(--amber); }
.rf .btn.sm{ width:auto; padding:8px 14px; font-size:13px; border-radius:9px; }

/* origin/dest pins in form */
.rf .endpoints{ position:relative; padding-left:26px; }
.rf .endpoints::before{ content:""; position:absolute; left:8px; top:14px; bottom:46px; width:2px;
  background:linear-gradient(var(--green),var(--amber)); border-radius:2px; }
.rf .pin{ position:absolute; left:2px; width:14px; height:14px; border-radius:50%; }
.rf .pin.o{ top:10px; background:var(--green); box-shadow:0 0 0 3px rgba(61,220,151,.2); }
.rf .pin.d{ bottom:42px; background:var(--amber); box-shadow:0 0 0 3px rgba(246,183,60,.2); }

/* map */
.rf .map{ width:100%; height:266px; border-radius:14px; overflow:hidden; border:1px solid var(--line2);
  background:radial-gradient(circle at 30% 20%, #16243b, #0a111d); position:relative; }
.rf .maptag{ position:absolute; top:10px; left:12px; font-family:'JetBrains Mono'; font-size:10.5px;
  color:var(--faint); letter-spacing:.1em; text-transform:uppercase; }
.rf .eta{ position:absolute; top:10px; right:12px; background:rgba(10,16,26,.8); backdrop-filter:blur(6px);
  border:1px solid var(--line2); border-radius:10px; padding:6px 11px; text-align:right; }
.rf .eta small{ color:var(--faint); font-size:10px; letter-spacing:.08em; text-transform:uppercase; display:block; }
.rf .eta b{ font-family:'JetBrains Mono'; font-size:17px; color:var(--cyan); }
.rf .route{ stroke:var(--blue); stroke-width:3; fill:none; stroke-linecap:round;
  stroke-dasharray:7 9; animation:flow 1s linear infinite; opacity:.85; }
@keyframes flow{ to{ stroke-dashoffset:-16; } }
.rf .routebg{ stroke:#22304a; stroke-width:7; fill:none; stroke-linecap:round; }
.rf .car{ transition:transform 1.1s cubic-bezier(.4,0,.2,1); }

/* stepper */
.rf .steps{ display:flex; justify-content:space-between; margin:4px 0 20px; position:relative; }
.rf .steps::before{ content:""; position:absolute; left:7%; right:7%; top:11px; height:2px; background:var(--line2); }
.rf .steps .barfill{ position:absolute; left:7%; top:11px; height:2px; background:linear-gradient(90deg,var(--green),var(--cyan));
  transition:width .6s ease; border-radius:2px; }
.rf .stp{ position:relative; z-index:1; display:flex; flex-direction:column; align-items:center; gap:7px; width:20%; }
.rf .stp .bub{ width:24px; height:24px; border-radius:50%; background:var(--panel); border:2px solid var(--line2);
  display:grid; place-items:center; font-size:11px; font-family:'JetBrains Mono'; color:var(--faint); transition:.3s; }
.rf .stp.done .bub{ background:var(--green); border-color:var(--green); color:#06231a; }
.rf .stp.now .bub{ background:var(--cyan); border-color:var(--cyan); color:#04231f; animation:pop 1.6s infinite; }
@keyframes pop{ 0%,100%{ box-shadow:0 0 0 0 rgba(54,212,196,.5);} 50%{ box-shadow:0 0 0 8px rgba(54,212,196,0);} }
.rf .stp small{ font-size:10px; color:var(--faint); text-align:center; line-height:1.2; max-width:74px; }
.rf .stp.done small,.rf .stp.now small{ color:var(--muted); }

/* status hero */
.rf .hero{ display:flex; align-items:center; gap:14px; margin-bottom:18px; }
.rf .heroicon{ width:52px; height:52px; border-radius:14px; display:grid; place-items:center; flex:none; }
.rf .hero h2{ margin:0; font-size:23px; }
.rf .hero p{ margin:2px 0 0; color:var(--muted); font-size:13.5px; }

/* delegation banner */
.rf .deleg{ display:flex; align-items:center; gap:12px; padding:13px 15px; border-radius:13px; margin-bottom:16px;
  background:linear-gradient(120deg, rgba(240,110,203,.14), rgba(90,162,255,.10));
  border:1px solid rgba(240,110,203,.4); }
.rf .deleg .di{ width:34px; height:34px; border-radius:10px; background:rgba(240,110,203,.18); display:grid; place-items:center; flex:none; }
.rf .deleg b{ font-family:'Archivo'; font-size:14px; color:var(--magenta); display:block; }
.rf .deleg span{ font-size:12.5px; color:var(--muted); }

/* chips */
.rf .chip{ display:inline-flex; align-items:center; gap:6px; font-size:11.5px; font-weight:600; padding:4px 10px;
  border-radius:20px; font-family:'JetBrains Mono'; letter-spacing:.02em; }
.rf .chip i{ width:7px; height:7px; border-radius:50%; }
.rf .c-amber{ background:rgba(246,183,60,.14); color:var(--amber); } .rf .c-amber i{ background:var(--amber); }
.rf .c-blue{ background:rgba(90,162,255,.14); color:var(--blue); } .rf .c-blue i{ background:var(--blue); }
.rf .c-cyan{ background:rgba(54,212,196,.14); color:var(--cyan); } .rf .c-cyan i{ background:var(--cyan); }
.rf .c-green{ background:rgba(61,220,151,.14); color:var(--green); } .rf .c-green i{ background:var(--green); }
.rf .c-red{ background:rgba(255,95,110,.14); color:var(--red); } .rf .c-red i{ background:var(--red); }

/* info rows */
.rf .info{ display:grid; grid-template-columns:1fr 1fr; gap:11px; margin-top:6px; }
.rf .ib{ background:#0c121d; border:1px solid var(--line); border-radius:11px; padding:11px 13px; }
.rf .ib small{ color:var(--faint); font-size:10.5px; letter-spacing:.07em; text-transform:uppercase; }
.rf .ib div{ font-family:'JetBrains Mono'; font-size:13px; margin-top:3px; word-break:break-all; }

/* KPIs */
.rf .kpis{ display:grid; grid-template-columns:repeat(5,1fr); gap:12px; margin-bottom:18px; }
@media(max-width:720px){ .rf .kpis{ grid-template-columns:repeat(2,1fr); } }
.rf .kpi{ background:linear-gradient(180deg,var(--panel),var(--bg2)); border:1px solid var(--line); border-radius:15px; padding:16px; }
.rf .kpi .v{ font-family:'Archivo'; font-weight:800; font-size:30px; line-height:1; }
.rf .kpi .k{ color:var(--faint); font-size:11px; letter-spacing:.08em; text-transform:uppercase; margin-top:7px; }

/* table */
.rf .tbl{ width:100%; border-collapse:collapse; }
.rf .tbl th{ text-align:left; font-size:10.5px; color:var(--faint); letter-spacing:.1em; text-transform:uppercase;
  padding:9px 12px; border-bottom:1px solid var(--line); font-weight:600; }
.rf .tbl td{ padding:11px 12px; border-bottom:1px solid var(--line); font-size:13px; vertical-align:middle; }
.rf .tbl tr:hover td{ background:rgba(255,255,255,.02); }
.rf .tbl .mono{ font-size:12px; color:var(--muted); }

/* banner / toasts */
.rf .banner{ display:flex; align-items:center; gap:12px; padding:13px 16px; border-radius:13px; margin-bottom:18px;
  background:rgba(255,95,110,.10); border:1px solid rgba(255,95,110,.4); font-size:13.5px; }
.rf .banner.ok{ background:rgba(61,220,151,.08); border-color:rgba(61,220,151,.35); }
.rf .banner b{ color:var(--txt); }
.rf .empty{ text-align:center; padding:34px 16px; color:var(--faint); }
.rf .empty svg{ opacity:.4; margin-bottom:10px; }
.rf .fade{ animation:fade .5s ease both; }
@keyframes fade{ from{ opacity:0; transform:translateY(8px);} to{ opacity:1; transform:none;} }
.rf .err{ color:var(--red); font-size:12.5px; margin-top:8px; min-height:16px; }
.rf .spin{ display:inline-block; width:15px; height:15px; border:2px solid rgba(255,255,255,.25);
  border-top-color:#1a1206; border-radius:50%; animation:sp .7s linear infinite; vertical-align:-2px; }
@keyframes sp{ to{ transform:rotate(360deg);} }
.rf .demobadge{ display:inline-flex; align-items:center; gap:6px; font-size:11px; color:var(--magenta);
  background:rgba(240,110,203,.12); border:1px solid rgba(240,110,203,.3); padding:3px 9px; border-radius:20px; font-weight:600; }
`;

/* ---------- icones inline ---------- */
const Ico = {
  car: (p) => (<svg viewBox="0 0 24 24" fill="none" {...p}><path d="M5 11l1.5-4.5A2 2 0 018.4 5h7.2a2 2 0 011.9 1.5L19 11m-14 0h14m-14 0a2 2 0 00-2 2v3h2m14-5a2 2 0 012 2v3h-2m-3 0H8m9 0a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0zm-9 0a1.5 1.5 0 11-3 0 1.5 1.5 0 013 0z" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>),
  pin: (p) => (<svg viewBox="0 0 24 24" fill="none" {...p}><path d="M12 21s7-5.5 7-11a7 7 0 10-14 0c0 5.5 7 11 7 11z" stroke="currentColor" strokeWidth="1.6"/><circle cx="12" cy="10" r="2.4" stroke="currentColor" strokeWidth="1.6"/></svg>),
  swap: (p) => (<svg viewBox="0 0 24 24" fill="none" {...p}><path d="M8 3v13m0 0l-3-3m3 3l3-3M16 21V8m0 0l3 3m-3-3l-3 3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"/></svg>),
  check: (p) => (<svg viewBox="0 0 24 24" fill="none" {...p}><path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/></svg>),
};

/* ===========================================================
   MODO DEMO — store em memoria (quando back-end offline)
   =========================================================== */
function makeDemo() {
  const rides = new Map();
  let n = 1;
  const now = () => new Date().toISOString();
  const groups = ["grupo-beta", "grupo-gamma", "movetech"];
  function seed() {
    const base = [
      { origin: "Centro, Vicosa", destination: "UFV Campus", status: "in_transit", driver_id: "drv-ana-01", from: null },
      { origin: "Rod. MG-280", destination: "Bairro Bela Vista", status: "complete", driver_id: "drv-leo-04", from: "grupo-beta" },
      { origin: "Av. PH Rolfs", destination: "Terminal", status: "match", driver_id: "drv-rui-02", from: null },
    ];
    base.forEach((b) => {
      const id = `demo-${String(n++).padStart(4, "0")}-${Math.random().toString(16).slice(2, 6)}`;
      rides.set(id, { id, passenger_id: "demo-pax", origin: b.origin, destination: b.destination,
        status: b.status, driver_id: b.driver_id, delegated_from: b.from, delegated_to: null,
        created_at: now(), updated_at: now() });
    });
  }
  seed();
  // progressao automatica das corridas em transito
  setInterval(() => {
    rides.forEach((r) => {
      if (r.status === "in_transit" && Math.random() < 0.25) { r.status = "complete"; r.updated_at = now(); }
      else if (r.status === "confirm" && Math.random() < 0.3) { r.status = "in_transit"; r.updated_at = now(); }
      else if (r.status === "match" && Math.random() < 0.3) { r.status = "confirm"; r.updated_at = now(); }
    });
  }, 3000);
  return {
    createPassenger: () => ({ id: "demo-pax-" + Math.random().toString(16).slice(2, 8) }),
    createRide: (b) => {
      const id = `demo-${String(n++).padStart(4, "0")}-${Math.random().toString(16).slice(2, 6)}`;
      const delegated = Math.random() < 0.4;
      const r = { id, passenger_id: b.passenger_id, origin: b.origin, destination: b.destination,
        status: "request", driver_id: null, delegated_from: null,
        delegated_to: delegated ? groups[Math.floor(Math.random() * groups.length)] : null,
        created_at: now(), updated_at: now() };
      rides.set(id, r);
      // simula ciclo de vida
      setTimeout(() => { const x = rides.get(id); if (x) { x.status = "match"; x.driver_id = "drv-demo-" + n;
        if (delegated) { x.delegated_from = x.delegated_to; x.delegated_to = null; } x.updated_at = now(); } }, 3500);
      setTimeout(() => { const x = rides.get(id); if (x && x.status === "match") { x.status = "confirm"; x.updated_at = now(); } }, 7000);
      setTimeout(() => { const x = rides.get(id); if (x && x.status === "confirm") { x.status = "in_transit"; x.updated_at = now(); } }, 10500);
      setTimeout(() => { const x = rides.get(id); if (x && x.status === "in_transit") { x.status = "complete"; x.updated_at = now(); } }, 15000);
      return r;
    },
    getRide: (id) => rides.get(id) || null,
    listRides: () => Array.from(rides.values()).sort((a, b) => b.created_at.localeCompare(a.created_at)),
    health: () => {
      const list = Array.from(rides.values());
      const active = list.filter((r) => !["complete", "cancelled"].includes(r.status)).length;
      return { status: "UP", available_drivers: 6, queue: { inbox: active, outbox: 1 }, latency_ms: 7.2 };
    },
    overflow: () => ({ should_delegate: false, available_drivers: 6, queue: { inbox: 2, outbox: 1 } }),
  };
}

/* =========================================================== */
export default function App() {
  const [tab, setTab] = useState("passenger");
  const [online, setOnline] = useState(null); // null=desconhecido, true/false
  const [demo, setDemo] = useState(false);
  const demoRef = useRef(null);
  const [health, setHealth] = useState(null);

  // injeta CSS uma vez
  useEffect(() => {
    if (document.getElementById("rf-css")) return;
    const s = document.createElement("style"); s.id = "rf-css"; s.textContent = CSS; document.head.appendChild(s);
  }, []);

  // cliente de API com fallback p/ demo
  const api = useCallback(async (path, opts) => {
    if (demo) {
      const d = demoRef.current;
      if (path === "/health") return d.health();
      if (path === "/rides/overflow/check") return d.overflow();
      if (path === "/rides/" && (!opts || opts.method !== "POST")) return d.listRides();
      if (path === "/passengers/" && opts?.method === "POST") return d.createPassenger();
      if (path === "/rides/" && opts?.method === "POST") return d.createRide(JSON.parse(opts.body));
      if (path.startsWith("/rides/")) return d.getRide(path.split("/")[2]);
      return null;
    }
    const res = await fetch(API_BASE + path, {
      ...opts, headers: { "Content-Type": "application/json", ...(opts?.headers || {}) },
    });
    setOnline(true);
    if (!res.ok) { const t = await res.text().catch(() => ""); throw new Error(`${res.status} ${t.slice(0, 120)}`); }
    if (res.status === 204) return null;
    return res.json();
  }, [demo]);

  // health polling
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try { const h = await api("/health"); if (!stop) { setHealth(h); setOnline(true); } }
      catch { if (!stop) { setOnline(false); setHealth(null); } }
    };
    tick();
    const iv = setInterval(tick, POLL_MS);
    return () => { stop = true; clearInterval(iv); };
  }, [api]);

  const activateDemo = () => { demoRef.current = makeDemo(); setDemo(true); setOnline(true); };

  const hStatus = health?.status || (online === false ? "DOWN" : "...");
  const hClass = hStatus === "UP" ? "live" : hStatus === "DEGRADED" ? "warn" : "down";

  return (
    <div className="rf">
      <div className="wrap">
        {/* topbar */}
        <div className="top">
          <div className="brand">
            <div className="logo">{Ico.car({ stroke: "#1a1206" })}</div>
            <div>
              <b>RideFleet</b>
              <span>SIN 142 · Sistemas Distribuidos</span>
            </div>
          </div>
          <div className="health">
            <div className="hstat"><span className={`dot ${hClass}`} />{hStatus}</div>
            <div className="hsep" />
            <div className="hmetric">motoristas<b>{health?.available_drivers ?? "—"}</b></div>
            <div className="hmetric">inbox<b>{health?.queue?.inbox ?? "—"}</b></div>
            <div className="hmetric">outbox<b>{health?.queue?.outbox ?? "—"}</b></div>
            {demo && <><div className="hsep" /><span className="demobadge">● DEMO</span></>}
          </div>
        </div>

        {/* banner de conexao */}
        {online === false && !demo && (
          <div className="banner fade">
            <span className="dot down" />
            <div style={{ flex: 1 }}>
              <b>Sem conexao com o back-end</b> em {API_BASE}. Verifique se o RideFleet esta no ar
              (e o CORS habilitado). Ou explore a interface no modo demonstracao.
            </div>
            <button className="btn sm ghost" onClick={activateDemo}>Ativar demo</button>
          </div>
        )}

        {/* tabs */}
        <div className="tabs">
          <button className={`tab ${tab === "passenger" ? "on" : ""}`} onClick={() => setTab("passenger")}>Passageiro</button>
          <button className={`tab ${tab === "operator" ? "on" : ""}`} onClick={() => setTab("operator")}>Operador</button>
        </div>

        {tab === "passenger"
          ? <Passenger api={api} demo={demo} />
          : <Operator api={api} demo={demo} health={health} />}
      </div>
    </div>
  );
}

/* =================== PASSAGEIRO =================== */
function Passenger({ api, demo }) {
  const [form, setForm] = useState({ name: "", phone: "", origin: "", destination: "" });
  const [ride, setRide] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    setErr("");
    if (!form.name || !form.origin || !form.destination) { setErr("Preencha nome, origem e destino."); return; }
    setLoading(true);
    try {
      const pax = await api("/passengers/", { method: "POST", body: JSON.stringify({ name: form.name, phone: form.phone || "000000000" }) });
      const r = await api("/rides/", { method: "POST", body: JSON.stringify({ passenger_id: pax.id, origin: form.origin, destination: form.destination }) });
      setRide(r);
    } catch (e) { setErr("Falha ao solicitar: " + e.message); }
    finally { setLoading(false); }
  };

  // polling do status da corrida ativa
  useEffect(() => {
    if (!ride) return;
    let stop = false;
    const tick = async () => {
      try { const r = await api("/rides/" + ride.id); if (!stop && r) setRide(r); } catch { /* mantem ultimo */ }
    };
    const iv = setInterval(tick, POLL_MS);
    return () => { stop = true; clearInterval(iv); };
  }, [ride?.id, api]);

  if (ride) return <Tracking ride={ride} onNew={() => { setRide(null); setForm({ name: "", phone: "", origin: "", destination: "" }); }} />;

  return (
    <div className="grid fade">
      <div className="card">
        <div className="corner" />
        <div className="ctitle">Solicitar corrida</div>
        <div className="field"><label>Seu nome</label>
          <input value={form.name} placeholder="Ex.: Maria Souza" onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div className="field"><label>Telefone (opcional)</label>
          <input value={form.phone} placeholder="(31) 90000-0000" onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
        <div className="endpoints">
          <span className="pin o" /><span className="pin d" />
          <div className="field"><label>Origem</label>
            <input value={form.origin} placeholder="De onde voce vai sair" onChange={(e) => setForm({ ...form, origin: e.target.value })} /></div>
          <div className="field"><label>Destino</label>
            <input value={form.destination} placeholder="Para onde voce vai" onChange={(e) => setForm({ ...form, destination: e.target.value })} /></div>
        </div>
        <button className="btn" onClick={submit} disabled={loading}>
          {loading ? <><span className="spin" /> Solicitando…</> : "Solicitar corrida"}</button>
        <div className="err">{err}</div>
      </div>

      <div className="card">
        <div className="ctitle">Como funciona</div>
        <Step n="1" t="Voce solicita" d="Informe origem e destino. A corrida entra no sistema com status request." />
        <Step n="2" t="Busca e leilao" d="Se houver motorista local, ele atende. Se nao, a corrida e delegada a outro grupo via Core (leilao)." />
        <Step n="3" t="Acompanhamento" d="O status atualiza em tempo real: a caminho, em transito, concluida — com ETA e mapa." />
        <Step n="4" t="Delegacao visivel" d="Se outro grupo atender, voce ve claramente de qual servico veio o motorista." />
        <div style={{ marginTop: 14, fontSize: 12, color: "var(--faint)" }}>
          {demo ? "Modo demonstracao ativo — dados simulados." : `Conectado a ${API_BASE}`}
        </div>
      </div>
    </div>
  );
}

function Step({ n, t, d }) {
  return (
    <div style={{ display: "flex", gap: 13, marginBottom: 15 }}>
      <div style={{ width: 28, height: 28, flex: "none", borderRadius: 9, background: "var(--panel2)",
        border: "1px solid var(--line2)", display: "grid", placeItems: "center",
        fontFamily: "'JetBrains Mono'", fontSize: 13, color: "var(--amber)" }}>{n}</div>
      <div><div style={{ fontFamily: "'Archivo'", fontWeight: 600, fontSize: 14.5 }}>{t}</div>
        <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 2 }}>{d}</div></div>
    </div>
  );
}

/* =================== ACOMPANHAMENTO =================== */
function Tracking({ ride, onNew }) {
  const st = STATUS[ride.status] || STATUS.request;
  const stepIdx = st.step;
  const eta = ETA[ride.status];

  return (
    <div className="grid fade">
      {/* coluna esquerda: status + mapa */}
      <div className="card">
        <div className="corner" />
        <div className="hero">
          <div className="heroicon" style={{ background: heroBg(st.color) }}>
            {ride.status === "complete" ? Ico.check({ stroke: "#06231a", width: 26, height: 26 }) : Ico.car({ stroke: tone(st.color), width: 26, height: 26 })}
          </div>
          <div><h2>{st.label}</h2><p>{st.short}</p></div>
        </div>

        {ride.status !== "cancelled" && (
          <div className="steps">
            <div className="barfill" style={{ width: `${Math.max(0, stepIdx) / 4 * 86}%` }} />
            {STEPS.map((s, i) => (
              <div key={s} className={`stp ${i < stepIdx ? "done" : ""} ${i === stepIdx ? "now" : ""}`}>
                <div className="bub">{i < stepIdx ? "✓" : i + 1}</div>
                <small>{STATUS[s].label}</small>
              </div>
            ))}
          </div>
        )}

        <Map status={ride.status} prog={st.prog} eta={eta} />
      </div>

      {/* coluna direita: detalhes */}
      <div className="card">
        <div className="ctitle">Detalhes da corrida</div>

        {ride.delegated_from && (
          <div className="deleg">
            <div className="di">{Ico.swap({ stroke: "var(--magenta)", width: 18, height: 18 })}</div>
            <div><b>Corrida delegada</b>
              <span>Seu motorista veio do servico <b style={{ color: "var(--txt)" }} className="mono">{ride.delegated_from}</b> via Core.</span></div>
          </div>
        )}
        {ride.delegated_to && !ride.delegated_from && (
          <div className="deleg" style={{ background: "linear-gradient(120deg, rgba(246,183,60,.12), rgba(90,162,255,.08))", borderColor: "rgba(246,183,60,.4)" }}>
            <div className="di" style={{ background: "rgba(246,183,60,.16)" }}>{Ico.swap({ stroke: "var(--amber)", width: 18, height: 18 })}</div>
            <div><b style={{ color: "var(--amber)" }}>Delegada para fora</b>
              <span>Encaminhada ao servico <b style={{ color: "var(--txt)" }} className="mono">{ride.delegated_to}</b> (overflow).</span></div>
          </div>
        )}

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span className={`chip c-${st.color}`}><i />{ride.status}</span>
          {eta != null && ride.status !== "complete" && <span style={{ color: "var(--faint)", fontSize: 12.5 }}>ETA estimado {eta} min</span>}
        </div>

        <div className="endpoints" style={{ margin: "16px 0 8px" }}>
          <span className="pin o" /><span className="pin d" />
          <div className="field" style={{ marginBottom: 18 }}><label>Origem</label>
            <div style={{ fontSize: 14.5 }}>{ride.origin}</div></div>
          <div className="field" style={{ marginBottom: 0 }}><label>Destino</label>
            <div style={{ fontSize: 14.5 }}>{ride.destination}</div></div>
        </div>

        <div className="info">
          <div className="ib"><small>Corrida</small><div>{shortId(ride.id)}</div></div>
          <div className="ib"><small>Motorista</small><div>{ride.driver_id ? shortId(ride.driver_id) : "—"}</div></div>
          <div className="ib"><small>Atualizado</small><div>{fmtTime(ride.updated_at)}</div></div>
          <div className="ib"><small>Origem da corrida</small><div>{ride.delegated_from || "local"}</div></div>
        </div>

        <button className="btn ghost" style={{ marginTop: 18 }} onClick={onNew}>Solicitar outra corrida</button>
      </div>
    </div>
  );
}

/* mapa estilizado (SVG) */
function Map({ status, prog, eta }) {
  const pathRef = useRef(null);
  const [pt, setPt] = useState({ x: 60, y: 200 });
  const [liveProg, setLiveProg] = useState(prog);

  // em transito, anima a posicao continuamente
  useEffect(() => {
    setLiveProg(prog);
    if (status !== "in_transit") return;
    let p = prog, raf;
    const loop = () => { p += 0.0016; if (p > 0.92) p = 0.45; setLiveProg(p); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [status, prog]);

  useEffect(() => {
    const path = pathRef.current; if (!path) return;
    const len = path.getTotalLength();
    const p = path.getPointAtLength(len * Math.min(1, Math.max(0, liveProg)));
    setPt({ x: p.x, y: p.y });
  }, [liveProg]);

  const etaTxt = status === "complete" ? "Chegou" : eta == null ? "—" : `${eta} min`;

  return (
    <div className="map">
      <div className="maptag">rota · esquematica</div>
      <div className="eta"><small>ETA</small><b>{etaTxt}</b></div>
      <svg viewBox="0 0 520 266" width="100%" height="100%" preserveAspectRatio="xMidYMid slice">
        {/* ruas de fundo */}
        <g stroke="#1b2a42" strokeWidth="10" strokeLinecap="round" opacity="0.5">
          <line x1="-20" y1="70" x2="540" y2="120" /><line x1="-20" y1="190" x2="540" y2="150" />
          <line x1="120" y1="-20" x2="170" y2="286" /><line x1="350" y1="-20" x2="300" y2="286" />
        </g>
        {/* rota */}
        <path ref={pathRef} className="routebg" d="M60 206 C 150 150, 200 120, 290 130 S 420 80, 460 56" />
        <path className="route" d="M60 206 C 150 150, 200 120, 290 130 S 420 80, 460 56" />
        {/* origem */}
        <g><circle cx="60" cy="206" r="9" fill="#3ddc97" /><circle cx="60" cy="206" r="16" fill="none" stroke="#3ddc97" strokeWidth="1.5" opacity="0.4" /></g>
        {/* destino */}
        <g transform="translate(460 56)"><path d="M0 14C0 14 9 6 9 -2A9 9 0 10-9 -2C-9 6 0 14 0 14Z" fill="#f6b73c" /><circle cx="0" cy="-2" r="3" fill="#1a1206" /></g>
        {/* carro */}
        <g className="car" transform={`translate(${pt.x} ${pt.y})`}>
          <circle r="15" fill="rgba(54,212,196,.15)" />
          <g transform="translate(-11 -10)">{Ico.car({ stroke: "#36d4c4", width: 22, height: 22 })}</g>
        </g>
      </svg>
    </div>
  );
}

/* =================== OPERADOR =================== */
function Operator({ api, demo, health }) {
  const [rides, setRides] = useState([]);
  const [drivers, setDrivers] = useState([]);
  const [over, setOver] = useState(null);
  const [busy, setBusy] = useState(null);
  const [drv, setDrv] = useState({ name: "", license_plate: "", phone: "" });
  const [msg, setMsg] = useState("");
  const [histFilter, setHistFilter] = useState("all");   // all | complete | cancelled

  const refresh = useCallback(async () => {
    try { const r = await api("/rides/"); if (Array.isArray(r)) setRides(r); } catch { /* */ }
    try { const d = await api("/drivers/"); if (Array.isArray(d)) setDrivers(d); } catch { /* */ }
    try { const o = await api("/rides/overflow/check"); setOver(o); } catch { /* */ }
  }, [api]);

  useEffect(() => { refresh(); const iv = setInterval(refresh, POLL_MS); return () => clearInterval(iv); }, [refresh]);

  const advance = async (ride) => {
    const next = { request: "match", match: "confirm", confirm: "in_transit", in_transit: "complete" }[ride.status];
    if (!next) return;
    setBusy(ride.id); setMsg("");
    try {
      let driver_id = ride.driver_id;
      if (next === "match" && !driver_id) {
        try { const avail = await api("/drivers/available"); if (Array.isArray(avail) && avail.length) driver_id = avail[0].id; } catch { /* */ }
      }
      await api(`/rides/${ride.id}/status`, { method: "PATCH", body: JSON.stringify({ new_status: next, driver_id }) });
      await refresh();
    } catch (e) { setMsg("Falha ao avancar: " + e.message); }
    finally { setBusy(null); }
  };

  const addDriver = async () => {
    if (!drv.name || !drv.license_plate) { setMsg("Motorista precisa de nome e placa."); return; }
    setMsg("");
    try { await api("/drivers/", { method: "POST", body: JSON.stringify({ ...drv, phone: drv.phone || "000" }) });
      setDrv({ name: "", license_plate: "", phone: "" }); setMsg("Motorista cadastrado."); refresh(); }
    catch (e) { setMsg("Falha: " + e.message); }
  };

  const active = rides.filter((r) => !["complete", "cancelled"].includes(r.status));
  const history = rides.filter((r) => ["complete", "cancelled"].includes(r.status));
  const delegatedCount = rides.filter((r) => r.delegated_from || r.delegated_to).length;
  const histSorted = [...history].sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
  const histConcluidas = history.filter((r) => r.status === "complete").length;
  const histCanceladas = history.filter((r) => r.status === "cancelled").length;
  const histList = histSorted.filter((r) => histFilter === "all" || r.status === histFilter);
  const driverMap = {};
  for (const d of drivers) driverMap[d.id] = d;

  return (
    <div className="fade">
      <div className="kpis">
        <Kpi v={health?.available_drivers ?? "—"} k="Motoristas livres" c="green" />
        <Kpi v={active.length} k="Corridas ativas" c="cyan" />
        <Kpi v={health?.queue?.inbox ?? "—"} k="Fila entrada" c="blue" />
        <Kpi v={health?.queue?.outbox ?? "—"} k="Fila saida" c="amber" />
        <Kpi v={delegatedCount} k="Delegadas" c="magenta" />
      </div>

      {over?.should_delegate && (
        <div className="banner fade" style={{ background: "rgba(246,183,60,.10)", borderColor: "rgba(246,183,60,.4)" }}>
          <span className="dot warn" /><div><b>Overflow ativo</b> — sem motoristas suficientes. Novas corridas serao delegadas via Core.</div>
        </div>
      )}

      <div className="grid">
        {/* corridas ativas */}
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <div className="ctitle">Corridas ativas {demo && <span className="demobadge" style={{ marginLeft: 8 }}>● DEMO</span>}</div>
          {active.length === 0
            ? <div className="empty">{Ico.car({ stroke: "var(--faint)", width: 34, height: 34 })}<div>Nenhuma corrida ativa no momento.</div></div>
            : (
              <div style={{ overflowX: "auto" }}>
                <table className="tbl">
                  <thead><tr><th>Corrida</th><th>Trajeto</th><th>Status</th><th>Origem</th><th>Motorista</th><th>Acao</th></tr></thead>
                  <tbody>
                    {active.map((r) => {
                      const s = STATUS[r.status] || STATUS.request;
                      return (
                        <tr key={r.id}>
                          <td className="mono">{shortId(r.id)}</td>
                          <td style={{ maxWidth: 220 }}>{r.origin} <span style={{ color: "var(--faint)" }}>→</span> {r.destination}</td>
                          <td><span className={`chip c-${s.color}`}><i />{r.status}</span></td>
                          <td>{r.delegated_from
                            ? <span className="chip" style={{ background: "rgba(240,110,203,.14)", color: "var(--magenta)" }}><i style={{ background: "var(--magenta)" }} />{r.delegated_from}</span>
                            : r.delegated_to
                            ? <span className="chip c-amber"><i />→ {r.delegated_to}</span>
                            : <span style={{ color: "var(--faint)", fontSize: 12 }}>local</span>}</td>
                          <td><DriverCell d={driverMap[r.driver_id]} id={r.driver_id} /></td>
                          <td><button className="btn sm" disabled={busy === r.id} onClick={() => advance(r)}>
                                {busy === r.id ? <span className="spin" /> : "Avancar"}</button></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          {msg && <div style={{ marginTop: 10, fontSize: 12.5, color: msg.startsWith("Falha") ? "var(--red)" : "var(--green)" }}>{msg}</div>}
        </div>

        {/* cadastro rapido de motorista */}
        <div className="card">
          <div className="ctitle">Cadastrar motorista</div>
          <div className="field"><label>Nome</label><input value={drv.name} onChange={(e) => setDrv({ ...drv, name: e.target.value })} placeholder="Nome do motorista" /></div>
          <div className="row2">
            <div className="field"><label>Placa</label><input value={drv.license_plate} onChange={(e) => setDrv({ ...drv, license_plate: e.target.value })} placeholder="ABC-1234" /></div>
            <div className="field"><label>Telefone</label><input value={drv.phone} onChange={(e) => setDrv({ ...drv, phone: e.target.value })} placeholder="(31)…" /></div>
          </div>
          <button className="btn" onClick={addDriver}>Adicionar motorista</button>
          <p style={{ color: "var(--faint)", fontSize: 12, marginTop: 12 }}>Mais motoristas livres evitam overflow e mantem corridas locais.</p>
        </div>

        {/* historico */}
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <div className="ctitle" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
            <span>Historico</span>
            <span style={{ fontSize: 11.5, color: "var(--faint)", fontWeight: 400 }}>
              {histConcluidas} concluida(s) · {histCanceladas} cancelada(s)
            </span>
          </div>

          <div style={{ display: "flex", gap: 6, margin: "0 0 12px" }}>
            {[["all", "Todas"], ["complete", "Concluidas"], ["cancelled", "Canceladas"]].map(([k, lbl]) => (
              <button key={k} className="btn sm" onClick={() => setHistFilter(k)}
                style={{
                  padding: "4px 10px", fontSize: 12,
                  background: histFilter === k ? "var(--panel2)" : "transparent",
                  borderColor: histFilter === k ? "var(--line2)" : "var(--line)",
                  color: histFilter === k ? "var(--txt)" : "var(--muted)",
                }}>{lbl}</button>
            ))}
          </div>

          {histList.length === 0
            ? <div className="empty" style={{ padding: 22 }}><div>Nenhuma corrida neste filtro.</div></div>
            : (
              <div style={{ maxHeight: 460, overflowY: "auto", overflowX: "auto" }}>
                <table className="tbl">
                  <thead><tr>
                    <th>Trajeto</th><th>Status</th><th>Tipo</th><th>Motorista</th>
                    <th>Passageiro</th><th>Concluida em</th><th>Duracao</th><th>Corrida</th>
                  </tr></thead>
                  <tbody>
                    {histList.slice(0, 80).map((r) => {
                      const s = STATUS[r.status] || STATUS.complete;
                      const dur = fmtDuration(r.created_at, r.updated_at);
                      const tipo = r.delegated_from
                        ? { txt: `recebida · ${r.delegated_from}`, color: "var(--magenta)", bg: "rgba(240,110,203,.14)" }
                        : r.delegated_to
                        ? { txt: `enviada · ${r.delegated_to}`, color: "var(--amber)", bg: "rgba(246,183,60,.14)" }
                        : { txt: "local", color: "var(--faint)", bg: "rgba(90,107,133,.12)" };
                      return (
                        <tr key={r.id}>
                          <td style={{ maxWidth: 280 }}>{r.origin} <span style={{ color: "var(--faint)" }}>→</span> {r.destination}</td>
                          <td><span className={`chip c-${s.color}`}><i />{s.label}</span></td>
                          <td><span className="chip" style={{ background: tipo.bg, color: tipo.color, fontSize: 11 }}><i style={{ background: tipo.color }} />{tipo.txt}</span></td>
                          <td><DriverCell d={driverMap[r.driver_id]} id={r.driver_id} /></td>
                          <td className="mono" style={{ fontSize: 11.5 }}>{shortId(r.passenger_id)}</td>
                          <td style={{ fontSize: 12, whiteSpace: "nowrap" }}>{fmtDateTime(r.updated_at)}</td>
                          <td style={{ fontSize: 12 }}>{dur || "—"}</td>
                          <td className="mono" style={{ fontSize: 11, color: "var(--faint)" }}>{shortId(r.id)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

function Kpi({ v, k, c }) {
  return (<div className="kpi"><div className="v" style={{ color: tone(c) }}>{v}</div><div className="k">{k}</div></div>);
}

/* ---------- helpers ---------- */
function shortId(id) { return id ? String(id).slice(0, 8) : "—"; }
function fmtTime(t) { if (!t) return "—"; try { return new Date(t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch { return "—"; } }
function fmtDateTime(t) { if (!t) return "—"; try { return new Date(t).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); } catch { return "—"; } }
function fmtDuration(a, b) {
  if (!a || !b) return null;
  const ms = new Date(b) - new Date(a);
  if (!(ms > 0)) return null;
  const s = Math.round(ms / 1000), m = Math.floor(s / 60), sec = s % 60;
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}
function DriverCell({ d, id }) {
  if (!id) return <span style={{ color: "var(--faint)" }}>—</span>;
  if (!d) return <span className="mono" style={{ fontSize: 11.5 }}>{shortId(id)}</span>;
  return (
    <div style={{ lineHeight: 1.3 }}>
      <div style={{ fontSize: 12.5 }}>{d.name || "—"}</div>
      <div className="mono" style={{ fontSize: 10.5, color: "var(--faint)" }}>{d.license_plate || "?"} · {shortId(id)}</div>
    </div>
  );
}
function tone(c) { return { amber: "#f6b73c", blue: "#5aa2ff", cyan: "#36d4c4", green: "#3ddc97", red: "#ff5f6e", magenta: "#f06ecb" }[c] || "#e7eef8"; }
function heroBg(c) { const t = tone(c); return `radial-gradient(circle at 30% 30%, ${t}33, ${t}10)`; }