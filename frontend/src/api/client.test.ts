import { ApiError, apiRequest, formatApiError } from './client';

describe('api client', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('converts backend detail into displayable error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ detail: { message: '工程校验未通过，拒绝导出。' } }), { status: 400 }))
    );

    await expect(apiRequest('/api/projects/demo/export')).rejects.toThrow(ApiError);
    try {
      await apiRequest('/api/projects/demo/export');
    } catch (error) {
      expect(formatApiError(error)).toBe('工程校验未通过，拒绝导出。');
    }
  });
});
