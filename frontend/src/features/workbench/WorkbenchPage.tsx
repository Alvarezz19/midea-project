import { Alert, Button, Segmented, Space, Tag, Typography } from 'antd';
import { CloudDownloadOutlined, HistoryOutlined, NodeIndexOutlined, RadarChartOutlined } from '@ant-design/icons';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { SessionPanel } from '../session/SessionPanel';
import { PlanningPanel } from '../planning/PlanningPanel';
import { GraphPanel } from '../graph/GraphPanel';
import { BottomObservabilityDrawer } from '../observability/BottomObservabilityDrawer';
import { useWorkbenchStore } from '../../store/workbenchStore';
import styles from './WorkbenchPage.module.css';

const { Text, Title } = Typography;

export function WorkbenchPage() {
  const state = useWorkbenchStore((store) => store.state);
  const projectType = useWorkbenchStore((store) => store.projectType);
  const setProjectType = useWorkbenchStore((store) => store.setProjectType);
  const projectId = state?.project_id ?? state?.current_project_id;
  const versionId = state?.version_id ?? state?.current_project_version_id;
  const validation = state?.validation_summary;
  const exportable = validation?.exportable === true;
  const statusColor = useMemo(() => {
    if (!validation) return 'default';
    if (validation.valid) return 'success';
    return validation.error_count ? 'error' : 'warning';
  }, [validation]);

  return (
    <main className={styles.shell}>
      <header className={styles.topbar}>
        <div className={styles.identity}>
          <div className={styles.mark}>M</div>
          <div>
            <Title level={1}>美的工程智能体工作台</Title>
            <Text>阶段 7 生产前端骨架 · 自然语言驱动工程 JSON 改造</Text>
          </div>
        </div>
        <Space size={12} wrap>
          <Segmented
            value={projectType}
            options={[
              { label: '机房群控程序', value: 'plant_room' },
              { label: 'AHU 程序', value: 'ahu' }
            ]}
            onChange={(value) => setProjectType(value as 'plant_room' | 'ahu')}
          />
          <Tag color={statusColor}>校验：{validation ? (validation.valid ? '通过' : '待处理') : '未开始'}</Tag>
          <Tag color="blue">版本：{versionId ?? '未创建'}</Tag>
          <Button type="primary" icon={<CloudDownloadOutlined />} disabled={!exportable}>
            导出
          </Button>
        </Space>
      </header>

      <section className={styles.statusRail}>
        <StatusItem icon={<RadarChartOutlined />} label="事件流" value="待接入 SSE" tone="blue" />
        <StatusItem icon={<NodeIndexOutlined />} label="项目" value={projectId ?? '未绑定'} />
        <StatusItem icon={<HistoryOutlined />} label="下一步" value={state?.next_action ?? 'send_message'} />
      </section>

      {state?.error ? <Alert className={styles.alert} type="error" showIcon message={state.error} /> : null}

      <section className={styles.workspace}>
        <SessionPanel />
        <PlanningPanel />
        <GraphPanel />
      </section>

      <BottomObservabilityDrawer />
    </main>
  );
}

function StatusItem({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string; tone?: 'blue' }) {
  return (
    <div className={tone === 'blue' ? `${styles.statusItem} ${styles.statusBlue}` : styles.statusItem}>
      <span>{icon}</span>
      <Text type="secondary">{label}</Text>
      <strong>{value}</strong>
    </div>
  );
}
