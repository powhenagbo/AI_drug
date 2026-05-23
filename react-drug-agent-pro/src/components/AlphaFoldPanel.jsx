import { useEffect, useRef, useState } from 'react';
import { fetchAlphaFold, getAlphaFold, alphaFoldPdbUrl } from '../api.js';

const PRESETS = [
  { label: 'EGFR',     uniprot: 'P00533', disease: 'cancer' },
  { label: 'DT Toxin', uniprot: '',       disease: 'diphtheria' },
  { label: 'DHFR',     uniprot: 'P00374', disease: 'malaria' },
  { label: 'InhA',     uniprot: 'P0A5Y3', disease: 'tuberculosis' },
];

const VIEW_STYLES  = ['cartoon', 'stick', 'sphere', 'surface', 'line'];
const COLORINGS    = ['b-factor', 'spectrum', 'chain', 'residue'];

// Standard AlphaFold pLDDT color scheme applied via B-factor column
function applyPLDDTColor(viewer, style) {
  const colorFn = (atom) => {
    const b = atom.b;
    if (b >= 90) return '#1565C0';
    if (b >= 70) return '#29B6F6';
    if (b >= 50) return '#FFC107';
    return '#FF7043';
  };
  if (style === 'surface') {
    viewer.setStyle({}, { cartoon: { colorfunc: colorFn } });
    viewer.addSurface(window.$3Dmol.SurfaceType.VDW, {
      opacity: 0.75,
      colorfunc: colorFn,
    });
  } else {
    viewer.setStyle({}, { [style]: { colorfunc: colorFn } });
  }
}

function applySchemeColor(viewer, style, scheme) {
  if (style === 'surface') {
    viewer.setStyle({}, { cartoon: { color: scheme } });
    viewer.addSurface(window.$3Dmol.SurfaceType.VDW, { opacity: 0.75, colorscheme: scheme });
  } else {
    viewer.setStyle({}, { [style]: { color: scheme } });
  }
}

function TierBar({ label, pct, color }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '.7rem', color: 'var(--text-muted)', width: 82, flexShrink: 0 }}>
        {label}
      </span>
      <div style={{ flex: 1, height: 5, background: 'rgba(255,255,255,.06)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{
          height: '100%', borderRadius: 3, background: color,
          width: `${Math.max(0, Math.min(100, pct || 0))}%`,
          transition: 'width 1s cubic-bezier(.4,0,.2,1)',
        }} />
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: '.7rem', color: 'var(--text-muted)', width: 32, textAlign: 'right' }}>
        {(pct || 0).toFixed(0)}%
      </span>
    </div>
  );
}

function plddtPillStyle(v) {
  if (v >= 90) return { background: 'rgba(0,212,255,.12)', color: 'var(--teal)' };
  if (v >= 70) return { background: 'rgba(0,212,160,.12)', color: '#00d4a0' };
  if (v >= 50) return { background: 'rgba(245,200,66,.12)', color: '#f5c842' };
  return { background: 'rgba(242,107,107,.1)', color: 'var(--red)' };
}

