import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  DatabaseOutlined,
  FieldTimeOutlined
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Descriptions, Empty, Layout, List, Skeleton, Space, Statistic, Tag, Timeline, Typography } from 'antd';
import { Link, useParams } from 'react-router-dom';
import { formatApiError, getTrace } from '../../api/client';
import type { AgentTrace, WorkflowEvent } from '../../api/types';
import panelStyles from '../../styles/panel.module.css';

const { Content } = Layout;
const { Paragraph, Text, Title } = Typography;

export function TracePage() {
  const { traceId } = useParams();
  const query = useQuery({
    queryKey: ['trace', traceId],
    queryFn: () => getTrace(traceId!),
    enabled: Boolean(traceId)
  });

  return (
    <Layout className={panelStyles.traceShell}>
      <Content className={panelStyles.traceContent}>
        <header className={panelStyles.traceHeader}>
          <div>
            <Link to="/">
              <Button icon={<ArrowLeftOutlined />} type="link" className={panelStyles.traceBack}>
                返回工作台
              </Button>
            </Link>
            <Title level={2}>Trace 详情</Title>
            <Text type="secondary">{traceId ?? '未选择 trace'}</Text>
          </div>
          {query.data ? <Tag color={traceStatusColor(query.data.status)}>{query.data.status ?? 'unknown'}</Tag> : null}
        </header>

        {query.isLoading ? <TraceSkeleton /> : null}
        {query.isError ? <Alert type="error" showIcon message="Trace 加载失败" description={formatApiError(query.error)} /> : null}
        {query.data ? <TraceDetail trace={query.data} /> : null}
      </Content>
    </Layout>
  );
}

