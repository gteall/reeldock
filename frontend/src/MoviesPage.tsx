import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import { FileSearchOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, explain } from './api'
import type { Asset, Film } from './api'

const names: Record<string, string> = {
  not_selected: '未纳入本次要求',
  stable: '已稳定',
  waiting_stable: '等待稳定',
  needs_review: '待确认',
  missing: '源已消失',
  pending: '未开始',
  matched: '已匹配',
  existing_unverified: '已有 · 待校验',
  processing: '处理中',
  remote_verified: '远程已验证',
  source_no_image: '来源无图',
  cached: '已缓存 · 待上传',
  failed: '失败',
  queued: '排队中',
  running: '执行中',
  blocked: '已阻塞',
  completed: '任务完成',
  not_started: '未开始',
  skipped_tmdb_chinese: '依据 TMDB 中文规则跳过',
  default_audio_chinese: '默认中文音轨 · 免下载',
  embedded_zh_hans: '完整内置简体 · 免下载',
  external_verified: '已有外挂已验证',
  downloaded_verified: '补充外挂已上传验证',
  archived: '已归档',
  sending: '移动中 · 待核对',
  retry_safe: '请求被拒绝 · 可重试',
  move_unknown: '移动结果不明',
  partial_failure: '移动部分失败',
  retry_wait: '等待重试',
  paused: '已暂停',
}
function Status({ value }: { value: string }) {
  const color = ['stable', 'matched', 'remote_verified', 'completed'].includes(value)
    ? 'success'
    : ['failed', 'blocked'].includes(value)
      ? 'error'
      : ['waiting_stable', 'needs_review', 'existing_unverified'].includes(value)
        ? 'warning'
        : undefined
  return <Tag color={color}>{names[value] ?? value}</Tag>
}
function assetState(film: Film, kind: string) {
  const rows = film.assets.filter((a) => a.kind === kind && (a.required || kind !== 'actor'))
  if (!rows.length) return <Typography.Text type="secondary">未检查</Typography.Text>
  if (kind === 'actor')
    return (
      <span>
        {rows.filter((a) => a.status === 'remote_verified').length} / {rows.length}
      </span>
    )
  return <Status value={rows[0].status} />
}

function Preview({ asset, close }: { asset: Asset | null; close: () => void }) {
  const nfo = useQuery({
    queryKey: ['nfo', asset?.id, asset?.sha256],
    enabled: ['nfo', 'subtitle', 'manifest'].includes(asset?.kind ?? '') && !!asset?.preview,
    queryFn: async () => {
      const response = await fetch(asset!.preview!, { credentials: 'same-origin' })
      if (!response.ok) throw new Error('预览缓存不可用，请重试资产任务')
      return response.text()
    },
  })
  return (
    <Modal open={!!asset} onCancel={close} footer={null} width={850} title={asset?.path}>
      {['nfo', 'subtitle', 'manifest'].includes(asset?.kind ?? '') ? (
        nfo.isPending ? (
          <Spin />
        ) : nfo.isError ? (
          <Alert type="error" title={String(nfo.error)} />
        ) : (
          <pre className="nfo-preview">{nfo.data}</pre>
        )
      ) : asset?.preview ? (
        <Image src={asset.preview} style={{ maxHeight: '70vh', objectFit: 'contain' }} />
      ) : (
        <Empty description="暂无缓存" />
      )}
    </Modal>
  )
}

