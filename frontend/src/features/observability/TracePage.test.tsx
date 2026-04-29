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
                event_type: 'workflow.validation.completed',
                step: 'validation',
                status: 'completed',
                message: '工程校验已完成',
                payload: { validation_summary: { valid: true, exportable: true, error_count: 0 } },
                created_at: '2026-04-29T08:00:03Z'
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
    expect(screen.getByText('validation_summary')).toBeInTheDocument();
  });
});