export default function AlphaFoldPanel({ setError }) {
  const containerRef = useRef(null);
  const viewerRef    = useRef(null);

  const [collapsed,   setCollapsed]   = useState(false);
  const [diseaseName, setDiseaseName] = useState('');
  const [uniprotId,   setUniprotId]   = useState('');
  const [organism,    setOrganism]    = useState('Homo sapiens');
  const [plddtCutoff, setPlddtCutoff] = useState(70);

  const [loading,      setLoading]      = useState(false);
  const [data,         setData]         = useState(null);
  const [status,       setStatus]       = useState('');
  const [statusOk,     setStatusOk]     = useState(true);

  // viewer
  const [viewerReady,  setViewerReady]  = useState(false);
  const [pdbLoaded,    setPdbLoaded]    = useState(false);
  const [molStyle,     setMolStyle]     = useState('cartoon');
  const [coloring,     setColoring]     = useState('b-factor');
  const [spinning,     setSpinning]     = useState(false);
  const [useFiltered,  setUseFiltered]  = useState(false);
  const [viewerMsg,    setViewerMsg]    = useState('Fetch a structure to render it here.');

  // ── Load 3Dmol.js once ───────────────────────────────────────────────────
  useEffect(() => {
    if (window.$3Dmol) { setViewerReady(true); return; }
    const s = document.createElement('script');
    s.src   = 'https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.0.1/3Dmol-min.js';
    s.async = true;
    s.onload = () => setViewerReady(true);
    document.head.appendChild(s);
  }, []);

  // ── Create viewer when container mounts and lib is ready ─────────────────
  useEffect(() => {
    if (!viewerReady || !containerRef.current || viewerRef.current || collapsed) return;
    viewerRef.current = window.$3Dmol.createViewer(containerRef.current, {
      backgroundColor: '0x0d1117',
      antialias: true,
    });
  }, [viewerReady, collapsed]);

  // ── Style helpers ────────────────────────────────────────────────────────
  function applyStyle(styleOverride, coloringOverride) {
    const v = viewerRef.current;
    if (!v || !pdbLoaded) return;
    const s = styleOverride  || molStyle;
    const c = coloringOverride || coloring;
    v.setStyle({}, {});
    if (c === 'b-factor') {
      applyPLDDTColor(v, s);
    } else {
      applySchemeColor(v, s, c);
    }
    v.render();
  }

  function changeStyle(s) {
    setMolStyle(s);
    applyStyle(s, coloring);
  }

  function changeColoring(c) {
    setColoring(c);
    applyStyle(molStyle, c);
  }

  function toggleSpin() {
    const next = !spinning;
    setSpinning(next);
    next ? viewerRef.current?.spin('y', 1) : viewerRef.current?.spin(false);
  }

  // ── Load PDB into 3D viewer ──────────────────────────────────────────────
  async function loadPdb(disease, filtered = false) {
    const v = viewerRef.current;
    if (!v) { setViewerMsg('Viewer not ready yet.'); return; }

    setViewerMsg('Loading PDB into viewer…');
    setPdbLoaded(false);

    try {
      const resp = await fetch(alphaFoldPdbUrl(disease, filtered));
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const pdbText = await resp.text();

      v.clear();
      v.addModel(pdbText, 'pdb');

      // default: cartoon + pLDDT color scheme
      applyPLDDTColor(v, 'cartoon');
      v.zoomTo();
      v.render();
      setPdbLoaded(true);
      setViewerMsg('Drag to rotate · Scroll to zoom · Right-click to pan');
    } catch (e) {
      setViewerMsg(`Failed to load PDB: ${e.message}`);
    }
  }

  // ── AlphaFold API calls ──────────────────────────────────────────────────
  function showStatus(msg, ok = true) { setStatus(msg); setStatusOk(ok); }

  async function handleFetch() {
    if (!diseaseName.trim()) { showStatus('Enter a disease name.', false); return; }
    setLoading(true);
    showStatus('Resolving UniProt ID and downloading structure…');
    setError('');
    try {
      const result = await fetchAlphaFold({
        disease_name: diseaseName.trim(),
        uniprot_id:   uniprotId.trim() || undefined,
        organism,
        plddt_cutoff: plddtCutoff,
      });
      if (!result.ok) { showStatus(result.message || 'Fetch failed.', false); return; }
      setData(result.data);
      showStatus(`✓ ${result.data?.protein_name || result.data?.uniprot_id} loaded.`);
      await loadPdb(diseaseName.trim(), false);
    } catch (e) {
      showStatus(e.message, false);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleLoad() {
    const name = diseaseName.trim();
    if (!name) { showStatus('Enter a disease name to load.', false); return; }
    setLoading(true);
    showStatus('Loading from database…');
    setError('');
    try {
      const result = await getAlphaFold(name);
      if (!result.ok) { showStatus(result.message || 'Not found.', false); return; }
      setData(result.data);
      showStatus(`✓ ${result.data?.protein_name || name} loaded from DB.`);
      await loadPdb(name, false);
    } catch (e) {
      showStatus(e.message, false);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function toggleFiltered(filtered) {
    setUseFiltered(filtered);
    if (diseaseName) await loadPdb(diseaseName, filtered);
  }

  const d      = data;
  const pctLow = Math.max(0, 100 - (d?.pct_confident || 0));
  const pctMid = Math.max(0, (d?.pct_confident || 0) - (d?.pct_very_high || 0));

  return (
    <section className="card stack">

      {/* ── Header ── */}
      <div className="panel-head">
        <div>
          <p className="eyebrow">AlphaFold · EBI</p>
          <h2>Protein structure</h2>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="badge" style={{ background: 'rgba(0,212,255,.15)', color: 'var(--teal)' }}>pLDDT</span>
          <button
            type="button" className="secondary-btn"
            onClick={() => setCollapsed(c => !c)}
            style={{ padding: '4px 10px', fontSize: '.75rem' }}
          >
            {collapsed ? '▾ Expand' : '▴ Collapse'}
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* Presets */}
          <div className="chip-row">
            {PRESETS.map(p => (
              <button key={p.label} type="button" className="chip"
                onClick={() => { setDiseaseName(p.disease); setUniprotId(p.uniprot); }}>
                {p.label}
              </button>
            ))}
          </div>

          {/* Inputs */}
          <div className="field">
            <label>Disease / target name</label>
            <input type="text" value={diseaseName}
              onChange={e => setDiseaseName(e.target.value)}
              placeholder="e.g. diphtheria, cancer"
              onKeyDown={e => e.key === 'Enter' && handleFetch()}
            />
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div className="field" style={{ margin: 0 }}>
              <label>UniProt ID (optional)</label>
              <input type="text" value={uniprotId} onChange={e => setUniprotId(e.target.value)} placeholder="e.g. P00533" />
            </div>
            <div className="field" style={{ margin: 0 }}>
              <label>pLDDT cutoff</label>
              <input type="number" value={plddtCutoff} onChange={e => setPlddtCutoff(Number(e.target.value))} min={0} max={100} step={5} />
            </div>
          </div>

          {/* Buttons */}
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="primary-btn" style={{ flex: 1 }} disabled={loading} onClick={handleFetch}>
              {loading ? 'Fetching…' : 'Fetch from AlphaFold →'}
            </button>
            <button className="secondary-btn" disabled={loading} onClick={handleLoad}>Load DB</button>
          </div>

          {status && (
            <p className="muted" style={{ fontSize: '.78rem', color: statusOk ? 'var(--teal)' : 'var(--red)' }}>
              {status}
            </p>
          )}

          {/* ── 3D Viewer ── */}
          <div>
            <div
              ref={containerRef}
              className="mol-viewer-box"
              style={{ position: 'relative', cursor: pdbLoaded ? 'grab' : 'default' }}
            >
              {!pdbLoaded && (
                <div style={{
                  position: 'absolute', inset: 0, pointerEvents: 'none',
                  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                  color: 'var(--text-muted)', fontSize: '.85rem', gap: 10,
                }}>
                  <span style={{ fontSize: '2.5rem', opacity: .25 }}>🧬</span>
                  <span>Fetch a structure to render it here</span>
                </div>
              )}
            </div>

            {/* pLDDT legend — only when b-factor coloring is active */}
            {pdbLoaded && coloring === 'b-factor' && (
              <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', padding: '8px 2px' }}>
                {[
                  { bg: '#1565C0', label: 'Very high ≥90' },
                  { bg: '#29B6F6', label: 'Confident 70–90' },
                  { bg: '#FFC107', label: 'Low 50–70' },
                  { bg: '#FF7043', label: 'Very low <50' },
                ].map(l => (
                  <span key={l.label} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: '.7rem', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                    <span style={{ width: 10, height: 10, borderRadius: 2, background: l.bg, flexShrink: 0 }} />
                    {l.label}
                  </span>
                ))}
              </div>
            )}

            {/* Viewer style controls */}
            <div className="mol-controls" style={{ flexWrap: 'wrap', gap: 6 }}>
              {VIEW_STYLES.map(s => (
                <button key={s} type="button" className="secondary-btn" disabled={!pdbLoaded}
                  style={molStyle === s ? { borderColor: 'var(--teal)', color: 'var(--teal)', background: 'var(--teal-dim)' } : {}}
                  onClick={() => changeStyle(s)}>
                  {s}
                </button>
              ))}
              <div style={{ width: 1, height: 20, background: 'rgba(255,255,255,.08)', margin: '0 2px' }} />
              {COLORINGS.map(c => (
                <button key={c} type="button" className="secondary-btn" disabled={!pdbLoaded}
                  style={{
                    fontSize: '.72rem',
                    ...(coloring === c ? { borderColor: '#7b61ff', color: '#7b61ff', background: 'rgba(123,97,255,.1)' } : {}),
                  }}
                  title={c === 'b-factor' ? 'Color by pLDDT confidence score' : `Color by ${c}`}
                  onClick={() => changeColoring(c)}>
                  {c === 'b-factor' ? 'pLDDT' : c}
                </button>
              ))}
              <button type="button" className="secondary-btn" disabled={!pdbLoaded}
                style={{ marginLeft: 'auto', ...(spinning ? { borderColor: 'var(--teal)', color: 'var(--teal)' } : {}) }}
                onClick={toggleSpin}>
                {spinning ? 'Stop spin' : 'Spin'}
              </button>
            </div>

            {/* Full / high-conf toggle */}
            {pdbLoaded && (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
                <span style={{ fontSize: '.72rem', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>View:</span>
                <button type="button" className="secondary-btn"
                  style={!useFiltered ? { borderColor: 'var(--teal)', color: 'var(--teal)', fontSize: '.72rem' } : { fontSize: '.72rem' }}
                  onClick={() => toggleFiltered(false)}>
                  Full structure
                </button>
                <button type="button" className="secondary-btn" disabled={!d?.filtered_pdb_path}
                  style={useFiltered ? { borderColor: '#f5c842', color: '#f5c842', fontSize: '.72rem' } : { fontSize: '.72rem' }}
                  title={`High-confidence residues only (pLDDT ≥ ${plddtCutoff})`}
                  onClick={() => toggleFiltered(true)}>
                  High-conf only
                </button>
              </div>
            )}

            <p className="muted" style={{ fontSize: '.72rem', marginTop: 6 }}>{viewerMsg}</p>
          </div>

          {/* ── Stats panel (visible after data loads) ── */}
          {d && (
            <>
              {/* Protein banner */}
              <div style={{
                padding: '12px 14px', borderRadius: 10,
                background: 'rgba(0,212,255,.05)', border: '1px solid rgba(0,212,255,.18)',
                display: 'flex', alignItems: 'center', gap: 14,
              }}>
                <div style={{
                  width: 42, height: 42, borderRadius: 8, flexShrink: 0,
                  background: 'linear-gradient(135deg, var(--teal), #7b61ff)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'var(--font-mono)', fontSize: '1rem', fontWeight: 700, color: '#0d1117',
                }}>
                  {(d.protein_name || d.uniprot_id || '?').slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <div style={{ fontSize: '.9rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                    {d.protein_name || d.uniprot_id}
                  </div>
                  <div className="muted" style={{ fontSize: '.75rem' }}>
                    {d.uniprot_id} · {d.organism} · {d.sequence_length} aa · Model v{d.af_model_version || '?'}
                  </div>
                </div>
              </div>

              {/* Stat mini-cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 8 }}>
                {[
                  { label: 'Mean pLDDT', value: d.mean_plddt?.toFixed(1) ?? '—',                               accent: 'var(--teal)' },
                  { label: 'Residues',   value: d.total_residues ?? d.sequence_length ?? '—',                   accent: '#7b61ff' },
                  { label: '≥70% conf',  value: d.pct_confident != null ? d.pct_confident.toFixed(0) + '%' : '—', accent: '#00d4a0' },
                  { label: 'Regions',    value: d.n_confident_regions ?? '—',                                   accent: '#f5c842' },
                ].map(s => (
                  <div key={s.label} className="stat-card" style={{ borderTop: `2px solid ${s.accent}`, padding: '10px 12px' }}>
                    <div className="stat-hint" style={{ marginBottom: 4 }}>{s.label}</div>
                    <div className="stat-value" style={{ fontSize: '1.2rem', color: s.accent }}>{s.value}</div>
                  </div>
                ))}
              </div>

              {/* Tier bars */}
              <div>
                <TierBar label="Very High" pct={d.pct_very_high} color="var(--teal)" />
                <TierBar label="Confident" pct={pctMid}          color="#00d4a0" />
                <TierBar label="Low"       pct={pctLow}          color="#f5c842" />
              </div>

              {/* Regions table */}
              {d.regions?.length > 0 && (
                <div>
                  <p style={{ fontFamily: 'var(--font-mono)', fontSize: '.72rem', color: 'var(--text-muted)', marginBottom: 8, letterSpacing: '.08em', textTransform: 'uppercase' }}>
                    High-confidence druggable regions
                  </p>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: '.78rem' }}>
                      <thead>
                        <tr>
                          {['#', 'Start', 'End', 'Length', 'Mean pLDDT'].map(h => (
                            <th key={h} style={{ textAlign: 'left', padding: '6px 10px', borderBottom: '1px solid rgba(255,255,255,.06)', color: 'var(--text-muted)', fontSize: '.68rem', letterSpacing: '.1em', textTransform: 'uppercase' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {d.regions.slice(0, 8).map((r, i) => (
                          <tr key={i}>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,.04)', color: 'var(--text-muted)' }}>{i + 1}</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,.04)' }}>{r.start}</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,.04)' }}>{r.end}</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,.04)' }}>{r.length} aa</td>
                            <td style={{ padding: '8px 10px', borderBottom: '1px solid rgba(255,255,255,.04)' }}>
                              <span style={{ padding: '2px 8px', borderRadius: 10, fontSize: '.7rem', ...plddtPillStyle(r.mean_plddt) }}>
                                {r.mean_plddt.toFixed(1)}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* PDB downloads */}
              <div>
                <p style={{ fontFamily: 'var(--font-mono)', fontSize: '.72rem', color: 'var(--text-muted)', marginBottom: 8, letterSpacing: '.08em', textTransform: 'uppercase' }}>
                  Download PDB files
                </p>
                <div style={{ display: 'flex', gap: 8 }}>
                  <a className="secondary-btn" href={alphaFoldPdbUrl(diseaseName)} download
                    style={{ textDecoration: 'none', flex: 1, textAlign: 'center' }}>
                    🧬 Full structure
                  </a>
                  <a className="secondary-btn" href={alphaFoldPdbUrl(diseaseName, true)} download
                    style={{
                      textDecoration: 'none', flex: 1, textAlign: 'center',
                      ...(d.filtered_pdb_path ? {} : { opacity: .4, pointerEvents: 'none' }),
                    }}>
                    🎯 High-conf only
                  </a>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
