import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { App, Button, Modal, Tag, Typography } from 'antd'
import { api, explain } from './api'
import type { Film, ProbeStream, Task } from './api'
import { newTaskKey } from './taskKey'

export function EmbeddedSubtitleButton({
  film,
  mediaId,
  stream,
  selected,
  manual,
  busy,
  refresh,
}: {
  film: Film
  mediaId: string
  stream: ProbeStream
  selected: boolean
  manual: boolean
  busy: boolean
  refresh: () => void
}) {
  const { message } = App.useApp()
  const [open, setOpen] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const reported = useRef<string | null>(null)
  const confirm = useMutation({
    mutationFn: () =>
      api<{ task_id: string }>(`/packages/${film.id}/subtitles/confirm-embedded`, {
        method: 'POST',
        body: JSON.stringify({
          media_id: mediaId,
          stream_index: stream.index,
          expected_context_version: film.context_version,
          idempotency_key: newTaskKey(),
        }),
      }),
    onSuccess: (result) => {
      setTaskId(result.task_id)
      setOpen(false)
      message.success('内置字幕确认任务已排队')
      refresh()
    },
    onError: (error) => message.error(error.message),
  })
  const task = useQuery({
    queryKey: ['tasks', 'embedded-subtitle', taskId],
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
    if (task.data.status === 'completed') message.success('已确认使用内置字幕，无需下载')
    else message.error(explain(task.data.error_code))
  }, [task.data, refresh, message])
  const working =
    !!taskId && !['completed', 'blocked', 'failed', 'paused'].includes(task.data?.status ?? '')
  const disabled =
    busy || working || film.scan_status !== 'stable' || film.base_status !== 'remote_verified'
  return (
    <>
      {selected ? (
        <Tag color="success">{manual ? '人工确认 · 正在使用' : '正在使用'}</Tag>
      ) : (
        <Button
          size="small"
          disabled={disabled}
          loading={confirm.isPending || working}
          onClick={() => setOpen(true)}
        >
          使用此字幕
        </Button>
      )}
      <Modal
        title="确认使用内置字幕"
        open={open}
        okText="确认使用，免下载"
        cancelText="取消"
        confirmLoading={confirm.isPending}
        okButtonProps={{ disabled }}
        onOk={() => confirm.mutate()}
        onCancel={() => !confirm.isPending && setOpen(false)}
      >
        <Typography.Paragraph>
          轨道 {stream.index} · {stream.title || stream.language || '未标记语言'} · {stream.codec}
        </Typography.Paragraph>
        <Typography.Paragraph>
          请确认此轨道包含完整的简体中文或简英双语对白。确认后使用该内置字幕，无需下载外挂字幕。
        </Typography.Paragraph>
        <Typography.Paragraph type="secondary">
          默认音轨信息保持原有探测结果。源文件变化后需重新确认。
        </Typography.Paragraph>
      </Modal>
    </>
  )
}
