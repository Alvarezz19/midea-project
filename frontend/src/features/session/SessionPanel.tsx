import { Button, Input, List, Space, Typography, message as antMessage } from 'antd';
import { SendOutlined, PlusOutlined } from '@ant-design/icons';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { createSession, formatApiError, sendMessage } from '../../api/client';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text, Title } = Typography;

export function SessionPanel() {
  const [draft, setDraft] = useState('');
  const threadId = useWorkbenchStore((store) => store.threadId);
  const projectType = useWorkbenchStore((store) => store.projectType);
  const state = useWorkbenchStore((store) => store.state);
  const setSession = useWorkbenchStore((store) => store.setSession);
  const [messageApi, holder] = antMessage.useMessage();

  const createMutation = useMutation({
    mutationFn: () => createSession(projectType),
    onSuccess: (data) => setSession({ threadId: data.thread_id, traceId: data.trace_id, state: data.state }),
    onError: (error) => messageApi.error(formatApiError(error))
  });

  const sendMutation = useMutation({
    mutationFn: () => sendMessage(threadId!, draft, projectType),
    onSuccess: (data) => {
      setSession({ threadId: data.thread_id, traceId: data.trace_id, state: data.state });
      setDraft('');
    },
    onError: (error) => messageApi.error(formatApiError(error))
  });

  const messages = state?.messages ?? [];

  return (
    <section className={panelStyles.panel}>
      {holder}
      <header className={panelStyles.header}>
        <div>
          <Title level={2}>会话与需求</Title>
          <Text type="secondary">{threadId ? `thread ${threadId}` : '先创建会话，再输入自然语言需求'}</Text>
        </div>
        <Button icon={<PlusOutlined />} loading={createMutation.isPending} onClick={() => createMutation.mutate()}>
          新建
        </Button>
      </header>

      <div className={panelStyles.section}>
        <Text strong>结构化需求</Text>
        <div className={panelStyles.chips}>
          {(state?.confirmed_requirements?.length ? state.confirmed_requirements : ['等待需求分析']).map((item) => (
            <span key={item}>{item}</span>
          ))}
        </div>
        {state?.open_questions?.length ? (
          <List
            size="small"
            dataSource={state.open_questions}
            renderItem={(item) => <List.Item className={panelStyles.question}>{item}</List.Item>}
          />
        ) : null}
      </div>

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
          onClick={() => sendMutation.mutate()}
        >
          发送
        </Button>
      </Space.Compact>
    </section>
  );
}
