import { Alert, Button, Drawer, Form, Input, List, Rate, Select, Skeleton, Space, Statistic, Tabs, Tag, Typography, message as antMessage } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import { useMutation, useQuery } from '@tanstack/react-query';
import { formatApiError, getMetricsSummary, submitFeedback } from '../../api/client';
import type { ObservabilityMetrics } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';
import { VersionHistoryPanel } from '../versions/VersionHistoryPanel';

const { Text } = Typography;

export function BottomObservabilityDrawer() {
  const open = useWorkbenchStore((store) => store.bottomDrawerOpen);
  const setOpen = useWorkbenchStore((store) => store.setBottomDrawerOpen);
  const threadId = useWorkbenchStore((store) => store.threadId);
  const traceId = useWorkbenchStore((store) => store.traceId);
  const state = useWorkbenchStore((store) => store.state);
  const workflowEvents = useWorkbenchStore((store) => store.workflowEvents);
  const [form] = Form.useForm<{ rating: number; category: string; comment: string }>();
  const [messageApi, holder] = antMessage.useMessage();
  const projectId = state?.project_id ?? state?.current_project_id;
  const versionId = state?.version_id ?? state?.current_project_version_id;
  const defaultCategory = feedbackCategory(state?.status);
  const feedbackMutation = useMutation({
    mutationFn: (values: { rating: number; category: string; comment: string }) =>
      submitFeedback({
        trace_id: traceId!,
        project_id: projectId,
        version_id: versionId,
        rating: values.rating,
        category: values.category,
        comment: values.comment ?? ''
      }),
    onSuccess: () => {
      form.resetFields();
      messageApi.success('反馈已提交。');
    },
    onError: (error) => messageApi.error(formatApiError(error))
  });

  return (
    <>
      {holder}
      <Button className={panelStyles.drawerToggle} onClick={() => setOpen(true)}>
        版本 / Trace / 反馈
      </Button>
      <Drawer
        title="开发者观测抽屉"
        placement="bottom"
        height={420}
        open={open}
        onClose={() => setOpen(false)}
        destroyOnHidden={false}
      >
        <Tabs
          items={[
            {
              key: 'versions',
              label: '版本历史',
              children: <VersionHistoryPanel />
            },
            {
              key: 'trace',
              label: 'Trace',
              children: (
                <pre className={panelStyles.jsonBlock}>
                  {JSON.stringify(
                    {
                      thread_id: threadId,
                      trace_id: traceId,
                      project_id: state?.project_id,
                      version_id: state?.version_id
                    },
                    null,
                    2
                  )}
                </pre>
              )
            },
            {
              key: 'events',
              label: '事件流',
              children: (
                <List
                  size="small"
                  dataSource={[...workflowEvents].reverse()}
                  locale={{ emptyText: '暂无事件' }}
                  renderItem={(event) => (
                    <List.Item className={panelStyles.eventItem}>
                      <div>
                        <Text strong>{event.message ?? event.event_type}</Text>
                        <div>
                          <Text type="secondary">
                            {event.step ?? 'unknown'} · {event.created_at ?? '无时间戳'} · {event.event_id}
                          </Text>
                        </div>
                      </div>
                      <Tag color={event.status === 'failed' || event.status === 'error' ? 'error' : 'processing'}>{event.status ?? 'unknown'}</Tag>
                    </List.Item>
                  )}
                />
              )
            },
            {
              key: 'cost',
              label: '指标',
              children: <MetricsPanel />
            },
            {
              key: 'feedback',
              label: '反馈',
              children: (
                <Form
                  form={form}
                  layout="vertical"
                  className={panelStyles.feedbackForm}
                  initialValues={{ rating: 4, category: defaultCategory, comment: '' }}
                  onFinish={(values) => feedbackMutation.mutate(values)}
                >
                  <Space className={panelStyles.feedbackMeta} wrap>
                    <Text type="secondary">trace：{traceId ?? '暂无'}</Text>
                    <Text type="secondary">版本：{versionId ?? '未创建'}</Text>
                  </Space>
                  <Form.Item name="rating" label="评分" rules={[{ required: true, message: '请选择评分。' }]}>
                    <Rate />
                  </Form.Item>
                  <Form.Item name="category" label="类型" rules={[{ required: true, message: '请选择类型。' }]}>
                    <Select
                      options={[
                        { value: 'export', label: '导出体验' },
                        { value: 'risk_cancel', label: '风险确认取消' },
                        { value: 'failure', label: '失败或阻塞' },
                        { value: 'suggestion', label: '改进建议' }
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="comment" label="备注">
                    <Input.TextArea rows={3} maxLength={500} showCount placeholder="记录问题、期望结果或导出后的复核意见" />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" icon={<SendOutlined />} disabled={!traceId} loading={feedbackMutation.isPending}>
                    提交反馈
                  </Button>
                </Form>
              )
            }
          ]}
        />
      </Drawer>
    </>
  );
}

function MetricsPanel() {
  const query = useQuery({
    queryKey: ['observability-metrics'],
    queryFn: getMetricsSummary,
    refetchInterval: 15000
  });

  if (query.isLoading) {
    return <Skeleton active paragraph={{ rows: 4 }} />;
  }
  if (query.isError) {
    return <Alert type="error" showIcon message="指标加载失败" description={formatApiError(query.error)} />;
  }
  if (!query.data) {
    return <Text type="secondary">暂无指标</Text>;
  }
  return <MetricsSummary metrics={query.data} />;
}

function MetricsSummary({ metrics }: { metrics: ObservabilityMetrics }) {
  const topTypes = Object.entries(metrics.eventsByType)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 6);

  return (
    <div className={panelStyles.metricsPanel}>
      <div className={panelStyles.metricsGrid}>
        <Statistic title="事件总数" value={metrics.workflowEventsTotal} />
        <Statistic title="Trace 总数" value={metrics.tracesTotal} />
        <Statistic title="失败 Trace" value={metrics.failedTracesTotal} valueStyle={metrics.failedTracesTotal ? { color: '#A53232' } : undefined} />
        <Statistic title="P95 耗时" value={formatMetricDuration(metrics.traceDurationP95Ms)} />
        <Statistic title="LLM 调用" value={metrics.llmCallsTotal} />
        <Statistic title="LLM 失败" value={metrics.llmCallsFailedTotal} valueStyle={metrics.llmCallsFailedTotal ? { color: '#A53232' } : undefined} />
        <Statistic title="导出事件" value={metrics.projectExportsTotal} />
        <Statistic title="反馈数" value={metrics.userFeedbackTotal} />
      </div>
      <div className={panelStyles.metricsColumns}>
        <div>
          <Text strong>状态分布</Text>
          <Space wrap className={panelStyles.metricsTags}>
            {Object.entries(metrics.eventsByStatus).map(([status, value]) => (
              <Tag key={status} color={status === 'failed' || status === 'error' ? 'error' : status === 'completed' ? 'success' : 'processing'}>
                {status}: {value}
              </Tag>
            ))}
          </Space>
        </div>
        <div>
          <Text strong>高频事件</Text>
          <List
            size="small"
            dataSource={topTypes}
            locale={{ emptyText: '暂无事件' }}
            renderItem={([type, value]) => (
              <List.Item className={panelStyles.metricTypeItem}>
                <Text ellipsis>{type}</Text>
                <Tag>{value}</Tag>
              </List.Item>
            )}
          />
        </div>
      </div>
    </div>
  );
}

function formatMetricDuration(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '0ms';
  }
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}

function feedbackCategory(status?: string | null): string {
  if (status === 'patch_confirmation_cancelled') {
    return 'risk_cancel';
  }
  if (status === 'validation_failed' || status === 'patch_failed' || status === 'error') {
    return 'failure';
  }
  return 'export';
}
