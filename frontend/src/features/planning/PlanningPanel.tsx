import { Alert, Button, Collapse, Descriptions, Empty, List, Popconfirm, Space, Statistic, Tag, Typography, message as antMessage } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ForkOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { confirmPatch, confirmTemplate, formatApiError } from '../../api/client';
import type { TemplateCandidate } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text, Title } = Typography;

export function PlanningPanel() {
  const threadId = useWorkbenchStore((store) => store.threadId);
  const state = useWorkbenchStore((store) => store.state);
  const setSession = useWorkbenchStore((store) => store.setSession);
  const candidates = state?.template_candidates ?? [];
  const risk = state?.risk_assessment;
  const pendingPatch = state?.pending_confirmation_patch;
  const dryRun = state?.planner_dry_run as { diff?: { summary?: Record<string, unknown>; affected_node_ids?: string[] }; valid?: boolean } | null | undefined;
  const planner = state?.planner_result as Record<string, unknown> | null | undefined;
  const visiblePatch = (planner?.pending_patch ?? pendingPatch) as Record<string, unknown> | null | undefined;
  const [messageApi, holder] = antMessage.useMessage();

  const templateMutation = useMutation({
    mutationFn: (templateId: string) => confirmTemplate(threadId!, templateId),
    onSuccess: (data) => setSession({ threadId: data.thread_id, traceId: data.trace_id, state: data.state }),
    onError: (error) => messageApi.error(formatApiError(error))
  });

  const patchMutation = useMutation({
    mutationFn: (action: 'approve' | 'cancel') => confirmPatch(threadId!, action),
    onSuccess: (data) => setSession({ threadId: data.thread_id, traceId: data.trace_id, state: data.state }),
    onError: (error) => messageApi.error(formatApiError(error))
  });

  return (
    <section className={panelStyles.panel}>
      {holder}
      <header className={panelStyles.header}>
        <div>
          <Title level={2}>模板、计划与风险</Title>
          <Text type="secondary">确认模板、查看结构化计划，并在确认门处理风险操作</Text>
        </div>
        <Tag color={risk?.requires_confirmation ? 'warning' : 'success'}>
          风险：{risk?.risk_level ?? '未评估'}
        </Tag>
      </header>

      <div className={panelStyles.panelScroll}>
        <div className={panelStyles.section}>
          <Text strong>模板候选</Text>
          {candidates.length ? (
            <List
              dataSource={candidates}
              renderItem={(item) => (
                <List.Item className={panelStyles.templateItem}>
                  <List.Item.Meta
                    title={item.file_name ?? item.template_id}
                    description={<TemplateDescription item={item} />}
                  />
                  <Button
                    type={state?.selected_template_id === item.template_id ? 'primary' : 'default'}
                    size="small"
                    loading={templateMutation.isPending && templateMutation.variables === item.template_id}
                    disabled={!threadId || state?.next_action !== 'confirm_template'}
                    onClick={() => templateMutation.mutate(item.template_id)}
                  >
                    {state?.selected_template_id === item.template_id ? '已选' : '确认'}
                  </Button>
                </List.Item>
              )}
            />
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无模板候选" />
          )}
        </div>

        <div className={panelStyles.section}>
          <Text strong>修改计划</Text>
          {planner ? (
            <Descriptions className={panelStyles.compactDescriptions} column={2} size="small" bordered>
              <Descriptions.Item label="状态">{String(planner.status ?? state?.status ?? '-')}</Descriptions.Item>
              <Descriptions.Item label="风险">{String(planner.risk_level ?? risk?.risk_level ?? '-')}</Descriptions.Item>
              <Descriptions.Item label="Planner">{String(planner.planner ?? 'rule')}</Descriptions.Item>
              <Descriptions.Item label="尝试次数">{state?.planner_attempts?.length ?? 0}</Descriptions.Item>
            </Descriptions>
          ) : (
            <Text type="secondary">创建工程版本后，继续输入局部改造需求即可生成计划。</Text>
          )}
          {dryRun ? (
            <div className={panelStyles.diffSummary}>
              <Tag color={dryRun.valid === false ? 'error' : 'success'}>{dryRun.valid === false ? 'dry-run 未通过' : 'dry-run 通过'}</Tag>
              <Text type="secondary">影响节点：{String(dryRun.diff?.summary?.affected_node_count ?? dryRun.diff?.affected_node_ids?.length ?? 0)}</Text>
              <Text type="secondary">新增/删除/修改：{summaryText(dryRun.diff?.summary)}</Text>
            </div>
          ) : null}
          {plannerQuestions(planner).length ? <Alert className={panelStyles.inlineAlert} type="warning" showIcon message={plannerQuestions(planner)[0]} /> : null}
          {visiblePatch ? <OperationPreview patch={visiblePatch} dryRun={dryRun} /> : null}
        </div>

        <div className={pendingPatch ? panelStyles.riskBox : panelStyles.section}>
          <Text strong>
            <SafetyCertificateOutlined /> 高风险确认
          </Text>
          {pendingPatch ? (
            <>
              <Alert
                className={panelStyles.inlineAlert}
                type="warning"
                showIcon
                message="确认后才会创建新版本；取消不会改变当前版本。"
                description={<RiskReasons reasons={risk?.reasons ?? risk?.risk_reasons ?? []} />}
              />
              <OperationPreview patch={pendingPatch} dryRun={dryRun} compact />
              <Space wrap>
                <Popconfirm
                  title="确认应用该计划？"
                  description="系统会从当前版本创建一个新的子版本。"
                  okText="确认应用"
                  cancelText="再检查"
                  onConfirm={() => patchMutation.mutate('approve')}
                >
                  <Button type="primary" icon={<CheckCircleOutlined />} loading={patchMutation.isPending && patchMutation.variables === 'approve'}>
                    确认应用
                  </Button>
                </Popconfirm>
                <Button
                  danger
                  icon={<CloseCircleOutlined />}
                  loading={patchMutation.isPending && patchMutation.variables === 'cancel'}
                  onClick={() => patchMutation.mutate('cancel')}
                >
                  取消
                </Button>
              </Space>
            </>
          ) : (
            <Text type="secondary">暂无待确认补丁。</Text>
          )}
        </div>
      </div>
    </section>
  );
}

