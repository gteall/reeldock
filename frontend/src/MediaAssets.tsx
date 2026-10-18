import { useQuery } from '@tanstack/react-query'
import { Alert, Empty, Image, Modal, Spin, Tag } from 'antd'
import type { Asset } from './api'

const names: Record<string, string> = {
  skipped: '已跳过',
  not_selected: '未纳入本次要求',
  not_needed: '不需要',
  mapped: '编号已确认',
  not_applicable: '不适用',
  all_media_verified: '全部媒体字幕策略通过',
  superseded: '已归入剧包',
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
  embedded_zh_hans: '内置简体 · 免下载',
  external_verified: '已有外挂已验证',
  downloaded_verified: '补充外挂已上传验证',
  archived: '历史已归档',
  sending: '移动中 · 待核对',
  retry_safe: '请求被拒绝 · 可重试',
  move_unknown: '移动结果不明',
  partial_failure: '移动部分失败',
  retry_wait: '等待重试',
  paused: '已暂停',
}
export function Status({ value }: { value: string }) {
  const color = ['stable', 'matched', 'remote_verified', 'completed', 'archived'].includes(value)
    ? 'success'
    : ['failed', 'blocked'].includes(value)
      ? 'error'
      : ['waiting_stable', 'needs_review', 'existing_unverified'].includes(value)
        ? 'warning'
        : undefined
  return <Tag color={color}>{names[value] ?? value}</Tag>
}
export function Preview({ asset, close }: { asset: Asset | null; close: () => void }) {
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
