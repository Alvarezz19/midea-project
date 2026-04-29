import { useEffect, useMemo, useRef, type RefObject } from 'react';
import { Alert, Button, Drawer, Form, Input, List, Rate, Select, Skeleton, Space, Statistic, Tabs, Tag, Typography, message as antMessage } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BarChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { formatApiError, getMetricsSummary, listProjectFeedback, submitFeedback } from '../../api/client';
import type { FeedbackResponse, ObservabilityMetrics } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';
import { VersionHistoryPanel } from '../versions/VersionHistoryPanel';

const { Text } = Typography;

echarts.use([BarChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer]);

export function BottomObservabilityDrawer() {
  const open = useWorkbenchStore((store) => store.bottomDrawerOpen);
  const setOpen = useWorkbenchStore((store) => store.setBottomDrawerOpen);
  const threadId = useWorkbenchStore((store) => store.threadId);
  const traceId = useWorkbenchStore((store) => store.traceId);
  const state = useWorkbenchStore((store) => store.state);
  const workflowEvents = useWorkbenchStore((store) => store.workflowEvents);
  const [form] = Form.useForm<{ rating: number; category: string; comment: string }>();
  const [messageApi, holder] = antMessage.useMessage();
  const queryClient = useQueryClient();
  const projectId = state?.project_id ?? state?.current_project_id;
  const versionId = state?.version_id ?? state?.current_project_version_id;
  const defaultCategory = feedbackCategory(state?.status);
  const feedbackQuery = useQuery({
    queryKey: ['project-feedback', projectId],
    queryFn: () => listProjectFeedback(projectId!),
    enabled: open && Boolean(projectId)
  });
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
      void queryClient.invalidateQueries({ queryKey: ['project-feedback', projectId] });
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
                <div className={panelStyles.feedbackPanel}>
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
                  <ProjectFeedbackList loading={feedbackQuery.isLoading} error={feedbackQuery.error} feedback={feedbackQuery.data?.feedback ?? []} />
                </div>
              )
            }
          ]}
        />
      </Drawer>
    </>
  );
}

function ProjectFeedbackList({ feedback, loading, error }: { feedback: FeedbackResponse[]; loading: boolean; error: unknown }) {
  if (loading) {
    return <Skeleton active paragraph={{ rows: 4 }} />;
  }
  if (error) {
    return <Alert type="error" showIcon message="反馈加载失败" description={formatApiError(error)} />;
  }
  return (
    <div className={panelStyles.feedbackHistory}>
      <Text strong>项目反馈记录</Text>
      <List
        size="small"
        dataSource={feedback}
        locale={{ emptyText: '暂无反馈' }}
        renderItem={(item) => (
          <List.Item className={panelStyles.feedbackHistoryItem}>
            <div>
              <Space size={6} wrap>
                <Tag color={feedbackColor(item.category)}>{feedbackLabel(item.category)}</Tag>
                <Tag>{item.rating}/5</Tag>
                <Text type="secondary">{formatFeedbackTime(item.created_at)}</Text>
              </Space>
              <div className={panelStyles.feedbackHistoryComment}>{item.comment || '无备注'}</div>
            </div>
          </List.Item>
        )}
      />
    </div>
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
        <Statistic title="输入 Token" value={metrics.llmInputTokensTotal} />
        <Statistic title="输出 Token" value={metrics.llmOutputTokensTotal} />
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
      <MetricsCharts metrics={metrics} topTypes={topTypes} />
    </div>
  );
}

function MetricsCharts({ metrics, topTypes }: { metrics: ObservabilityMetrics; topTypes: Array<[string, number]> }) {
  const statusRef = useRef<HTMLDivElement>(null);
  const typeRef = useRef<HTMLDivElement>(null);
  const statusOptions = useMemo<EChartsOption>(
    () => ({
      tooltip: { trigger: 'item' },
      color: ['#20704A', '#A53232', '#A55C00', '#0098D1', '#66737C'],
      series: [
        {
          type: 'pie',
          radius: ['48%', '72%'],
          avoidLabelOverlap: true,
          label: { formatter: '{b}: {c}' },
          data: Object.entries(metrics.eventsByStatus).map(([name, value]) => ({ name, value }))
        }
      ]
    }),
    [metrics.eventsByStatus]
  );
  const typeOptions = useMemo<EChartsOption>(
    () => ({
      grid: { top: 8, right: 16, bottom: 28, left: 130 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      color: ['#0098D1'],
      xAxis: { type: 'value', minInterval: 1 },
      yAxis: {
        type: 'category',
        data: [...topTypes].reverse().map(([type]) => type),
        axisLabel: { width: 118, overflow: 'truncate' }
      },
      series: [
        {
          type: 'bar',
          data: [...topTypes].reverse().map(([, value]) => value),
          barMaxWidth: 18
        }
      ]
    }),
    [topTypes]
  );

  useEChart(statusRef, statusOptions);
  useEChart(typeRef, typeOptions);

  return (
    <div className={panelStyles.metricsChartGrid}>
      <div>
        <Text strong>事件状态图</Text>
        <div ref={statusRef} className={panelStyles.metricsChart} role="img" aria-label="事件状态分布图" />
      </div>
      <div>
        <Text strong>高频事件图</Text>
        <div ref={typeRef} className={panelStyles.metricsChart} role="img" aria-label="高频事件柱状图" />
      </div>
    </div>
  );
}

function useEChart(ref: RefObject<HTMLDivElement | null>, option: EChartsOption): void {
  useEffect(() => {
    if (!ref.current) {
      return;
    }
    const chart = echarts.init(ref.current);
    chart.setOption(option);
    const resize = () => chart.resize();
    window.addEventListener('resize', resize);
    return () => {
      window.removeEventListener('resize', resize);
      chart.dispose();
    };
  }, [option, ref]);
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

function feedbackLabel(category?: string | null): string {
  if (category === 'risk_cancel') return '风险确认取消';
  if (category === 'failure') return '失败或阻塞';
  if (category === 'suggestion') return '改进建议';
  return '导出体验';
}

function feedbackColor(category?: string | null): string {
  if (category === 'failure') return 'error';
  if (category === 'risk_cancel') return 'warning';
  if (category === 'suggestion') return 'processing';
  return 'success';
}

function formatFeedbackTime(value?: string): string {
  if (!value) {
    return '-';
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value));
}
