export type ProjectType = 'plant_room' | 'ahu';

export interface ValidationSummary {
  valid?: boolean;
  exportable?: boolean;
  error_count?: number;
  warning_count?: number;
  risk_count?: number;
  blocked_export_reasons?: string[];
  requirement_reviewed?: boolean;
  requirement_valid?: boolean;
  conformance_blocked_count?: number;
  conformance_missing_count?: number;
}

export interface RequirementConformanceEntry {
  requirement?: string;
  category?: string;
  status?: 'covered' | 'partial' | 'missing';
  evidence?: Array<Record<string, unknown>>;
  reason?: string;
}

export interface RequirementConformanceReport {
  context_available?: boolean;
  status?: 'passed' | 'blocked' | 'not_reviewed';
  valid_for_requirement?: boolean;
  covered?: RequirementConformanceEntry[];
  partial?: RequirementConformanceEntry[];
  missing?: RequirementConformanceEntry[];
  blocked?: string[];
  evidence?: Array<Record<string, unknown>>;
  warnings?: string[];
  summary?: {
    covered_count?: number;
    partial_count?: number;
    missing_count?: number;
    blocked_count?: number;
  };
}

export interface RiskAssessment {
  risk_level?: 'low' | 'medium' | 'high' | 'blocked';
  requires_confirmation?: boolean;
  reasons?: string[];
  risk_reasons?: string[];
}

export interface SessionState {
  messages: Array<{ role: string; content: string }>;
  project_type?: ProjectType | null;
  requirement_summary?: RequirementSummary;
  requirement_slots?: Record<string, unknown>;
  design_brief?: DesignBrief | null;
  conformance_report?: RequirementConformanceReport | null;
  conversation_summary?: Record<string, unknown> | null;
  recent_user_intents?: Array<Record<string, unknown>>;
  last_affected_node_ids?: string[];
  last_touched_entities?: Array<Record<string, unknown>>;
  last_patch_summary?: Record<string, unknown> | null;
  semantic_target_candidates?: SemanticTargetCandidate[];
  advisory_result?: AdvisoryResult | null;
  pending_advice?: PendingAdvice | null;
  advice_history?: PendingAdvice[];
  advice_context_summary?: Record<string, unknown> | null;
  candidate_requirements?: CandidateRequirement[];
  open_questions?: Array<string | RequirementQuestion>;
  confirmed_requirements?: string[];
  template_candidates?: TemplateCandidate[];
  selected_template_id?: string | null;
  project_id?: string | null;
  version_id?: string | null;
  current_project_id?: string | null;
  current_project_version_id?: string | null;
  current_project_path?: string | null;
  pending_confirmation_patch?: Record<string, unknown> | null;
  planner_result?: Record<string, unknown> | null;
  planner_dry_run?: Record<string, unknown> | null;
  planner_attempts?: Array<Record<string, unknown>>;
  risk_assessment?: RiskAssessment | null;
  validation_summary?: ValidationSummary | null;
  validation_report?: (Record<string, unknown> & { conformance_report?: RequirementConformanceReport }) | null;
  patch_result?: Record<string, unknown> | null;
  patch_confirmation?: Record<string, unknown> | null;
  status?: string | null;
  next_action?: string | null;
  error?: string | null;
  use_llm_planner?: boolean;
}

export interface AdvisoryBasis {
  source?: string;
  summary?: string;
}

export interface CandidateRequirement {
  content?: string;
  status?: 'observed' | 'candidate' | 'confirmed' | 'rejected' | string;
  needs_confirmation?: boolean;
  created_at?: string;
}

export interface AdoptablePatchIntent {
  executable?: boolean;
  message?: string;
  reason?: string;
}

export interface AdvisoryResult {
  status?: 'answered' | 'needs_more_info' | 'out_of_scope' | 'unsafe_request' | string;
  answer?: string;
  topic?: string;
  recommendation?: Record<string, unknown>;
  basis?: AdvisoryBasis[];
  assumptions?: string[];
  risks?: string[];
  missing_info?: string[];
  candidate_requirements?: CandidateRequirement[];
  adoptable_patch_intent?: AdoptablePatchIntent;
  next_action?: string;
  context_used?: Record<string, unknown>;
}

