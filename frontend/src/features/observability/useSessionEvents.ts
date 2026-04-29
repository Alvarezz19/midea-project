import { useEffect } from 'react';
import { openSessionEventStream } from '../../api/client';
import { useWorkbenchStore } from '../../store/workbenchStore';

export function useSessionEvents() {
  const threadId = useWorkbenchStore((store) => store.threadId);
  const mergeWorkflowEvent = useWorkbenchStore((store) => store.mergeWorkflowEvent);
  const setEventConnectionStatus = useWorkbenchStore((store) => store.setEventConnectionStatus);

  useEffect(() => {
    if (!threadId) {
      setEventConnectionStatus('idle');
      return undefined;
    }
    setEventConnectionStatus('connecting');
    const stream = openSessionEventStream(threadId, {
      lastEventId: useWorkbenchStore.getState().lastWorkflowEventId,
      onOpen: () => setEventConnectionStatus('connected'),
      onEvent: mergeWorkflowEvent,
      onReconnect: () => setEventConnectionStatus('reconnecting'),
      onError: () => setEventConnectionStatus('error')
    });
    return () => stream.close();
  }, [threadId, mergeWorkflowEvent, setEventConnectionStatus]);
}
