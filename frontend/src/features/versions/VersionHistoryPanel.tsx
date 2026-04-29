import { Alert, Button, Descriptions, Empty, List, Select, Space, Tag, Tooltip, Typography, message as antMessage } from 'antd';
import { BranchesOutlined, CheckCircleOutlined, EyeOutlined, RollbackOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { formatApiError, getProjectDiff, listProjectVersions, rollbackProject } from '../../api/client';
import type { NodeDiff, ProjectVersion } from '../../api/types';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text } = Typography;

export function VersionHistoryPanel() {
  const state = useWorkbenchStore((store) => store.state);
  const inspectedVersionId = useWorkbenchStore((store) => store.inspectedVersionId);
  const inspectedFromVersionId = useWorkbenchStore((store) => store.inspectedFromVersionId);
  const inspectVersion = useWorkbenchStore((store) => store.inspectVersion);
  const patchState = useWorkbenchStore((store) => store.patchState);
  const projectId = state?.project_id ?? state?.current_project_id ?? undefined;
  const currentVersionId = state?.version_id ?? state?.current_project_version_id ?? undefined;
  const [fromVersionId, setFromVersionId] = useState<string | undefined>();
  const [toVersionId, setToVersionId] = useState<string | undefined>();
  const [rollbackTarget, setRollbackTarget] = useState<ProjectVersion | undefined>();
  const [messageApi, holder] = antMessage.useMessage();
  const queryClient = useQueryClient();

  const versionsQuery = useQuery({
    queryKey: ['project-versions', projectId],
    queryFn: () => listProjectVersions(projectId!),
    enabled: Boolean(projectId),
    retry: false
  });

  const versions = versionsQuery.data?.versions ?? [];
  const newestFirst = useMemo(() => [...versions].reverse(), [versions]);
  const activeToVersionId = inspectedVersionId ?? toVersionId ?? currentVersionId;
  const activeFromVersionId = inspectedFromVersionId ?? fromVersionId;
  const diffQuery = useQuery({
    queryKey: ['project-diff-preview', projectId, activeToVersionId, activeFromVersionId],
    queryFn: () => getProjectDiff(projectId!, activeToVersionId, activeFromVersionId),
    enabled: Boolean(projectId && activeToVersionId && activeFromVersionId),
    retry: false
  });

  const rollbackMutation = useMutation({
    mutationFn: (targetVersionId: string) => rollbackProject(projectId!, targetVersionId),
    onSuccess: (data) => {
      if (data.state) {
        patchState(data.state);
      }
      setRollbackTarget(undefined);
      inspectVersion(undefined, undefined);
      void queryClient.invalidateQueries({ queryKey: ['project-versions', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['project-diff'] });
      void queryClient.invalidateQueries({ queryKey: ['project-flow'] });
      messageApi.success('已回滚到目标版本。');
    },
    onError: (error) => messageApi.error(formatApiError(error))
  });

  const compareOptions = versions.map((version) => ({
    label: shortVersion(version.version_id),
    value: version.version_id
  }));

  if (!projectId) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="创建工程版本后显示版本历史" />;
  }

  return (
    <div className={panelStyles.versionPanel}>
      {holder}
      <div className={panelStyles.versionCompareBar}>
        <Space wrap>
          <Text strong>版本对比</Text>
          <Select
            size="small"
            placeholder="起始版本"
            value={fromVersionId}
            options={compareOptions}
            popupMatchSelectWidth={false}
            onChange={setFromVersionId}
            style={{ minWidth: 150 }}
          />
          <Select
            size="small"
            placeholder="目标版本"
            value={toVersionId}
            options={compareOptions}
            popupMatchSelectWidth={false}
            onChange={setToVersionId}
            style={{ minWidth: 150 }}
          />
          <Button
            size="small"
            icon={<BranchesOutlined />}
            disabled={!fromVersionId || !toVersionId || fromVersionId === toVersionId}
            onClick={() => inspectVersion(toVersionId, fromVersionId)}
          >
            查看 diff
          </Button>
          {inspectedVersionId ? (
            <Button size="small" onClick={() => inspectVersion(undefined, undefined)}>
              回到当前版本
            </Button>
          ) : null}
        </Space>
        <DiffSummary diff={diffQuery.data?.diff} error={diffQuery.error} isError={diffQuery.isError} />
      </div>

      {rollbackTarget ? (
        <Alert
          type="warning"
          showIcon
          message={`准备回滚到 ${shortVersion(rollbackTarget.version_id)}`}
          description={
            <Space direction="vertical" size={8}>
              <Text>后端会重新校验目标版本；成功后只切换当前版本指针，不删除历史版本。</Text>
              <Space wrap>
                <Button
                  danger
                  type="primary"
                  size="small"
                  icon={<RollbackOutlined />}
                  loading={rollbackMutation.isPending}
                  onClick={() => rollbackMutation.mutate(rollbackTarget.version_id)}
                >
                  确认回滚
                </Button>
                <Button size="small" onClick={() => setRollbackTarget(undefined)}>
                  取消
                </Button>
              </Space>
            </Space>
          }
        />
      ) : null}

      {versionsQuery.isError ? <Alert type="warning" showIcon message={formatApiError(versionsQuery.error)} /> : null}
      {newestFirst.length ? (
        <List
          className={panelStyles.versionList}
          dataSource={newestFirst}
          renderItem={(version) => (
            <VersionItem
              version={version}
              currentVersionId={currentVersionId}
              inspectedVersionId={inspectedVersionId}
              onInspect={(target, from) => inspectVersion(target, from)}
              onRollback={(target) => setRollbackTarget(target)}
              rollbackLoading={rollbackMutation.isPending && rollbackMutation.variables === version.version_id}
            />
          )}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无版本记录" />
      )}
    </div>
  );
}

