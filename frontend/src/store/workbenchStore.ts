import { create } from 'zustand';
import type { ProjectType, SessionState } from '../api/types';

interface WorkbenchStore {
  threadId?: string;
  traceId?: string;
  projectType?: ProjectType;
  state?: SessionState;
  selectedNodeId?: string;
  bottomDrawerOpen: boolean;
  useLlmPlanner: boolean;
  setProjectType: (projectType: ProjectType) => void;
  setSession: (payload: { threadId: string; traceId?: string; state: SessionState }) => void;
  patchState: (patch: Partial<SessionState>) => void;
  selectNode: (nodeId?: string) => void;
  setBottomDrawerOpen: (open: boolean) => void;
  setUseLlmPlanner: (enabled: boolean) => void;
}

export const useWorkbenchStore = create<WorkbenchStore>((set) => ({
  bottomDrawerOpen: false,
  useLlmPlanner: false,
  setProjectType: (projectType) => set({ projectType }),
  setSession: ({ threadId, traceId, state }) =>
    set({
      threadId,
      traceId,
      state,
      projectType: state.project_type ?? undefined,
      useLlmPlanner: state.use_llm_planner ?? false
    }),
  patchState: (patch) => set((store) => ({ state: store.state ? { ...store.state, ...patch } : store.state })),
  selectNode: (selectedNodeId) => set({ selectedNodeId }),
  setBottomDrawerOpen: (bottomDrawerOpen) => set({ bottomDrawerOpen }),
  setUseLlmPlanner: (useLlmPlanner) => set({ useLlmPlanner })
}));
