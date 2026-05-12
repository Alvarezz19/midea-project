import { Alert, Button, Checkbox, Collapse, Descriptions, Empty, List, Popconfirm, Space, Statistic, Tag, Typography, message as antMessage } from 'antd';
import { BulbOutlined, CheckCircleOutlined, CloseCircleOutlined, ForkOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { confirmPatch, confirmTemplate, formatApiError, sendMessage } from '../../api/client';
import type { AdvisoryResult, CandidateRequirement, DesignBrief, RequirementConformanceReport, SemanticTargetCandidate, TemplateCandidate } from '../../api/types';
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
  const designBrief = state?.design_brief;
  const conformance = state?.conformance_report ?? state?.validation_report?.conformance_report;
  const advisory = state?.advisory_result;
  const pendingAdvice = state?.pending_advice?.status === 'pending_review' ? state.pending_advice : null;
  const candidateRequirements = state?.candidate_requirements ?? [];
  const semanticCandidates = state?.semantic_target_candidates ?? [];
  const touchedEntities = state?.last_touched_entities ?? [];
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

  const adviceActionMutation = useMutation({
    mutationFn: (payload: { message: string; selectedCandidateRequirementIds?: string[] }) =>
      sendMessage(threadId!, payload.message, undefined, { selectedCandidateRequirementIds: payload.selectedCandidateRequirementIds }),
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

        {designBrief ? <DesignBriefView brief={designBrief} /> : null}
        {advisory || pendingAdvice || candidateRequirements.length ? (
          <AdvisoryView
            advisory={advisory ?? pendingAdvice}
            candidateRequirements={candidateRequirements}
            onAction={(message, selectedCandidateRequirementIds) => adviceActionMutation.mutate({ message, selectedCandidateRequirementIds })}
            actionLoading={adviceActionMutation.isPending}
            disabled={!threadId}
          />
        ) : null}
        {conformance ? <ConformanceReportView report={conformance} /> : null}
        {semanticCandidates.length || touchedEntities.length ? (
          <TargetResolutionView candidates={semanticCandidates} touchedEntities={touchedEntities} />
        ) : null}

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

function AdvisoryView({
  advisory,
  candidateRequirements,
  onAction,
  actionLoading,
  disabled
}: {
  advisory?: AdvisoryResult | null;
  candidateRequirements: CandidateRequirement[];
  onAction: (message: string, selectedCandidateRequirementIds?: string[]) => void;
  actionLoading: boolean;
  disabled: boolean;
}) {
  const adoptable = advisory?.adoptable_patch_intent;
  const executable = Boolean(adoptable?.executable && adoptable.message);
  const alertType = advisory?.status === 'unsafe_request' ? 'warning' : advisory?.status === 'out_of_scope' ? 'info' : 'success';
  const currentCandidates = useMemo(() => uniqueCandidateRequirements(advisory?.candidate_requirements ?? []), [advisory?.candidate_requirements]);
  const currentCandidateKey = currentCandidates.map(candidateRequirementKey).join('|');
  const [selectedRequirementIds, setSelectedRequirementIds] = useState<string[]>([]);
  useEffect(() => {
    setSelectedRequirementIds([]);
  }, [currentCandidateKey]);
  const toggleRequirement = (candidate: CandidateRequirement, checked: boolean) => {
    const id = candidateRequirementKey(candidate);
    setSelectedRequirementIds((current) => (checked ? Array.from(new Set([...current, id])) : current.filter((item) => item !== id)));
  };
  const hasCurrentCandidates = currentCandidates.length > 0;
  return (
    <div className={panelStyles.section}>
      <div className={panelStyles.sectionHeading}>
        <Text strong>
          <BulbOutlined /> 设计建议
        </Text>
        <Tag color={advisory?.status === 'unsafe_request' ? 'orange' : advisory?.status === 'out_of_scope' ? 'default' : 'cyan'}>
          {advisoryStatusText(advisory?.status)}
        </Tag>
      </div>
      {advisory?.answer ? (
        <Alert className={panelStyles.inlineAlertTight} type={alertType} showIcon message={advisory.topic || '工程咨询'} description={advisory.answer} />
      ) : (
        <Text type="secondary">咨询建议会在这里展示，不会直接写入工程版本。</Text>
      )}
      {advisory?.recommendation && Object.keys(advisory.recommendation).length ? (
        <Descriptions className={panelStyles.compactDescriptions} column={2} size="small" bordered>
          <Descriptions.Item label="推荐">{recommendationText(advisory.recommendation)}</Descriptions.Item>
          <Descriptions.Item label="置信度">{String(advisory.recommendation.confidence ?? '-')}</Descriptions.Item>
        </Descriptions>
      ) : null}
      <BriefTags title="依据" items={(advisory?.basis ?? []).map((item) => [item.source, item.summary].filter(Boolean).join('：'))} color="blue" />
      <BriefTags title="前提" items={advisory?.assumptions ?? []} color="cyan" />
      <BriefTags title="风险" items={advisory?.risks ?? []} color="orange" />
      <BriefTags title="缺失" items={advisory?.missing_info ?? []} color="warning" />
      <CandidateRequirementsView
        current={currentCandidates}
        accumulated={candidateRequirements}
        selectedIds={selectedRequirementIds}
        onToggle={toggleRequirement}
      />
      <Space className={panelStyles.adviceActions} wrap>
        <Button
          type="primary"
          icon={<CheckCircleOutlined />}
          disabled={disabled || !executable}
          loading={actionLoading}
          onClick={() => onAction('采纳建议')}
        >
          采纳并生成修改计划
        </Button>
        <Button
          disabled={disabled || !hasCurrentCandidates || !selectedRequirementIds.length}
          loading={actionLoading}
          onClick={() => onAction('先不改，但把选中的候选需求记录下来', selectedRequirementIds)}
        >
          纳入选中 {selectedRequirementIds.length}
        </Button>
        <Button disabled={disabled} loading={actionLoading} onClick={() => onAction('先不改')}>
          暂不采用
        </Button>
      </Space>
      {adoptable?.reason ? <Text type="secondary">{adoptable.reason}</Text> : null}
    </div>
  );
}

function CandidateRequirementsView({
  current,
  accumulated,
  selectedIds,
  onToggle
}: {
  current: CandidateRequirement[];
  accumulated: CandidateRequirement[];
  selectedIds: string[];
  onToggle: (item: CandidateRequirement, checked: boolean) => void;
}) {
  const accumulatedRows = uniqueCandidateRequirements(accumulated);
  if (!current.length && !accumulatedRows.length) {
    return null;
  }
  return (
    <div className={panelStyles.candidateRequirementBox}>
      {current.length ? (
        <>
          <div className={panelStyles.sectionHeading}>
            <Text type="secondary">本轮候选需求</Text>
            <Tag color="cyan">待选择 {current.length}</Tag>
          </div>
          <List
            size="small"
            dataSource={current}
            renderItem={(item) => {
              const id = candidateRequirementKey(item);
              return (
                <List.Item className={panelStyles.requirementChoiceItem}>
                  <Checkbox checked={selectedIds.includes(id)} onChange={(event) => onToggle(item, event.target.checked)}>
                    <span className={panelStyles.requirementText}>{item.content ?? '未命名候选需求'}</span>
                  </Checkbox>
                </List.Item>
              );
            }}
          />
        </>
      ) : null}
      {accumulatedRows.length ? (
        <>
          <div className={panelStyles.sectionHeading}>
            <Text type="secondary">已纳入候选需求</Text>
            <Tag color="blue">累计 {accumulatedRows.length}</Tag>
          </div>
          <List
            size="small"
            dataSource={accumulatedRows}
            renderItem={(item) => (
              <List.Item className={panelStyles.requirementItem}>
                <Tag color={candidateRequirementColor(item.status)}>{candidateRequirementStatus(item.status)}</Tag>
                <Text>{item.content ?? '未命名候选需求'}</Text>
              </List.Item>
            )}
          />
        </>
      ) : null}
    </div>
  );
}

function TargetResolutionView({
  candidates,
  touchedEntities
}: {
  candidates: SemanticTargetCandidate[];
  touchedEntities: Array<Record<string, unknown>>;
}) {
  return (
    <div className={panelStyles.section}>
      <Text strong>语义定位</Text>
      {touchedEntities.length ? (
        <div className={panelStyles.impactStrip}>
          {touchedEntities.slice(0, 4).map((item, index) => (
            <div key={`${entityName(item)}-${index}`} className={panelStyles.impactPill}>
              <Text strong>{entityName(item) || '已影响对象'}</Text>
              {entityDescription(item) ? <Text type="secondary">{entityDescription(item)}</Text> : null}
            </div>
          ))}
        </div>
      ) : null}
      {candidates.length ? (
        <List
          size="small"
          dataSource={candidates.slice(0, 5)}
          renderItem={(candidate, index) => (
            <List.Item className={panelStyles.candidateItemReadOnly}>
              <List.Item.Meta
                title={`${index + 1}. ${candidate.display_name ?? '候选对象'}`}
                description={candidate.description || candidateReadableMeta(candidate)}
              />
              <Tag color={candidate.confidence && candidate.confidence >= 0.85 ? 'success' : 'warning'}>
                {confidenceText(candidate.confidence)}
              </Tag>
            </List.Item>
          )}
        />
      ) : null}
    </div>
  );
}

function ConformanceReportView({ report }: { report: RequirementConformanceReport }) {
  const blocked = report.blocked ?? [];
  const partial = report.partial ?? [];
  const missing = report.missing ?? [];
  const covered = report.covered ?? [];
  const rows = conformanceRows(blocked, missing, partial);
  const type = report.context_available === false ? 'info' : blocked.length ? 'error' : 'success';
  const message = report.context_available === false ? '需求覆盖未复核' : blocked.length ? '需求覆盖阻塞导出' : '需求覆盖已复核';
  return (
    <div className={panelStyles.section}>
      <Text strong>导出门禁</Text>
      <Alert
        className={panelStyles.inlineAlert}
        type={type}
        showIcon
        message={message}
        description={blocked[0] ?? report.warnings?.[0] ?? `已覆盖 ${covered.length} 项，需人工复核 ${partial.length} 项。`}
      />
      {rows.length ? (
        <List
          size="small"
          dataSource={rows}
          renderItem={(item) => (
            <List.Item className={panelStyles.operationItem}>
              <Tag color={item.tone}>{item.tone === 'error' ? '阻塞' : item.tone === 'warning' ? '缺失' : '复核'}</Tag>
              <Text>{item.label}</Text>
            </List.Item>
          )}
        />
      ) : null}
    </div>
  );
}

function conformanceRows(
  blocked: string[],
  missing: NonNullable<RequirementConformanceReport['missing']>,
  partial: NonNullable<RequirementConformanceReport['partial']>
): Array<{ label: string; tone: 'error' | 'warning' | 'default' }> {
  const rows: Array<{ label: string; tone: 'error' | 'warning' | 'default' }> = [];
  const seen = new Set<string>();
  const push = (label: string, tone: 'error' | 'warning' | 'default') => {
    if (!label || seen.has(label)) {
      return;
    }
    seen.add(label);
    rows.push({ label, tone });
  };
  blocked.forEach((item) => push(item, 'error'));
  missing.slice(0, 3).forEach((item) => push(item.reason ?? item.requirement ?? '未覆盖需求', 'warning'));
  partial.slice(0, 3).forEach((item) => push(item.reason ?? item.requirement ?? '需人工复核', 'default'));
  return rows;
}

function DesignBriefView({ brief }: { brief: DesignBrief }) {
  const selected = brief.selected_template;
  return (
    <div className={panelStyles.section}>
      <Text strong>设计摘要</Text>
      {brief.summary ? <p>{brief.summary}</p> : null}
      <Descriptions className={panelStyles.compactDescriptions} column={2} size="small" bordered>
        <Descriptions.Item label="推荐模板">{selected?.file_name ?? selected?.template_id ?? '-'}</Descriptions.Item>
        <Descriptions.Item label="模板得分">{String(selected?.score ?? '-')}</Descriptions.Item>
        <Descriptions.Item label="已满足">{String(brief.satisfied_requirements?.length ?? 0)}</Descriptions.Item>
        <Descriptions.Item label="需改造">{String(brief.modification_items?.length ?? 0)}</Descriptions.Item>
      </Descriptions>
      <BriefTags title="设备" items={brief.equipment_plan ?? []} color="blue" />
      <BriefTags title="控制" items={brief.control_plan ?? []} color="cyan" />
      <BriefTags title="点表" items={brief.point_plan ?? []} color="geekblue" />
      <BriefTags title="保护" items={brief.protection_plan ?? []} color="green" />
      {brief.recommendation_reasons?.length ? (
        <List
          size="small"
          dataSource={brief.recommendation_reasons.slice(0, 3)}
          renderItem={(item) => <List.Item className={panelStyles.operationItem}><Text strong>推荐理由</Text><Text>{item}</Text></List.Item>}
        />
      ) : null}
      {brief.modification_items?.length ? (
        <Alert
          className={panelStyles.inlineAlertTight}
          type="warning"
          showIcon
          message="需改造或复核"
          description={brief.modification_items.slice(0, 4).join('；')}
        />
      ) : null}
    </div>
  );
}

function entityName(value: Record<string, unknown>): string {
  return String(value.display_name ?? value.name ?? value.label ?? '').trim();
}

function entityDescription(value: Record<string, unknown>): string {
  return String(value.description ?? value.reason ?? '').trim();
}

function candidateReadableMeta(candidate: SemanticTargetCandidate): string {
  return [candidate.tab_label, candidate.type].filter(Boolean).join(' / ') || '系统已定位到一个可能的工程对象。';
}

function confidenceText(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '匹配 -';
  }
  return `匹配 ${Math.round(value * 100)}%`;
}

function BriefTags({ title, items, color }: { title: string; items: string[]; color: string }) {
  if (!items.length) {
    return null;
  }
  return (
    <div className={panelStyles.tagLine}>
      <Text type="secondary">{title}</Text>
      {items.slice(0, 6).map((item) => (
        <Tag key={`${title}-${item}`} color={color}>
          {item}
        </Tag>
      ))}
    </div>
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
      <IoPointChangeList dryRun={dryRun} />
      <CopyBlockImpactList dryRun={dryRun} />
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
      <Text type="secondary">预计改造：{cost?.level ?? item.estimated_effort ?? '未知'}{costReasons(cost).length ? `，${costReasons(cost).join('；')}` : ''}</Text>
      {item.recommendation_reasons?.length ? <Text type="secondary">推荐理由：{item.recommendation_reasons.slice(0, 2).join('；')}</Text> : null}
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

function costReasons(cost: TemplateCandidate['estimated_modification_cost']): string[] {
  if (!cost) {
    return [];
  }
  if (Array.isArray(cost.reasons)) {
    return cost.reasons.map(String).filter(Boolean);
  }
  return cost.reason ? [cost.reason] : [];
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

function advisoryStatusText(status?: string): string {
  const labels: Record<string, string> = {
    answered: '已回答',
    needs_more_info: '需补充',
    out_of_scope: '范围外',
    unsafe_request: '高风险'
  };
  return labels[status ?? ''] ?? '建议';
}

function recommendationText(value: Record<string, unknown>): string {
  const main = [value.value, value.unit].filter((item) => item !== undefined && item !== null && item !== '').join('');
  const range = value.range ? `范围 ${String(value.range)}` : '';
  return [main || '-', range].filter(Boolean).join(' · ');
}

function uniqueCandidateRequirements(items: CandidateRequirement[]): CandidateRequirement[] {
  const result: CandidateRequirement[] = [];
  const seen = new Set<string>();
  items.forEach((item) => {
    const content = String(item.content ?? '').trim();
    if (!content || seen.has(content)) {
      return;
    }
    seen.add(content);
    result.push(item);
  });
  return result;
}

function candidateRequirementKey(item: CandidateRequirement): string {
  return String(item.candidate_id ?? item.content ?? '').trim();
}

function candidateRequirementStatus(status?: string): string {
  const labels: Record<string, string> = {
    observed: '提及',
    candidate: '候选',
    confirmed: '确认',
    rejected: '拒绝'
  };
  return labels[status ?? ''] ?? '候选';
}

function candidateRequirementColor(status?: string): string {
  if (status === 'confirmed') return 'success';
  if (status === 'rejected') return 'default';
  if (status === 'observed') return 'processing';
  return 'cyan';
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
    add_tab: '新增页面',
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
    valueText('页面', operation.label),
    valueText('输入端', operation.target_input),
    valueText('新名称', operation.new_name),
    valueText('新值', operation.new_value),
    valueText('block_id', operation.block_id),
    paramsText(operation.params)
  ].filter(Boolean);
  return parts.join(' · ') || '请在确认前检查影响节点、风险原因和校验结果。';
}

function IoPointChangeList({ dryRun }: { dryRun?: { diff?: { summary?: Record<string, unknown>; affected_node_ids?: string[] }; valid?: boolean } | null }) {
  const changes = dryRunChanges(dryRun).filter((item) => item.op === 'set_io_point');
  if (!changes.length) {
    return null;
  }
  return (
    <List
      size="small"
      dataSource={changes.slice(0, 6)}
      renderItem={(item) => (
        <List.Item className={panelStyles.operationItem}>
          <Tag color="orange">点位</Tag>
          <Text>{String(item.node_id ?? '未知节点')}</Text>
          <Text type="secondary">
            {String(item.field ?? '字段')}：{String(item.old_value ?? '-')} -&gt; {String(item.new_value ?? '-')}
          </Text>
        </List.Item>
      )}
    />
  );
}

function CopyBlockImpactList({ dryRun }: { dryRun?: { diff?: { summary?: Record<string, unknown>; affected_node_ids?: string[] }; valid?: boolean } | null }) {
  const changes = dryRunChanges(dryRun).filter((item) => item.op === 'copy_block');
  if (!changes.length) {
    return null;
  }
  return (
    <List
      size="small"
      dataSource={changes.slice(0, 3)}
      renderItem={(item) => (
        <List.Item className={panelStyles.operationItem}>
          <Tag color="red">边界</Tag>
          <Text>{String(item.block_id ?? 'copy_block')}</Text>
          <Text type="secondary">
            复制 {String(item.copied_node_count ?? 0)} 个节点，入口外部线丢弃 {String(item.dropped_external_input_count ?? 0)} 条，边界接线 {String(item.boundary_connection_count ?? 0)} 条。
          </Text>
        </List.Item>
      )}
    />
  );
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

function paramsText(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return '';
  }
  return Object.entries(value as Record<string, unknown>)
    .slice(0, 3)
    .map(([key, item]) => `${key}=${String(item)}`)
    .join('，');
}

function dryRunChanges(value: unknown): Array<Record<string, unknown>> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return [];
  }
  const changes = (value as { changes?: unknown }).changes;
  if (!Array.isArray(changes)) {
    return [];
  }
  return changes.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item));
}