function OperationPreview({
  patch,
  dryRun,
  compact = false
}: {
  patch: Record<string, unknown>;
  dryRun?: { diff?: { summary?: Record<string, unknown>; affected_node_ids?: string[] }; valid?: boolean } | null;
  compact?: boolean;
}) {
  const operations = normalizeOperations(patch);
  const summary = dryRun?.diff?.summary;
  const affectedIds = dryRun?.diff?.affected_node_ids ?? [];
  return (
    <div className={compact ? panelStyles.impactBoxCompact : panelStyles.impactBox}>
      <div className={panelStyles.impactMetrics}>
        <Statistic title="操作数" value={operations.length || 1} prefix={<ForkOutlined />} />
        <Statistic title="影响节点" value={String(summary?.affected_node_count ?? affectedIds.length ?? 0)} />
        <Statistic title="修改项" value={String(summary?.modified_count ?? 0)} />
      </div>
      <List
        size="small"
        dataSource={operations}
        renderItem={(operation, index) => (
          <List.Item className={panelStyles.operationItem}>
            <Text strong>{operationTitle(operation, index)}</Text>
            <Text type="secondary">{operationDetail(operation)}</Text>
          </List.Item>
        )}
      />
      {affectedIds.length ? (
        <div className={panelStyles.chips}>
          {affectedIds.slice(0, 10).map((id) => (
            <span key={id}>{id}</span>
          ))}
          {affectedIds.length > 10 ? <span>+{affectedIds.length - 10}</span> : null}
        </div>
      ) : null}
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: 'patch-json',
            label: '开发者补丁 JSON',
            children: <pre className={panelStyles.jsonBlock}>{JSON.stringify(patch, null, 2)}</pre>
          }
        ]}
      />
    </div>
  );
}

