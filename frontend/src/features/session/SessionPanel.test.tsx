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
});
