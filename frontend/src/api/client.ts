import { z } from 'zod';
import type {
  AgentTrace,
  ApiProblem,
  CostSummaryResponse,
  FeedbackPayload,
  FeedbackListResponse,
  FeedbackResponse,
  ObservabilityMetrics,
  ObservabilityTrendResponse,
  PatchConfirmationResponse,
  ProjectDiffResponse,
  ProjectFlowResponse,
  ProjectVersionsResponse,
  ProjectType,
  RollbackProjectResponse,
  SessionResponse,
  WorkflowEvent
} from './types';

const apiProblemSchema = z.object({
  detail: z.unknown().optional()
});

export class ApiError extends Error {
  readonly problem: ApiProblem;

  constructor(problem: ApiProblem) {
    super(problem.message);
    this.name = 'ApiError';
    this.problem = problem;
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init.headers
    }
  });
  if (!response.ok) {
    throw new ApiError(await toProblem(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function createSession(projectType?: ProjectType, options: { useLlmPlanner?: boolean } = {}): Promise<SessionResponse> {
  return apiRequest<SessionResponse>('/api/sessions', {
    method: 'POST',
    body: JSON.stringify({
      project_type: projectType,
      auto_confirm_template: false,
      use_llm_planner: options.useLlmPlanner
    })
  });
}

export function sendMessage(
  threadId: string,
  message: string,
  projectType?: ProjectType,
  options: { useLlmPlanner?: boolean } = {}
): Promise<SessionResponse> {
  return apiRequest<SessionResponse>(`/api/sessions/${threadId}/message`, {
    method: 'POST',
    body: JSON.stringify({
      message,
      project_type: projectType,
      use_llm_planner: options.useLlmPlanner
    })
  });
}

export function confirmTemplate(threadId: string, templateId: string): Promise<SessionResponse> {
  return apiRequest<SessionResponse>(`/api/sessions/${threadId}/message`, {
    method: 'POST',
    body: JSON.stringify({
      message: `确认使用模板 ${templateId}`,
      selected_template_id: templateId
    })
  });
}

export function confirmPatch(threadId: string, action: 'approve' | 'cancel'): Promise<PatchConfirmationResponse> {
  return apiRequest<PatchConfirmationResponse>(`/api/sessions/${threadId}/patch-confirmation`, {
    method: 'POST',
    body: JSON.stringify({ action })
  });
}

export function validateProject(projectId: string): Promise<Record<string, unknown>> {
  return apiRequest<Record<string, unknown>>(`/api/projects/${projectId}/validate`, {
    method: 'POST'
  });
}

export function getProjectFlow(
  projectId: string,
  versionId: string,
  options: { centerNodeId?: string; focusNodeIds?: string[]; tabId?: string } = {}
): Promise<ProjectFlowResponse> {
  const params = new URLSearchParams({
    max_nodes: '120',
    max_edges: '260',
    max_chars: '160000'
  });
  if (options.centerNodeId) {
    params.set('center_node_id', options.centerNodeId);
  }
  for (const nodeId of options.focusNodeIds ?? []) {
    params.append('focus_node_ids', nodeId);
  }
  if (options.tabId) {
    params.set('tab_id', options.tabId);
  }
  return apiRequest<ProjectFlowResponse>(`/api/projects/${projectId}/versions/${versionId}/flow?${params.toString()}`);
}

export function getProjectDiff(projectId: string, toVersionId?: string, fromVersionId?: string): Promise<ProjectDiffResponse> {
  const params = new URLSearchParams();
  if (toVersionId) {
    params.set('to_version_id', toVersionId);
  }
  if (fromVersionId) {
    params.set('from_version_id', fromVersionId);
  }
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return apiRequest<ProjectDiffResponse>(`/api/projects/${projectId}/diff${suffix}`);
}

export function listProjectVersions(projectId: string): Promise<ProjectVersionsResponse> {
  return apiRequest<ProjectVersionsResponse>(`/api/projects/${projectId}/versions`);
}

export function rollbackProject(projectId: string, targetVersionId: string): Promise<RollbackProjectResponse> {
  return apiRequest<RollbackProjectResponse>(`/api/projects/${projectId}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ target_version_id: targetVersionId })
  });
}

export function projectExportUrl(projectId: string): string {
  return `/api/projects/${projectId}/export`;
}

export function submitFeedback(payload: FeedbackPayload): Promise<FeedbackResponse> {
  return apiRequest<FeedbackResponse>('/api/feedback', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export function listTraceFeedback(traceId: string): Promise<FeedbackListResponse> {
  return apiRequest<FeedbackListResponse>(`/api/traces/${encodeURIComponent(traceId)}/feedback`);
}

export function listProjectFeedback(projectId: string): Promise<FeedbackListResponse> {
  return apiRequest<FeedbackListResponse>(`/api/projects/${encodeURIComponent(projectId)}/feedback`);
}

export function getTrace(traceId: string): Promise<AgentTrace> {
  return apiRequest<AgentTrace>(`/api/traces/${encodeURIComponent(traceId)}`);
}

export function getCostSummary(projectId?: string | null): Promise<CostSummaryResponse> {
  const params = new URLSearchParams({ limit: '50' });
  if (projectId) {
    params.set('project_id', projectId);
  }
  return apiRequest<CostSummaryResponse>(`/api/observability/costs?${params.toString()}`);
}

export function getTrendSummary(projectId?: string | null): Promise<ObservabilityTrendResponse> {
  const params = new URLSearchParams({ limit: '30' });
  if (projectId) {
    params.set('project_id', projectId);
  }
  return apiRequest<ObservabilityTrendResponse>(`/api/observability/trends?${params.toString()}`);
}

export async function getMetricsSummary(): Promise<ObservabilityMetrics> {
  const response = await fetch('/metrics', {
    headers: {
      Accept: 'text/plain'
    }
  });
  if (!response.ok) {
    throw new ApiError(await toProblem(response));
  }
  return parsePrometheusMetrics(await response.text());
}

export interface SessionEventStreamHandlers {
  lastEventId?: string;
  onOpen?: () => void;
  onEvent: (event: WorkflowEvent) => void;
  onReconnect?: () => void;
  onError?: (error: unknown) => void;
}

export interface SessionEventStream {
  close: () => void;
}

export function openSessionEventStream(threadId: string, handlers: SessionEventStreamHandlers): SessionEventStream {
  const controller = new AbortController();
  let closed = false;
  let cursor = handlers.lastEventId;

  const run = async () => {
    while (!closed) {
      try {
        const params = new URLSearchParams({ timeout_seconds: '30' });
        if (cursor) {
          params.set('last_event_id', cursor);
        }
        const response = await fetch(`/api/sessions/${threadId}/events?${params.toString()}`, {
          headers: cursor ? { 'Last-Event-ID': cursor } : undefined,
          signal: controller.signal
        });
        if (!response.ok) {
          throw new ApiError(await toProblem(response));
        }
        handlers.onOpen?.();
        await readSseResponse(response, controller.signal, (event) => {
          cursor = event.event_id;
          handlers.onEvent(event);
        });
        if (!closed) {
          handlers.onReconnect?.();
          await delay(900, controller.signal);
        }
      } catch (error) {
        if (closed || controller.signal.aborted) {
          return;
        }
        handlers.onError?.(error);
        await delay(1400, controller.signal).catch(() => undefined);
      }
    }
  };

  void run();

  return {
    close: () => {
      closed = true;
      controller.abort();
    }
  };
}

export function formatApiError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.problem.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return '请求失败，请稍后重试。';
}

async function toProblem(response: Response): Promise<ApiProblem> {
  let detail: unknown;
  try {
    detail = apiProblemSchema.parse(await response.json()).detail;
  } catch {
    detail = await response.text();
  }
  return {
    status: response.status,
    detail,
    message: detailToMessage(detail) || `请求失败：HTTP ${response.status}`
  };
}

function detailToMessage(detail: unknown): string {
  if (typeof detail === 'string') {
    return detail;
  }
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return detail.message;
  }
  return '';
}

async function readSseResponse(response: Response, signal: AbortSignal, onEvent: (event: WorkflowEvent) => void): Promise<void> {
  if (!response.body) {
    parseSseEvents(await response.text()).forEach(onEvent);
    return;
  }
  const reader = response.body.getReader();
  const cancelReader = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener('abort', cancelReader, { once: true });
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop() ?? '';
      parts.flatMap(parseSseEvents).forEach(onEvent);
    }
    buffer += decoder.decode();
    parseSseEvents(buffer).forEach(onEvent);
  } finally {
    signal.removeEventListener('abort', cancelReader);
  }
}

export function parseSseEvents(input: string): WorkflowEvent[] {
  return input
    .split(/\r?\n\r?\n/)
    .map((block) => parseSseBlock(block))
    .filter((event): event is WorkflowEvent => Boolean(event));
}

export function parsePrometheusMetrics(input: string): ObservabilityMetrics {
  const summary: ObservabilityMetrics = {
    workflowEventsTotal: 0,
    tracesTotal: 0,
    failedTracesTotal: 0,
    traceDurationP95Ms: 0,
    userFeedbackTotal: 0,
    llmCallsTotal: 0,
    llmCallsFailedTotal: 0,
    llmInputTokensTotal: 0,
    llmOutputTokensTotal: 0,
    llmEstimatedCostTotal: 0,
    projectExportsTotal: 0,
    projectExportsFailedTotal: 0,
    eventsByStatus: {},
    eventsByType: {}
  };
  for (const line of input.split(/\r?\n/)) {
    if (!line || line.startsWith('#')) {
      continue;
    }
    const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+(-?\d+(?:\.\d+)?)/);
    if (!match) {
      continue;
    }
    const [, name, labels, rawValue] = match;
    const value = Number(rawValue);
    if (!Number.isFinite(value)) {
      continue;
    }
    if (name === 'midea_workflow_events_total') summary.workflowEventsTotal = value;
    if (name === 'midea_agent_traces_total') summary.tracesTotal = value;
    if (name === 'midea_agent_traces_failed_total') summary.failedTracesTotal = value;
    if (name === 'midea_trace_duration_ms_p95') summary.traceDurationP95Ms = value;
    if (name === 'midea_user_feedback_total') summary.userFeedbackTotal = value;
    if (name === 'midea_llm_calls_total') summary.llmCallsTotal = value;
    if (name === 'midea_llm_calls_failed_total') summary.llmCallsFailedTotal = value;
    if (name === 'midea_llm_input_tokens_total') summary.llmInputTokensTotal = value;
    if (name === 'midea_llm_output_tokens_total') summary.llmOutputTokensTotal = value;
    if (name === 'midea_llm_estimated_cost_total') summary.llmEstimatedCostTotal = value;
    if (name === 'midea_project_exports_total') summary.projectExportsTotal = value;
    if (name === 'midea_project_exports_failed_total') summary.projectExportsFailedTotal = value;
    const parsedLabels = labels ? parseMetricLabels(labels) : {};
    if (name === 'midea_workflow_events_by_status_total' && parsedLabels.status) {
      summary.eventsByStatus[parsedLabels.status] = value;
    }
    if (name === 'midea_workflow_events_by_type_total' && parsedLabels.event_type) {
      summary.eventsByType[parsedLabels.event_type] = value;
    }
  }
  return summary;
}

function parseMetricLabels(input: string): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const part of input.match(/[a-zA-Z_][a-zA-Z0-9_]*="(?:\\.|[^"])*"/g) ?? []) {
    const separator = part.indexOf('=');
    const key = part.slice(0, separator);
    const value = part.slice(separator + 2, -1).replace(/\\"/g, '"').replace(/\\n/g, '\n').replace(/\\\\/g, '\\');
    labels[key] = value;
  }
  return labels;
}

function parseSseBlock(block: string): WorkflowEvent | null {
  const lines = block.split(/\r?\n/);
  let id = '';
  let eventType = 'message';
  const dataLines: string[] = [];
  for (const line of lines) {
    if (!line || line.startsWith(':')) {
      continue;
    }
    const separator = line.indexOf(':');
    const field = separator >= 0 ? line.slice(0, separator) : line;
    const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, '') : '';
    if (field === 'id') {
      id = value;
    } else if (field === 'event') {
      eventType = value || eventType;
    } else if (field === 'data') {
      dataLines.push(value);
    }
  }
  if (!id && !dataLines.length) {
    return null;
  }
  const data = dataLines.join('\n');
  let payload: unknown = {};
  try {
    payload = data ? JSON.parse(data) : {};
  } catch {
    payload = { message: data };
  }
  const item = payload && typeof payload === 'object' ? (payload as Partial<WorkflowEvent>) : {};
  const eventId = String(item.event_id ?? id);
  const threadId = String(item.thread_id ?? '');
  if (!eventId || !threadId) {
    return null;
  }
  return {
    event_id: eventId,
    trace_id: item.trace_id ?? null,
    thread_id: threadId,
    project_id: item.project_id ?? null,
    version_id: item.version_id ?? null,
    event_type: String(item.event_type ?? eventType),
    step: item.step ?? null,
    status: item.status ?? null,
    message: item.message ?? null,
    payload: item.payload && typeof item.payload === 'object' ? (item.payload as Record<string, unknown>) : null,
    created_at: item.created_at ?? null
  };
}

function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        window.clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true }
    );
  });
}
