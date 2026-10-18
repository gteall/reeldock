import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, App, Button, Modal, Table, Typography } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { api, explain } from './api'
import type { Film, Task } from './api'
import { newTaskKey } from './taskKey'

interface DeletionPreview {
  preview_id: string
  video_files_preserved: number
  files: { path: string; size: number | null }[]
}

export function DeleteMetadataButton({
  film,
  busy,
  refresh,
}: {
  film: Film
  busy: boolean
  refresh: () => void
}) {
  const { message } = App.useApp()
  const [preview, setPreview] = useState<DeletionPreview | null>(null)
  const [taskId, setTaskId] = useState<string | null>(null)
  const reported = useRef<string | null>(null)
  const inspect = useMutation({
    mutationFn: () =>
      api<DeletionPreview>(`/packages/${film.id}/metadata/preview`, { method: 'POST' }),
    onSuccess: setPreview,
    onError: (error) => message.error(error.message),
  })
  const remove = useMutation({
    mutationFn: () =>
      api<{ task_id: string }>(`/packages/${film.id}/metadata/delete`, {
        method: 'POST',
        body: JSON.stringify({ preview_id: preview!.preview_id, idempotency_key: newTaskKey() }),
      }),
    onSuccess: (result) => {
      setTaskId(result.task_id)
      setPreview(null)
      message.success('删除元数据任务已排队')
      refresh()
    },
    onError: (error) => message.error(error.message),
  })
  const task = useQuery({
    queryKey: ['tasks', 'metadata-reset', taskId],
    queryFn: () => api<Task>(`/tasks/${taskId}`),
    enabled: !!taskId,
    refetchInterval: (query) =>
      ['completed', 'failed', 'blocked', 'paused'].includes(query.state.data?.status ?? '')
        ? false
        : 1000,
  })
  useEffect(() => {
    if (!task.data || !['completed', 'failed', 'blocked'].includes(task.data.status)) return
    const key = task.data.id + ':' + task.data.status
    if (reported.current === key) return
    reported.current = key
    refresh()
    if (task.data.status === 'completed') message.success('元数据已删除，可以重新刮削')
    else message.error(explain(task.data.error_code))
  }, [task.data, refresh, message])
  const working =
    !!taskId && !['completed', 'failed', 'blocked', 'paused'].includes(task.data?.status ?? '')
  const disabled = busy || working || ['missing', 'superseded'].includes(film.scan_status)
  return (
    <>
      <Button
        danger
        icon={<DeleteOutlined />}
        disabled={disabled}
        loading={inspect.isPending || working}
        onClick={() => inspect.mutate()}
      >
        删除元数据
      </Button>
      <Modal
        title={`删除元数据 · ${film.title}`}
        open={!!preview}
        width={720}
        okText="删除所列元数据"
        cancelText="取消"
        okButtonProps={{ danger: true, disabled }}
        confirmLoading={remove.isPending}
        onOk={() => remove.mutate()}
        onCancel={() => !remove.isPending && setPreview(null)}
      >
        <Alert
          type="warning"
          showIcon
          title="删除后可重新刮削，所列文件将永久删除。"
          description={`保留 ${preview?.video_files_preserved ?? 0} 个视频文件。清除刮削记录和下方 NFO、图片、外挂字幕、内置字幕标志及清单。`}
        />
        <Typography.Paragraph style={{ marginTop: 16 }}>
          共 {preview?.files.length ?? 0} 个元数据文件，请核对后删除。
        </Typography.Paragraph>
        <Table
          size="small"
          rowKey="path"
          dataSource={preview?.files ?? []}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          scroll={{ x: 500 }}
          columns={[
            { title: '将删除的文件', dataIndex: 'path' },
            {
              title: '大小',
              width: 100,
              render: (_, item) =>
                item.size === null ? '未知' : `${(item.size / 1024).toFixed(1)} KB`,
            },
          ]}
        />
      </Modal>
    </>
  )
}
