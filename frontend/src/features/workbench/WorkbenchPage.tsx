import { Alert, Button, Segmented, Space, Tag, Typography, message as antMessage } from 'antd';
import { CloudDownloadOutlined, HistoryOutlined, NodeIndexOutlined, RadarChartOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { formatApiError, projectExportUrl, validateProject } from '../../api/client';
import type { RequirementConformanceReport } from '../../api/types';
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
  const conformance = state?.conformance_report ?? (state?.validation_report?.conformance_report as RequirementConformanceReport | undefined);
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
        blocked_export_reasons: Array.isArray(report.blocked_export_reasons) ? report.blocked_export_reasons.map(String) : [],
        requirement_reviewed: typeof report.conformance_report === 'object' && report.conformance_report
          ? Boolean((report.conformance_report as RequirementConformanceReport).context_available)
          : undefined,
        requirement_valid: typeof report.conformance_report === 'object' && report.conformance_report
          ? Boolean((report.conformance_report as RequirementConformanceReport).valid_for_requirement)
          : undefined,
        conformance_blocked_count: conformanceNumber(report.conformance_report, 'blocked_count'),
        conformance_missing_count: conformanceNumber(report.conformance_report, 'missing_count')
      };
      patchState({ validation_report: report, validation_summary: summary, conformance_report: report.conformance_report as RequirementConformanceReport | undefined });
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
            <Title level={1}>美控（KONG）智能体工作台</Title>
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
      {conformance ? <ConformanceBanner report={conformance} blockedReasons={validation?.blocked_export_reasons ?? []} /> : null}

      <section className={styles.workspace}>
        <SessionPanel />
        <PlanningPanel />
        <GraphPanel />
      </section>

      <BottomObservabilityDrawer />
    </main>
  );
}

function ConformanceBanner({ report, blockedReasons }: { report: RequirementConformanceReport; blockedReasons: string[] }) {
  if (report.context_available === false) {
    return <Alert className={styles.alert} type="info" showIcon message="需求覆盖未复核" description={report.warnings?.[0] ?? '当前导出只完成结构校验。'} />;
  }
  if (report.valid_for_requirement === false) {
    return (
      <Alert
        className={styles.alert}
        type="error"
        showIcon
        message="需求覆盖存在阻塞项"
        description={(report.blocked?.length ? report.blocked : blockedReasons).slice(0, 3).join('；')}
      />
    );
  }
  return <Alert className={styles.alert} type="success" showIcon message="需求覆盖已复核" description={`已覆盖 ${report.summary?.covered_count ?? 0} 项，需人工复核 ${report.summary?.partial_count ?? 0} 项。`} />;
}

function conformanceNumber(value: unknown, key: 'blocked_count' | 'missing_count'): number | undefined {
  if (!value || typeof value !== 'object') {
    return undefined;
  }
  const summary = (value as RequirementConformanceReport).summary;
  const count = summary?.[key];
  return typeof count === 'number' ? count : undefined;
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
