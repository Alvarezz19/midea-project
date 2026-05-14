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
    expect(screen.getByText('自动定位 node_1')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '定位 node_1' }));

    await waitFor(() => expect(useWorkbenchStore.getState().selectedNodeId).toBe('node_1'));
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/projects/project_1/versions/v_2/flow?max_nodes=120&max_edges=260&max_chars=160000&center_node_id=node_1&focus_node_ids=node_1',
      expect.any(Object)
    );
  });

  it('uses a strict tab-scoped flow request without synthetic diff nodes', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/projects/project_1/diff')) {
        return new Response(JSON.stringify({ project_id: 'project_1', diff: { summary: {} } }), { status: 200 });
      }
      if (url.startsWith('/api/projects/project_1/versions/v_2/flow')) {
        const params = new URL(url, 'http://localhost').searchParams;
        const tabId = params.get('tab_id');
        return new Response(
          JSON.stringify({
            project_id: 'project_1',
            version_id: 'v_2',
            flow: {
              nodes: tabId
                ? [
                    {
                      id: 'timer_1',
                      type: 'engineeringNode',
                      position: { x: 100, y: 80 },
                      data: { label: 'TIME_EN', module_type: 'swInput', role: 'input', tab_id: 'tab_timer', tab_label: '定时', inputs: 2, outputs: 2 }
                    }
                  ]
                : [
                    {
                      id: 'outside_added',
                      type: 'engineeringNode',
                      position: { x: 100, y: 80 },
                      data: { label: '外页新增', module_type: 'constant', role: 'unknown', tab_id: 'tab_control', tab_label: '控制' }
                    }
                  ],
              edges: [],
              tabs: [
                { id: 'tab_control', label: '控制' },
                { id: 'tab_timer', label: '定时' }
              ],
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
        planner_dry_run: {
          valid: true,
          diff: {
            summary: { added_count: 1, removed_count: 0, modified_count: 0, affected_node_count: 1 },
            affected_node_ids: ['outside_added'],
            added: [{ node_id: 'outside_added', type: 'constant', name: '外页新增', tab_id: 'tab_control' }],
            removed: [],
            modified: []
          }
        }
      }
    });

    render(
      <AppProviders>
        <GraphPanel />
      </AppProviders>
    );

    fireEvent.click(await screen.findByRole('button', { name: '定时' }));
    expect(await screen.findByText('TIME_EN')).toBeInTheDocument();

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return (
            url === '/api/projects/project_1/versions/v_2/flow?max_nodes=120&max_edges=260&max_chars=160000&tab_id=tab_timer'
          );
        })
      ).toBe(true)
    );
  });

  it('shows dry-run added nodes and risk IO changes before they are saved', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/projects/project_1/diff')) {
        return new Response(JSON.stringify({ project_id: 'project_1', diff: { summary: {} } }), { status: 200 });
      }
      if (url.startsWith('/api/projects/project_1/versions/v_2/flow')) {
        return new Response(
          JSON.stringify({
            project_id: 'project_1',
            version_id: 'v_2',
            flow: {
              nodes: [
                {
                  id: 'target_1',
                  type: 'engineeringNode',
                  position: { x: 280, y: 80 },
                  data: { label: '新风阀控制', module_type: 'channel_selector', role: 'logic', tab_label: '控制', inputs: 2, outputs: 1 }
                }
              ],
              edges: [],
              tabs: [{ id: 'tab_control', label: '控制' }],
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
        pending_confirmation_patch: { op: 'set_io_point', node_selector: { id: 'target_1' }, params: { modbusAddress: 12 } },
        planner_dry_run: {
          valid: true,
          changes: [{ op: 'set_io_point', node_id: 'target_1', field: 'modbusAddress', old_value: 1, new_value: 12 }],
          diff: {
            summary: { added_count: 1, removed_count: 0, modified_count: 1, affected_node_count: 2 },
            affected_node_ids: ['added_1', 'target_1'],
            added: [{ node_id: 'added_1', type: 'constant', name: 'CO2 设定', tab_id: 'tab_control' }],
            removed: [],
            modified: [{ node_id: 'target_1', type: 'channel_selector', name: '新风阀控制', field_changes: [{ field: 'modbusAddress' }] }]
          }
        },
        risk_assessment: { risk_level: 'high', requires_confirmation: true }
      }
    });

    render(
      <AppProviders>
        <GraphPanel />
      </AppProviders>
    );

    expect((await screen.findAllByText('CO2 设定')).length).toBeGreaterThan(0);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return url.includes('center_node_id=added_1');
        })
      ).toBe(false)
    );
    fireEvent.click(screen.getByRole('radio', { name: '修改前' }));
    await waitFor(() => expect(screen.queryByText('CO2 设定')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('radio', { name: 'diff 高亮' }));
    expect((await screen.findAllByText('CO2 设定')).length).toBeGreaterThan(0);
    expect(screen.getByText('自动定位 added_1')).toBeInTheDocument();
    expect(screen.getByText(/target_1 · channel_selector · 字段 modbusAddress/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '控制' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return url === '/api/projects/project_1/versions/v_2/flow?max_nodes=120&max_edges=260&max_chars=160000&tab_id=tab_control';
        })
      ).toBe(true)
    );
    fireEvent.click(screen.getByRole('button', { name: '定位 target_1' }));
    await waitFor(() => expect(useWorkbenchStore.getState().selectedNodeId).toBe('target_1'));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) => {
          const url = String(input);
          return (
            url ===
            '/api/projects/project_1/versions/v_2/flow?max_nodes=120&max_edges=260&max_chars=160000&center_node_id=target_1&focus_node_ids=target_1&tab_id=tab_control'
          );
        })
      ).toBe(true)
    );
  });

  it('uses last affected nodes to focus the local graph when no diff is available yet', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/projects/project_1/diff')) {
        return new Response(
          JSON.stringify({
            project_id: 'project_1',
            from_version_id: 'v_1',
            to_version_id: 'v_2',
            diff: {
              summary: { added_count: 0, removed_count: 0, modified_count: 0, affected_node_count: 0 },
              affected_node_ids: [],
              added: [],
              removed: [],
              modified: []
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
                  id: 'node_memory_1',
                  type: 'engineeringNode',
                  position: { x: 100, y: 80 },
                  data: { label: '演示节点', module_type: 'compare', role: 'compare', tab_label: '水泵控制', inputs: 1, outputs: 1 }
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
        project_type: 'plant_room',
        project_id: 'project_1',
        version_id: 'v_2',
        last_affected_node_ids: ['node_memory_1'],
        last_touched_entities: [
          {
            display_name: '水泵控制 / 演示节点 / compare',
            selector: { id: 'node_memory_1' }
          }
        ]
      }
    });

    render(
      <AppProviders>
        <GraphPanel />
      </AppProviders>
    );

    expect(await screen.findByText('自动定位 水泵控制 / 演示节点 / compare')).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/projects/project_1/versions/v_2/flow?max_nodes=120&max_edges=260&max_chars=160000&center_node_id=node_memory_1&focus_node_ids=node_memory_1',
        expect.any(Object)
      )
    );
  });
});
