import { z } from 'zod';
import type { ApiProblem, ProjectType, SessionResponse } from './types';

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

export function createSession(projectType?: ProjectType): Promise<SessionResponse> {
  return apiRequest<SessionResponse>('/api/sessions', {
    method: 'POST',
    body: JSON.stringify({
      project_type: projectType,
      auto_confirm_template: false
    })
  });
}

export function sendMessage(threadId: string, message: string, projectType?: ProjectType): Promise<SessionResponse> {
  return apiRequest<SessionResponse>(`/api/sessions/${threadId}/message`, {
    method: 'POST',
    body: JSON.stringify({
      message,
      project_type: projectType
    })
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
