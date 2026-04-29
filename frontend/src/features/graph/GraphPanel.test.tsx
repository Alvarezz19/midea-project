import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AppProviders } from '../../app/providers';
import { useWorkbenchStore } from '../../store/workbenchStore';
import { GraphPanel } from './GraphPanel';

describe('GraphPanel', () => {
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

  it('renders validation blockers and issue severity without loading full project JSON', () => {
    useWorkbenchStore.setState({
      state: {
        messages: [],
        project_type: 'ahu',
        status: 'validation_failed',
        validation_summary: {
          valid: false,
          exportable: false,
          error_count: 1,
          warning_count: 1,
          blocked_export_reasons: ['目标版本存在 error 级校验问题，拒绝导出。']
        },
        validation_report: {
          issues: [
            { severity: 'error', code: 'dynamic_port_mismatch', message: 'PID 输入端口配置不一致。' },
            { severity: 'warning', code: 'protection_hint_missing', message: '建议补充过滤网报警线索。' }
          ],
          blocked_export_reasons: ['报告中的阻塞原因不应覆盖 summary 优先级。']
        }
      }
    });

    render(
      <AppProviders>
        <GraphPanel />
      </AppProviders>
    );

    expect(screen.getByText('校验摘要')).toBeInTheDocument();
    expect(screen.getByText('目标版本存在 error 级校验问题，拒绝导出。')).toBeInTheDocument();
    expect(screen.getByText('PID 输入端口配置不一致。')).toBeInTheDocument();
    expect(screen.getByText('建议补充过滤网报警线索。')).toBeInTheDocument();
    expect(screen.getByText(/"exportable": false/)).toBeInTheDocument();
  });

  it('focuses an affected node from the diff list and keeps the budgeted flow request scoped', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
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
                  position: { x: 100, y: 80 },
                  data: { label: '送风温度设定', module_type: 'compare', role: 'compare', tab_label: '送风控制', inputs: 2, outputs: 1 }
                }
              ],
              edges: [],
              budget: { max_nodes: 120, max_edges: 260, max_chars: 160000, node_count: 1, edge_count: 0, truncated: false }
            }
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      state: {
        messages: [],
        project_type: 'ahu',
        project_id: 'project_1',
        version_id: 'v_2',
        validation_summary: { valid: true, exportable: true, error_count: 0, warning_count: 0, blocked_export_reasons: [] }
      }
    });

    render(
      <AppProviders>
        <GraphPanel />
      </AppProviders>
    );

    expect((await screen.findAllByText('送风温度设定')).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: '定位 node_1' }));

    await waitFor(() => expect(useWorkbenchStore.getState().selectedNodeId).toBe('node_1'));
    expect(fetchMock).toHaveBeenCalledWith('/api/projects/project_1/versions/v_2/flow?max_nodes=120&max_edges=260&max_chars=160000', expect.any(Object));
  });
});
