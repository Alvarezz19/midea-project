import { createBrowserRouter, Navigate } from 'react-router-dom';
import { WorkbenchPage } from '../features/workbench/WorkbenchPage';
import { TracePlaceholder } from '../features/observability/TracePlaceholder';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <WorkbenchPage />
  },
  {
    path: '/projects/:projectId',
    element: <WorkbenchPage />
  },
  {
    path: '/traces/:traceId',
    element: <TracePlaceholder />
  },
  {
    path: '*',
    element: <Navigate to="/" replace />
  }
]);
