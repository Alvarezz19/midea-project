import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppProviders } from '../../app/providers';
import { TracePage } from './TracePage';

describe('TracePage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders trace metadata, event timeline and payload summaries', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            trace_id: 'trace_1',
            thread_id: 'thread_1',
            project_id: 'project_1',
            version_id: 'v_1',
            root_input: '把 AHU 送风温度设定值改成 24 度',
            status: 'completed',
            started_at: '2026-04-29T08:00:00Z',
            finished_at: '2026-04-29T08:00:03Z',
            llm_calls: [
              {
                llm_call_id: 'llm_1',
                trace_id: 'trace_1',
                provider: 'deepseek',
                model: 'deepseek-chat',
                prompt_name: 'llm_planner',
                attempt: 1,
                latency_ms: 1234,
                input_tokens: 120,
                output_tokens: 80,
                estimated_cost: 0,
                status: 'completed',
                error: null
              }
            ],
            feedback: [
              {
                feedback_id: 'fb_1',
                trace_id: 'trace_1',
                project_id: 'project_1',
                version_id: 'v_1',
                rating: 5,
                category: 'export',
                comment: '导出后复核通过',
                created_at: '2026-04-29T08:00:04Z'
              }
            ],
            events: [
              {
                event_id: 'evt_1',
                trace_id: 'trace_1',
                thread_id: 'thread_1',
                project_id: 'project_1',
                version_id: 'v_1',
                event_type: 'workflow.step.completed',
                step: 'requirement_analysis',
                status: 'completed',
                message: '结构化需求分析已完成',
                payload: { node: 'collect_requirements', updated_fields: ['requirement_summary', 'status'] },
                created_at: '2026-04-29T08:00:01Z'
              },
              {
                event_id: 'evt_2',
                trace_id: 'trace_1',
                thread_id: 'thread_1',
                project_id: 'project_1',
                version_id: 'v_1',
                event_type: 'workflow.dry_run.completed',
                step: 'dry_run',
                status: 'completed',
                message: '补丁 dry-run 已完成',
                payload: {
                  dry_run: {
                    valid: true,
                    change_count: 1,
                    diff_summary: { added_count: 0, removed_count: 0, modified_count: 1, affected_node_count: 1 },
                    validation_summary: { valid: true, exportable: true, error_count: 0, warning_count: 0 }
                  }
                },
                created_at: '2026-04-29T08:00:02Z'
              },
              {
                event_id: 'evt_3',
                trace_id: 'trace_1',
                thread_id: 'thread_1',
                project_id: 'project_1',
                version_id: 'v_1',
                event_type: 'workflow.validation.completed',
                step: 'validation',
                status: 'completed',
                message: '工程校验已完成',
                payload: { validation_summary: { valid: true, exportable: true, error_count: 0 } },
                created_at: '2026-04-29T08:00:03Z'
              },
              {
                event_id: 'evt_4',
                trace_id: 'trace_1',
                thread_id: 'thread_1',
                project_id: 'project_1',
                version_id: 'v_1',
                event_type: 'api.export.completed',
                step: 'export',
                status: 'completed',
                message: '工程导出已通过校验',
                payload: { validation_summary: { valid: true, exportable: true, error_count: 0, warning_count: 0 } },
                created_at: '2026-04-29T08:00:04Z'
              }
            ]
          }),
          { status: 200 }
        )
    );
    vi.stubGlobal('fetch', fetchMock);

    render(
      <AppProviders>
        <MemoryRouter initialEntries={['/traces/trace_1']}>
          <Routes>
            <Route path="/traces/:traceId" element={<TracePage />} />
          </Routes>
        </MemoryRouter>
      </AppProviders>
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/traces/trace_1', expect.any(Object)));
    expect(await screen.findByText('Trace 详情')).toBeInTheDocument();
    expect(screen.getByText('project_1')).toBeInTheDocument();
    expect(screen.getByText('把 AHU 送风温度设定值改成 24 度')).toBeInTheDocument();
    expect(screen.getByText('结构化需求分析已完成')).toBeInTheDocument();
    expect(screen.getByText('workflow.validation.completed')).toBeInTheDocument();
    expect(screen.getAllByText('validation_summary').length).toBeGreaterThan(0);
    expect(screen.getByText('关键产物')).toBeInTheDocument();
    expect(screen.getByText('计划 / dry-run')).toBeInTheDocument();
    expect(screen.getByText('导出')).toBeInTheDocument();
    expect(screen.getByText(/新增 0 \/ 删除 0 \/ 修改 1 \/ 影响 1/)).toBeInTheDocument();
    expect(screen.getByText('llm_planner')).toBeInTheDocument();
    expect(screen.getByText(/deepseek\/deepseek-chat/)).toBeInTheDocument();
    expect(screen.getByText('120 in')).toBeInTheDocument();
    expect(screen.getByText('用户反馈')).toBeInTheDocument();
    expect(screen.getByText('导出后复核通过')).toBeInTheDocument();
  });
});
