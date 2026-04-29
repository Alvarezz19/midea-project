import { Background, Controls, MiniMap, ReactFlow } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Empty, List, Tag, Typography } from 'antd';
import { useMemo } from 'react';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text, Title } = Typography;

export function GraphPanel() {
  const state = useWorkbenchStore((store) => store.state);
  const affectedNodeIds = useMemo(() => {
    const dryRun = state?.planner_dry_run as { diff?: { affected_node_ids?: string[] } } | undefined;
    return dryRun?.diff?.affected_node_ids ?? [];
  }, [state?.planner_dry_run]);
  const report = state?.validation_report as { issues?: Array<Record<string, unknown>>; blocked_export_reasons?: string[] } | null | undefined;
  const issues = report?.issues ?? [];
  const blockedReasons = state?.validation_summary?.blocked_export_reasons ?? report?.blocked_export_reasons ?? [];

  return (
    <section className={panelStyles.panel}>
      <header className={panelStyles.header}>
        <div>
          <Title level={2}>局部流程图与校验</Title>
          <Text type="secondary">7.4 会接入版本 flow 接口；移动端降级为影响列表</Text>
        </div>
        <Tag color={state?.validation_summary?.valid ? 'success' : 'default'}>
          {state?.validation_summary?.valid ? '可导出' : '待校验'}
        </Tag>
      </header>

      <div className={panelStyles.flowPreview}>
        <ReactFlow nodes={[]} edges={[]} fitView>
          <Background />
          <MiniMap pannable zoomable />
          <Controls />
        </ReactFlow>
        <div className={panelStyles.flowEmpty}>
          <Empty description="等待局部图数据" />
        </div>
      </div>

      <div className={panelStyles.section}>
        <Text strong>影响节点</Text>
        <div className={panelStyles.chips}>
          {affectedNodeIds.length ? affectedNodeIds.map((id) => <span key={id}>{id}</span>) : <span>暂无 diff</span>}
        </div>
      </div>

      <div className={panelStyles.section}>
        <Text strong>校验摘要</Text>
        {blockedReasons.length ? (
          <List
            size="small"
            dataSource={blockedReasons}
            renderItem={(item) => <List.Item className={panelStyles.question}>{item}</List.Item>}
          />
        ) : null}
        <pre className={panelStyles.jsonBlock}>{JSON.stringify(state?.validation_summary ?? {}, null, 2)}</pre>
        {issues.length ? (
          <List
            size="small"
            dataSource={issues.slice(0, 8)}
            renderItem={(item) => (
              <List.Item className={item.severity === 'error' ? panelStyles.errorIssue : panelStyles.warningIssue}>
                <Text>{String(item.message ?? item.code ?? '校验问题')}</Text>
              </List.Item>
            )}
          />
        ) : null}
      </div>
    </section>
  );
}
