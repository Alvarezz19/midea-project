import { Alert, Empty, List, Steps, Tag, Typography } from 'antd';
import type { WorkflowEvent } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text } = Typography;

type StepKey = 'requirement' | 'template' | 'template_confirmation' | 'planning' | 'dry_run' | 'risk_confirmation' | 'commit' | 'validation' | 'export';

const workflowSteps: Array<{ key: StepKey; title: string; aliases: string[] }> = [
  { key: 'requirement', title: '需求分析', aliases: ['create_session', 'receive_message', 'analyze_requirement', 'requirement_analysis'] },
  { key: 'template', title: '模板检索', aliases: ['template_search', 'select_template', 'retrieve_template'] },
  { key: 'template_confirmation', title: '模板确认', aliases: ['template_confirmation'] },
  { key: 'planning', title: '规划', aliases: ['plan_change', 'invoke_workflow', 'llm_planner'] },
  { key: 'dry_run', title: 'dry-run', aliases: ['dry_run', 'patch_dry_run'] },
  { key: 'risk_confirmation', title: '风险确认', aliases: ['risk_confirmation'] },
  { key: 'commit', title: '提交版本', aliases: ['apply_confirmed_patch', 'commit_version', 'create_version'] },
  { key: 'validation', title: '校验', aliases: ['validate', 'validation'] },
  { key: 'export', title: '导出', aliases: ['export'] }
];

export function WorkflowProgress() {
  const events = useWorkbenchStore((store) => store.workflowEvents);
  const connectionStatus = useWorkbenchStore((store) => store.eventConnectionStatus);
  const latestError = [...events].reverse().find((event) => event.status === 'failed' || event.status === 'error');
  const stepItems = workflowSteps.map((step) => toStepItem(step.key, step.title, stepEvents(events, step)));

  return (
    <section className={panelStyles.workflowProgress}>
      <header className={panelStyles.sectionHeading}>
        <div>
          <Text strong>工作流步骤</Text>
          <Text type="secondary"> 事件流：{connectionStatusLabel(connectionStatus)}</Text>
        </div>
        <Tag color={connectionColor(connectionStatus)}>{connectionStatusLabel(connectionStatus)}</Tag>
      </header>
      <Steps size="small" responsive items={stepItems} />
      {latestError ? <Alert className={panelStyles.inlineAlertTight} type="error" showIcon message={latestError.message ?? '工作流事件报告失败。'} /> : null}
      <List
        className={panelStyles.eventList}
        size="small"
        dataSource={[...events].slice(-6).reverse()}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无事件" /> }}
        renderItem={(event) => (
          <List.Item className={panelStyles.eventItem}>
            <div>
              <Text strong>{event.message ?? event.event_type}</Text>
              <div>
                <Text type="secondary">
                  {event.step ?? 'unknown'} · {event.status ?? 'unknown'} · {formatTime(event.created_at)}
                </Text>
              </div>
            </div>
            <Tag color={event.status === 'failed' || event.status === 'error' ? 'error' : event.status === 'running' ? 'processing' : 'success'}>
              {event.event_type}
            </Tag>
          </List.Item>
        )}
      />
    </section>
  );
}

function stepEvents(events: WorkflowEvent[], step: { key: StepKey; aliases: string[] }): WorkflowEvent[] {
  return events.filter((event) => {
    const rawStep = event.step ?? '';
    const rawType = event.event_type ?? '';
    return step.aliases.some((alias) => rawStep.includes(alias) || rawType.includes(alias));
  });
}

function toStepItem(key: StepKey, title: string, events: WorkflowEvent[]) {
  const failed = events.find((event) => event.status === 'failed' || event.status === 'error');
  const running = events.find((event) => event.status === 'running');
  const latest = events.at(-1);
  const duration = durationLabel(events);
  return {
    key,
    title,
    status: failed ? 'error' : running ? 'process' : latest ? 'finish' : 'wait',
    description: latest ? `${duration} · ${latest.message ?? latest.status ?? '已记录'}` : '等待'
  } as const;
}

function durationLabel(events: WorkflowEvent[]): string {
  const dated = events
    .map((event) => (event.created_at ? new Date(event.created_at).getTime() : Number.NaN))
    .filter((value) => Number.isFinite(value));
  if (dated.length < 2) {
    return '耗时 --';
  }
  const ms = Math.max(...dated) - Math.min(...dated);
  if (ms < 1000) {
    return `耗时 ${ms}ms`;
  }
  return `耗时 ${(ms / 1000).toFixed(1)}s`;
}

export function connectionStatusLabel(status: string): string {
  if (status === 'connecting') return '连接中';
  if (status === 'connected') return '已连接';
  if (status === 'reconnecting') return '重连中';
  if (status === 'disconnected') return '断开';
  if (status === 'error') return '重连中';
  return '未连接';
}

export function connectionColor(status: string): string {
  if (status === 'connected') return 'success';
  if (status === 'connecting' || status === 'reconnecting' || status === 'error') return 'processing';
  if (status === 'disconnected') return 'error';
  return 'default';
}

function formatTime(value?: string | null): string {
  if (!value) {
    return '无时间戳';
  }
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(new Date(value));
}
