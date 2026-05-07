import { create } from 'zustand';
import type { EventConnectionStatus, ProjectType, SessionState, WorkflowEvent } from '../api/types';

interface WorkbenchStore {
  threadId?: string;
  traceId?: string;
  projectType?: ProjectType;
  state?: SessionState;
  workflowEvents: WorkflowEvent[];
  lastWorkflowEventId?: string;
  eventConnectionStatus: EventConnectionStatus;
  selectedNodeId?: string;
  inspectedVersionId?: string;
  inspectedFromVersionId?: string;
  bottomDrawerOpen: boolean;
  useLlmPlanner: boolean;
  setProjectType: (projectType: ProjectType) => void;
  setSession: (payload: { threadId: string; traceId?: string; state: SessionState }) => void;
  patchState: (patch: Partial<SessionState>) => void;
  appendMessage: (message: { role: string; content: string }) => void;
  mergeWorkflowEvent: (event: WorkflowEvent) => void;
  setEventConnectionStatus: (status: EventConnectionStatus) => void;
  selectNode: (nodeId?: string) => void;
  inspectVersion: (versionId?: string, fromVersionId?: string) => void;
  setBottomDrawerOpen: (open: boolean) => void;
  setUseLlmPlanner: (enabled: boolean) => void;
}

export const useWorkbenchStore = create<WorkbenchStore>((set) => ({
  workflowEvents: [],
  eventConnectionStatus: 'idle',
  bottomDrawerOpen: false,
  useLlmPlanner: true,
  setProjectType: (projectType) => set({ projectType }),
  setSession: ({ threadId, traceId, state }) =>
    set((store) => {
      const sameThread = store.threadId === threadId;
      return {
        threadId,
        traceId,
        state,
        projectType: state.project_type ?? undefined,
        useLlmPlanner: state.use_llm_planner ?? true,
        inspectedVersionId: undefined,
        inspectedFromVersionId: undefined,
        workflowEvents: sameThread ? store.workflowEvents : [],
        lastWorkflowEventId: sameThread ? store.lastWorkflowEventId : undefined,
        eventConnectionStatus: sameThread ? store.eventConnectionStatus : 'connecting'
      };
    }),
  patchState: (patch) => set((store) => ({ state: store.state ? { ...store.state, ...patch } : store.state })),
  appendMessage: (message) =>
    set((store) => ({
      state: store.state ? { ...store.state, messages: [...(store.state.messages ?? []), message] } : store.state
    })),
  mergeWorkflowEvent: (event) =>
    set((store) => {
      if (store.workflowEvents.some((item) => item.event_id === event.event_id)) {
        return store;
      }
      const workflowEvents = [...store.workflowEvents, event].slice(-100);
      return {
        workflowEvents,
        lastWorkflowEventId: event.event_id,
        traceId: event.trace_id ?? store.traceId,
        state: mergeStateFromEvent(store.state, event),
        eventConnectionStatus: store.eventConnectionStatus === 'idle' ? 'connected' : store.eventConnectionStatus
      };
    }),
  setEventConnectionStatus: (eventConnectionStatus) => set({ eventConnectionStatus }),
  selectNode: (selectedNodeId) => set({ selectedNodeId }),
  inspectVersion: (inspectedVersionId, inspectedFromVersionId) => set({ inspectedVersionId, inspectedFromVersionId, selectedNodeId: undefined }),
  setBottomDrawerOpen: (bottomDrawerOpen) => set({ bottomDrawerOpen }),
  setUseLlmPlanner: (useLlmPlanner) => set({ useLlmPlanner })
}));

function mergeStateFromEvent(state: SessionState | undefined, event: WorkflowEvent): SessionState | undefined {
  if (!state || !event.payload || typeof event.payload !== 'object') {
    return state;
  }
  const nextState = event.payload.state;
  if (!nextState || typeof nextState !== 'object' || Array.isArray(nextState)) {
    return state;
  }
  return {
    ...state,
    ...(nextState as Partial<SessionState>)
  };
}
