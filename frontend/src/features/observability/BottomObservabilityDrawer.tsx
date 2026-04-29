import { Button, Drawer, Tabs, Typography } from 'antd';
import { useWorkbenchStore } from '../../store/workbenchStore';
import panelStyles from '../../styles/panel.module.css';

const { Text } = Typography;

export function BottomObservabilityDrawer() {
  const open = useWorkbenchStore((store) => store.bottomDrawerOpen);
  const setOpen = useWorkbenchStore((store) => store.setBottomDrawerOpen);
  const threadId = useWorkbenchStore((store) => store.threadId);
  const traceId = useWorkbenchStore((store) => store.traceId);
  const state = useWorkbenchStore((store) => store.state);

  return (
    <>
      <Button className={panelStyles.drawerToggle} onClick={() => setOpen(true)}>
        版本 / Trace / 成本
      </Button>
      <Drawer
        title="开发者观测抽屉"
        placement="bottom"
        height={320}
        open={open}
        onClose={() => setOpen(false)}
        destroyOnHidden={false}
      >
        <Tabs
          items={[
            {
              key: 'versions',
              label: '版本历史',
              children: <Text type="secondary">7.5 接入版本时间线、任意版本对比和回滚确认。</Text>
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
              key: 'cost',
              label: '成本',
              children: <Text type="secondary">7.7 接入 LLM 调用、token、成本和失败率聚合。</Text>
            }
          ]}
        />
      </Drawer>
    </>
  );
}
