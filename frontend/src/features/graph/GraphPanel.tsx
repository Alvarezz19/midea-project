import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Alert, Button, Empty, List, Segmented, Space, Tag, Tooltip, Typography } from 'antd';
import { AimOutlined, BranchesOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { formatApiError, getProjectDiff, getProjectFlow } from '../../api/client';
import type { DiffNode, FlowNodeData, NodeDiff } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text, Title } = Typography;

type DiffKind = 'added' | 'removed' | 'modified';
type EngineeringFlowNode = Node<FlowNodeData, 'engineeringNode'>;

const nodeTypes = {
  engineeringNode: EngineeringNode
};

export function GraphPanel() {
  const state = useWorkbenchStore((store) => store.state);
  const selectedNodeId = useWorkbenchStore((store) => store.selectedNodeId);
  const inspectedVersionId = useWorkbenchStore((store) => store.inspectedVersionId);
  const inspectedFromVersionId = useWorkbenchStore((store) => store.inspectedFromVersionId);
  const selectNode = useWorkbenchStore((store) => store.selectNode);
  const [diffKind, setDiffKind] = useState<DiffKind>('modified');
  const projectId = state?.project_id ?? state?.current_project_id ?? undefined;
  const currentVersionId = state?.version_id ?? state?.current_project_version_id ?? undefined;
  const versionId = inspectedVersionId ?? currentVersionId;
  const report = state?.validation_report as { issues?: Array<Record<string, unknown>>; blocked_export_reasons?: string[] } | null | undefined;
  const blockedReasons = state?.validation_summary?.blocked_export_reasons ?? report?.blocked_export_reasons ?? [];
  const issues = report?.issues ?? [];

  const localDiff = useMemo(() => {
    const dryRun = state?.planner_dry_run as { diff?: NodeDiff } | undefined;
    return dryRun?.diff;
  }, [state?.planner_dry_run]);

  const diffQuery = useQuery({
    queryKey: ['project-diff', projectId, versionId, inspectedFromVersionId],
    queryFn: () => getProjectDiff(projectId!, versionId, inspectedFromVersionId),
    enabled: Boolean(projectId && versionId),
    retry: false
  });

  const diff = inspectedVersionId ? diffQuery.data?.diff : localDiff ?? diffQuery.data?.diff;
  const affectedNodeIds = diff?.affected_node_ids ?? [];
  const centerNodeId = selectedNodeId && affectedNodeIds.includes(selectedNodeId) && state?.status !== 'awaiting_patch_confirmation' ? selectedNodeId : undefined;
  const flowQuery = useQuery({
    queryKey: ['project-flow', projectId, versionId, centerNodeId],
    queryFn: () => getProjectFlow(projectId!, versionId!, centerNodeId),
    enabled: Boolean(projectId && versionId),
    retry: false
  });

  const flowNodes = useMemo(
    () => (flowQuery.data?.flow?.nodes ?? []).map((node) => toFlowNode(node, selectedNodeId, affectedNodeIds)),
    [affectedNodeIds, flowQuery.data?.flow?.nodes, selectedNodeId]
  );
  const flowEdges = useMemo(() => (flowQuery.data?.flow?.edges ?? []).map(toFlowEdge), [flowQuery.data?.flow?.edges]);
  const selectedNode = flowNodes.find((node) => node.id === selectedNodeId);
  const boundaryPreview = useMemo(() => findCopyBlockBoundaryPreview(state?.planner_dry_run), [state?.planner_dry_run]);

  const focusNode = (nodeId?: string) => {
    if (!nodeId) {
      return;
    }
    selectNode(nodeId);
  };

  return (
    <section className={panelStyles.panel}>
      <header className={panelStyles.header}>
        <div>
          <Title level={2}>局部流程图与校验</Title>
          <Text type="secondary">按版本摘要渲染流程图，diff 节点可点击定位</Text>
        </div>
        <Tag color={state?.validation_summary?.valid ? 'success' : 'default'}>
          {state?.validation_summary?.valid ? '可导出' : '待校验'}
        </Tag>
      </header>

      <div className={panelStyles.flowToolbar}>
        <Space size={8} wrap>
          <Tag color="blue">节点 {flowQuery.data?.flow?.budget.node_count ?? 0}</Tag>
          <Tag color="geekblue">连线 {flowQuery.data?.flow?.budget.edge_count ?? 0}</Tag>
          {inspectedVersionId ? <Tag color="warning">查看版本 {inspectedVersionId}</Tag> : null}
          {flowQuery.data?.flow?.budget.truncated ? <Tag color="warning">已按预算截断</Tag> : null}
        </Space>
        {selectedNodeId ? (
          <Button size="small" icon={<AimOutlined />} onClick={() => selectNode(undefined)}>
            清除定位
          </Button>
        ) : null}
      </div>

      <div className={panelStyles.flowPreview}>
        {projectId && versionId ? (
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            fitView
            minZoom={0.2}
            maxZoom={1.5}
            onNodeClick={(_, node) => focusNode(node.id)}
          >
            <Background />
            <MiniMap pannable zoomable nodeColor={(node) => nodeColor(String(node.data?.role ?? 'unknown'))} />
            <Controls />
          </ReactFlow>
        ) : null}
        {!projectId || !versionId ? (
          <div className={panelStyles.flowEmpty}>
            <Empty description="创建工程版本后显示局部流程图" />
          </div>
        ) : null}
        {flowQuery.isError ? (
          <div className={panelStyles.flowError}>
            <Alert type="warning" showIcon message="局部图加载失败" description={formatApiError(flowQuery.error)} />
          </div>
        ) : null}
      </div>

      <div className={panelStyles.section}>
        <div className={panelStyles.sectionHeading}>
          <Text strong>Diff 影响范围</Text>
          <Segmented
            size="small"
            value={diffKind}
            options={[
              { label: `新增 ${diff?.summary?.added_count ?? 0}`, value: 'added' },
              { label: `删除 ${diff?.summary?.removed_count ?? 0}`, value: 'removed' },
              { label: `修改 ${diff?.summary?.modified_count ?? 0}`, value: 'modified' }
            ]}
            onChange={(value) => setDiffKind(value as DiffKind)}
          />
        </div>
        {diffQuery.isError && !localDiff ? (
          <Alert className={panelStyles.inlineAlert} type="info" showIcon message={formatApiError(diffQuery.error)} />
        ) : null}
        <DiffList items={diff?.[diffKind] ?? []} selectedNodeId={selectedNodeId} onFocus={focusNode} />
        {selectedNode ? (
          <div className={panelStyles.nodeDetail}>
            <Text strong>{String(selectedNode.data.label ?? selectedNode.id)}</Text>
            <Text type="secondary">
              {String(selectedNode.data.module_type ?? 'unknown')} · {roleLabel(String(selectedNode.data.role ?? 'unknown'))}
              {selectedNode.data.tab_label ? ` · ${String(selectedNode.data.tab_label)}` : ''}
            </Text>
          </div>
        ) : affectedNodeIds.length ? (
          <div className={panelStyles.chips}>
            {affectedNodeIds.slice(0, 14).map((id) => (
              <button key={id} type="button" onClick={() => focusNode(id)}>
                {id}
              </button>
            ))}
            {affectedNodeIds.length > 14 ? <span>+{affectedNodeIds.length - 14}</span> : null}
          </div>
        ) : null}
      </div>

      {boundaryPreview ? <CopyBlockBoundaryPreview preview={boundaryPreview} /> : null}

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

function EngineeringNode({ data, selected }: NodeProps<EngineeringFlowNode>) {
  const role = String(data.role ?? 'unknown');
  return (
    <div
      className={`${panelStyles.engineeringNode} ${panelStyles[`role_${role}`] ?? panelStyles.role_unknown} ${
        data.affected ? panelStyles.nodeAffected : ''
      } ${selected ? panelStyles.nodeSelected : ''}`}
    >
      <Handle type="target" position={Position.Left} />
      <div className={panelStyles.nodeLabel}>{String(data.label ?? data.node_id ?? '未命名节点')}</div>
      <div className={panelStyles.nodeMeta}>
        {roleLabel(role)} · {String(data.module_type ?? 'unknown')}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function DiffList({ items, selectedNodeId, onFocus }: { items: DiffNode[]; selectedNodeId?: string; onFocus: (nodeId?: string) => void }) {
  if (!items.length) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无该类 diff" />;
  }
  return (
    <List
      size="small"
      dataSource={items.slice(0, 8)}
      renderItem={(item) => (
        <List.Item
          className={item.node_id === selectedNodeId ? `${panelStyles.diffItem} ${panelStyles.diffItemActive}` : panelStyles.diffItem}
          actions={[
            <Tooltip key="focus" title="在右侧图中定位">
              <Button size="small" aria-label={`定位 ${item.node_id ?? '节点'}`} icon={<AimOutlined />} onClick={() => onFocus(item.node_id)} />
            </Tooltip>
          ]}
        >
          <List.Item.Meta
            title={item.name || item.node_id || '未命名节点'}
            description={
              <span>
                {item.node_id} · {item.type ?? 'unknown'}
                {item.field_changes?.length ? ` · 字段 ${item.field_changes.map((change) => change.field).filter(Boolean).slice(0, 3).join('、')}` : ''}
              </span>
            }
          />
        </List.Item>
      )}
    />
  );
}

function CopyBlockBoundaryPreview({ preview }: { preview: Record<string, unknown> }) {
  const entryPorts = Array.isArray(preview.entry_ports) ? preview.entry_ports : [];
  const exitPorts = Array.isArray(preview.exit_ports) ? preview.exit_ports : [];
  return (
    <div className={panelStyles.section}>
      <div className={panelStyles.sectionHeading}>
        <Text strong>
          <BranchesOutlined /> copy_block 边界预览
        </Text>
        <Tag color="warning">外部线默认不接入</Tag>
      </div>
      <Alert
        className={panelStyles.inlineAlert}
        type="warning"
        showIcon
        message="入口、出口和外部接线草案仅用于确认，不会自动应用。"
      />
      <div className={panelStyles.boundaryGrid}>
        <BoundaryList title="入口端口" items={entryPorts} />
        <BoundaryList title="出口端口" items={exitPorts} />
      </div>
    </div>
  );
}

function BoundaryList({ title, items }: { title: string; items: unknown[] }) {
  return (
    <div className={panelStyles.boundaryColumn}>
      <Text strong>{title}</Text>
      {items.length ? (
        <List
          size="small"
          dataSource={items.slice(0, 4)}
          renderItem={(item) => {
            const value = item && typeof item === 'object' ? (item as Record<string, unknown>) : {};
            return (
              <List.Item className={panelStyles.boundaryItem}>
                <InfoCircleOutlined />
                <Text type="secondary">{boundaryText(value)}</Text>
              </List.Item>
            );
          }}
        />
      ) : (
        <Text type="secondary">暂无边界端口</Text>
      )}
    </div>
  );
}

function toFlowNode(node: ProjectFlowNode, selectedNodeId: string | undefined, affectedNodeIds: string[]): EngineeringFlowNode {
  return {
    id: node.id,
    type: 'engineeringNode',
    position: node.position,
    data: {
      ...node.data,
      affected: affectedNodeIds.includes(node.id)
    },
    selected: node.id === selectedNodeId
  };
}

function toFlowEdge(edge: ProjectFlowEdge): Edge {
  return {
    ...edge,
    animated: false,
    style: { stroke: '#8aa0aa', strokeWidth: 1.4 }
  };
}

type ProjectFlowNode = NonNullable<Awaited<ReturnType<typeof getProjectFlow>>['flow']['nodes'][number]>;
type ProjectFlowEdge = NonNullable<Awaited<ReturnType<typeof getProjectFlow>>['flow']['edges'][number]>;

function findCopyBlockBoundaryPreview(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return undefined;
  }
  const changes = (value as { changes?: unknown }).changes;
  if (!Array.isArray(changes)) {
    return undefined;
  }
  for (const change of changes) {
    if (change && typeof change === 'object' && !Array.isArray(change) && (change as Record<string, unknown>).op === 'copy_block') {
      const preview = (change as Record<string, unknown>).boundary_preview;
      if (preview && typeof preview === 'object' && !Array.isArray(preview)) {
        return preview as Record<string, unknown>;
      }
    }
  }
  return undefined;
}

function boundaryText(item: Record<string, unknown>): string {
  const source = nodeRefText(item.source);
  const target = nodeRefText(item.target);
  const sourceOutput = item.source_output ?? '-';
  const targetInput = item.target_input ?? '-';
  return `${source} out-${String(sourceOutput)} -> ${target} in-${String(targetInput)}`;
}

function nodeRefText(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return '未知节点';
  }
  const node = value as Record<string, unknown>;
  return String(node.name ?? node.label ?? node.id ?? '未知节点');
}

function roleLabel(role: string): string {
  const labels: Record<string, string> = {
    input: '输入',
    output: '输出',
    communication: '通讯',
    compare: '比较',
    pid: 'PID',
    logic: '逻辑',
    protection: '保护',
    note: '备注',
    unknown: '未知'
  };
  return labels[role] ?? labels.unknown;
}

function nodeColor(role: string): string {
  const colors: Record<string, string> = {
    input: '#0098D1',
    output: '#A55C00',
    communication: '#006A94',
    compare: '#6C7A00',
    pid: '#20704A',
    logic: '#66737C',
    protection: '#A53232',
    note: '#C97A34',
    unknown: '#8AA0AA'
  };
  return colors[role] ?? colors.unknown;
}
