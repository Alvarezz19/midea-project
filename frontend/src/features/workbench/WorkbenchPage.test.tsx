import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppProviders } from '../../app/providers';
import { WorkbenchPage } from './WorkbenchPage';

describe('WorkbenchPage', () => {
  it('renders the production workbench shell', () => {
    render(
      <AppProviders>
        <MemoryRouter>
          <WorkbenchPage />
        </MemoryRouter>
      </AppProviders>
    );

    expect(screen.getByText('美的工程智能体工作台')).toBeInTheDocument();
    expect(screen.getByText('会话与需求')).toBeInTheDocument();
    expect(screen.getByText('模板、计划与风险')).toBeInTheDocument();
    expect(screen.getByText('局部流程图与校验')).toBeInTheDocument();
  });
});
