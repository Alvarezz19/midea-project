import { useEffect, useMemo, useRef, type RefObject } from 'react';
import { Alert, Button, Drawer, Form, Input, List, Rate, Select, Skeleton, Space, Statistic, Tabs, Tag, Typography, message as antMessage } from 'antd';
import { SendOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { BarChart, LineChart, PieChart } from 'echarts/charts';
import { GridComponent, TooltipComponent } from 'echarts/components';
import * as echarts from 'echarts/core';
import { SVGRenderer } from 'echarts/renderers';
import type { EChartsOption } from 'echarts';
import { formatApiError, getCostSummary, getMetricsSummary, getTrendSummary, listProjectFeedback, submitFeedback } from '../../api/client';
import type { CostSummaryResponse, FeedbackResponse, ObservabilityMetrics, ObservabilityTrendResponse } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';
import { VersionHistoryPanel } from '../versions/VersionHistoryPanel';

const { Text } = Typography;

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, SVGRenderer]);

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
  const projectId = useWorkbenchStore((store) => store.state?.project_id ?? store.state?.current_project_id);
  const query = useQuery({
    queryKey: ['observability-metrics'],
    queryFn: getMetricsSummary,
    refetchInterval: 15000
  });
  const costQuery = useQuery({
    queryKey: ['observability-costs'],
    queryFn: () => getCostSummary(),
    refetchInterval: 30000
  });
  const trendQuery = useQuery({
    queryKey: ['observability-trends', projectId ?? null],
    queryFn: () => getTrendSummary(projectId),
    refetchInterval: 30000
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
  return (
    <>
      <MetricsSummary metrics={query.data} />
      <TrendSummaryPanel trends={trendQuery.data} loading={trendQuery.isLoading} error={trendQuery.error} />
      <CostSummaryPanel cost={costQuery.data} loading={costQuery.isLoading} error={costQuery.error} />
    </>
  );
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

function CostSummaryPanel({ cost, loading, error }: { cost?: CostSummaryResponse; loading: boolean; error: unknown }) {
  if (loading) {
    return <Skeleton active paragraph={{ rows: 3 }} />;
  }
  if (error) {
    return <Alert type="error" showIcon message="成本聚合加载失败" description={formatApiError(error)} />;
  }
  if (!cost) {
    return <Text type="secondary">暂无成本数据</Text>;
  }
  return (
    <div className={panelStyles.costPanel}>
      <div className={panelStyles.sectionHeading}>
        <Text strong>LLM 成本聚合</Text>
        <Text type="secondary">按项目、供应商、模型、prompt 和日期聚合</Text>
      </div>
      <div className={panelStyles.costStats}>
        <Statistic title="估算成本" value={formatCost(cost.total.estimated_cost)} />
        <Statistic title="调用次数" value={cost.total.calls} />
        <Statistic title="失败调用" value={cost.total.failed_calls} valueStyle={cost.total.failed_calls ? { color: '#A53232' } : undefined} />
        <Statistic title="Token 合计" value={formatTokenCount(cost.total.input_tokens + cost.total.output_tokens)} />
      </div>
      <CostDateChart buckets={cost.by_date} />
      <div className={panelStyles.costColumns}>
        <CostList
          title="供应商 / 模型"
          items={cost.by_provider_model.slice(0, 6)}
          label={(item) => `${item.provider ?? 'unknown'} / ${item.model ?? 'unknown'}`}
        />
        <CostList title="Prompt" items={cost.by_prompt.slice(0, 6)} label={(item) => item.prompt_name ?? 'unknown'} />
        <CostList title="项目" items={cost.by_project.slice(0, 6)} label={(item) => item.project_id ?? 'unknown'} />
      </div>
    </div>
  );
}

function TrendSummaryPanel({ trends, loading, error }: { trends?: ObservabilityTrendResponse; loading: boolean; error: unknown }) {
  if (loading) {
    return <Skeleton active paragraph={{ rows: 3 }} />;
  }
  if (error) {
    return <Alert type="error" showIcon message="趋势加载失败" description={formatApiError(error)} />;
  }
  if (!trends?.buckets.length) {
    return (
      <div className={panelStyles.trendPanel}>
        <div className={panelStyles.sectionHeading}>
          <Text strong>趋势维度</Text>
          <Text type="secondary">暂无趋势数据</Text>
        </div>
      </div>
    );
  }
  const latest = trends.buckets[trends.buckets.length - 1];
  return (
    <div className={panelStyles.trendPanel}>
      <div className={panelStyles.sectionHeading}>
        <Text strong>趋势维度</Text>
        <Text type="secondary">请求量、失败率、P95、LLM 调用和风险确认取消率</Text>
      </div>
      <div className={panelStyles.trendStats}>
        <Statistic title="最近请求" value={latest.request_count} />
        <Statistic title="最近失败率" value={formatRate(latest.error_rate)} valueStyle={latest.error_rate ? { color: '#A53232' } : undefined} />
        <Statistic title="最近 P95" value={formatMetricDuration(latest.p95_duration_ms)} />
        <Statistic title="确认取消率" value={formatRate(latest.confirmation_cancel_rate)} valueStyle={latest.confirmation_cancel_rate ? { color: '#A55C00' } : undefined} />
      </div>
      <TrendCharts trends={trends} />
    </div>
  );
}

function TrendCharts({ trends }: { trends: ObservabilityTrendResponse }) {
  const trafficRef = useRef<HTMLDivElement>(null);
  const qualityRef = useRef<HTMLDivElement>(null);
  const labels = trends.buckets.map((item) => item.date);
  const trafficOptions = useMemo<EChartsOption>(
    () => ({
      grid: { top: 10, right: 20, bottom: 32, left: 48 },
      tooltip: { trigger: 'axis' },
      color: ['#0098D1', '#C97A34'],
      xAxis: { type: 'category', data: labels },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        {
          name: '请求量',
          type: 'bar',
          data: trends.buckets.map((item) => item.request_count),
          barMaxWidth: 22
        },
        {
          name: 'LLM 调用',
          type: 'line',
          data: trends.buckets.map((item) => item.llm_calls),
          smooth: true
        }
      ]
    }),
    [labels, trends.buckets]
  );
  const qualityOptions = useMemo<EChartsOption>(
    () => ({
      grid: { top: 10, right: 24, bottom: 32, left: 58 },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (value) => (typeof value === 'number' && value <= 1 ? formatRate(value) : String(value))
      },
      color: ['#A53232', '#006A94', '#A55C00'],
      xAxis: { type: 'category', data: labels },
      yAxis: [
        { type: 'value', min: 0, axisLabel: { formatter: (value: number) => formatRate(value) } },
        { type: 'value', min: 0, axisLabel: { formatter: (value: number) => formatMetricDuration(value) } }
      ],
      series: [
        {
          name: '失败率',
          type: 'line',
          data: trends.buckets.map((item) => item.error_rate),
          smooth: true
        },
        {
          name: 'P95',
          type: 'line',
          yAxisIndex: 1,
          data: trends.buckets.map((item) => item.p95_duration_ms),
          smooth: true
        },
        {
          name: '确认取消率',
          type: 'line',
          data: trends.buckets.map((item) => item.confirmation_cancel_rate),
          smooth: true
        }
      ]
    }),
    [labels, trends.buckets]
  );

  useEChart(trafficRef, trafficOptions);
  useEChart(qualityRef, qualityOptions);

  return (
    <div className={panelStyles.trendChartGrid}>
      <div>
        <Text strong>请求与 LLM 调用</Text>
        <div ref={trafficRef} className={panelStyles.trendChart} role="img" aria-label="请求与 LLM 调用趋势图" />
      </div>
      <div>
        <Text strong>失败率、P95 与确认取消率</Text>
        <div ref={qualityRef} className={panelStyles.trendChart} role="img" aria-label="质量与确认趋势图" />
      </div>
    </div>
  );
}

