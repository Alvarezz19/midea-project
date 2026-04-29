import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AppProviders } from '../../app/providers';
import { useWorkbenchStore } from '../../store/workbenchStore';
import { VersionHistoryPanel } from './VersionHistoryPanel';

describe('VersionHistoryPanel', () => {
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

  it('confirms rollback inline and updates current version state', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/projects/project_1/versions') {
        return new Response(
          JSON.stringify({
            project_id: 'project_1',
            versions: [
              {
                project_id: 'project_1',
                version_id: 'v_1',
                parent_version_id: null,
                exportable: true,
                validation_summary: { valid: true, error_count: 0, warning_count: 0 },
                summary: { node_count: 10, tab_count: 2 }
              },
              {
                project_id: 'project_1',
                version_id: 'v_2',
                parent_version_id: 'v_1',
                exportable: true,
                validation_summary: { valid: true, error_count: 0, warning_count: 0 },
                summary: { node_count: 11, tab_count: 2 }
              }
            ]
          }),
          { status: 200 }
        );
      }
      if (url === '/api/projects/project_1/rollback' && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            project_id: 'project_1',
            target_version_id: 'v_1',
            state: {
              messages: [],
              project_type: 'ahu',
              project_id: 'project_1',
              version_id: 'v_1',
              current_project_id: 'project_1',
              current_project_version_id: 'v_1',
              status: 'rolled_back',
              validation_summary: { valid: true, exportable: true, error_count: 0, warning_count: 0, blocked_export_reasons: [] }
            },
            validation_report: { valid: true }
          }),
          { status: 200 }
        );
      }
      return new Response(JSON.stringify({ project_id: 'project_1', diff: { summary: {} } }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      state: {
        messages: [],
        project_type: 'ahu',
        project_id: 'project_1',
        version_id: 'v_2',
        current_project_id: 'project_1',
        current_project_version_id: 'v_2'
      }
    });

    render(
      <AppProviders>
        <VersionHistoryPanel />
      </AppProviders>
    );

    expect(await screen.findByText(/模板创建的初始版本/)).toBeInTheDocument();
    const targetVersionRow = screen.getByText('v_1').closest('.ant-list-item');
    expect(targetVersionRow).toBeTruthy();
    fireEvent.click(within(targetVersionRow as HTMLElement).getByRole('button', { name: /回滚/ }));
    expect(await screen.findByText(/准备回滚到 v_1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /确认回滚/ }));

    await waitFor(() => expect(useWorkbenchStore.getState().state?.current_project_version_id).toBe('v_1'));
    expect(fetchMock).toHaveBeenCalledWith('/api/projects/project_1/rollback', expect.objectContaining({ body: JSON.stringify({ target_version_id: 'v_1' }) }));
  }, 20000);

  it('shows backend rollback rejection and keeps the current version pointer unchanged', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/projects/project_bad/versions') {
        return new Response(
          JSON.stringify({
            project_id: 'project_bad',
            versions: [
              {
                project_id: 'project_bad',
                version_id: 'v_bad',
                parent_version_id: null,
                exportable: false,
                validation_summary: { valid: false, error_count: 1, warning_count: 0 },
                summary: { node_count: 10, tab_count: 2 }
              },
              {
                project_id: 'project_bad',
                version_id: 'v_2',
                parent_version_id: 'v_bad',
                exportable: true,
                validation_summary: { valid: true, error_count: 0, warning_count: 0 },
                summary: { node_count: 11, tab_count: 2 }
              }
            ]
          }),
          { status: 200 }
        );
      }
      if (url === '/api/projects/project_bad/rollback' && init?.method === 'POST') {
        return new Response(
          JSON.stringify({
            detail: {
              message: '目标版本校验未通过，拒绝回滚。'
            }
          }),
          { status: 400 }
        );
      }
      return new Response(JSON.stringify({ project_id: 'project_bad', diff: { summary: {} } }), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    useWorkbenchStore.setState({
      state: {
        messages: [],
        project_type: 'ahu',
        project_id: 'project_bad',
        version_id: 'v_2',
        current_project_id: 'project_bad',
        current_project_version_id: 'v_2'
      }
    });

    render(
      <AppProviders>
        <VersionHistoryPanel />
      </AppProviders>
    );

    expect(await screen.findByText(/模板创建的初始版本/)).toBeInTheDocument();
    const targetVersionRow = screen.getByText('v_bad').closest('.ant-list-item');
    expect(targetVersionRow).toBeTruthy();
    fireEvent.click(within(targetVersionRow as HTMLElement).getByRole('button', { name: /回滚/ }));
    expect(await screen.findByText(/准备回滚到 v_bad/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /确认回滚/ }));

    expect(await screen.findByText('目标版本校验未通过，拒绝回滚。')).toBeInTheDocument();
    expect(useWorkbenchStore.getState().state?.current_project_version_id).toBe('v_2');
  }, 20000);
});
