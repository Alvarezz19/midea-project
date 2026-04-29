import { Button, Empty, List, Tag, Typography } from 'antd';
import { SafetyCertificateOutlined } from '@ant-design/icons';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text, Title } = Typography;

export function PlanningPanel() {
  const state = useWorkbenchStore((store) => store.state);
  const candidates = state?.template_candidates ?? [];
  const risk = state?.risk_assessment;
  const pendingPatch = state?.pending_confirmation_patch;

  return (
    <section className={panelStyles.panel}>
      <header className={panelStyles.header}>
        <div>
          <Title level={2}>模板、计划与风险</Title>
          <Text type="secondary">候选模板、结构化补丁摘要和 dry-run 状态集中展示</Text>
        </div>
        <Tag color={risk?.requires_confirmation ? 'warning' : 'success'}>
          风险：{risk?.risk_level ?? '未评估'}
        </Tag>
      </header>

      <div className={panelStyles.section}>
        <Text strong>模板候选</Text>
        {candidates.length ? (
          <List
            dataSource={candidates}
            renderItem={(item) => (
              <List.Item className={panelStyles.templateItem}>
                <List.Item.Meta
                  title={item.file_name ?? item.template_id}
                  description={`匹配分：${item.score ?? '-'} · ${item.template_id}`}
                />
                <Button type={state?.selected_template_id === item.template_id ? 'primary' : 'default'} size="small">
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
        <pre className={panelStyles.jsonBlock}>{JSON.stringify(state?.planner_result ?? {}, null, 2)}</pre>
      </div>

      <div className={pendingPatch ? panelStyles.riskBox : panelStyles.section}>
        <Text strong>
          <SafetyCertificateOutlined /> 高风险确认
        </Text>
        {pendingPatch ? (
          <>
            <p>当前计划需要确认后才能创建新版本。后续 7.3 会接入确认/取消动作和影响范围展示。</p>
            <pre className={panelStyles.jsonBlock}>{JSON.stringify(pendingPatch, null, 2)}</pre>
          </>
        ) : (
          <Text type="secondary">暂无待确认补丁。</Text>
        )}
      </div>
    </section>
  );
}
