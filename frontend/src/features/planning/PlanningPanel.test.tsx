import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AppProviders } from '../../app/providers';
import { useWorkbenchStore } from '../../store/workbenchStore';
import { PlanningPanel } from './PlanningPanel';

describe('PlanningPanel', () => {
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

  it('approves a pending risk patch only through the confirmation API', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/sessions/thread_1/patch-confirmation' && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            thread_id: 'thread_1',
            trace_id: 'trace_4',
            state: {
              messages: [],
              project_type: 'plant_room',
              project_id: 'project_1',
              version_id: 'v_2',
              status: 'project_version_ready',
              next_action: 'send_message',
              pending_confirmation_patch: null,
              validation_summary: { valid: true, exportable: true, error_count: 0, warning_count: 0, blocked_export_reasons: [] }
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
      traceId: 'trace_3',
      projectType: 'plant_room',
      state: {
        messages: [],
        project_type: 'plant_room',
        project_id: 'project_1',
        version_id: 'v_1',
        status: 'awaiting_patch_confirmation',
        next_action: 'confirm_patch',
        pending_confirmation_patch: {
          op: 'disconnect',
          target_node_selector: { id: '2853d21' },
          target_input: 0
        },
        risk_assessment: {
          risk_level: 'medium',
          requires_confirmation: true,
          reasons: ['断开连线会改变保护链路，需要确认。']
        },
        planner_dry_run: {
          valid: true,
          diff: {
            summary: { affected_node_count: 1, modified_count: 1 },
            affected_node_ids: ['2853d21']
          }
        }
      }
    });

    render(
      <AppProviders>
        <PlanningPanel />
      </AppProviders>
    );

    expect(screen.getByText('确认后才会创建新版本；取消不会改变当前版本。')).toBeInTheDocument();
    expect(screen.getByText('断开连线会改变保护链路，需要确认。')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /确认应用/ }));
    const confirmButtons = await screen.findAllByRole('button', { name: /确认应用/ });
    await userEvent.click(confirmButtons[confirmButtons.length - 1]);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/sessions/thread_1/patch-confirmation', expect.any(Object)));
    const confirmCall = fetchMock.mock.calls.find((call) => call[0] === '/api/sessions/thread_1/patch-confirmation') as [string, RequestInit] | undefined;
    expect(confirmCall?.[1].body).toBe(JSON.stringify({ action: 'approve' }));
    expect(useWorkbenchStore.getState().state?.version_id).toBe('v_2');
    expect(useWorkbenchStore.getState().state?.pending_confirmation_patch).toBeNull();
  });

  it('keeps raw patch JSON folded behind the developer entry', () => {
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'ahu',
      state: {
        messages: [],
        project_type: 'ahu',
        status: 'project_version_ready',
        planner_result: {
          status: 'planned',
          risk_level: 'low',
          pending_patch: {
            op: 'replace_constant',
            node_selector: { id: 'setpoint_1' },
            new_value: 24
          }
        },
        planner_dry_run: {
          valid: true,
          diff: {
            summary: { affected_node_count: 1, modified_count: 1 },
            affected_node_ids: ['setpoint_1']
          }
        }
      }
    });

    render(
      <AppProviders>
        <PlanningPanel />
      </AppProviders>
    );

    expect(screen.getByText(/替换设定值/)).toBeInTheDocument();
    expect(screen.queryByText('"op": "replace_constant"')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('开发者补丁 JSON'));

    expect(screen.getByText(/"op": "replace_constant"/)).toBeInTheDocument();
  });
});
