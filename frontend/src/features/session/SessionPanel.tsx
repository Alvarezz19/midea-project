import { Alert, Button, Input, List, Space, Switch, Tag, Typography, message as antMessage } from 'antd';
import { SendOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { createSession, formatApiError, sendMessage } from '../../api/client';
import type { RequirementQuestion, SemanticTargetCandidate } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text, Title } = Typography;
type SendPayload = { content: string; selectedCandidateId?: string };

export function SessionPanel() {
  const [draft, setDraft] = useState('');
  const messageListRef = useRef<HTMLDivElement>(null);
  const threadId = useWorkbenchStore((store) => store.threadId);
  const projectType = useWorkbenchStore((store) => store.projectType);
  const state = useWorkbenchStore((store) => store.state);
  const useLlmPlanner = useWorkbenchStore((store) => store.useLlmPlanner);
  const setUseLlmPlanner = useWorkbenchStore((store) => store.setUseLlmPlanner);
  const setSession = useWorkbenchStore((store) => store.setSession);
  const appendMessage = useWorkbenchStore((store) => store.appendMessage);
  const [messageApi, holder] = antMessage.useMessage();

  const createMutation = useMutation({
    mutationFn: () => createSession(projectType, { useLlmPlanner }),
    onSuccess: (data) => setSession({ threadId: data.thread_id, traceId: data.trace_id, state: data.state }),
    onError: (error) => messageApi.error(formatApiError(error))
  });

  const sendMutation = useMutation({
    mutationFn: ({ content, selectedCandidateId }: SendPayload) =>
      sendMessage(threadId!, content, projectType, { useLlmPlanner, selectedCandidateId }),
    onSuccess: (data) => {
      setSession({ threadId: data.thread_id, traceId: data.trace_id, state: data.state });
    },
    onError: (error) => messageApi.error(formatApiError(error))
  });

  const messages = state?.messages ?? [];
  useEffect(() => {
    const list = messageListRef.current;
    if (!list) {
      return;
    }
    list.scrollTop = list.scrollHeight;
  }, [messages.length]);

  const submitDraft = () => {
    const content = draft.trim();
    if (!threadId || !content) {
      return;
    }
    appendMessage({ role: 'user', content });
    setDraft('');
    sendMutation.mutate({ content });
  };

  const selectCandidate = (candidate: SemanticTargetCandidate, index: number) => {
    if (!threadId) {
      return;
    }
    const content = `选择第 ${index + 1} 个：${candidate.display_name ?? '候选对象'}`;
    appendMessage({ role: 'user', content });
    sendMutation.mutate({ content, selectedCandidateId: candidate.candidate_id });
  };

  const questions = (state?.open_questions ?? []).map(questionText);
  const designBrief = state?.design_brief;
  const semanticCandidates = state?.semantic_target_candidates ?? [];
  const touchedEntities = state?.last_touched_entities ?? [];
  const showSemanticCandidateSelection = semanticCandidates.length > 0 && state?.next_action === 'clarify_patch';
  const summaryItems = [
    ...(state?.requirement_summary?.equipment ?? []),
    ...(state?.requirement_summary?.control_features ?? []),
    ...(state?.requirement_summary?.communication ?? []),
    ...(state?.requirement_summary?.io_points ?? []),
    ...(state?.requirement_summary?.protection_logic ?? [])
  ].map(String);
  const requirements = state?.confirmed_requirements?.length ? state.confirmed_requirements : summaryItems;

  return (
    <section className={panelStyles.panel}>
      {holder}
      <header className={panelStyles.header}>
        <div>
          <Title level={2}>会话与需求</Title>
          <Text type="secondary">{threadId ? `thread ${threadId}` : '选择项目类型后创建会话'}</Text>
        </div>
        <Button icon={<PlusOutlined />} loading={createMutation.isPending} disabled={!projectType} onClick={() => createMutation.mutate()}>
          新建
        </Button>
      </header>

      <div ref={messageListRef} className={panelStyles.sessionTimeline}>
        {!projectType ? (
          <Alert className={panelStyles.inlineAlert} type="info" showIcon message="请先在顶部选择机房群控程序或 AHU 程序。" />
        ) : null}

        <div className={panelStyles.section}>
          <Text strong>结构化需求</Text>
          <div className={panelStyles.chips}>
            {(requirements.length ? requirements : ['等待需求分析']).map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
          {questions.length ? (
            <List
              size="small"
              dataSource={questions}
              renderItem={(item) => <List.Item className={panelStyles.question}>{item}</List.Item>}
            />
          ) : null}
        </div>

        {designBrief ? (
          <div className={panelStyles.section}>
            <Text strong>轻量设计摘要</Text>
            {designBrief.summary ? <p>{designBrief.summary}</p> : null}
            <div className={panelStyles.chips}>
              {[
                ...(designBrief.equipment_plan ?? []),
                ...(designBrief.control_plan ?? []),
                ...(designBrief.point_plan ?? []),
                ...(designBrief.protection_plan ?? [])
              ]
                .slice(0, 10)
                .map((item) => (
                  <span key={item}>{item}</span>
                ))}
            </div>
            {designBrief.clarification_items?.length ? (
              <List
                size="small"
                dataSource={designBrief.clarification_items.slice(0, 3)}
                renderItem={(item) => <List.Item className={panelStyles.question}>{item}</List.Item>}
              />
            ) : null}
          </div>
        ) : null}

        {showSemanticCandidateSelection ? (
          <SemanticCandidateList candidates={semanticCandidates} loadingId={sendMutation.variables?.selectedCandidateId} onSelect={selectCandidate} />
        ) : null}

        {touchedEntities.length ? <TouchedEntityList entities={touchedEntities} /> : null}

        <div className={panelStyles.messageList}>
          {messages.length ? (
            messages.map((item, index) => (
              <article key={`${item.role}-${index}`} className={item.role === 'user' ? panelStyles.userBubble : panelStyles.agentBubble}>
                <Text strong>{item.role === 'user' ? '工程师' : '智能体'}</Text>
                <p>{item.content}</p>
              </article>
            ))
          ) : (
            <div className={panelStyles.empty}>选择项目类型后，描述设备、控制目标、通讯和保护要求。</div>
          )}
        </div>
      </div>

      <Space.Compact className={panelStyles.composer} block>
        <Input.TextArea
          value={draft}
          rows={4}
          placeholder="例如：我要做 AHU 程序，需要直膨机、排风机和 Modbus 通讯"
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          disabled={!threadId || !draft.trim()}
          loading={sendMutation.isPending}
          onClick={submitDraft}
        >
          发送
        </Button>
      </Space.Compact>
      <div className={panelStyles.composerOptions}>
        <Space>
          <RobotOutlined />
          <Text type="secondary">LLM 结构化规划</Text>
          <Switch size="small" checked={useLlmPlanner} onChange={setUseLlmPlanner} />
        </Space>
      </div>
    </section>
  );
}

function questionText(item: string | RequirementQuestion): string {
  if (typeof item === 'string') {
    return item;
  }
  return item.question ?? item.field ?? '请补充需求信息。';
}

function SemanticCandidateList({
  candidates,
  loadingId,
  onSelect
}: {
  candidates: SemanticTargetCandidate[];
  loadingId?: string;
  onSelect: (candidate: SemanticTargetCandidate, index: number) => void;
}) {
  return (
    <div className={panelStyles.section}>
      <Text strong>请选择目标对象</Text>
      <List
        size="small"
        dataSource={candidates.slice(0, 5)}
        renderItem={(candidate, index) => (
          <List.Item className={panelStyles.candidateItem}>
            <div className={panelStyles.candidateBody}>
              <div className={panelStyles.candidateTitleRow}>
                <Text strong>{candidate.display_name ?? `候选 ${index + 1}`}</Text>
                <Tag color="blue">匹配 {confidenceText(candidate.confidence)}</Tag>
              </div>
              <Text type="secondary">{candidate.description || candidateMeta(candidate) || '系统已定位到一个可能的工程对象。'}</Text>
              <div className={panelStyles.tagLine}>
                {candidate.tab_label ? <Tag color="geekblue">{candidate.tab_label}</Tag> : null}
                {candidate.type ? <Tag color="cyan">{candidate.type}</Tag> : null}
                {keyParamTags(candidate.key_params).map((item) => (
                  <Tag key={item} color="default">
                    {item}
                  </Tag>
                ))}
              </div>
            </div>
            <Button
              size="small"
              type="primary"
              disabled={!candidate.candidate_id}
              loading={Boolean(candidate.candidate_id && loadingId === candidate.candidate_id)}
              onClick={() => onSelect(candidate, index)}
            >
              选择
            </Button>
          </List.Item>
        )}
      />
    </div>
  );
}

function TouchedEntityList({ entities }: { entities: Array<Record<string, unknown>> }) {
  const labels = entities.map(entityDisplayName).filter(Boolean).slice(0, 6);
  if (!labels.length) {
    return null;
  }
  return (
    <div className={panelStyles.section}>
      <Text strong>本次定位目标</Text>
      <div className={panelStyles.chips}>
        {labels.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
    </div>
  );
}

function candidateMeta(candidate: SemanticTargetCandidate): string {
  const parts = [candidate.tab_label, candidate.type].filter(Boolean).map(String);
  return parts.join(' / ');
}

function confidenceText(value: number | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '-';
  }
  return `${Math.round(value * 100)}%`;
}

function keyParamTags(value: Record<string, unknown> | undefined): string[] {
  if (!value) {
    return [];
  }
  return Object.entries(value)
    .filter(([, item]) => item !== undefined && item !== null && item !== '')
    .slice(0, 3)
    .map(([key, item]) => `${key}=${String(item)}`);
}

function entityDisplayName(value: Record<string, unknown>): string {
  return String(value.display_name ?? value.name ?? value.label ?? '').trim();
}