function VersionItem({
  version,
  currentVersionId,
  inspectedVersionId,
  onInspect,
  onRollback,
  rollbackLoading
}: {
  version: ProjectVersion;
  currentVersionId?: string;
  inspectedVersionId?: string;
  onInspect: (toVersionId: string, fromVersionId?: string) => void;
  onRollback: (target: ProjectVersion) => void;
  rollbackLoading: boolean;
}) {
  const isCurrent = version.version_id === currentVersionId;
  const isInspected = version.version_id === inspectedVersionId;
  const validation = version.validation_summary;
  const parentVersionId = version.parent_version_id ?? undefined;
  return (
    <List.Item
      className={`${panelStyles.versionItem} ${isCurrent ? panelStyles.versionItemCurrent : ''} ${isInspected ? panelStyles.versionItemInspected : ''}`}
      actions={[
        <Tooltip key="inspect" title={parentVersionId ? '查看该版本相对父版本的 diff' : '初始版本没有父版本'}>
          <Button size="small" icon={<EyeOutlined />} disabled={!parentVersionId} onClick={() => onInspect(version.version_id, parentVersionId)}>
            查看
          </Button>
        </Tooltip>,
        <Button key="rollback" size="small" danger icon={<RollbackOutlined />} disabled={isCurrent} loading={rollbackLoading} onClick={() => onRollback(version)}>
          回滚
        </Button>
      ]}
    >
      <List.Item.Meta
        title={
          <Space size={6} wrap>
            <Text strong>{shortVersion(version.version_id)}</Text>
            {isCurrent ? <Tag color="success">当前</Tag> : null}
            {isInspected ? <Tag color="warning">查看中</Tag> : null}
            <ValidationTag valid={validation?.valid} exportable={version.exportable} />
          </Space>
        }
        description={
          <div className={panelStyles.versionMeta}>
            <Text type="secondary">父版本：{parentVersionId ? shortVersion(parentVersionId) : '无'}</Text>
            <Text type="secondary">创建：{formatDate(version.created_at)}</Text>
            <Text type="secondary">摘要：{versionSummary(version)}</Text>
            <Descriptions size="small" column={3} className={panelStyles.versionStats}>
              <Descriptions.Item label="节点">{String(version.summary?.node_count ?? '-')}</Descriptions.Item>
              <Descriptions.Item label="页面">{String(version.summary?.tab_count ?? '-')}</Descriptions.Item>
              <Descriptions.Item label="风险">{String(version.risk_level ?? version.patch_summary?.risk_level ?? '-')}</Descriptions.Item>
            </Descriptions>
          </div>
        }
      />
    </List.Item>
  );
}

function DiffSummary({ diff, error, isError }: { diff?: NodeDiff; error: unknown; isError: boolean }) {
  if (isError) {
    return <Text type="secondary">{formatApiError(error)}</Text>;
  }
  if (!diff?.summary) {
    return <Text type="secondary">选择两个版本后查看摘要</Text>;
  }
  return (
    <Space size={6} wrap>
      <Tag color="blue">新增 {diff.summary.added_count ?? 0}</Tag>
      <Tag color="orange">删除 {diff.summary.removed_count ?? 0}</Tag>
      <Tag color="geekblue">修改 {diff.summary.modified_count ?? 0}</Tag>
      <Text type="secondary">右侧图将定位到目标版本摘要。</Text>
    </Space>
  );
}

function ValidationTag({ valid, exportable }: { valid?: boolean; exportable?: boolean }) {
  if (valid === true || exportable === true) {
    return (
      <Tag color="success">
        <CheckCircleOutlined /> 可导出
      </Tag>
    );
  }
  if (valid === false) {
    return <Tag color="error">校验失败</Tag>;
  }
  return <Tag>待校验</Tag>;
}

function shortVersion(versionId?: string): string {
  if (!versionId) {
    return '-';
  }
  return versionId.length > 18 ? `${versionId.slice(0, 12)}...${versionId.slice(-4)}` : versionId;
}

function formatDate(value?: string): string {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date);
}

function versionSummary(version: ProjectVersion): string {
  if (version.note) {
    return version.note;
  }
  const patchSummary = version.patch_summary;
  const changed = patchSummary?.changed_node_count ?? patchSummary?.affected_node_count;
  if (changed !== undefined) {
    return `影响 ${String(changed)} 个节点`;
  }
  if (version.parent_version_id) {
    return '结构化补丁生成的子版本';
  }
  return '模板创建的初始版本';
}