export interface PendingAdvice extends AdvisoryResult {
  accepted_patch_message?: string;
  created_at?: string;
}

export interface SemanticTargetCandidate {
  candidate_id?: string;
  kind?: string;
  display_name?: string;
  description?: string;
  confidence?: number;
  selector?: Record<string, unknown>;
  tab_label?: string;
  type?: string;
  key_params?: Record<string, unknown>;
}

export interface TemplateCandidate {
  template_id: string;
  file_name?: string;
  project_type?: ProjectType;
  project_type_label?: string;
  score?: number;
  node_count?: number;
  tab_count?: number;
  summary?: string;
  reasons?: string[];
  matched_features?: string[];
  missing_features?: string[];
  estimated_effort?: string;
  risk_notes?: string[];
  matched_items?: string[];
  missing_items?: string[];
  estimated_modification_cost?: {
    level?: string;
    reason?: string;
    reasons?: string[];
    estimated_steps?: number;
  };
  risk_points?: string[];
  recommendation_reasons?: string[];
}

export interface TemplateCoverage {
  template_id?: string;
  file_name?: string;
  score?: number;
  matched_items?: string[];
  missing_items?: string[];
  estimated_modification_cost?: {
    level?: string;
    reasons?: string[];
  };
  risk_points?: string[];
  recommendation_reasons?: string[];
}

export interface DesignBrief {
  selected_template?: {
    template_id?: string;
    file_name?: string;
    project_type?: ProjectType;
    project_type_label?: string;
    score?: number;
    tabs?: string[];
  };
  recommendation_reasons?: string[];
  satisfied_requirements?: string[];
  modification_items?: string[];
  clarification_items?: string[];
  risk_items?: string[];
  equipment_plan?: string[];
  control_plan?: string[];
  point_plan?: string[];
  protection_plan?: string[];
  export_gate?: string[];
  template_coverage?: TemplateCoverage[];
  requirement_source?: Record<string, unknown>;
  summary?: string;
}

export interface SessionResponse {
  thread_id: string;
  trace_id?: string;
  state: SessionState;
}

export interface ApiProblem {
  message: string;
  status: number;
  detail?: unknown;
}

export interface RequirementQuestion {
  field?: string;
  question?: string;
}

export interface RequirementSummary {
  project_type?: ProjectType | null;
  ready_for_template_search?: boolean;
  blocking_missing_fields?: string[];
  raw_requirements?: string[];
  equipment?: string[];
  control_features?: string[];
  communication?: string[];
  io_points?: string[];
  protection_logic?: string[];
  risk_level?: 'low' | 'medium' | 'high';
  risk_items?: string[];
  [key: string]: unknown;
}

export interface PatchConfirmationResponse extends SessionResponse {}

export interface FeedbackPayload {
  trace_id: string;
  project_id?: string | null;
  version_id?: string | null;
  rating: number;
  category: string;
  comment: string;
}

export interface FeedbackResponse {
  feedback_id: string;
  trace_id: string;
  project_id?: string | null;
  version_id?: string | null;
  rating: number;
  category: string;
  comment: string;
  created_at?: string;
}

export interface FeedbackListResponse {
  trace_id?: string;
  project_id?: string;
  feedback: FeedbackResponse[];
}

export interface FlowNodeData {
  [key: string]: unknown;
  node_id?: string;
  label?: string;
  module_type?: string;
  role?: EngineeringNodeRole;
  tab_id?: string;
  tab_label?: string;
  inputs?: number;
  outputs?: number;
  affected?: boolean;
  diff_kind?: 'added' | 'removed' | 'modified';
  risk?: boolean;
}

export type EngineeringNodeRole = 'input' | 'output' | 'communication' | 'compare' | 'pid' | 'logic' | 'protection' | 'note' | 'unknown';

export interface FlowBudget {
  max_nodes: number;
  max_edges: number;
  max_chars: number;
  node_count: number;
  edge_count: number;
  truncated: boolean;
}

export interface ProjectFlowResponse {
  project_id: string;
  version_id: string;
  flow: {
    nodes: Array<{
      id: string;
      type?: string;
      position: { x: number; y: number };
      data: FlowNodeData;
    }>;
    edges: Array<{
      id: string;
      source: string;
      target: string;
      sourceHandle?: string;
      targetHandle?: string;
      data?: Record<string, unknown>;
    }>;
    tabs?: Array<{ id: string; label: string }>;
    budget: FlowBudget;
  };
}

