import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AppProviders } from '../../app/providers';
import { useWorkbenchStore } from '../../store/workbenchStore';
import { WorkbenchPage } from './WorkbenchPage';

describe('WorkbenchPage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    useWorkbenchStore.setState({
      threadId: undefined,
      traceId: undefined,
      projectType: undefined,
      state: undefined,
      selectedNodeId: undefined,
      bottomDrawerOpen: false,
      useLlmPlanner: false
    });
  });

  it('renders the production workbench shell', () => {
    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    expect(screen.getByText('美的工程智能体工作台')).toBeInTheDocument();
    expect(screen.getByText('会话与需求')).toBeInTheDocument();
    expect(screen.getByText('模板、计划与风险')).toBeInTheDocument();
    expect(screen.getByText('局部流程图与校验')).toBeInTheDocument();
  });

  it('confirms a template candidate through the session API', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            thread_id: 'thread_1',
            trace_id: 'trace_2',
            state: {
              messages: [],
              project_type: 'ahu',
              selected_template_id: 'tpl_ahu',
              status: 'project_version_ready',
              next_action: null,
              validation_summary: { valid: true, exportable: true, error_count: 0, warning_count: 0, blocked_export_reasons: [] }
            }
          }),
          { status: 200 }
        )
    );
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'ahu',
      bottomDrawerOpen: false,
      state: {
        messages: [],
        project_type: 'ahu',
        status: 'awaiting_template_confirmation',
        next_action: 'confirm_template',
        template_candidates: [
          {
            template_id: 'tpl_ahu',
            file_name: 'AHU 模板.json',
            score: 0.92,
            node_count: 120,
            tab_count: 6,
            summary: '适合 AHU 直膨机控制。',
            matched_items: ['AHU', 'Modbus'],
            missing_items: ['排风机反馈'],
            estimated_modification_cost: { level: 'low', reason: '只需少量配置' },
            risk_points: ['确认通讯点位']
          }
        ]
      }
    });

    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    await userEvent.click(screen.getByRole('button', { name: /确\s*认/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/sessions/thread_1/message', expect.any(Object)));
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body).toBe(
      JSON.stringify({ message: '确认使用模板 tpl_ahu', selected_template_id: 'tpl_ahu' })
    );
  });

  it('cancels pending risk confirmation without creating a version', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            thread_id: 'thread_1',
            trace_id: 'trace_3',
            state: {
              messages: [],
              project_type: 'plant_room',
              status: 'patch_confirmation_cancelled',
              next_action: 'send_message',
              pending_confirmation_patch: null
            }
          }),
          { status: 200 }
        )
    );
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'plant_room',
      bottomDrawerOpen: false,
      state: {
        messages: [],
        project_type: 'plant_room',
        status: 'awaiting_patch_confirmation',
        next_action: 'confirm_patch',
        pending_confirmation_patch: { op: 'disconnect', target_input: 0 },
        risk_assessment: { risk_level: 'medium', requires_confirmation: true, reasons: ['disconnect 属于需要确认的结构或连线变更。'] },
        planner_dry_run: { valid: true, diff: { summary: { affected_node_count: 1 } } }
      }
    });

    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    fireEvent.click(screen.getByRole('button', { name: /取消/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/sessions/thread_1/patch-confirmation', expect.any(Object)));
    expect((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body).toBe(JSON.stringify({ action: 'cancel' }));
  });

  it('keeps patch JSON behind the developer collapse and submits feedback', async () => {
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL) => {
        if (String(input) === '/api/feedback') {
          return new Response(JSON.stringify({ feedback_id: 'fb_1', trace_id: 'trace_1', rating: 4, category: 'export', comment: '复核通过' }), {
            status: 200
          });
        }
        return new Response(JSON.stringify({}), { status: 200 });
      }
    );
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      traceId: 'trace_1',
      projectType: 'ahu',
      bottomDrawerOpen: false,
      state: {
        messages: [],
        project_type: 'ahu',
        project_id: 'project_1',
        version_id: 'v_1',
        status: 'project_version_ready',
        planner_result: {
          status: 'planned',
          risk_level: 'low',
          pending_patch: { op: 'rename_node', node_selector: { id: 'node_1' }, new_name: '送风温度设定' }
        },
        planner_dry_run: { valid: true, diff: { summary: { affected_node_count: 1, modified_count: 1 }, affected_node_ids: ['node_1'] } },
        validation_summary: { valid: true, exportable: true, error_count: 0, warning_count: 0, blocked_export_reasons: [] }
      }
    });

    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    expect(screen.getByText(/重命名节点/)).toBeInTheDocument();
    expect(screen.queryByText('"op": "rename_node"')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '版本 / Trace / 反馈' }));
    fireEvent.click(screen.getByRole('tab', { name: '反馈' }));
    fireEvent.change(screen.getByPlaceholderText('记录问题、期望结果或导出后的复核意见'), { target: { value: '复核通过' } });
    fireEvent.click(screen.getByRole('button', { name: /提交反馈/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/feedback', expect.any(Object)));
    const feedbackCall = fetchMock.mock.calls.find((call) => call[0] === '/api/feedback') as [string, RequestInit] | undefined;
    expect(feedbackCall?.[1].body).toBe(
      JSON.stringify({
        trace_id: 'trace_1',
        project_id: 'project_1',
        version_id: 'v_1',
        rating: 4,
        category: 'export',
        comment: '复核通过'
      })
    );
  }, 15000);
});
