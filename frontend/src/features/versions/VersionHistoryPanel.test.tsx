import { fireEvent, render, screen } from '@testing-library/react';
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

    await settle();
    expect(screen.getByText(/模板创建的初始版本/)).toBeInTheDocument();
    const rollbackButton = screen.getAllByRole('button', { name: /回滚/ }).find((button) => button.textContent?.trim() === '回滚' && !button.hasAttribute('disabled'));
    expect(rollbackButton).toBeTruthy();
    fireEvent.click(rollbackButton!);
    await settle();
    expect(screen.getByText(/准备回滚到 v_1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /确认回滚/ }));

    await settle();
    expect(useWorkbenchStore.getState().state?.current_project_version_id).toBe('v_1');
    expect(fetchMock).toHaveBeenCalledWith('/api/projects/project_1/rollback', expect.objectContaining({ body: JSON.stringify({ target_version_id: 'v_1' }) }));
  }, 10000);
});

function settle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 80));
}
