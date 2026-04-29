import { Alert, Button, Segmented, Space, Tag, Typography, message as antMessage } from 'antd';
import { CloudDownloadOutlined, HistoryOutlined, NodeIndexOutlined, RadarChartOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { formatApiError, projectExportUrl, validateProject } from '../../api/client';
import { SessionPanel } from '../session/SessionPanel';
import { PlanningPanel } from '../planning/PlanningPanel';
import { GraphPanel } from '../graph/GraphPanel';
import { BottomObservabilityDrawer } from '../observability/BottomObservabilityDrawer';
import { WorkflowProgress, connectionColor, connectionStatusLabel } from '../observability/WorkflowProgress';
import { useSessionEvents } from '../observability/useSessionEvents';
import { useWorkbenchStore } from '../../store/workbenchStore';
import styles from './WorkbenchPage.module.css';

const { Text, Title } = Typography;

export function WorkbenchPage() {
  useSessionEvents();
  const state = useWorkbenchStore((store) => store.state);
  const projectType = useWorkbenchStore((store) => store.projectType);
  const setProjectType = useWorkbenchStore((store) => store.setProjectType);
  const patchState = useWorkbenchStore((store) => store.patchState);
  const connectionStatus = useWorkbenchStore((store) => store.eventConnectionStatus);
  const projectId = state?.project_id ?? state?.current_project_id;
  const versionId = state?.version_id ?? state?.current_project_version_id;
  const validation = state?.validation_summary;
  const exportable = validation?.exportable === true;
  const [messageApi, holder] = antMessage.useMessage();
  const statusColor = useMemo(() => {
    if (!validation) return 'default';
    if (validation.valid) return 'success';
    return validation.error_count ? 'error' : 'warning';
  }, [validation]);
  const validateMutation = useMutation({
    mutationFn: () => validateProject(projectId!),
    onSuccess: (report) => {
      const summary = {
        valid: Boolean(report.valid),
        exportable: Boolean(report.exportable),
        error_count: Number(report.error_count ?? 0),
        warning_count: Number(report.warning_count ?? 0),
        risk_count: typeof report.summary === 'object' && report.summary ? Number((report.summary as Record<string, unknown>).risk_count ?? 0) : 0,
        blocked_export_reasons: Array.isArray(report.blocked_export_reasons) ? report.blocked_export_reasons.map(String) : []
      };
      patchState({ validation_report: report, validation_summary: summary });
      messageApi.success(summary.exportable ? '校验通过，可以导出。' : '校验完成，请处理阻塞项。');
    },
    onError: (error) => messageApi.error(formatApiError(error))
  });

  const exportProject = () => {
    if (!projectId || !exportable) {
      return;
    }
    window.location.assign(projectExportUrl(projectId));
  };

  return (
    <main className={styles.shell}>
      {holder}
      <header className={styles.topbar}>
        <div className={styles.identity}>
          <div className={styles.mark}>M</div>
          <div>
            <Title level={1}>美的工程智能体工作台</Title>
            <Text>阶段 7 工程工作台 · 自然语言驱动工程 JSON 改造</Text>
          </div>
        </div>
        <Space size={12} wrap>
          <Segmented
            value={projectType ?? '__unset__'}
            options={[
              { label: '机房群控程序', value: 'plant_room' },
              { label: 'AHU 程序', value: 'ahu' }
            ]}
            onChange={(value) => setProjectType(value as 'plant_room' | 'ahu')}
          />
          <Tag color={statusColor}>校验：{validation ? (validation.valid ? '通过' : '待处理') : '未开始'}</Tag>
          <Tag color="blue">版本：{versionId ?? '未创建'}</Tag>
          <Button icon={<SafetyCertificateOutlined />} disabled={!projectId} loading={validateMutation.isPending} onClick={() => validateMutation.mutate()}>
            校验
          </Button>
          <Button type="primary" icon={<CloudDownloadOutlined />} disabled={!exportable || !projectId} onClick={exportProject}>
            导出
          </Button>
        </Space>
      </header>

      <section className={styles.statusRail}>
        <StatusItem icon={<RadarChartOutlined />} label="事件流" value={connectionStatusLabel(connectionStatus)} tone={connectionColor(connectionStatus) === 'success' ? 'blue' : undefined} />
        <StatusItem icon={<NodeIndexOutlined />} label="项目" value={projectId ?? '未绑定'} />
        <StatusItem icon={<HistoryOutlined />} label="下一步" value={state?.next_action ?? 'send_message'} />
      </section>

      <WorkflowProgress />

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
