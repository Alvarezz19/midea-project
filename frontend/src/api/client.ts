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
  SessionResponse
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
