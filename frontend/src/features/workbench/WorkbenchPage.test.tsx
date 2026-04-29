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
      workflowEvents: [],
      lastWorkflowEventId: undefined,
      eventConnectionStatus: 'idle',
      selectedNodeId: undefined,
      inspectedVersionId: undefined,
      inspectedFromVersionId: undefined,
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
    const confirmCall = (fetchMock.mock.calls as unknown as Array<[string, RequestInit]>).find((call) => call[0] === '/api/sessions/thread_1/message');
    expect(confirmCall?.[1].body).toBe(
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
    const cancelCall = (fetchMock.mock.calls as unknown as Array<[string, RequestInit]>).find((call) => call[0] === '/api/sessions/thread_1/patch-confirmation');
    expect(cancelCall?.[1].body).toBe(JSON.stringify({ action: 'cancel' }));
  });

  it('renders SSE connection state, workflow steps and deduplicated events', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () => {
          const encoder = new TextEncoder();
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  encoder.encode(
                    [
                      'id: evt_3',
                      'event: workflow.step.started',
                      'data: {"event_id":"evt_3","thread_id":"thread_1","trace_id":"trace_1","event_type":"workflow.step.started","step":"plan_change","status":"running","message":"正在生成结构化修改计划"}',
                      '',
                      ''
                    ].join('\n')
                  )
                );
              }
            }),
            { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
          );
        }
      )
    );
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      traceId: 'trace_1',
      projectType: 'ahu',
      eventConnectionStatus: 'connected',
      workflowEvents: [
        {
          event_id: 'evt_1',
          thread_id: 'thread_1',
          trace_id: 'trace_1',
          event_type: 'workflow.message.received',
          step: 'receive_message',
          status: 'completed',
          message: '已接收需求',
          created_at: '2026-04-29T10:00:00+08:00'
        },
        {
          event_id: 'evt_2',
          thread_id: 'thread_1',
          trace_id: 'trace_1',
          event_type: 'workflow.run.completed',
          step: 'invoke_workflow',
          status: 'completed',
          message: '工作流完成',
          created_at: '2026-04-29T10:00:03+08:00'
        }
      ],
      lastWorkflowEventId: 'evt_2',
      bottomDrawerOpen: false,
      state: {
        messages: [],
        project_type: 'ahu',
        status: 'project_version_ready',
        next_action: 'export'
      }
    });

    useWorkbenchStore.getState().mergeWorkflowEvent({
      event_id: 'evt_2',
      thread_id: 'thread_1',
      event_type: 'workflow.run.completed',
      status: 'completed',
      message: '重复事件不应渲染'
    });

    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    await waitFor(() => expect(screen.getAllByText('已连接').length).toBeGreaterThan(0));
    expect(screen.getByText('工作流步骤')).toBeInTheDocument();
    expect(screen.getByText('需求分析')).toBeInTheDocument();
    expect(screen.getByText('规划')).toBeInTheDocument();
    expect(screen.getByText('已接收需求')).toBeInTheDocument();
    expect(screen.queryByText('重复事件不应渲染')).not.toBeInTheDocument();
  });

  it('loads the budgeted flow and focuses a diff node from the graph panel', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/projects/project_1/versions/v_2/flow')) {
        return new Response(
          JSON.stringify({
            project_id: 'project_1',
            version_id: 'v_2',
            flow: {
              nodes: [
                {
                  id: 'node_1',
                  type: 'engineeringNode',
                  position: { x: 120, y: 80 },
                  data: {
                    label: '送风温度设定',
                    module_type: 'compare',
                    role: 'compare',
                    tab_label: '送风控制',
                    inputs: 2,
                    outputs: 1
                  }
                }
              ],
              edges: [],
              budget: { max_nodes: 120, max_edges: 260, max_chars: 160000, node_count: 1, edge_count: 0, truncated: false }
            }
          }),
          { status: 200 }
        );
      }
      if (url.startsWith('/api/projects/project_1/diff')) {
        return new Response(
          JSON.stringify({
            project_id: 'project_1',
            from_version_id: 'v_1',
            to_version_id: 'v_2',
            diff: {
              summary: { added_count: 0, removed_count: 0, modified_count: 1, affected_node_count: 1 },
              affected_node_ids: ['node_1'],
              added: [],
              removed: [],
              modified: [{ node_id: 'node_1', type: 'compare', name: '送风温度设定', field_changes: [{ field: 'tripPoint' }] }]
            }
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });
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
        version_id: 'v_2',
        planner_dry_run: {
          valid: true,
          diff: {
            summary: { added_count: 0, removed_count: 0, modified_count: 1, affected_node_count: 1 },
            affected_node_ids: ['node_1'],
            added: [],
            removed: [],
            modified: [{ node_id: 'node_1', type: 'compare', name: '送风温度设定', field_changes: [{ field: 'tripPoint' }] }]
          }
        },
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

    expect(await screen.findByText('送风温度设定')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '定位 node_1' }));

    await waitFor(() => expect(useWorkbenchStore.getState().selectedNodeId).toBe('node_1'));
  });

  it('shows copy_block boundary preview without applying external connection drafts', () => {
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'plant_room',
      bottomDrawerOpen: false,
      state: {
        messages: [],
        project_type: 'plant_room',
        status: 'awaiting_patch_confirmation',
        planner_dry_run: {
          valid: true,
          diff: { summary: { affected_node_count: 2 }, affected_node_ids: ['copy_1', 'copy_2'] },
          changes: [
            {
              op: 'copy_block',
              boundary_preview: {
                connection_policy: 'not_connected_by_default',
                entry_ports: [
                  {
                    source: { id: 'src_1', name: '压差输入' },
                    source_output: 0,
                    target: { id: 'old_1', name: '旁通阀控制' },
                    target_input: 1
                  }
                ],
                exit_ports: [
                  {
                    source: { id: 'old_2', name: '阀门输出' },
                    source_output: 0,
                    target: { id: 'out_1', name: '旁通阀 AO' },
                    target_input: 0
                  }
                ]
              }
            }
          ]
        }
      }
    });

    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    expect(screen.getByText('copy_block 边界预览')).toBeInTheDocument();
    expect(screen.getByText('外部线默认不接入')).toBeInTheDocument();
    expect(screen.getByText(/压差输入 out-0 -> 旁通阀控制 in-1/)).toBeInTheDocument();
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
  }, 60000);

  it('renders observability cost aggregation in the metrics drawer', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/metrics') {
        return new Response(
          [
            'midea_workflow_events_total 4',
            'midea_agent_traces_total 2',
            'midea_agent_traces_failed_total 0',
            'midea_trace_duration_ms_p95 1200',
            'midea_user_feedback_total 1',
            'midea_llm_calls_total 2',
            'midea_llm_calls_failed_total 0',
            'midea_llm_input_tokens_total 300',
            'midea_llm_output_tokens_total 90',
            'midea_llm_estimated_cost_total 0.004',
            'midea_project_exports_total 1',
            'midea_project_exports_failed_total 0',
            'midea_workflow_events_by_status_total{status="completed"} 4',
            'midea_workflow_events_by_type_total{event_type="api.export.completed"} 1'
          ].join('\n'),
          { status: 200, headers: { 'Content-Type': 'text/plain' } }
        );
      }
      if (url === '/api/observability/costs?limit=50') {
        return new Response(
          JSON.stringify({
            project_id: null,
            total: { calls: 2, failed_calls: 0, input_tokens: 300, output_tokens: 90, estimated_cost: 0.004, average_latency_ms: 600 },
            by_project: [{ project_id: 'project_1', calls: 2, failed_calls: 0, input_tokens: 300, output_tokens: 90, estimated_cost: 0.004, average_latency_ms: 600 }],
            by_provider_model: [{ provider: 'deepseek', model: 'deepseek-chat', calls: 2, failed_calls: 0, input_tokens: 300, output_tokens: 90, estimated_cost: 0.004, average_latency_ms: 600 }],
            by_prompt: [{ prompt_name: 'llm_planner', calls: 2, failed_calls: 0, input_tokens: 300, output_tokens: 90, estimated_cost: 0.004, average_latency_ms: 600 }],
            by_date: [{ date: '2026-04-29', calls: 2, failed_calls: 0, input_tokens: 300, output_tokens: 90, estimated_cost: 0.004, average_latency_ms: 600 }]
          }),
          { status: 200 }
        );
      }
      if (url === '/api/observability/trends?limit=30') {
        return new Response(
          JSON.stringify({
            project_id: null,
            buckets: [
              {
                date: '2026-04-29',
                request_count: 3,
                failed_request_count: 1,
                error_rate: 0.3333,
                p95_duration_ms: 1200,
                llm_calls: 2,
                llm_failed_calls: 0,
                llm_input_tokens: 300,
                llm_output_tokens: 90,
                llm_estimated_cost: 0.004,
                confirmation_approved_count: 1,
                confirmation_cancelled_count: 1,
                confirmation_cancel_rate: 0.5
              }
            ]
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    fireEvent.click(screen.getByRole('button', { name: '版本 / Trace / 反馈' }));
    fireEvent.click(screen.getByRole('tab', { name: '指标' }));

    expect(await screen.findByText('LLM 成本聚合')).toBeInTheDocument();
    expect(screen.getByText('趋势维度')).toBeInTheDocument();
    expect(screen.getByText('deepseek / deepseek-chat')).toBeInTheDocument();
    expect(screen.getByText('llm_planner')).toBeInTheDocument();
    expect(screen.getByText('project_1')).toBeInTheDocument();
  }, 60000);
});
