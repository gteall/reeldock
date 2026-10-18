import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, App, Button, Card, Empty, Input, Select, Space, Table, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { api, explain } from './api'
import type { Film, Task } from './api'
import { newTaskKey } from './taskKey'

interface Candidate {
  id: string
  title: string
  release: string
  language: string
  format: string
}
interface SearchResult {
  search_id: string
  keyword: string
  items: Candidate[]
}
interface SubtitleFile {
  index: number
  name: string
  format: string
}

export function SubtitleSearch({
  film,
  busy,
  refresh,
}: {
  film: Film
  busy: boolean
  refresh: () => void
}) {
  const { message } = App.useApp()
  const [mediaId, setMediaId] = useState(
    () =>
      film.media.find((item) => ['failed', 'needs_review'].includes(item.subtitle_status))?.id ??
      film.media[0]?.id ??
      '',
  )
  const media = film.media.find((item) => item.id === mediaId)
  const [keyword, setKeyword] = useState('')
  const [result, setResult] = useState<SearchResult | null>(null)
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [files, setFiles] = useState<SubtitleFile[]>([])
  const [fileIndex, setFileIndex] = useState<number | undefined>()
  const [taskId, setTaskId] = useState<string | null>(null)
  const reported = useRef<string | null>(null)
  const requestSequence = useRef(0)
  useEffect(() => {
    requestSequence.current += 1
    setKeyword(
      media?.subtitle_search_keyword ??
        media?.path
          .split('/')
          .pop()
          ?.replace(/\.[^.]+$/, '') ??
        '',
    )
    setResult(null)
    setCandidate(null)
    setFiles([])
    setFileIndex(undefined)
    setTaskId(null)
  }, [film.id, film.context_version, media?.id, media?.path, media?.subtitle_search_keyword])
  const error = (value: unknown) =>
    message.error(value instanceof Error ? value.message : '操作失败')
  const search = useMutation({
    mutationFn: async () => {
      const sequence = ++requestSequence.current
      const data = await api<SearchResult>(`/packages/${film.id}/subtitles/search`, {
        method: 'POST',
        body: JSON.stringify({
          media_id: mediaId,
          keyword: keyword.trim(),
          expected_context_version: film.context_version,
        }),
      })
      return { sequence, data }
    },
    onMutate: () => {
      setResult(null)
      setCandidate(null)
      setFiles([])
      setFileIndex(undefined)
    },
    onSuccess: ({ sequence, data }) => {
      if (sequence === requestSequence.current) setResult(data)
    },
    onError: error,
  })
  const loadFiles = useMutation({
    mutationFn: async (item: Candidate) => {
      const sequence = ++requestSequence.current
      const data = await api<{ files: SubtitleFile[] }>(`/packages/${film.id}/subtitles/files`, {
        method: 'POST',
        body: JSON.stringify({ search_id: result!.search_id, candidate_id: item.id }),
      })
      return { sequence, item, data }
    },
    onMutate: () => {
      setCandidate(null)
      setFiles([])
      setFileIndex(undefined)
    },
    onSuccess: ({ sequence, item, data }) => {
      if (sequence !== requestSequence.current) return
      setCandidate(item)
      setFiles(data.files)
      setFileIndex(data.files.length === 1 ? data.files[0].index : undefined)
    },
    onError: error,
  })
  const apply = useMutation({
    mutationFn: () =>
      api<{ task_id: string }>(`/packages/${film.id}/subtitles/apply`, {
        method: 'POST',
        body: JSON.stringify({
          search_id: result!.search_id,
          candidate_id: candidate!.id,
          file_index: fileIndex,
          idempotency_key: newTaskKey(),
        }),
      }),
    onSuccess: (data) => {
      setTaskId(data.task_id)
      refresh()
      message.success('字幕应用任务已排队')
    },
    onError: error,
  })
  const task = useQuery({
    queryKey: ['tasks', 'subtitle-apply', taskId],
    queryFn: () => api<Task>(`/tasks/${taskId}`),
    enabled: !!taskId,
    refetchInterval: (query) =>
      ['completed', 'blocked', 'failed', 'paused'].includes(query.state.data?.status ?? '')
        ? false
        : 1000,
  })
  useEffect(() => {
    if (!task.data || !['completed', 'blocked', 'failed'].includes(task.data.status)) return
    const key = task.data.id + ':' + task.data.status
    if (reported.current === key) return
    reported.current = key
    refresh()
    if (task.data.status === 'completed') message.success('所选字幕已应用')
  }, [task.data, refresh, message])
  const working =
    !!taskId && !['completed', 'blocked', 'failed', 'paused'].includes(task.data?.status ?? '')
  const disabled = busy || working || !media || film.scan_status !== 'stable'
  return (
    <Card title="手动搜索字幕">
      <Typography.Paragraph type="secondary">
        默认使用自动匹配时的视频文件名，可修改为片名、年份或发布版本。选择结果中的字幕文件后应用到原媒体目录。
      </Typography.Paragraph>
      {!film.manual_subtitles_enabled && (
        <Alert
          type="info"
          showIcon
          title="请先在设置中填写 ASSRT Token"
          action={<Button href="#/settings">前往设置</Button>}
          style={{ marginBottom: 16 }}
        />
      )}
      {film.kind === 'tv' && (
        <Select
          aria-label="字幕目标剧集"
          value={mediaId}
          onChange={setMediaId}
          disabled={working || apply.isPending}
          style={{ width: '100%', marginBottom: 12 }}
          options={film.media.map((item) => ({
            value: item.id,
            label: `${item.season !== null ? `S${String(item.season).padStart(2, '0')}E${String(item.episode ?? '?').padStart(2, '0')} · ` : ''}${item.path.split('/').pop()}`,
          }))}
        />
      )}
      <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
        <Input
          aria-label="字幕搜索关键词"
          value={keyword}
          maxLength={512}
          placeholder="片名 / 年份 / 视频文件名"
          disabled={working || apply.isPending}
          onChange={(event) => {
            requestSequence.current += 1
            setKeyword(event.target.value)
            setResult(null)
            setCandidate(null)
            setFiles([])
            setFileIndex(undefined)
          }}
          onPressEnter={() =>
            keyword.trim().length >= 3 &&
            !disabled &&
            film.manual_subtitles_enabled &&
            !search.isPending &&
            search.mutate()
          }
        />
        <Button
          icon={<SearchOutlined />}
          loading={search.isPending}
          disabled={
            disabled ||
            !film.manual_subtitles_enabled ||
            keyword.trim().length < 3 ||
            loadFiles.isPending ||
            apply.isPending
          }
          onClick={() => search.mutate()}
        >
          搜索字幕
        </Button>
      </Space.Compact>
      {result && (
        <Table<Candidate>
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={result.items}
          scroll={{ x: 650 }}
          locale={{ emptyText: <Empty description="没有找到匹配字幕，请修改关键词后重试" /> }}
          columns={[
            {
              title: '字幕 / 发布版本',
              key: 'title',
              render: (_, item) => (
                <>
                  <strong>{item.title}</strong>
                  {item.release && (
                    <div>
                      <Typography.Text type="secondary">{item.release}</Typography.Text>
                    </div>
                  )}
                </>
              ),
            },
            { title: '语言', dataIndex: 'language', width: 100 },
            { title: '格式', dataIndex: 'format', width: 100 },
            {
              title: '选择',
              key: 'choose',
              width: 85,
              render: (_, item) => (
                <Button
                  size="small"
                  type={candidate?.id === item.id ? 'primary' : 'default'}
                  disabled={disabled || apply.isPending}
                  loading={loadFiles.isPending && loadFiles.variables?.id === item.id}
                  onClick={() => loadFiles.mutate(item)}
                >
                  选择
                </Button>
              ),
            },
          ]}
        />
      )}
      {candidate && (
        <Space orientation="vertical" style={{ width: '100%', marginTop: 16 }}>
          <Typography.Text>已选：{candidate.title}</Typography.Text>
          {files.length ? (
            <>
              <Select
                aria-label="选择字幕文件"
                placeholder="请选择要应用的字幕文件"
                value={fileIndex}
                onChange={setFileIndex}
                disabled={disabled || apply.isPending}
                style={{ width: '100%' }}
                options={files.map((file) => ({ value: file.index, label: file.name }))}
              />
              <Button
                type="primary"
                loading={apply.isPending || working}
                disabled={disabled || fileIndex === undefined}
                onClick={() => apply.mutate()}
              >
                应用所选字幕
              </Button>
            </>
          ) : (
            <Alert type="warning" title="此结果没有可应用的 SRT / ASS / SSA 文件，请选择其他结果" />
          )}
        </Space>
      )}
      {task.data && (
        <Alert
          style={{ marginTop: 16 }}
          type={
            task.data.status === 'completed'
              ? 'success'
              : ['blocked', 'failed'].includes(task.data.status)
                ? 'error'
                : 'info'
          }
          title={
            task.data.status === 'completed'
              ? '所选字幕已应用'
              : ['blocked', 'failed'].includes(task.data.status)
                ? '字幕应用失败'
                : '字幕正在应用，可在任务中心查看进度'
          }
          description={task.data.error_code ? explain(task.data.error_code) : undefined}
        />
      )}
      <Typography.Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
        字幕服务由{' '}
        <a href="https://assrt.net" target="_blank" rel="noreferrer">
          assrt.net
        </a>{' '}
        提供。
      </Typography.Paragraph>
    </Card>
  )
}
