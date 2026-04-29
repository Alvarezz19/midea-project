import { Result } from 'antd';
import { Link, useParams } from 'react-router-dom';

export function TracePlaceholder() {
  const { traceId } = useParams();
  return (
    <Result
      status="info"
      title="Trace 详情页骨架"
      subTitle={`trace_id: ${traceId ?? '-'}`}
      extra={<Link to="/">返回工作台</Link>}
    />
  );
}
