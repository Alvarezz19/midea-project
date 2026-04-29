import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider, theme } from 'antd';
import type { ReactNode } from 'react';
import zhCN from 'antd/locale/zh_CN';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false
    }
  }
});

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: {
          colorPrimary: '#0098D1',
          colorInfo: '#0098D1',
          colorSuccess: '#20704A',
          colorWarning: '#A55C00',
          colorError: '#A53232',
          colorText: '#172026',
          colorTextSecondary: '#66737C',
          colorBorder: '#D8E0E4',
          colorBgLayout: '#F4F7F8',
          borderRadius: 10,
          fontFamily: '"Alibaba PuHuiTi", "MiSans", "Microsoft YaHei UI", sans-serif'
        },
        components: {
          Button: {
            controlHeight: 34,
            borderRadius: 8
          },
          Card: {
            borderRadiusLG: 14
          },
          Layout: {
            headerBg: '#006A94',
            bodyBg: '#F4F7F8'
          }
        }
      }}
    >
      <QueryClientProvider client={queryClient}>
        <AntApp>{children}</AntApp>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