function TemplateDescription({ item }: { item: TemplateCandidate }) {
  const matched = item.matched_items ?? item.matched_features ?? [];
  const missing = item.missing_items ?? item.missing_features ?? [];
  const risk = item.risk_points ?? item.risk_notes ?? [];
  const cost = item.estimated_modification_cost;
  return (
    <div className={panelStyles.templateMeta}>
      <Text type="secondary">
        匹配分：{item.score ?? '-'} · {item.node_count ?? '-'} 节点 · {item.tab_count ?? '-'} 页面
      </Text>
      {item.summary ? <Text>{item.summary}</Text> : null}
      <div className={panelStyles.tagLine}>
        {(matched.length ? matched : ['待补充匹配项']).slice(0, 4).map((text) => (
          <Tag key={text} color="blue">
            {text}
          </Tag>
        ))}
        {missing.slice(0, 3).map((text) => (
          <Tag key={text} color="warning">
            缺：{text}
          </Tag>
        ))}
      </div>
      <Text type="secondary">预计改造：{cost?.level ?? item.estimated_effort ?? '未知'}{cost?.reason ? `，${cost.reason}` : ''}</Text>
      {risk.length ? <Text type="danger">风险点：{risk.slice(0, 2).join('；')}</Text> : null}
    </div>
  );
}

function RiskReasons({ reasons }: { reasons: string[] }) {
  if (!reasons.length) {
    return <span>请检查 dry-run、影响节点和校验摘要后再确认。</span>;
  }
  return <span>{reasons.join('；')}</span>;
}

function plannerQuestions(planner: Record<string, unknown> | null | undefined): string[] {
  const questions = planner?.questions;
  if (!Array.isArray(questions)) {
    return [];
  }
  return questions.map(String).filter(Boolean);
}

function summaryText(summary?: Record<string, unknown>): string {
  if (!summary) {
    return '-';
  }
  return `${String(summary.added_count ?? 0)}/${String(summary.removed_count ?? 0)}/${String(summary.modified_count ?? 0)}`;
}

function normalizeOperations(patch: Record<string, unknown>): Array<Record<string, unknown>> {
  if (Array.isArray(patch.operations)) {
    return patch.operations.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
  }
  return [patch];
}

function operationTitle(operation: Record<string, unknown>, index: number): string {
  const op = String(operation.op ?? 'unknown');
  const titles: Record<string, string> = {
    rename_node: '重命名节点',
    replace_constant: '替换设定值',
    enable_dynamic_input: '启用动态输入',
    copy_block: '复制功能块',
    set_io_point: '修改 IO/通讯点位',
    connect: '新增连线',
    disconnect: '断开连线',
    add_node_from_schema: '新增模块节点'
  };
  return `${index + 1}. ${titles[op] ?? op}`;
}

function operationDetail(operation: Record<string, unknown>): string {
  const parts = [
    selectorText(operation.node_selector ?? operation.target_node_selector ?? operation.source_node_selector),
    valueText('输入端', operation.target_input),
    valueText('新名称', operation.new_name),
    valueText('新值', operation.new_value),
    valueText('block_id', operation.block_id)
  ].filter(Boolean);
  return parts.join(' · ') || '请在确认前检查影响节点、风险原因和校验结果。';
}

function selectorText(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return '';
  }
  const selector = value as Record<string, unknown>;
  return String(selector.id ?? selector.name ?? selector.selector ?? '') ? `目标 ${String(selector.id ?? selector.name ?? selector.selector)}` : '';
}

function valueText(label: string, value: unknown): string {
  if (value === undefined || value === null || value === '') {
    return '';
  }
  return `${label} ${String(value)}`;
}
