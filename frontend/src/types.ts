export interface Artifact { id: string; kind: string; version: number; content: unknown }
export interface Check { id: string; name: string; command: string; status: string; exit_code: number | null; output: string; version: number }
export interface Finding { id: string; severity: string; category: string; file: string; evidence: string; proposed_fix: string; status: string; version: number }
export interface Approval { kind: string; version: number; created_at: string }
export interface WorkflowEvent { id: string; agent: string; message: string; created_at: string }
export interface Trace { requirement_id: string; requirement: string; ac_id: string; criterion: string; implementation: string; test: string; execution: string }
export interface Gate { name: string; status: string; reason: string }
export interface Report { recommendation: string; explanation: string; gates: Gate[] }
export interface Run { id: string; title: string; requirement: string; mode: 'demo' | 'llm'; current_stage: string; status: string; version: number; correction_round: number; created_at: string; artifacts: Artifact[]; checks: Check[]; findings: Finding[]; approvals: Approval[]; events: WorkflowEvent[]; traceability: Trace[]; report?: Report | null }
