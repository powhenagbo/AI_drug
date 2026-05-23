import { useEffect, useRef, useState } from 'react';

const EXAMPLES = [
  { label: 'Aspirin',    smiles: 'CC(=O)Oc1ccccc1C(=O)O' },
  { label: 'Caffeine',   smiles: 'Cn1cnc2c1c(=O)n(c(=O)n2C)C' },
  { label: 'Ibuprofen',  smiles: 'CC(C)Cc1ccc(cc1)C(C)C(=O)O' },
  { label: 'Glucose',    smiles: 'C(C1C(C(C(C(O1)O)O)O)O)O' },
];

const STYLES = ['stick', 'sphere', 'ball+stick', 'surface'];

export default function MolViewer() {
  const containerRef = useRef(null);
  const viewerRef    = useRef(null);
  const rdkitRef     = useRef(null);

  const [collapsed, setCollapsed]   = useState(false);
  const [smiles, setSmiles]         = useState('');
  const [status, setStatus]         = useState('Loading chemistry engine…');
  const [molStyle, setMolStyle]     = useState('stick');
  const [spinning, setSpinning]     = useState(false);
  const [loaded, setLoaded]         = useState(false);
  const [molblock, setMolblock]     = useState(null);

  /* ── load external libs dynamically ── */
  useEffect(() => {
    function addScript(src, onLoad) {
      const s = document.createElement('script');
      s.src = src; s.async = true;
      s.onload = onLoad;
      document.head.appendChild(s);
    }

    let rdkitLoaded = false, dmolLoaded = false;

    function tryInit() {
      if (!rdkitLoaded || !dmolLoaded) return;
      if (!window.$3Dmol || !window.initRDKitModule) return;
      window.initRDKitModule().then((rdk) => {
        rdkitRef.current = rdk;
        if (containerRef.current && !viewerRef.current) {
          viewerRef.current = window.$3Dmol.createViewer(containerRef.current, {
            backgroundColor: '0x0d1117',
            antialias: true,
          });
        }
        setStatus('Ready — enter a SMILES or pick an example.');
        setLoaded(true);
      }).catch(() => setStatus('Failed to load RDKit.'));
    }

    addScript('https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.0.1/3Dmol-min.js', () => {
      dmolLoaded = true; tryInit();
    });
    addScript('https://unpkg.com/@rdkit/rdkit/Code/MinimalLib/dist/RDKit_minimal.js', () => {
      rdkitLoaded = true; tryInit();
    });
  }, []);

  function renderMolblock(mb) {
    const v = viewerRef.current;
    if (!v) return;
    v.clear();
    v.addModel(mb, 'mol');
    applyStyle(molStyle, v);
    v.zoomTo();
    v.render();
  }

  function applyStyle(s, v) {
    const vRef = v || viewerRef.current;
    if (!vRef) return;
    vRef.setStyle({}, {});
    if (s === 'stick')      vRef.setStyle({}, { stick: { radius: .12 }, sphere: { radius: .22 } });
    if (s === 'sphere')     vRef.setStyle({}, { sphere: {} });
    if (s === 'ball+stick') vRef.setStyle({}, { stick: { radius: .1 }, sphere: { radius: .35 } });
    if (s === 'surface') {
      vRef.setStyle({}, { stick: { radius: .08 } });
      vRef.addSurface(window.$3Dmol.SurfaceType.VDW, { opacity: .7 });
    }
    vRef.render();
  }

  function loadSmiles(s) {
    const rdk = rdkitRef.current;
    if (!rdk) { setStatus('Chemistry engine not ready yet.'); return; }
    const mol = rdk.get_mol(s);
    if (!mol?.is_valid()) { mol?.delete(); setStatus('Invalid SMILES — please check and retry.'); return; }
    mol.set_new_coords(true);
    const mb = mol.get_molblock();
    mol.delete();
    setMolblock(mb);
    renderMolblock(mb);
    setStatus('Drag to rotate · scroll to zoom · right-click to pan');
  }

  function handleSubmit(e) {
    e.preventDefault();
    if (smiles.trim()) loadSmiles(smiles.trim());
  }

  function changeStyle(s) {
    setMolStyle(s);
    if (molblock) { viewerRef.current?.clear(); viewerRef.current?.addModel(molblock, 'mol'); applyStyle(s); viewerRef.current?.zoomTo(); }
  }

  function toggleSpin() {
    const next = !spinning;
    setSpinning(next);
    next ? viewerRef.current?.spin('y', 1) : viewerRef.current?.spin(false);
  }

  return (
    <div className="card stack">
      {/* ── header with collapse toggle ── */}
      <div className="panel-head">
        <div>
          <p className="eyebrow">Molecular viewer</p>
          <h2>3D SMILES renderer</h2>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="badge">py3Dmol · RDKit</span>
          <button
            type="button"
            className="secondary-btn"
            title={collapsed ? 'Expand' : 'Collapse'}
            onClick={() => setCollapsed(c => !c)}
            style={{ padding: '4px 10px', fontSize: '.75rem' }}
          >
            {collapsed ? '▾ Expand' : '▴ Collapse'}
          </button>
        </div>
      </div>

      {/* ── collapsible body ── */}
      {!collapsed && (
        <>
          {/* quick examples */}
          <div className="chip-row">
            {EXAMPLES.map((ex) => (
              <button
                key={ex.label}
                type="button"
                className="chip"
                onClick={() => { setSmiles(ex.smiles); loadSmiles(ex.smiles); }}
              >
                {ex.label}
              </button>
            ))}
          </div>

          {/* SMILES input */}
          <form onSubmit={handleSubmit} style={{ display: 'flex', gap: 10 }}>
            <input
              type="text"
              value={smiles}
              onChange={(e) => setSmiles(e.target.value)}
              placeholder="Paste SMILES — e.g. CC(=O)Oc1ccccc1C(=O)O"
            />
            <button type="submit" className="primary-btn" disabled={!loaded} style={{ whiteSpace: 'nowrap' }}>
              View 3D
            </button>
          </form>

          {/* viewer box */}
          <div
            ref={containerRef}
            className="mol-viewer-box"
            style={{ cursor: molblock ? 'grab' : 'default' }}
          />

          {/* controls */}
          <div className="mol-controls">
            {STYLES.map((s) => (
              <button
                key={s}
                type="button"
                className={`secondary-btn${molStyle === s ? ' active' : ''}`}
                style={molStyle === s ? { borderColor: 'var(--teal)', color: 'var(--teal)', background: 'var(--teal-dim)' } : {}}
                onClick={() => changeStyle(s)}
              >
                {s}
              </button>
            ))}
            <button
              type="button"
              className="secondary-btn"
              style={{ marginLeft: 'auto', ...(spinning ? { borderColor: 'var(--teal)', color: 'var(--teal)' } : {}) }}
              onClick={toggleSpin}
            >
              {spinning ? 'Stop spin' : 'Spin'}
            </button>
          </div>

          <p className="muted" style={{ fontSize: '.78rem' }}>{status}</p>
        </>
      )}
    </div>
  );
}