export interface DiffNode {
  node_id?: string;
  type?: string;
  name?: string;
  tab_id?: string;
  field_changes?: Array<{
    field?: string;
    old_value?: unknown;
    new_value?: unknown;
  }>;
}

export interface NodeDiff {
  summary?: {
    added_count?: number;
    removed_count?: number;
    modified_count?: number;
    affected_node_count?: number;
  };
  affected_node_ids?: string[];
  added?: DiffNode[];
  removed?: DiffNode[];
  modified?: DiffNode[];
}

export interface ProjectDiffResponse {
  project_id: string;
  from_version_id: string;
  to_version_id: string;
  diff: NodeDiff;
}

export interface ProjectVersion {
  project_id?: string;
  version_id: string;
  parent_version_id?: string | null;
  source_template_path?: string | null;
  version_path?: string;
  json_sha256?: string;
  exportable?: boolean;
  created_at?: string;
  note?: string;
  patch_summary?: Record<string, unknown>;
  validation_summary?: ValidationSummary | null;
  requirement_context?: {
    conformance_report?: RequirementConformanceReport;
    requirement_slots?: Record<string, unknown>;
    design_brief?: DesignBrief;
  };
  summary?: Record<string, unknown>;
  risk_level?: string;
}

export interface ProjectVersionsResponse {
  project_id: string;
  versions: ProjectVersion[];
}

export interface RollbackProjectResponse {
  project_id: string;
  target_version_id: string;
  state?: SessionState;
  validation_report?: Record<string, unknown>;
}

export type EventConnectionStatus = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected' | 'error';

export interface WorkflowEvent {
  event_id: string;
  trace_id?: string | null;
  thread_id: string;
  project_id?: string | null;
  version_id?: string | null;
  event_type: string;
  step?: string | null;
  status?: string | null;
  message?: string | null;
  payload?: Record<string, unknown> | null;
  created_at?: string | null;
}

export interface AgentTrace {
  trace_id: string;
  thread_id: string;
  project_id?: string | null;
  version_id?: string | null;
  root_input?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  metadata?: Record<string, unknown> | null;
  events: WorkflowEvent[];
  llm_calls?: LlmCallRecord[];
  feedback?: FeedbackResponse[];
}

export interface ObservabilityMetrics {
  workflowEventsTotal: number;
  tracesTotal: number;
  failedTracesTotal: number;
  traceDurationP95Ms: number;
  userFeedbackTotal: number;
  llmCallsTotal: number;
  llmCallsFailedTotal: number;
  llmInputTokensTotal: number;
  llmOutputTokensTotal: number;
  llmEstimatedCostTotal: number;
  projectExportsTotal: number;
  projectExportsFailedTotal: number;
  eventsByStatus: Record<string, number>;
  eventsByType: Record<string, number>;
}

export interface CostBucket {
  project_id?: string | null;
  provider?: string;
  model?: string;
  prompt_name?: string;
  date?: string;
  calls: number;
  failed_calls: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  average_latency_ms: number;
}

export interface CostSummaryResponse {
  project_id?: string | null;
  total: CostBucket;
  by_project: CostBucket[];
  by_provider_model: CostBucket[];
  by_prompt: CostBucket[];
  by_date: CostBucket[];
}

export interface ObservabilityTrendBucket {
  date: string;
  request_count: number;
  failed_request_count: number;
  error_rate: number;
  p95_duration_ms: number;
  llm_calls: number;
  llm_failed_calls: number;
  llm_input_tokens: number;
  llm_output_tokens: number;
  llm_estimated_cost: number;
  confirmation_approved_count: number;
  confirmation_cancelled_count: number;
  confirmation_cancel_rate: number;
}

export interface ObservabilityTrendResponse {
  project_id?: string | null;
  buckets: ObservabilityTrendBucket[];
}

export interface LlmCallRecord {
  id?: string;
  llm_call_id?: string;
  trace_id: string;
  provider: string;
  model: string;
  prompt_name: string;
  attempt: number;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  status: string;
  error?: string | null;
  created_at?: string;
}
