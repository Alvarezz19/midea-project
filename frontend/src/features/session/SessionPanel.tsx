import { Alert, Button, Input, List, Space, Switch, Typography, message as antMessage } from 'antd';
import { SendOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { createSession, formatApiError, sendMessage } from '../../api/client';
import type { RequirementQuestion } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text, Title } = Typography;

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
    mutationFn: (content: string) => sendMessage(threadId!, content, projectType, { useLlmPlanner }),
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
    sendMutation.mutate(content);
  };

  const questions = (state?.open_questions ?? []).map(questionText);
  const designBrief = state?.design_brief;
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

      <div ref={messageListRef} className={panelStyles.messageList}>
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
