import { useCallback, useEffect, useState } from 'react';
import { ArrowDown, ArrowRight, ArrowUpRight, BookOpen, Box, Braces, Check, CheckCheck, ChevronDown, ChevronRight, Circle, CircleCheck, Clock3, Code2, Download, FileCode2, FlaskConical, GitBranch, Layers3, LoaderCircle, PanelLeftClose, Plus, Radar, Search, ShieldCheck, Sparkles, Terminal, TriangleAlert, Workflow, X } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Artifact, Run } from './types';

const DEMO = 'Build a customer contact API with FastAPI. Customers can update their phone number. Validate an international phone number in E.164 format, reject unauthorized updates, and return appropriate HTTP errors. Include an audit record for successful changes.';
const STAGES = [
  { key: 'requirements', label: 'Requirement analysis', agent: 'Business Analyst', icon: BookOpen },
  { key: 'design', label: 'Technical design', agent: 'Solution Designer', icon: Layers3 },
  { key: 'code', label: 'Code generation', agent: 'Developer', icon: Code2 },
  { key: 'tests', label: 'Test generation', agent: 'Test Engineer', icon: FlaskConical },
  { key: 'checks', label: 'Engineering checks', agent: 'Validation tools', icon: Terminal },
  { key: 'review', label: 'Review & security', agent: 'Review agents', icon: ShieldCheck },
  { key: 'fix', label: 'Defect resolution', agent: 'Fix Agent', icon: GitBranch },
  { key: 'release', label: 'Release assessment', agent: 'Release Agent', icon: Box },
];
const BUSY = ['analyzing', 'generating', 'validating'];

/** Request backend data and surface API failures. */
async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('The backend is unavailable. Check that the FastAPI server is running, then try again.');
  }
  const payload: unknown = await response.json();
  if (!response.ok) {
    const result = payload as { detail?: string; error?: { message?: string } };
    throw new Error(result.error?.message || result.detail || 'The request could not be completed.');
  }
  return (payload as { data: T }).data;
}

