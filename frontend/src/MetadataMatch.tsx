import { useMutation } from '@tanstack/react-query'
import {
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Space,
  Table,
  Typography,
} from 'antd'
import { api, explain } from './api'
import type { Film } from './api'

const sources: Record<string, string> = {
  directory: '当前目录',
  filename: '视频文件名',
  parent: '上级目录',
  alternate: '中英文名称',
  partial: '标题片段',
  manual: '自定义搜索词',
}
const reasons: Record<string, string> = {
  title_exact: '名称一致',
  title_similar: '名称相似',
  year_conflict: '年份不一致',
  season_year_differs: '剧名匹配，年份可能属于其他季',
  partial_title: '仅标题片段匹配，需确认',
}

export function MetadataMatch({
  film,
  busy,
  refresh,
}: {
  film: Film
  busy: boolean
  refresh: () => void
}) {
  const { message } = App.useApp()
  const match = useMutation({
    mutationFn: (tmdbId: string) =>
      api(`/movies/${film.id}/match`, {
        method: 'PUT',
        body: JSON.stringify({ tmdb_id: tmdbId }),
      }),
    onSuccess: () => {
      refresh()
      message.success('ID 已保存，请启动原地刮削')
    },
    onError: (error) => message.error(error.message),
  })
  const search = useMutation({
    mutationFn: (query: string) =>
      api(`/packages/${film.id}/search-query`, {
        method: 'PUT',
        body: JSON.stringify({ query, expected_context_version: film.context_version }),
      }),
    onSuccess: () => {
      refresh()
      message.success('搜索词已保存，请启动原地刮削重新匹配')
    },
    onError: (error) => message.error(error.message),
  })
  const evidence = film.match_evidence
  return (
    <Card title="匹配与人工指定">
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        {evidence?.parsed_title && (
          <Descriptions
            size="small"
            column={1}
            items={[
              { key: 'name', label: '识别名称', children: evidence.parsed_title },
              { key: 'year', label: '年份线索', children: evidence.year ?? '未识别' },
              ...(evidence.season != null
                ? [{ key: 'season', label: '季号线索', children: evidence.season }]
                : []),
            ]}
          />
        )}
        {!!evidence?.queries?.length && (
          <Table
            size="small"
            rowKey="query"
            pagination={false}
            dataSource={evidence.queries}
            columns={[
              { title: '实际搜索词', dataIndex: 'query' },
              {
                title: '来源',
                dataIndex: 'source',
                render: (value: string) => sources[value] ?? value,
              },
              {
                title: '返回结果',
                render: (_, row) =>
                  row.error_code
                    ? explain(row.error_code)
                    : row.result_count == null
                      ? '搜索中'
                      : `${row.result_count} 条`,
              },
            ]}
          />
        )}
        <Form
          key={`${film.id}:${film.search_query ?? ''}`}
          layout="inline"
          initialValues={{ query: film.search_query ?? '' }}
          onFinish={(values: { query: string }) => search.mutate(values.query)}
        >
          <Form.Item name="query" label="搜索词">
            <Input
              maxLength={200}
              allowClear
              placeholder={evidence?.parsed_title ?? '填写片名，留空恢复自动识别'}
              style={{ width: 260 }}
            />
          </Form.Item>
          <Button htmlType="submit" loading={search.isPending} disabled={busy}>
            保存搜索词
          </Button>
        </Form>
        <Typography.Text type="secondary">
          保存后启动原地刮削。只修改搜索词，不改动文件名；留空恢复自动识别。文件名中的 TMDB ID
          仍优先。
        </Typography.Text>
        <Form
          key={film.id}
          layout="inline"
          onFinish={(values: { tmdbId: number }) => match.mutate(String(values.tmdbId))}
        >
          <Form.Item name="tmdbId" rules={[{ required: true }]}>
            <InputNumber
              min={1}
              precision={0}
              placeholder={film.kind === 'tv' ? '剧 TMDB ID' : '电影 TMDB ID'}
              style={{ width: 200 }}
            />
          </Form.Item>
          <Button htmlType="submit" loading={match.isPending} disabled={busy}>
            保存 ID
          </Button>
        </Form>
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          dataSource={film.candidates}
          locale={{
            emptyText:
              evidence?.outcome === 'no_results'
                ? '没有搜索到候选，可修改上方搜索词'
                : '暂无候选，请先启动刮削匹配',
          }}
          columns={[
            { title: '候选', dataIndex: 'title' },
            { title: '年份', dataIndex: 'year' },
            {
              title: '置信度',
              dataIndex: 'confidence',
              render: (value: number) => `${Math.round(value * 100)}%`,
            },
            {
              title: '匹配依据',
              dataIndex: 'reason',
              render: (value?: string) => (value ? (reasons[value] ?? value) : '—'),
            },
            {
              title: '选择',
              render: (_, row) => (
                <Button size="small" disabled={busy} onClick={() => match.mutate(row.id)}>
                  #{row.id}
                </Button>
              ),
            },
          ]}
        />
      </Space>
    </Card>
  )
}
