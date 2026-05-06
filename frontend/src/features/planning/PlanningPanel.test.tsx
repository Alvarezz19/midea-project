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

  it('renders backend estimated modification cost reasons', () => {
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'ahu',
      state: {
        messages: [],
        project_type: 'ahu',
        status: 'awaiting_template_confirmation',
        next_action: 'confirm_template',
        template_candidates: [
          {
            template_id: 'tpl_1',
            file_name: 'AHU 六页模板.json',
            score: 0.91,
            node_count: 280,
            tab_count: 6,
            summary: '包含排风机和直膨故障页。',
            matched_items: ['项目类型匹配：AHU 程序', '排风机'],
            missing_items: ['CO2 控制'],
            estimated_modification_cost: {
              level: 'medium',
              reasons: ['需要补齐：CO2 控制', '通讯/IO 方式未确认']
            },
            risk_points: ['设备数量未确认，涉及数量变化时必须人工确认。']
          }
        ]
      }
    });

    render(
      <AppProviders>
        <PlanningPanel />
      </AppProviders>
    );

    expect(screen.getByText(/预计改造：medium，需要补齐：CO2 控制；通讯\/IO 方式未确认/)).toBeInTheDocument();
  });

  it('renders the lightweight design brief from backend state', () => {
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'ahu',
      state: {
        messages: [],
        project_type: 'ahu',
        status: 'awaiting_template_confirmation',
        next_action: 'confirm_template',
        design_brief: {
          summary: '推荐模板：AHU 六页模板.json；设备：排风机、直膨机；控制：CO2 控制',
          selected_template: {
            template_id: 'tpl_1',
            file_name: 'AHU 六页模板.json',
            score: 12.4
          },
          recommendation_reasons: ['包含独立排风机/直膨机故障页面。'],
          satisfied_requirements: ['排风机：模板已包含排风机', '直膨机：模板已包含直膨机'],
          modification_items: ['CO2 控制：需要后续补丁或人工确认'],
          equipment_plan: ['排风机', '直膨机'],
          control_plan: ['CO2 控制'],
          point_plan: ['Modbus'],
          protection_plan: ['故障报警'],
          export_gate: ['导出前必须通过工程结构校验。']
        },
        template_candidates: []
      }
    });

    render(
      <AppProviders>
        <PlanningPanel />
      </AppProviders>
    );

    expect(screen.getByText('设计摘要')).toBeInTheDocument();
    expect(screen.getByText(/推荐模板：AHU 六页模板/)).toBeInTheDocument();
    expect(screen.getByText('CO2 控制')).toBeInTheDocument();
    expect(screen.getByText('需改造或复核')).toBeInTheDocument();
  });

  it('renders semantic target candidates and touched entities in user-readable form', () => {
    useWorkbenchStore.setState({
      threadId: 'thread_1',
      projectType: 'plant_room',
      state: {
        messages: [],
        project_type: 'plant_room',
        status: 'needs_clarification',
        semantic_target_candidates: [
          {
            candidate_id: 'candidate_1',
            display_name: '旁通阀控制 / 比较判断 / compare',
            description: '当前阈值 45，上游为旁通阀压差信号',
            confidence: 0.82,
            selector: { id: 'node_internal_1' },
            tab_label: '旁通阀控制',
            type: 'compare'
          }
        ],
        last_touched_entities: [
          {
            display_name: '水泵控制 / 工作流规划-水泵比较节点',
            description: '已重命名并通过 dry-run',
            selector: { id: 'node_internal_2' }
          }
        ]
      }
    });

    render(
      <AppProviders>
        <PlanningPanel />
      </AppProviders>
    );

    expect(screen.getByText('语义定位')).toBeInTheDocument();
    expect(screen.getByText(/旁通阀控制 \/ 比较判断 \/ compare/)).toBeInTheDocument();
    expect(screen.getByText('当前阈值 45，上游为旁通阀压差信号')).toBeInTheDocument();
    expect(screen.getByText('水泵控制 / 工作流规划-水泵比较节点')).toBeInTheDocument();
    expect(screen.queryByText('node_internal_1')).not.toBeInTheDocument();
    expect(screen.queryByText('node_internal_2')).not.toBeInTheDocument();
  });
});