function CostList({ title, items, label }: { title: string; items: CostSummaryResponse['by_project']; label: (item: CostSummaryResponse['total']) => string }) {
  return (
    <div>
      <Text strong>{title}</Text>
      <List
        size="small"
        dataSource={items}
        locale={{ emptyText: '暂无调用' }}
        renderItem={(item) => (
          <List.Item className={panelStyles.costItem}>
            <div>
              <Text ellipsis>{label(item)}</Text>
              <div>
                <Text type="secondary">
                  {item.calls} 次 · {formatTokenCount(item.input_tokens + item.output_tokens)} token · {formatMetricDuration(item.average_latency_ms)}
                </Text>
              </div>
            </div>
            <Tag>{formatCost(item.estimated_cost)}</Tag>
          </List.Item>
        )}
      />
    </div>
  );
}

function CostDateChart({ buckets }: { buckets: CostSummaryResponse['by_date'] }) {
  const ref = useRef<HTMLDivElement>(null);
  const option = useMemo<EChartsOption>(
    () => ({
      grid: { top: 10, right: 20, bottom: 32, left: 58 },
      tooltip: { trigger: 'axis' },
      color: ['#C97A34'],
      xAxis: { type: 'category', data: buckets.map((item) => item.date ?? 'unknown') },
      yAxis: { type: 'value' },
      series: [
        {
          type: 'bar',
          data: buckets.map((item) => item.estimated_cost),
          barMaxWidth: 22
        }
      ]
    }),
    [buckets]
  );
  useEChart(ref, option);
  return (
    <div>
      <Text strong>日期成本</Text>
      <div ref={ref} className={panelStyles.costChart} role="img" aria-label="日期成本柱状图" />
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
    if (typeof navigator !== 'undefined' && navigator.userAgent.toLowerCase().includes('jsdom')) {
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

function formatCost(value: number): string {
  return `$${Number.isFinite(value) ? value.toFixed(4) : '0.0000'}`;
}

function formatRate(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '0%';
  }
  return `${(value * 100).toFixed(value < 0.1 ? 1 : 0)}%`;
}

function formatTokenCount(value: number): string {
  if (!Number.isFinite(value)) {
    return '0';
  }
  if (value >= 1000000) {
    return `${(value / 1000000).toFixed(1)}M`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)}K`;
  }
  return String(value);
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
