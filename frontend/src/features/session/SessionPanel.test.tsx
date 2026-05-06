import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppProviders } from '../../app/providers';
import { useWorkbenchStore } from '../../store/workbenchStore';
import { SessionPanel } from './SessionPanel';

describe('SessionPanel', () => {
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

  it('adds the user message to the conversation before the API response returns', async () => {
    let resolveResponse!: (response: Response) => void;
    const pendingResponse = new Promise<Response>((resolve) => {
      resolveResponse = resolve;
    });
    const fetchMock = vi.fn(async () => pendingResponse);
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'ahu',
      state: {
        messages: [{ role: 'assistant', content: '需要补充排风机页面或控制页面的信息。' }],
        project_type: 'ahu',
        status: 'project_version_ready',
        next_action: 'send_message'
      }
    });

    render(
      <AppProviders>
        <SessionPanel />
      </AppProviders>
    );

    const textarea = screen.getByPlaceholderText('例如：我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯');
    await userEvent.type(textarea, '复制一个排风机控制功能块到控制页面。');
    await userEvent.click(screen.getByRole('button', { name: /发送/ }));

    expect(screen.getByText('复制一个排风机控制功能块到控制页面。')).toBeInTheDocument();
    expect(textarea).toHaveValue('');
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/sessions/thread_1/message', expect.any(Object)));

    resolveResponse(
      new Response(
        JSON.stringify({
          thread_id: 'thread_1',
          trace_id: 'trace_1',
          state: {
            messages: [
              { role: 'assistant', content: '需要补充排风机页面或控制页面的信息。' },
              { role: 'user', content: '复制一个排风机控制功能块到控制页面。' },
              { role: 'assistant', content: '正在分析可复制的功能块。' }
            ],
            project_type: 'ahu',
            status: 'running',
            next_action: 'send_message'
          }
        }),
        { status: 200 }
      )
    );

    await waitFor(() => expect(screen.getByText('正在分析可复制的功能块。')).toBeInTheDocument());
  });

  it('renders normalized requirement summary fields from the backend contract', () => {
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'ahu',
      state: {
        messages: [],
        project_type: 'ahu',
        status: 'awaiting_template_confirmation',
        next_action: 'confirm_template',
        requirement_summary: {
          project_type: 'ahu',
          equipment: ['直膨机', '排风机'],
          control_features: ['CO2 控制'],
          communication: ['Modbus'],
          io_points: ['过滤网报警'],
          protection_logic: ['防冻保护']
        }
      }
    });

    render(
      <AppProviders>
        <SessionPanel />
      </AppProviders>
    );

    expect(screen.getByText('直膨机')).toBeInTheDocument();
    expect(screen.getByText('排风机')).toBeInTheDocument();
    expect(screen.getByText('CO2 控制')).toBeInTheDocument();
    expect(screen.getByText('Modbus')).toBeInTheDocument();
    expect(screen.getByText('过滤网报警')).toBeInTheDocument();
    expect(screen.getByText('防冻保护')).toBeInTheDocument();
  });

  it('renders design brief plans in the session context', () => {
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'plant_room',
      state: {
        messages: [],
        project_type: 'plant_room',
        status: 'awaiting_template_confirmation',
        next_action: 'confirm_template',
        design_brief: {
          summary: '推荐模板：风冷热泵标准控制程序；设备：水泵、旁通阀；控制：压差控制',
          selected_template: { template_id: 'tpl_plant', file_name: '风冷热泵标准控制程序.json' },
          equipment_plan: ['水泵', '旁通阀'],
          control_plan: ['压差控制'],
          point_plan: ['Modbus'],
          protection_plan: ['运行反馈'],
          clarification_items: ['请确认关键设备数量。']
        }
      }
    });

    render(
      <AppProviders>
        <SessionPanel />
      </AppProviders>
    );

    expect(screen.getByText('轻量设计摘要')).toBeInTheDocument();
    expect(screen.getByText(/推荐模板：风冷热泵标准控制程序/)).toBeInTheDocument();
    expect(screen.getByText('压差控制')).toBeInTheDocument();
    expect(screen.getByText('请确认关键设备数量。')).toBeInTheDocument();
  });

  it('lets the user select a semantic candidate without exposing the internal node id', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          thread_id: 'thread_1',
          trace_id: 'trace_2',
          state: {
            messages: [{ role: 'assistant', content: '已收到候选选择。' }],
            project_type: 'plant_room',
            status: 'running',
            next_action: 'send_message',
            semantic_target_candidates: []
          }
        }),
        { status: 200 }
      )
    );
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'plant_room',
      state: {
        messages: [{ role: 'assistant', content: '我找到了多个比较判断，请确认要修改哪一个。' }],
        project_type: 'plant_room',
        status: 'needs_clarification',
        next_action: 'clarify_patch',
        semantic_target_candidates: [
          {
            candidate_id: 'candidate_1',
            display_name: '水泵控制 / 比较判断 / compare',
            description: '当前阈值 2，上游为水泵运行台数',
            confidence: 0.91,
            selector: { id: 'node_secret_1' },
            tab_label: '水泵控制',
            type: 'compare',
            key_params: { tripPoint: 2 }
          }
        ]
      }
    });

    render(
      <AppProviders>
        <SessionPanel />
      </AppProviders>
    );

    expect(screen.getByText('请选择目标对象')).toBeInTheDocument();
    expect(screen.getByText('水泵控制 / 比较判断 / compare')).toBeInTheDocument();
    expect(screen.getByText('当前阈值 2，上游为水泵运行台数')).toBeInTheDocument();
    expect(screen.queryByText('node_secret_1')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /选\s*择/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/sessions/thread_1/message', expect.any(Object)));
    const [, request] = fetchMock.mock.calls[0] as unknown as [RequestInfo | URL, RequestInit];
    expect(request.body).toBe(
      JSON.stringify({
        message: '选择第 1 个：水泵控制 / 比较判断 / compare',
        project_type: 'plant_room',
        selected_candidate_id: 'candidate_1',
        use_llm_planner: false
      })
    );
  });
});
