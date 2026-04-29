export type ProjectType = 'plant_room' | 'ahu';

export interface ValidationSummary {
  valid?: boolean;
  exportable?: boolean;
  error_count?: number;
  warning_count?: number;
  risk_count?: number;
  blocked_export_reasons?: string[];
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
  validation_report?: Record<string, unknown> | null;
  patch_result?: Record<string, unknown> | null;
  patch_confirmation?: Record<string, unknown> | null;
  status?: string | null;
  next_action?: string | null;
  error?: string | null;
  use_llm_planner?: boolean;
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
    estimated_steps?: number;
  };
  risk_points?: string[];
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
  control_targets?: string[];
  communication?: string[];
  protections?: string[];
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