function human(value: string): string { return value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()); }
function timestamp(value: string): string { return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
function artifactFor(run: Run, stage: string): Artifact | undefined {
  const aliases: Record<string, string[]> = { requirements: ['requirements', 'analysis', 'analyst'], design: ['design', 'designer'], code: ['code', 'files', 'implementation', 'developer'], tests: ['tests', 'test_plan', 'tester'], review: ['review', 'reviewer', 'security', 'code_review'], fix: ['fix', 'fixer', 'proposed_fix', 'fix_proposal', 'remediation'], release: ['release', 'report'] };
  return [...run.artifacts].reverse().find(a => (aliases[stage] || [stage]).includes(a.kind));
}
function stageIndex(run: Run): number {
  return ({ analyzing: 0, design_approval: 1, generating: 2, validating: 4, fix_approval: 6, release_review: 7, completed: 8, manual_review: 7, error: 0 } as Record<string, number>)[run.current_stage] ?? 0;
}
function Badge({ value }: { value: string }): ReactNode {
  const normalized = value.toLowerCase().replaceAll(' ', '_');
  const color = ['passed', 'ready', 'completed', 'approved', 'resolved'].includes(normalized) ? 'green' : ['failed', 'not_ready', 'high', 'critical', 'error'].includes(normalized) ? 'red' : ['pending', 'not_executed', 'manual_review', 'conditional', 'design_approval', 'fix_approval', 'release_review', 'medium'].includes(normalized) ? 'amber' : 'neutral';
  return <span className={`badge ${color}`}><span className="status-dot" />{human(value)}</span>;
}

/** Display the engineering workspace and its persisted runs. */
export default function App(): ReactNode {
  const [runs, setRuns] = useState<Run[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [screen, setScreen] = useState<'new' | 'workflow' | 'report'>('new');
  const [selected, setSelected] = useState('requirements');
  const [tab, setTab] = useState('output');
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [search, setSearch] = useState('');
  const refreshRuns = useCallback(async (): Promise<void> => { try { setRuns(await api<Run[]>('/runs')); } catch (e) { setError(e instanceof Error ? e.message : 'Backend unavailable'); } }, []);
  useEffect(() => { void refreshRuns(); }, [refreshRuns]);
  useEffect(() => {
    if (!run || !BUSY.includes(run.current_stage)) return;
    const timer = window.setInterval(() => { void api<Run>(`/runs/${run.id}`).then(updated => { setRun(updated); if (updated.current_stage !== run.current_stage) { setSelected(STAGES[Math.min(stageIndex(updated), 7)].key); void refreshRuns(); } }).catch(e => setError(String(e))); }, 1500);
    return () => window.clearInterval(timer);
  }, [run, refreshRuns]);
  async function openRun(id: string): Promise<void> {
    setWorking(true); setError('');
    try { const result = await api<Run>(`/runs/${id}`); setRun(result); setSelected(STAGES[Math.min(stageIndex(result), 7)].key); setScreen('workflow'); setTab('output'); } catch (e) { setError(String(e)); } finally { setWorking(false); }
  }
  async function create(requirement: string, mode: string): Promise<void> {
    setWorking(true); setError('');
    try { const result = await api<Run>('/runs', { requirement, mode }); setRun(result); setScreen('workflow'); setSelected('requirements'); setTab('output'); void refreshRuns(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setWorking(false); }
  }
  async function approve(kind: string, answers: string): Promise<void> {
    if (!run) return;
    setWorking(true); setError('');
    try { const result = await api<Run>(`/runs/${run.id}/approve`, { kind, version: run.version, answers }); setRun(result); setSelected(STAGES[Math.min(stageIndex(result), 7)].key); void refreshRuns(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setWorking(false); }
  }
  async function resume(): Promise<void> {
    if (!run) return;
    setWorking(true); setError('');
    try { setRun(await api<Run>(`/runs/${run.id}/resume`, {})); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setWorking(false); }
  }
  const activeCount = runs.filter(r => !['completed', 'error'].includes(r.current_stage)).length;
  return <div className="app-shell">
    <aside className="workspace-nav" aria-label="Workspace navigation">
      <a className="brand" href="#" onClick={e => { e.preventDefault(); setScreen('new'); }}><span className="brand-symbol"><Layers3 size={21}/></span>forge<span className="brand-tag">SDLC</span></a>
      <div className="workspace-switch"><span className="workspace-avatar">E</span><div><strong>Engineering workspace</strong><small>Hackathon edition</small></div><ChevronDown size={14}/></div>
      <div className="nav-label">WORKSPACE</div>
      <button className={`nav-item ${screen === 'new' ? 'active' : ''}`} onClick={() => setScreen('new')}><Plus size={17}/>New feature<span className="shortcut">N</span></button>
      <button className={`nav-item ${screen === 'workflow' ? 'active' : ''}`} onClick={() => { if (run) setScreen('workflow'); else if (runs[0]) void openRun(runs[0].id); }}><Workflow size={17}/>Workflows<span className="nav-count">{runs.length}</span></button>
      <button className={`nav-item ${screen === 'report' ? 'active' : ''}`} onClick={() => { if (run) setScreen('report'); else if (runs[0]) { void openRun(runs[0].id).then(() => setScreen('report')); } }}><CheckCheck size={17}/>Release reports</button>
      <div className="nav-divider"/>
      <div className="recent-heading"><span className="nav-label">RECENT WORKFLOWS</span><span>{activeCount} active</span></div>
      <label className="run-search"><Search size={14}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder="Find a workflow" aria-label="Find a workflow"/></label>
      <div className="recent-list">{runs.filter(r => `${r.title} ${r.requirement}`.toLowerCase().includes(search.toLowerCase())).map(r => <button key={r.id} className={`recent-run ${run?.id === r.id && screen !== 'new' ? 'selected' : ''}`} onClick={() => void openRun(r.id)}><span className={`run-dot ${r.current_stage === 'completed' ? 'done' : ''}`}/><span>{r.title || r.requirement.slice(0, 35)}<small>{human(r.current_stage)}</small></span></button>)}{runs.length === 0 && <p className="empty-recent">Your features will appear here.<br/>Start with a requirement.</p>}</div>
      <div className="nav-bottom"><div className="local-status"><span className="status-dot"/>Local workspace <span>v1.0</span></div><div className="profile"><span className="profile-avatar">EN</span><div><strong>Engineering team</strong><small>Human in the loop</small></div><PanelLeftClose size={16}/></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14}/><strong>{screen === 'new' ? 'New feature' : screen === 'report' ? 'Release report' : 'Workflow'}</strong></div><div className="topbar-right"><span className="environment"><span className="status-dot"/>Development</span><a href="/api/docs" target="_blank" rel="noreferrer">API docs<ArrowUpRight size={13}/></a><span className="team-avatar">EN</span></div></header>
      {error && <div className="error-banner" role="alert"><TriangleAlert size={17}/><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16}/></button></div>}
      <main>{screen === 'new' ? <NewFeature working={working} onCreate={create}/> : run ? screen === 'report' ? <FinalReport run={run} onBack={() => setScreen('workflow')}/> : <>
        <div className="workflow-heading"><div><div className="title-context"><span className="mono">WF-{run.id.slice(0, 6).toUpperCase()}</span><span>Python / FastAPI</span><span>{run.mode === 'demo' ? 'Guided demo' : 'Live AI'}</span></div><h1>{run.title || 'New API feature'}</h1><p>One requirement. Every engineering step. A complete trail of evidence.</p></div><button className="button secondary" onClick={() => setScreen('report')}>View report<ArrowUpRight size={15}/></button></div>
        <div className="workflow-meta"><span><GitBranch size={14}/>Artifact version <strong>{run.version}</strong></span><span><Clock3 size={14}/>{new Date(run.created_at).toLocaleDateString()}</span><Badge value={run.current_stage}/><div className="meta-spacer"/><span>Correction rounds <strong>{run.correction_round} / 2</strong></span></div>
        <div className="workflow-grid"><aside className="stages-panel" aria-label="Workflow stages"><div className="panel-heading">WORKFLOW STAGES <span>{Math.min(stageIndex(run), 8)}/8</span></div><nav aria-label="Workflow stages">{STAGES.map((stage, index) => { const Icon = stage.icon; const active = selected === stage.key; const done = index < stageIndex(run); return <button key={stage.key} className={`stage ${active ? 'selected' : ''} ${done ? 'done' : ''}`} onClick={() => { setSelected(stage.key); setTab('output'); }}><span className="stage-icon">{done ? <Check size={16}/> : <Icon size={17}/>}</span><span><strong>{stage.label}</strong><small>{stage.agent}</small></span>{index === stageIndex(run) && <span className="live-point"/>}</button>; })}</nav><div className="human-note"><ShieldCheck size={18}/><strong>AI generates. You govern.</strong><p>Every release is backed by real checks and recorded approvals.</p></div></aside>
          <section className="output-panel"><div className="output-heading"><div className="agent-avatar"><Sparkles size={19}/></div><div><h2>{STAGES.find(s => s.key === selected)?.label}</h2><p>{STAGES.find(s => s.key === selected)?.agent} <span>·</span> Structured output</p></div><span className="version-label">v{artifactFor(run, selected)?.version ?? run.version}</span></div><div className="output-tabs">{['output', 'files', 'checks', 'traceability', 'activity'].map(t => <button key={t} className={tab === t ? 'selected' : ''} onClick={() => setTab(t)}>{human(t)}{t === 'checks' && run.checks.length > 0 && <span>{run.checks.filter(c => c.version === run.version).length}</span>}</button>)}</div><div className="output-body">{tab === 'output' ? <StageOutput run={run} selected={selected}/> : tab === 'files' ? <FileBrowser run={run}/> : tab === 'checks' ? <Checks run={run}/> : tab === 'traceability' ? <Traceability run={run}/> : <Events run={run}/>}</div></section>
          <ApprovalPanel run={run} working={working} onApprove={approve} onResume={resume} onReport={() => setScreen('report')}/>
        </div></> : <div className="no-data"><Workflow/><h1>No workflow selected</h1><button className="button primary" onClick={() => setScreen('new')}>Create a feature<Plus size={16}/></button></div>}</main>
      <footer className="app-footer"><span><Layers3 size={13}/> FORGE SDLC</span><span>AI generates. Engineering tools validate. Humans govern.</span><span><ShieldCheck size={13}/>Evidence by design</span></footer>
    </div>
  </div>;
}

function NewFeature({ working, onCreate }: { working: boolean; onCreate: (requirement: string, mode: string) => Promise<void> }): ReactNode {
  const [requirement, setRequirement] = useState('');
  const [mode, setMode] = useState('demo');
  return <div className="new-feature"><div className="intro"><span className="intro-label"><span className="status-dot"/>YOUR NEXT RELEASE STARTS HERE</span><h1>From an idea to<br/><span>evidence-backed code.</span></h1><p>Your AI engineering team, in one workspace. Describe a feature.<br className="desktop-break"/>Review the design. Follow every step to a confident release.</p></div>
    <div className="new-feature-grid"><section className="requirement-card"><div className="card-title"><span className="outline-icon"><Braces size={20}/></span><div><h2>What are we building?</h2><p>Start with a small Python / FastAPI feature.</p></div><span className="step-label">STEP 1 OF 3</span></div>
      <form onSubmit={e => { e.preventDefault(); if (requirement.trim().length >= 20) void onCreate(requirement.trim(), mode); }}><label className="field-label" htmlFor="requirement">Feature requirement</label><div className="textarea-shell"><textarea id="requirement" value={requirement} onChange={e => setRequirement(e.target.value)} minLength={20} maxLength={10000} required placeholder="Describe what your API should do, who it serves, and how success should be measured…"/><span className="character-count">{requirement.length.toLocaleString()} / 10,000</span></div><div className="input-hint"><BookOpen size={14}/>Include validation rules, expected errors, and authorization.</div>
      <div className="mode-section"><label className="field-label" htmlFor="mode">Generation mode</label><select id="mode" value={mode} onChange={e => setMode(e.target.value)}><option value="demo">Guided demo · Customer contact API</option><option value="llm">Live AI · Connected language model</option></select><p>{mode === 'demo' ? 'A repeatable example with an intentional defect, human approval, and real revalidation. Uses the sample customer API.' : 'Your configured model analyzes and generates a small API from your requirement.'}</p></div>
      <div className="form-bottom"><span><ShieldCheck size={15}/>Design approval required</span><button className="button primary" disabled={working || requirement.trim().length < 20} type="submit">{working ? <LoaderCircle className="spin" size={16}/> : <Sparkles size={16}/>}Analyze feature<ArrowRight size={16}/></button></div></form>
      <div className="sample-strip"><span>Need a starting point?</span><button onClick={() => { setRequirement(DEMO); setMode('demo'); }}>Try the customer API demo<ArrowUpRight size={14}/></button></div>
    </section>
    <aside className="journey-card" aria-label="Engineering journey"><div className="journey-title"><Workflow size={17}/><span>THE PATH TO RELEASE</span><span className="badge green">8 agents</span></div><div className="journey-track"><div className="journey-node first"><span><BookOpen size={20}/></span><div><strong>Understand & design</strong><small>Requirements become a clear blueprint</small></div></div><div className="journey-connector"><span/><div><span className="tiny-dot"/>You approve the design</div></div><div className="journey-node"><span><Code2 size={20}/></span><div><strong>Build & validate</strong><small>Generate code. Run real engineering tools.</small></div></div><div className="tool-pills"><span>pytest</span><span>Ruff</span><span>Bandit</span><span>pip-audit</span></div><div className="journey-connector"><span/><div><GitBranch size={12}/>Review & approve corrections</div></div><div className="journey-node last"><span><Box size={20}/></span><div><strong>Assess & release</strong><small>A release decision supported by evidence</small></div></div></div><div className="isolation-note"><Box size={16}/><p>Generated code runs in isolated Docker containers. No network. No application secrets.</p></div></aside></div>
    <section className="principles"><div><span><Radar size={19}/></span><p><strong>Full visibility</strong>Inspect agent decisions, files, and every tool result.</p></div><div><span><ShieldCheck size={19}/></span><p><strong>You stay in control</strong>Approve designs and changes before they proceed.</p></div><div><span><GitBranch size={19}/></span><p><strong>Connected evidence</strong>Trace each requirement to code, tests, and results.</p></div></section>
    <div className="scope-note"><span className="scope-tag">BUILT FOR FOCUS</span>Small Python APIs. A complete engineering lifecycle.<span>FastAPI <span className="separator">/</span> SQLite <span className="separator">/</span> Docker</span></div>
  </div>;
}

function StructuredOutput({ value, depth = 0 }: { value: unknown; depth?: number }): ReactNode {
  if (value === null || value === undefined) return <span className="muted">None</span>;
  if (typeof value === 'string') return <p className="structured-string">{value}</p>;
  if (typeof value !== 'object') return <span>{String(value)}</span>;
  if (Array.isArray(value)) return <div className="structured-list">{value.map((item: unknown, i: number) => <div className="structured-item" key={i}>{typeof item === 'string' ? <><CircleCheck size={14}/><p>{item}</p></> : <StructuredOutput value={item} depth={depth + 1}/>}</div>)}{value.length === 0 && <p className="muted">None recorded.</p>}</div>;
  return <div className={`structured-object depth-${Math.min(depth, 2)}`}>{Object.entries(value as Record<string, unknown>).map(([key, content]) => <section key={key}><h3>{human(key)}</h3>{key === 'id' || key.endsWith('_id') ? <span className="id-tag">{String(content)}</span> : <StructuredOutput value={content} depth={depth + 1}/>}</section>)}</div>;
}

function StageOutput({ run, selected }: { run: Run; selected: string }): ReactNode {
  if (selected === 'checks') return <Checks run={run}/>;
  if (selected === 'release' && run.report) return <><div className="assessment-inline"><Badge value={run.report.recommendation}/><p>{run.report.explanation}</p></div><GateList run={run}/></>;
  if (selected === 'review') return <Findings run={run}/>;
  const artifact = artifactFor(run, selected);
  if (!artifact) return <div className="waiting-state">{BUSY.includes(run.current_stage) ? <LoaderCircle size={30} className="spin"/> : <Circle size={30}/>}<h3>{BUSY.includes(run.current_stage) ? 'Your engineering team is working' : 'This stage is up next'}</h3><p>Outputs appear here when the orchestrator reaches this stage. Every artifact is saved and versioned.</p></div>;
  if (selected === 'fix') return <FixProposal run={run} artifact={artifact}/>;
  if (selected === 'code' || selected === 'tests') return <><div className="section-description"><FileCode2 size={17}/><p>Generated project artifacts · version {artifact.version}</p></div><FileBrowser run={run} testOnly={selected === 'tests'}/></>;
  return <><div className="artifact-summary"><span className="status-dot"/>Artifact saved<span>Version {artifact.version}</span></div><StructuredOutput value={artifact.content}/></>;
}

function FixProposal({ run, artifact }: { run: Run; artifact: Artifact }): ReactNode {
  const current = filesFrom(run);
  const proposal = artifact.content as { summary?: string; files?: { path: string; content: string }[] };
  const changes = (proposal.files || []).filter(file => file.content !== current[file.path]);
  return <div><div className="artifact-summary"><GitBranch size={14}/>{run.current_stage === 'fix_approval' ? 'Proposed changes · awaiting your approval' : 'Recorded correction proposal'}<span>v{artifact.version}</span></div>{proposal.summary && <p className="structured-string">{proposal.summary}</p>}{changes.map(file => <details className="check-row" key={file.path}><summary><span><FileCode2 size={16}/><strong>{file.path}</strong></span><Badge value="pending"/><ChevronDown size={14}/></summary><div className="proposal-diff"><h3>Current version</h3><pre className="code-block">{current[file.path] || '(new file)'}</pre><h3>Proposed version</h3><pre className="code-block">{file.content}</pre></div></details>)}{!changes.length && <p className="empty-copy">This proposal has been applied to the current files. Review the versioned engineering evidence in Checks.</p>}</div>;
}

function filesFrom(run: Run): Record<string, string> {
  const files: Record<string, string> = {};
  const latest = [...run.artifacts].reverse().find(a => a.kind === 'files');
  const artifacts = latest ? [latest] : run.artifacts.filter(a => ['developer', 'tester'].includes(a.kind) && a.version === run.version);
  for (const artifact of artifacts) {
    const content = artifact.content;
    if (typeof content !== 'object' || content === null) continue;
    const record = content as Record<string, unknown>;
    const source = record.files ?? (['code', 'files', 'tests'].includes(artifact.kind) ? record : undefined);
    if (Array.isArray(source)) { for (const file of source as Record<string, unknown>[]) { const path = file.path ?? file.filename; if (typeof path === 'string' && typeof file.content === 'string') files[path] = file.content; } }
    else if (source && typeof source === 'object') { for (const [path, code] of Object.entries(source)) if (typeof code === 'string') files[path] = code; }
  }
  return files;
}
function FileBrowser({ run, testOnly = false }: { run: Run; testOnly?: boolean }): ReactNode {
  const files = filesFrom(run);
  const paths = Object.keys(files).filter(p => !testOnly || p.includes('test'));
  const [selected, setSelected] = useState('');
  const active = paths.includes(selected) ? selected : paths[0];
  if (!paths.length) return <div className="waiting-state"><FileCode2 size={28}/><h3>No generated files yet</h3><p>Approve the design to start code and test generation.</p></div>;
  return <div className="file-browser"><div className="file-tabs">{paths.map(path => <button key={path} className={path === active ? 'selected' : ''} onClick={() => setSelected(path)}><FileCode2 size={14}/>{path}</button>)}</div><div className="code-heading"><span>{active}</span><span>Python · v{run.version}</span></div><pre className="code-block"><code>{files[active]}</code></pre></div>;
}
function Checks({ run }: { run: Run }): ReactNode {
  const checks = run.checks.filter(c => c.version === run.version);
  return <div><div className="section-description"><Terminal size={17}/><p>Actual tool executions for artifact version {run.version}. Unavailable checks are never counted as passed.</p></div>{checks.length ? checks.map(check => <details className="check-row" key={check.id}><summary><span>{check.status === 'passed' ? <CircleCheck className="green-text" size={17}/> : check.status === 'failed' ? <TriangleAlert className="red-text" size={17}/> : <Circle className="amber-text" size={17}/>}<strong>{check.name}</strong></span><Badge value={check.status}/><ChevronDown size={14}/></summary><div className="check-details"><code>$ {check.command}</code><pre>{check.output || 'No output recorded.'}</pre><small>Exit code: {check.exit_code ?? 'Not executed'} · Artifact v{check.version}</small></div></details>) : <p className="empty-copy">Engineering checks will run after code and test generation.</p>}</div>;
}
function Findings({ run }: { run: Run }): ReactNode {
  return <div><div className="section-description"><ShieldCheck size={17}/><p>Evidence from code review and security agents. Findings remain visible after remediation.</p></div>{run.findings.length ? run.findings.map(finding => <article className="finding" key={finding.id}><div className="finding-top"><Badge value={finding.severity}/><span>{human(finding.category)}</span><Badge value={finding.status}/></div><h3>{finding.file || 'Generated project'}</h3><p>{finding.evidence}</p><div className="suggested-fix"><GitBranch size={14}/><span>{finding.proposed_fix}</span></div><small>Artifact v{finding.version}</small></article>) : <p className="empty-copy">No findings recorded yet. Reviews run after engineering checks.</p>}</div>;
}
function Traceability({ run }: { run: Run }): ReactNode {
  return <div><div className="section-description"><GitBranch size={17}/><p>Requirement → acceptance criterion → implementation → test → execution.</p></div>{run.traceability.length ? <div className="table-scroll"><table><thead><tr><th>Requirement / criterion</th><th>Implementation / test</th><th>Execution</th></tr></thead><tbody>{run.traceability.map((row, i) => <tr key={`${row.ac_id}-${i}`}><td><span className="id-tag">{row.requirement_id}</span><p>{row.requirement}</p><span className="id-tag">{row.ac_id}</span><p>{row.criterion}</p></td><td><code>{row.implementation}</code><ArrowDown size={13}/><code>{row.test}</code></td><td><Badge value={row.execution}/></td></tr>)}</tbody></table></div> : <p className="empty-copy">Traceability will connect acceptance criteria to generated files and actual test results.</p>}</div>;
}
function Events({ run }: { run: Run }): ReactNode {
  return <div className="event-list">{[...run.events].reverse().map(event => <div className="event" key={event.id}><span className="event-symbol"><Sparkles size={13}/></span><div><strong>{human(event.agent)}</strong><p>{event.message}</p></div><time>{timestamp(event.created_at)}</time></div>)}{!run.events.length && <p className="empty-copy">Workflow activity will be recorded here.</p>}</div>;
}

function ApprovalPanel({ run, working, onApprove, onResume, onReport }: { run: Run; working: boolean; onApprove: (kind: string, answers: string) => Promise<void>; onResume: () => Promise<void>; onReport: () => void }): ReactNode {
  const [answers, setAnswers] = useState('');
  const kind = ({ design_approval: 'design', fix_approval: 'fix', release_review: 'release' } as Record<string, string>)[run.current_stage];
  const analysis = artifactFor(run, 'requirements')?.content as { questions?: unknown[] } | undefined;
  const hasQuestions = kind === 'design' && Boolean(analysis?.questions?.length);
  useEffect(() => { setAnswers(''); }, [run.id, run.version, run.current_stage]);
  const checks = run.checks.filter(c => c.version === run.version);
  const passed = checks.filter(c => c.status === 'passed').length;
  return <aside className="results-panel" aria-label="Run controls and results"><div className="panel-heading">RUN CONTROL <Radar size={14}/></div><div className="control-status"><div className={`control-icon ${kind ? 'amber-text' : ''}`}>{BUSY.includes(run.current_stage) ? <LoaderCircle size={22} className="spin"/> : kind ? <ShieldCheck size={24}/> : <Radar size={24}/>}</div><h3>{kind ? 'Your approval is needed' : BUSY.includes(run.current_stage) ? 'Workflow in progress' : human(run.current_stage)}</h3><p>{kind === 'design' ? 'Review the requirements and technical design before code generation.' : kind === 'fix' ? 'Inspect the proposed correction. Approval applies changes and reruns validation.' : kind === 'release' ? 'Review the engineering evidence and record your final decision.' : BUSY.includes(run.current_stage) ? 'Agents and engineering tools are building the evidence for your next decision.' : 'Inspect the evidence and release report for the current artifact version.'}</p></div>
    {kind && <div className="approval-form"><label htmlFor="approval-notes">{kind === 'design' ? 'Clarifications & decisions' : 'Review notes'}<span>{hasQuestions ? 'Required' : 'Optional'}</span></label><textarea id="approval-notes" value={answers} onChange={e => setAnswers(e.target.value)} placeholder={kind === 'design' ? 'Resolve open questions or record your design decisions…' : 'Record your review notes…'}/><button className="button primary" disabled={working || (hasQuestions && !answers.trim())} onClick={() => void onApprove(kind, answers)}>{working ? <LoaderCircle className="spin" size={15}/> : <Check size={16}/>}{hasQuestions ? 'Apply clarifications' : `Approve ${kind === 'fix' ? 'correction' : kind}`}<ArrowRight size={15}/></button><p className="approval-footnote"><ShieldCheck size={12}/>Approval is recorded for version {run.version}.</p></div>}
    <div className="results-divider"/><div className="panel-heading">VALIDATION SNAPSHOT <span>v{run.version}</span></div><div className="validation-count"><strong>{passed}<span> / {checks.length || '—'}</span></strong><p>checks passed</p></div><div className="check-progress"><span style={{ width: `${checks.length ? passed / checks.length * 100 : 0}%` }}/></div><div className="snapshot-row"><span>Blocking findings</span><strong>{run.findings.filter(f => f.status === 'open' && f.version === run.version && ['high', 'critical'].includes(f.severity.toLowerCase())).length}</strong></div><div className="snapshot-row"><span>Acceptance criteria</span><strong>{new Set(run.traceability.map(t => t.ac_id)).size || '—'}</strong></div><div className="snapshot-row"><span>Recorded approvals</span><strong>{run.approvals.filter(a => a.version === run.version).length}</strong></div>
    <button className="report-link" onClick={onReport}>Open release report<ArrowUpRight size={15}/></button>
    {(run.current_stage === 'error' || run.current_stage === 'manual_review') && <button className="button secondary full-width" disabled={working} onClick={() => void onResume()}>Retry workflow<ArrowRight size={14}/></button>}
    <div className="results-divider"/><div className="panel-heading">LATEST ACTIVITY</div><div className="compact-events">{[...run.events].slice(-3).reverse().map(event => <div key={event.id}><span className="tiny-dot"/><p>{event.message}<small>{timestamp(event.created_at)} · {human(event.agent)}</small></p></div>)}{!run.events.length && <p className="muted">Waiting for the first agent.</p>}</div>
  </aside>;
}
function GateList({ run }: { run: Run }): ReactNode {
  return <div className="gate-list">{run.report?.gates.map(gate => <div className="gate" key={gate.name}><span>{['passed', 'pass'].includes(gate.status.toLowerCase()) ? <CircleCheck size={18} className="green-text"/> : <TriangleAlert size={18} className="amber-text"/>}</span><div><h3>{gate.name}</h3><p>{gate.reason}</p></div><Badge value={gate.status}/></div>)}</div>;
}
function FinalReport({ run, onBack }: { run: Run; onBack: () => void }): ReactNode {
  const report = run.report;
  return <div className="final-report"><div className="report-header"><div><div className="title-context"><span className="mono">WF-{run.id.slice(0, 6).toUpperCase()}</span><span>Artifact v{run.version}</span></div><h1>Release readiness report</h1><p>{run.title || 'API feature'} · Every decision, backed by evidence.</p></div><div className="report-actions"><button className="button secondary" onClick={onBack}>Back to workflow</button><a className="button primary" href={`/api/runs/${run.id}/download`}><Download size={16}/>Download code</a></div></div><section className="release-decision"><div className="decision-icon"><ShieldCheck size={32}/></div><div><span className="field-label">RELEASE RECOMMENDATION</span><h2>{report?.recommendation || 'AWAITING ASSESSMENT'}</h2><p>{report?.explanation || 'Complete the engineering workflow to produce a release recommendation. Required checks and human approval determine readiness.'}</p></div><span className="decision-version">VERSION {run.version}<br/>{run.mode === 'demo' ? 'GUIDED DEMO' : 'LIVE AI'}</span></section><div className="report-grid"><section className="report-section"><div className="report-section-title"><h2>Engineering quality gates</h2><span>{report?.gates.length || 0} gates</span></div>{report ? <GateList run={run}/> : <p className="empty-copy">Gates are calculated after validation. Missing checks cannot pass.</p>}</section><section className="report-section evidence-summary"><h2>Evidence summary</h2><div className="snapshot-row"><span>Generated artifacts</span><strong>{run.artifacts.length}</strong></div><div className="snapshot-row"><span>Current checks</span><strong>{run.checks.filter(c => c.version === run.version).length}</strong></div><div className="snapshot-row"><span>Correction rounds</span><strong>{run.correction_round} / 2</strong></div><div className="snapshot-row"><span>Human approvals</span><strong>{run.approvals.filter(a => a.version === run.version).length}</strong></div><div className="evidence-note"><ShieldCheck size={18}/><p>Release recommendations follow backend rules. AI explains the evidence; humans approve the result.</p></div></section></div><section className="report-section"><div className="report-section-title"><h2>Requirement coverage</h2><span>{new Set(run.traceability.map(t => t.ac_id)).size} acceptance criteria</span></div><Traceability run={run}/></section><section className="report-section"><div className="report-section-title"><h2>Issues & remediation</h2><span>{run.findings.filter(f => f.status === 'open' && f.version === run.version).length} unresolved</span></div><Findings run={run}/></section></div>;
}
