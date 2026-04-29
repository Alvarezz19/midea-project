import { z } from 'zod';
import type {
  ApiProblem,
  FeedbackPayload,
  FeedbackResponse,
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

export function getProjectFlow(projectId: string, versionId: string, centerNodeId?: string): Promise<ProjectFlowResponse> {
  const params = new URLSearchParams({
    max_nodes: '120',
    max_edges: '260',
    max_chars: '160000'
  });
  if (centerNodeId) {
    params.set('center_node_id', centerNodeId);
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