function TraceDetail({ trace }: { trace: AgentTrace }) {
  const events = trace.events ?? [];
  const llmCalls = trace.llm_calls ?? [];
  const feedback = trace.feedback ?? [];
  const failedEvents = events.filter((event) => event.status === 'failed' || event.status === 'error');
  const llmEvents = events.filter((event) => event.event_type.startsWith('llm.'));
  const durationMs = elapsedMs(trace.started_at, trace.finished_at);

  return (
    <div className={panelStyles.traceGrid}>
      <section className={`${panelStyles.panel} ${panelStyles.traceSummary}`}>
        <div className={panelStyles.header}>
          <div>
            <Title level={3}>链路概览</Title>
            <Text type="secondary">用户输入、工作流节点、LLM 尝试、补丁、校验和导出事件</Text>
          </div>
        </div>
        <div className={panelStyles.traceStats}>
          <Statistic title="事件数" value={events.length} prefix={<FieldTimeOutlined />} />
          <Statistic title="失败事件" value={failedEvents.length} prefix={failedEvents.length ? <CloseCircleOutlined /> : <CheckCircleOutlined />} />
          <Statistic title="LLM 调用" value={llmCalls.length || llmEvents.length} prefix={<CodeOutlined />} />
          <Statistic title="耗时" value={durationMs === null ? '--' : formatDuration(durationMs)} prefix={<ClockCircleOutlined />} />
        </div>
        <Descriptions size="small" column={1} className={panelStyles.traceDescriptions}>
          <Descriptions.Item label="thread_id">{trace.thread_id}</Descriptions.Item>
          <Descriptions.Item label="project_id">{trace.project_id ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="version_id">{trace.version_id ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="开始时间">{formatDateTime(trace.started_at)}</Descriptions.Item>
          <Descriptions.Item label="结束时间">{formatDateTime(trace.finished_at)}</Descriptions.Item>
          {trace.error ? <Descriptions.Item label="错误">{trace.error}</Descriptions.Item> : null}
        </Descriptions>
        <div className={panelStyles.traceInput}>
          <Text type="secondary">用户输入</Text>
          <Paragraph ellipsis={{ rows: 3, expandable: true, symbol: '展开' }}>{trace.root_input || '无输入记录'}</Paragraph>
        </div>
        {llmCalls.length ? <LlmCallList calls={llmCalls} /> : null}
        {feedback.length ? <FeedbackList feedback={feedback} /> : null}
      </section>

      <section className={`${panelStyles.panel} ${panelStyles.traceTimelinePanel}`}>
        <div className={panelStyles.header}>
          <div>
            <Title level={3}>事件时间线</Title>
            <Text type="secondary">按写入顺序展示 workflow_events 摘要</Text>
          </div>
        </div>
        {events.length ? <TraceTimeline events={events} /> : <Empty className={panelStyles.traceEmpty} description="暂无事件" />}
      </section>
    </div>
  );
}

function FeedbackList({ feedback }: { feedback: NonNullable<AgentTrace['feedback']> }) {
  return (
    <div className={panelStyles.traceFeedback}>
      <Text strong>用户反馈</Text>
      <List
        size="small"
        dataSource={feedback}
        renderItem={(item) => (
          <List.Item className={panelStyles.traceFeedbackItem}>
            <div>
              <Space size={6} wrap>
                <Tag color={feedbackColor(item.category)}>{feedbackLabel(item.category)}</Tag>
                <Tag>{item.rating}/5</Tag>
                <Text type="secondary">{formatDateTime(item.created_at)}</Text>
              </Space>
              <Paragraph className={panelStyles.feedbackComment} ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}>
                {item.comment || '无备注'}
              </Paragraph>
            </div>
          </List.Item>
        )}
      />
    </div>
  );
}

function LlmCallList({ calls }: { calls: NonNullable<AgentTrace['llm_calls']> }) {
  return (
    <div className={panelStyles.traceLlmCalls}>
      <Text strong>LLM 调用</Text>
      <List
        size="small"
        dataSource={calls}
        renderItem={(call) => (
          <List.Item className={panelStyles.traceLlmCallItem}>
            <div>
              <Text>{call.prompt_name || 'unknown'}</Text>
              <div>
                <Text type="secondary">
                  {call.provider}/{call.model} · attempt {call.attempt} · {formatDuration(call.latency_ms || 0)}
                </Text>
              </div>
              {call.error ? <Text type="danger">{call.error}</Text> : null}
            </div>
            <Space size={4} wrap>
              <Tag color={call.status === 'failed' ? 'error' : 'success'}>{call.status}</Tag>
              <Tag>{call.input_tokens ?? 0} in</Tag>
              <Tag>{call.output_tokens ?? 0} out</Tag>
            </Space>
          </List.Item>
        )}
      />
    </div>
  );
}

function TraceTimeline({ events }: { events: WorkflowEvent[] }) {
  return (
    <Timeline
      className={panelStyles.traceTimeline}
      items={events.map((event) => ({
        color: eventColor(event),
        dot: event.event_type.startsWith('llm.') ? <CodeOutlined /> : event.event_type.includes('version') ? <DatabaseOutlined /> : undefined,
        children: <TraceEvent event={event} />
      }))}
    />
  );
}

function TraceEvent({ event }: { event: WorkflowEvent }) {
  return (
    <div className={panelStyles.traceEvent}>
      <div className={panelStyles.traceEventHeader}>
        <Space size={8} wrap>
          <Text strong>{event.message ?? event.event_type}</Text>
          <Tag color={eventColor(event)}>{event.status ?? 'unknown'}</Tag>
          <Tag>{event.step ?? 'unknown'}</Tag>
        </Space>
        <Text type="secondary">{formatDateTime(event.created_at)}</Text>
      </div>
      <Text type="secondary">{event.event_type}</Text>
      <List
        size="small"
        className={panelStyles.tracePayloadList}
        dataSource={payloadRows(event.payload)}
        locale={{ emptyText: null }}
        renderItem={(item) => (
          <List.Item>
            <Text type="secondary">{item.label}</Text>
            <Text>{item.value}</Text>
          </List.Item>
        )}
      />
    </div>
  );
}

function TraceSkeleton() {
  return (
    <div className={panelStyles.traceGrid}>
      <section className={panelStyles.panel}>
        <Skeleton active paragraph={{ rows: 6 }} />
      </section>
      <section className={panelStyles.panel}>
        <Skeleton active paragraph={{ rows: 10 }} />
      </section>
    </div>
  );
}

function payloadRows(payload?: Record<string, unknown> | null): Array<{ label: string; value: string }> {
  if (!payload) {
    return [];
  }
  const rows: Array<{ label: string; value: string }> = [];
  const keys = ['node', 'updated_fields', 'template_candidate_count', 'planner_attempt_count', 'status', 'next_action'];
  for (const key of keys) {
    if (payload[key] !== undefined) {
      rows.push({ label: key, value: formatPayloadValue(payload[key]) });
    }
  }
  for (const key of ['project', 'planner', 'dry_run', 'patch_result', 'validation_summary', 'risk_assessment']) {
    if (payload[key] !== undefined) {
      rows.push({ label: key, value: formatPayloadValue(payload[key]) });
    }
  }
  return rows.slice(0, 8);
}

function formatPayloadValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join(', ');
  }
  if (value && typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

function traceStatusColor(status?: string | null): string {
  if (status === 'completed') return 'success';
  if (status === 'failed' || status === 'error') return 'error';
  if (status === 'cancelled') return 'warning';
  return 'processing';
}

function eventColor(event: WorkflowEvent): string {
  if (event.status === 'failed' || event.status === 'error') return 'red';
  if (event.status === 'running' || event.status === 'waiting') return 'blue';
  if (event.event_type.startsWith('llm.')) return 'purple';
  if (event.event_type.includes('validation')) return event.status === 'completed' ? 'green' : 'orange';
  return 'gray';
}

function elapsedMs(start?: string | null, end?: string | null): number | null {
  if (!start || !end) {
    return null;
  }
  const value = new Date(end).getTime() - new Date(start).getTime();
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function formatDuration(ms: number): string {
  if (ms < 1000) {
    return `${ms}ms`;
  }
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return '-';
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(new Date(value));
}

function feedbackLabel(category?: string | null): string {
  if (category === 'risk_cancel') return '风险取消';
  if (category === 'failure') return '失败阻塞';
  if (category === 'suggestion') return '改进建议';
  return '导出体验';
}

function feedbackColor(category?: string | null): string {
  if (category === 'failure') return 'error';
  if (category === 'risk_cancel') return 'warning';
  if (category === 'suggestion') return 'processing';
  return 'success';
}
