import { createBrowserRouter, Navigate } from 'react-router-dom';
import { WorkbenchPage } from '../features/workbench/WorkbenchPage';
import { TracePage } from '../features/observability/TracePage';

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
    element: <TracePage />
  },
  {
    path: '*',
    element: <Navigate to="/" replace />
  }
]);
