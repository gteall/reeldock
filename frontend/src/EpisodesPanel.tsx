import { useState } from 'react'
import { App, Button, Descriptions, Form, InputNumber, Modal, Space, Table, Typography } from 'antd'
import { api, explain } from './api'
import type { Asset, Film, MediaItem } from './api'

export function ProbeEvidence({
  item,
}: {
  item: Pick<MediaItem, 'probe_status' | 'probe_evidence' | 'subtitle_status' | 'subtitle_reason'>
}) {
  const audio = item.probe_evidence.audio
  return (
    <>
      <Descriptions
        size="small"
        column={2}
        items={[
          {
            key: 'audio',
            label: '实际默认音轨',
            children:
              item.probe_status !== 'probed'
                ? '未探测'
                : audio
                  ? `${audio.language}（${audio.selection === 'explicit_default' ? '默认标记' : '首音轨推断'}）`
                  : '已探测 · 待确认',
          },
          { key: 'reason', label: '字幕依据', children: explain(item.subtitle_reason) },
          {
            key: 'duration',
            label: '实际片长',
            children: item.probe_evidence.probe?.duration
              ? `${item.probe_evidence.probe.duration} 秒`
              : '未探测或未知',
          },
          {
            key: 'bytes',
            label: '探测读取',
            children:
              item.probe_evidence.probe?.bytes_requested ??
              (item.probe_status === 'probed' ? '读取量未记录' : '未探测'),
          },
        ]}
      />
      {item.probe_status === 'probed' && (
        <Table
          size="small"
          pagination={false}
          rowKey="index"
          dataSource={item.probe_evidence.probe?.streams ?? []}
          columns={[
            { title: '轨道', dataIndex: 'index' },
            { title: '类型', dataIndex: 'type' },
            { title: '编码', dataIndex: 'codec' },
            { title: '语言标签', dataIndex: 'language' },
            { title: '标题', dataIndex: 'title' },
            {
              title: '默认 / forced',
              render: (_, s) => `${s.default ? '默认' : '—'} / ${s.forced ? 'forced' : '—'}`,
            },
          ]}
        />
      )}
    </>
  )
}

