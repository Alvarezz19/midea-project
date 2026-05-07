import {
  ApiError,
  apiRequest,
  confirmPatch,
  confirmTemplate,
  formatApiError,
  getCostSummary,
  getProjectDiff,
  getTrendSummary,
  listProjectFeedback,
  listProjectVersions,
  listTraceFeedback,
  parsePrometheusMetrics,
  parseSseEvents,
  rollbackProject,
  submitFeedback,
  validateProject
} from './client';

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

  it('sends template, risk confirmation and validation requests through the frozen contract', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ thread_id: 't1', state: { messages: [] } }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await confirmTemplate('t1', 'tpl_ahu');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/sessions/t1/message',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ message: '确认使用模板 tpl_ahu', selected_template_id: 'tpl_ahu' })
      })
    );

    await confirmPatch('t1', 'approve');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/sessions/t1/patch-confirmation',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ action: 'approve' })
      })
    );

    await validateProject('project_1');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/projects/project_1/validate',
      expect.objectContaining({
        method: 'POST'
      })
    );
  });

  it('submits user feedback with trace and version context', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ feedback_id: 'fb_1', trace_id: 'trace_1', rating: 5, category: 'export', comment: '' })));
    vi.stubGlobal('fetch', fetchMock);

    await submitFeedback({
      trace_id: 'trace_1',
      project_id: 'project_1',
      version_id: 'v_1',
      rating: 5,
      category: 'export',
      comment: '导出后复核通过'
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/feedback',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          trace_id: 'trace_1',
          project_id: 'project_1',
          version_id: 'v_1',
          rating: 5,
          category: 'export',
          comment: '导出后复核通过'
        })
      })
    );
  });

  it('loads feedback by trace and project', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ feedback: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await listTraceFeedback('trace_1');
    expect(fetchMock).toHaveBeenLastCalledWith('/api/traces/trace_1/feedback', expect.any(Object));

    await listProjectFeedback('project_1');
    expect(fetchMock).toHaveBeenLastCalledWith('/api/projects/project_1/feedback', expect.any(Object));
  });

  it('loads cost summary globally and by project', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ total: { calls: 0 }, by_project: [], by_provider_model: [], by_prompt: [], by_date: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await getCostSummary();
    expect(fetchMock).toHaveBeenLastCalledWith('/api/observability/costs?limit=50', expect.any(Object));

    await getCostSummary('project_1');
    expect(fetchMock).toHaveBeenLastCalledWith('/api/observability/costs?limit=50&project_id=project_1', expect.any(Object));
  });

  it('loads trend summary globally and by project', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ project_id: null, buckets: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await getTrendSummary();
    expect(fetchMock).toHaveBeenLastCalledWith('/api/observability/trends?limit=30', expect.any(Object));

    await getTrendSummary('project_1');
    expect(fetchMock).toHaveBeenLastCalledWith('/api/observability/trends?limit=30&project_id=project_1', expect.any(Object));
  });

  it('requests versions, explicit diff and rollback through project APIs', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ project_id: 'project_1', versions: [] }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    await listProjectVersions('project_1');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/projects/project_1/versions',
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' })
      })
    );

    await getProjectDiff('project_1', 'v_2', 'v_1');
    expect(fetchMock).toHaveBeenLastCalledWith('/api/projects/project_1/diff?to_version_id=v_2&from_version_id=v_1', expect.any(Object));

    await rollbackProject('project_1', 'v_1');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/projects/project_1/rollback',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ target_version_id: 'v_1' })
      })
    );
  });

  it('parses SSE events with custom event names and skips duplicates upstream safely', () => {
    const events = parseSseEvents(
      [
        'id: evt_1',
        'event: workflow.run.completed',
        'data: {"event_id":"evt_1","thread_id":"thread_1","trace_id":"trace_1","event_type":"workflow.run.completed","step":"invoke_workflow","status":"completed","message":"工作流完成","payload":{"state":{"next_action":"export"}}}',
        '',
        'id: evt_2',
        'event: workflow.step.started',
        'data: {"event_id":"evt_2","thread_id":"thread_1","event_type":"workflow.step.started","step":"plan_change","status":"running","message":"正在生成结构化修改计划"}',
        ''
      ].join('\n')
    );

    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({
      event_id: 'evt_1',
      thread_id: 'thread_1',
      trace_id: 'trace_1',
      event_type: 'workflow.run.completed',
      step: 'invoke_workflow',
      status: 'completed'
    });
    expect(events[0].payload?.state).toEqual({ next_action: 'export' });
    expect(events[1].message).toBe('正在生成结构化修改计划');
  });

  it('parses Prometheus observability metrics into drawer summary data', () => {
    const metrics = parsePrometheusMetrics(
      [
        '# HELP midea_workflow_events_total 工作流事件总数',
        'midea_workflow_events_total 8',
        'midea_agent_traces_total 3',
        'midea_agent_traces_failed_total 1',
        'midea_trace_duration_ms_p95 1530.5',
        'midea_user_feedback_total 2',
        'midea_llm_calls_total 4',
        'midea_llm_calls_failed_total 1',
        'midea_llm_input_tokens_total 240',
        'midea_llm_output_tokens_total 120',
        'midea_llm_estimated_cost_total 0.012000',
        'midea_project_exports_total 2',
        'midea_project_exports_failed_total 1',
        'midea_workflow_events_by_status_total{status="completed"} 6',
        'midea_workflow_events_by_status_total{status="failed"} 2',
        'midea_workflow_events_by_type_total{event_type="api.export.completed"} 1'
      ].join('\n')
    );

    expect(metrics.workflowEventsTotal).toBe(8);
    expect(metrics.failedTracesTotal).toBe(1);
    expect(metrics.traceDurationP95Ms).toBe(1530.5);
    expect(metrics.llmInputTokensTotal).toBe(240);
    expect(metrics.llmEstimatedCostTotal).toBe(0.012);
    expect(metrics.eventsByStatus.completed).toBe(6);
    expect(metrics.eventsByType['api.export.completed']).toBe(1);
  });
});