export function MoviesPage() {
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [selected, setSelected] = useState<string | null>(null)
  const [preview, setPreview] = useState<Asset | null>(null)
  const [filter, setFilter] = useState('')
  const films = useQuery({
    queryKey: ['movies'],
    queryFn: () => api<{ items: Film[] }>('/movies'),
    refetchInterval: 2000,
  })
  const film = films.data?.items.find((f) => f.id === selected)
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['movies'] })
    queryClient.invalidateQueries({ queryKey: ['tasks'] })
  }
  const error = (e: unknown) => message.error(e instanceof Error ? e.message : '操作失败')
  const scan = useMutation({
    mutationFn: () =>
      api('/scan', {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      }),
    onSuccess: () => {
      refresh()
      message.success('扫描已排队；稳定窗口结束后请再次扫描')
    },
    onError: error,
  })
  const start = useMutation({
    mutationFn: (f: Film) =>
      api('/tasks', {
        method: 'POST',
        body: JSON.stringify({
          kind: 'package_pipeline',
          package_path: f.path,
          idempotency_key: crypto.randomUUID(),
        }),
      }),
    onSuccess: () => {
      refresh()
      message.success('刮削任务已排队；归档需已验证 MOVE 能力')
    },
    onError: error,
  })
  const control = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      api(`/tasks/${id}/${action}`, { method: 'POST' }),
    onSuccess: refresh,
    onError: error,
  })
  const match = useMutation({
    mutationFn: ({ id, tmdbId }: { id: string; tmdbId: string }) =>
      api(`/movies/${id}/match`, { method: 'PUT', body: JSON.stringify({ tmdb_id: tmdbId }) }),
    onSuccess: () => {
      refresh()
      message.success('选择已保存，请启动刮削并按策略归档；现存 NFO 冲突仍会受保护')
    },
    onError: error,
  })
  const busy = (f: Film) =>
    !!f.archive_intent || ['running', 'queued', 'retry_wait'].includes(f.task_status ?? '')
  return (
    <>
      <Alert
        type="info"
        showIcon
        title="P3 · 字幕与安全归档"
        description="基础必需资产全部上传并读回验证后才处理字幕。TMDB 中文作品跳过探测。归档需要最终清单及已确认的存储 MOVE 能力。"
      />
      <Card
        className="form-card"
        title="待刮削电影"
        extra={
          <Button
            icon={<FileSearchOutlined />}
            loading={scan.isPending}
            onClick={() => scan.mutate()}
          >
            扫描目录
          </Button>
        }
      >
        <Input.Search
          placeholder="搜索片名 / 路径"
          allowClear
          onChange={(event) => setFilter(event.target.value)}
          style={{ maxWidth: 360, marginBottom: 16 }}
        />
        {films.isError && <Alert type="error" title={String(films.error)} />}
        <Table<Film>
          rowKey="id"
          loading={films.isPending}
          scroll={{ x: 1150 }}
          dataSource={films.data?.items.filter((f) =>
            `${f.title} ${f.path}`.toLowerCase().includes(filter.toLowerCase()),
          )}
          locale={{
            emptyText: <Empty description="保存连接配置后扫描目录，至少两次扫描用于判断稳定" />,
          }}
          columns={[
            {
              title: '电影',
              key: 'title',
              width: 240,
              render: (_, f) => (
                <>
                  <Button type="link" onClick={() => setSelected(f.id)}>
                    {f.title} {f.year ? `(${f.year})` : ''}
                  </Button>
                  <div>
                    <Typography.Text type="secondary" ellipsis={{ tooltip: f.path }}>
                      {f.path}
                    </Typography.Text>
                  </div>
                </>
              ),
            },
            {
              title: '稳定性',
              dataIndex: 'scan_status',
              render: (value) => <Status value={value} />,
            },
            {
              title: 'TMDB',
              key: 'match',
              render: (_, f) => (
                <>
                  <Status value={f.match_status} />
                  {f.tmdb_id && (
                    <small>
                      #{f.tmdb_id} · {f.original_language ?? '语言未知'}
                    </small>
                  )}
                </>
              ),
            },
            ...['nfo', 'poster', 'fanart', 'actor'].map((kind) => ({
              title: { nfo: 'NFO', poster: '海报', fanart: '背景图', actor: '头像' }[kind],
              key: kind,
              render: (_: unknown, f: Film) => assetState(f, kind),
            })),
            {
              title: '字幕',
              key: 'subtitle',
              render: (_, f) => <Status value={f.subtitle_status} />,
            },
            {
              title: '归档',
              key: 'archive',
              render: (_, f) => <Status value={f.archive_status} />,
            },
            {
              title: '任务',
              key: 'task',
              render: (_, f) => (f.task_status ? <Status value={f.task_status} /> : '未开始'),
            },
            {
              title: '操作',
              key: 'action',
              render: (_, f) => (
                <Button
                  icon={<PlayCircleOutlined />}
                  disabled={f.scan_status !== 'stable' || busy(f)}
                  onClick={() => start.mutate(f)}
                >
                  刮削并按策略归档
                </Button>
              ),
            },
          ]}
        />
      </Card>
      <Drawer
        title={film?.title ?? '电影详情'}
        open={!!film}
        onClose={() => setSelected(null)}
        size={850}
      >
        {film && (
          <Space orientation="vertical" size="large" style={{ width: '100%' }}>
            <Descriptions
              bordered
              column={2}
              items={[
                { key: 'path', label: '远程目录', children: film.path, span: 2 },
                { key: 'stable', label: '稳定性', children: <Status value={film.scan_status} /> },
                { key: 'id', label: 'TMDB ID', children: film.tmdb_id ?? '尚未匹配' },
                {
                  key: 'lang',
                  label: 'original_language',
                  children: film.original_language ?? '未知',
                },
                { key: 'base', label: '基础资产', children: <Status value={film.base_status} /> },
                {
                  key: 'audio',
                  label: '实际默认音轨',
                  children:
                    film.probe_status !== 'probed'
                      ? '未探测'
                      : film.probe_evidence.audio
                        ? `${film.probe_evidence.audio.language}（${film.probe_evidence.audio.selection === 'explicit_default' ? '默认标记' : '首音轨推断'}）`
                        : '已探测 · 默认音轨待确认',
                },
                { key: 'sub', label: '字幕', children: <Status value={film.subtitle_status} /> },
                { key: 'subreason', label: '字幕原因', children: explain(film.subtitle_reason) },
                { key: 'archive', label: '归档', children: <Status value={film.archive_status} /> },
                {
                  key: 'capability',
                  label: 'MOVE 能力',
                  children: film.archive_enabled
                    ? '管理员已确认当前范围'
                    : '未验证 · 停留在刮削完成',
                },
              ]}
            />
            {film.error_code && <Alert type="warning" showIcon title={explain(film.error_code)} />}
            <Space wrap>
              <Button
                type="primary"
                disabled={film.scan_status !== 'stable' || busy(film)}
                onClick={() => start.mutate(film)}
              >
                启动刮削并按策略归档
              </Button>
              {film.task_id &&
                ['failed', 'blocked', 'retry_wait'].includes(film.task_status ?? '') && (
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={() => control.mutate({ id: film.task_id!, action: 'retry' })}
                  >
                    重试失败项
                  </Button>
                )}
              {film.task_id &&
                ['queued', 'running', 'retry_wait'].includes(film.task_status ?? '') && (
                  <Button onClick={() => control.mutate({ id: film.task_id!, action: 'pause' })}>
                    暂停
                  </Button>
                )}
              {film.task_id && film.task_status === 'paused' && (
                <Button onClick={() => control.mutate({ id: film.task_id!, action: 'resume' })}>
                  继续
                </Button>
              )}
            </Space>
            {film.probe_status === 'probed' && (
              <Card title="实际探测证据">
                <Typography.Paragraph>
                  片长 {film.probe_evidence.probe?.duration ?? '未知'} 秒 · 探测读取{' '}
                  {film.probe_evidence.probe?.bytes_requested ?? '未记录'} 字节
                </Typography.Paragraph>
                <Table
                  size="small"
                  rowKey="index"
                  pagination={false}
                  dataSource={film.probe_evidence.probe?.streams ?? []}
                  columns={[
                    { title: '轨道', dataIndex: 'index' },
                    { title: '类型', dataIndex: 'type' },
                    { title: '编码', dataIndex: 'codec' },
                    { title: '语言标签', dataIndex: 'language' },
                    { title: '标题', dataIndex: 'title' },
                    {
                      title: '默认 / forced',
                      render: (_, stream) =>
                        `${stream.default ? '默认' : '—'} / ${stream.forced ? 'forced' : '—'}`,
                    },
                  ]}
                />
              </Card>
            )}
            <Card title="匹配与人工指定">
              <Form
                layout="inline"
                onFinish={(values: { tmdbId: number }) =>
                  match.mutate({ id: film.id, tmdbId: String(values.tmdbId) })
                }
              >
                <Form.Item name="tmdbId" rules={[{ required: true }]}>
                  <InputNumber
                    min={1}
                    precision={0}
                    placeholder="电影 TMDB ID"
                    style={{ width: 200 }}
                  />
                </Form.Item>
                <Button htmlType="submit" loading={match.isPending} disabled={busy(film)}>
                  保存 ID
                </Button>
              </Form>
              <Table
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={film.candidates}
                style={{ marginTop: 16 }}
                columns={[
                  { title: '候选', dataIndex: 'title' },
                  { title: '年份', dataIndex: 'year' },
                  {
                    title: '置信度',
                    dataIndex: 'confidence',
                    render: (value: number) => `${Math.round(value * 100)}%`,
                  },
                  {
                    title: '选择',
                    render: (_, c) => (
                      <Button
                        size="small"
                        disabled={busy(film)}
                        onClick={() => match.mutate({ id: film.id, tmdbId: c.id })}
                      >
                        #{c.id}
                      </Button>
                    ),
                  },
                ]}
              />
            </Card>
            {film.archive_intent && (
              <Alert
                type="info"
                title={`归档意图 ${film.archive_intent.id}`}
                description={
                  <>
                    <span>{explain(film.archive_intent.error_code)}</span>
                    {film.archive_intent.failed_paths?.length ? (
                      <pre>{film.archive_intent.failed_paths.join('\n')}</pre>
                    ) : null}
                  </>
                }
              />
            )}
            <Card title="资产与最终清单 · 上传后完整读回验证">
              <Table<Asset>
                size="small"
                rowKey="id"
                pagination={false}
                dataSource={film.assets}
                columns={[
                  { title: '路径', dataIndex: 'path' },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    render: (value) => <Status value={value} />,
                  },
                  { title: '错误', dataIndex: 'error_code', render: (value) => explain(value) },
                  {
                    title: '预览',
                    render: (_, a) => (
                      <Button size="small" disabled={!a.preview} onClick={() => setPreview(a)}>
                        查看
                      </Button>
                    ),
                  },
                ]}
              />
            </Card>
            <Card
              title={`演员 · ${film.cast.filter((p) => p.selected).length} / ${film.cast.length} 纳入头像要求`}
            >
              <div className="actors-grid">
                {film.cast.map((person) => {
                  const asset = film.assets.find((a) => a.path === person.path)
                  return (
                    <div key={person.id} className="actor-card">
                      {asset?.preview ? (
                        <Image
                          src={asset.preview}
                          width={75}
                          height={100}
                          style={{ objectFit: 'cover' }}
                        />
                      ) : (
                        <div className="actor-placeholder">无头像</div>
                      )}
                      <strong>{person.name}</strong>
                      <small>{person.role}</small>
                      <Status
                        value={
                          asset?.status ??
                          (!person.selected
                            ? 'not_selected'
                            : person.source_has_image
                              ? 'pending'
                              : 'source_no_image')
                        }
                      />
                    </div>
                  )
                })}
              </div>
            </Card>
          </Space>
        )}
      </Drawer>
      <Preview asset={preview} close={() => setPreview(null)} />
    </>
  )
}