export function EpisodesPanel({
  film,
  busy,
  refresh,
  preview,
  status,
  retry,
}: {
  film: Film
  busy: boolean
  refresh: () => void
  preview: (asset: Asset) => void
  status: (value: string) => React.ReactNode
  retry: () => void
}) {
  const { message } = App.useApp()
  const [mapping, setMapping] = useState<{ item: MediaItem; version: number } | null>(null)
  const [saving, setSaving] = useState(false)
  const seasons = [...new Set(film.media.map((m) => m.season))].sort(
    (a, b) => (a ?? 100) - (b ?? 100),
  )
  const asset = (item: MediaItem, kind: string) => {
    const found = film.assets.find((a) => a.media_id === item.id && a.kind === kind)
    return found ? (
      <Space>
        {status(found.status)}
        <Button size="small" disabled={!found.preview} onClick={() => preview(found)}>
          预览
        </Button>
      </Space>
    ) : (
      '未检查'
    )
  }
  return (
    <>
      <Typography.Paragraph type="secondary">
        各集继承剧级 original_language：{film.original_language ?? '未知'}
        。当前包内全部基础必需资产验证后才处理字幕，无需等待未来集数。
      </Typography.Paragraph>
      <Table
        size="small"
        pagination={false}
        rowKey="key"
        dataSource={seasons.map((number) => ({
          key: String(number),
          number,
          count: film.media.filter((m) => m.season === number).length,
        }))}
        columns={[
          {
            title: '季',
            render: (_, row) =>
              row.number === null
                ? '编号待确认'
                : row.number === 0
                  ? 'Specials'
                  : `第 ${row.number} 季`,
          },
          { title: '当前文件', dataIndex: 'count' },
          {
            title: '季海报 · 增强项',
            render: (_, row) => {
              const a = film.assets.find(
                (a) => a.kind === 'season_poster' && a.season === row.number,
              )
              return a ? (
                <Space>
                  {status(a.status)}
                  <Button size="small" disabled={!a.preview} onClick={() => preview(a)}>
                    预览
                  </Button>
                </Space>
              ) : (
                '未检查'
              )
            },
          },
        ]}
        expandable={{
          defaultExpandAllRows: true,
          expandedRowRender: (season) => (
            <Table<MediaItem>
              size="small"
              pagination={false}
              rowKey="id"
              dataSource={film.media.filter((m) => m.season === season.number)}
              scroll={{ x: 850 }}
              columns={[
                {
                  title: '集 / 文件',
                  width: 210,
                  render: (_, item) => (
                    <>
                      <strong>
                        E{item.episode ?? '?'} · {item.title}
                      </strong>
                      <div>
                        <Typography.Text type="secondary" ellipsis={{ tooltip: item.path }}>
                          {item.path.slice(film.path.length + 1)}
                        </Typography.Text>
                      </div>
                    </>
                  ),
                },
                {
                  title: '映射 / TMDB',
                  render: (_, item) => (
                    <>
                      {status(item.mapping_status)}
                      <small>{item.tmdb_id ? '#' + item.tmdb_id : '未匹配'}</small>
                    </>
                  ),
                },
                { title: 'NFO', render: (_, item) => asset(item, 'nfo') },
                { title: '缩略图 · 增强', render: (_, item) => asset(item, 'thumb') },
                {
                  title: '字幕',
                  render: (_, item) => (
                    <>
                      {status(item.subtitle_status)}
                      {film.assets
                        .filter((a) => a.media_id === item.id && a.kind === 'subtitle' && a.preview)
                        .map((a) => (
                          <Button key={a.id} size="small" onClick={() => preview(a)}>
                            字幕预览
                          </Button>
                        ))}
                      {item.error_code && <div>{explain(item.error_code)}</div>}
                    </>
                  ),
                },
                {
                  title: '操作',
                  render: (_, item) => (
                    <Space>
                      <Button
                        size="small"
                        disabled={busy}
                        onClick={() => setMapping({ item, version: film.context_version })}
                      >
                        修正编号
                      </Button>
                      {['failed', 'needs_review'].includes(item.subtitle_status) &&
                        film.task_id && (
                          <Button size="small" onClick={retry}>
                            重试失败集
                          </Button>
                        )}
                    </Space>
                  ),
                },
              ]}
              expandable={{ expandedRowRender: (item) => <ProbeEvidence item={item} /> }}
            />
          ),
        }}
      />
      <Modal
        open={!!mapping}
        title="修正季集编号"
        footer={null}
        onCancel={() => setMapping(null)}
        destroyOnHidden
      >
        {mapping && (
          <Form
            key={`${mapping.item.id}:${mapping.version}`}
            layout="vertical"
            initialValues={{ season: mapping.item.season ?? 1, episode: mapping.item.episode ?? 1 }}
            onFinish={async (values: { season: number; episode: number }) => {
              setSaving(true)
              try {
                await api(`/packages/${film.id}/episodes/${mapping.item.id}`, {
                  method: 'PUT',
                  body: JSON.stringify({ ...values, expected_context_version: mapping.version }),
                })
                setMapping(null)
                refresh()
                message.success('编号已保存，请再次扫描确认稳定后启动任务')
              } catch (error) {
                message.error(error instanceof Error ? error.message : '保存失败')
              } finally {
                setSaving(false)
              }
            }}
          >
            <Typography.Paragraph>{mapping.item.path}</Typography.Paragraph>
            <Form.Item name="season" label="季（Specials 为 0）" rules={[{ required: true }]}>
              <InputNumber min={0} max={99} precision={0} />
            </Form.Item>
            <Form.Item name="episode" label="集" rules={[{ required: true }]}>
              <InputNumber min={1} max={999} precision={0} />
            </Form.Item>
            <Button htmlType="submit" type="primary" loading={saving}>
              保存编号
            </Button>
          </Form>
        )}
      </Modal>
    </>
  )
}
