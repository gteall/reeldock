export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
  }
}

const messages: Record<string, string> = {
  tmdb_credentials_required: '请先保存 TMDB 凭证',
  tmdb_timeout: 'TMDB 连接超时，请检查网络或代理',
  tmdb_not_found: 'TMDB 电影 ID 不存在，请核对电影与剧集类型',
  tmdb_match_needs_review: '匹配存在歧义，请选择候选或指定电影 ID',
  tmdb_id_conflict: '文件名、现存 NFO 与指定 ID 冲突，已保留原件',
  existing_nfo_id_missing: '已有 NFO 缺少明确 TMDB ID，已保留原件，请人工核对',
  invalid_nfo: '已有 NFO 无法安全解析，已保留原件',
  invalid_image: '图片内容无效，不能解码为受支持的图片',
  upload_verification_failed: '上传后内容不一致，已停止；请核对或移除专用测试坏文件后重试',
  actor_source_missing: '严格模式下演员来源无图，请人工补图后重试',
  actor_filename_collision: '演员头像文件名冲突，请人工核对',
  package_not_stable: '媒体包尚未稳定，请扫描并等待稳定窗口',
  source_changed_scan_again: '源文件发生变化，请重新扫描并等待稳定',
  loose_media_requires_directory: '散放视频需要整理为独立目录',
  multiple_media_requires_review: '同一目录包含多个视频，需要人工整理',
  tv_not_supported_p2: '电视剧将在 P4 支持',
  nested_package_requires_review: '目录层级不明确，需要人工整理',
  package_busy_pause_first: '包正在处理中，请暂停并等待任务释放租约',
  existing_asset_identity_conflict: '现存影坞资产属于另一个 TMDB ID，已保留，请人工核对',
  download_in_progress: '检测到下载临时文件，等待下载完成',
  login_required: '请先登录',
  invalid_credentials: '用户名或密码不正确',
  login_rate_limited: '尝试次数过多，请一分钟后重试',
  request_origin_denied: '请求来源未被允许，请检查服务端 REELDOCK_ALLOWED_ORIGINS',
  csrf_check_failed: '登录状态已变化，请刷新页面后重试',
  configuration_revision_conflict: '配置已被其他页面修改，请刷新后重试',
  configuration_required: '请先保存连接配置',
  configuration_changed_submit_new_task: '配置已变化，请新建任务',
  context_changed_submit_new_task: '媒体包信息已变化，请新建任务',
  idempotency_key_conflict: '任务标识与原请求不一致',
  task_action_not_allowed: '当前状态不支持此操作',
  stage_not_implemented_p1: '当前阶段未启用此处理步骤',
  archive_disabled_p1: '当前阶段未启用归档',
  storage_authentication_failed: 'WebDAV 账号或密码不正确',
  storage_permission_denied: 'WebDAV 目录权限不足',
  storage_not_found: 'WebDAV 目录不存在',
  storage_not_webdav: '此地址不是 WebDAV 端点，请检查 /dav/ 路径',
  storage_not_directory: '配置路径不是目录',
  storage_invalid_multistatus: 'WebDAV 返回了无法安全解析的目录信息',
  storage_timeout: 'WebDAV 请求超时，将按重试策略处理',
  storage_network_error: '无法连接 WebDAV 服务',
  base_assets_not_remote_verified: '基础必需资产尚未全部远程验证',
  subtitle_policy_not_satisfied: '字幕策略尚未满足',
  final_manifest_not_remote_verified: '最终清单尚未远程验证',
}

export function explain(code?: string | null): string {
  return code ? (messages[code] ?? code) : '—'
}

let csrfToken = ''
export function setCsrf(value: string) {
  csrfToken = value
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (options.method && !['GET', 'HEAD'].includes(options.method))
    headers.set('X-CSRF-Token', csrfToken)
  let response: Response
  try {
    response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' })
  } catch {
    throw new ApiError(0, 'network_error', '无法连接 ReelDock 服务')
  }
  const result = await response.json()
  if (!response.ok) {
    const code = typeof result.detail === 'string' ? result.detail : 'invalid_request'
    const validation = result.errors?.map((error: { message: string }) => error.message).join('；')
    throw new ApiError(response.status, code, validation || explain(code))
  }
  return result as T
}

export interface Session {
  username: string
  csrf_token: string
}
export interface Config {
  configured: boolean
  revision: number
  webdav_url?: string
  webdav_username?: string
  input_path?: string
  output_path?: string
  webdav_password_set?: boolean
  tmdb_token_set?: boolean
  proxy_url?: string | null
  tmdb_proxy_url?: string | null
  redirect_hosts?: string[]
  http_timeout_seconds?: number
  probe_timeout_seconds?: number
  probe_max_bytes?: number
  chinese_languages?: string[]
  stable_seconds?: number
  actor_limit?: number
  actor_policy?: string
  policy_version?: number
}
export interface Task {
  id: string
  kind: string
  package_path: string | null
  status: string
  stage: string
  attempts: number
  pause_requested: boolean
  error_code: string | null
  created_at: number
  original_language: string | null
  base_status: string
  subtitle_status: string
  subtitle_reason: string | null
  steps: {
    stage: string
    status: string
    attempts: number
    error_code: string | null
    checkpoint: Record<string, unknown>
  }[]
}
export interface TaskEvent {
  id: number
  task_id: string | null
  code: string
  created_at: number
  details: Record<string, unknown>
}

export interface Asset {
  id: string
  path: string
  kind: string
  required: boolean
  status: string
  error_code: string | null
  managed: boolean
  sha256: string | null
  preview: string | null
}
export interface Film {
  id: string
  path: string
  title: string
  year: number | null
  scan_status: string
  match_status: string
  tmdb_id: string | null
  original_language: string | null
  base_status: string
  error_code: string | null
  task_id: string | null
  task_status: string | null
  candidates: { id: string; title: string; year: number | null; confidence: number }[]
  assets: Asset[]
  cast: {
    id: string
    name: string
    role: string
    path: string | null
    source_has_image: boolean
    selected: boolean
  }[]
}
