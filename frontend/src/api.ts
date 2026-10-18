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
  context_changed: '媒体信息已更新，等待重新刮削',
  tmdb_original_language_unknown: 'TMDB 原始语言缺失或无法识别，需要人工确认',
  tmdb_original_language_chinese: '依据 TMDB 中文规则跳过；实际音轨未探测',
  default_audio_unknown: '无法确认实际默认音轨语言，需要人工确认',
  default_audio_ambiguous: '默认音轨或解说轨存在冲突，需要人工确认',
  embedded_subtitle_ambiguous: '内置字幕繁简标签冲突，需要人工确认',
  subtitle_no_match: 'ASSRT 没有匹配字幕，可在详情中修改关键词并手动搜索',
  assrt_token_required: '请先在设置中配置 ASSRT Token，用于自动下载和手动搜索字幕',
  assrt_token_invalid: 'ASSRT Token 无效，请在设置中检查或更新',
  assrt_rate_limited: 'ASSRT 搜索次数已达限制，请稍后重试',
  assrt_unavailable: 'ASSRT 暂时不可用，请稍后重试',
  subtitle_keyword_too_short: '字幕搜索词至少需要 3 个字符',
  subtitle_search_expired: '搜索结果已过期或影片信息已变化，请重新搜索',
  subtitle_candidate_expired: '字幕文件已变化或不存在，请重新搜索并选择',
  subtitle_manual_remaining: '所选字幕已应用，其他剧集仍需处理字幕',
  subtitle_probe_required: '当前媒体没有有效的探测结果，请先运行字幕检查',
  embedded_subtitle_not_found: '所选内置字幕轨道已失效，请刷新详情后重新选择',
  metadata_preview_expired: '删除预览已失效，请重新点击删除元数据并核对文件',
  metadata_changed_refresh_preview: '元数据文件已发生变化，请重新预览后删除',
  metadata_delete_rejected: '只能删除当前影片的元数据文件',
  metadata_delete_not_confirmed: '未能确认文件已删除，后台将重试核对',
  metadata_deleted: '元数据已删除，可重新刮削',
  metadata_delete_limit: '元数据文件过多，请先检查目录结构',
  subtitle_marker_too_large: '内置字幕标志文件过大，请检查探测结果',
  subtitle_timeout: '字幕服务请求超时，请稍后重试',
  subtitle_network_error: '无法连接字幕服务，请检查网络或代理',
  subtitle_host_not_allowed: '字幕下载地址不属于受支持的字幕来源',
  subtitle_http_error: '字幕下载失败，请稍后重试或选择其他版本',
  subtitle_invalid_candidates: '字幕服务返回的数据无效，请稍后重试',
  subtitle_duration_unknown: '缺少影片时长，请先执行字幕探测后再应用',
  subtitle_format_unsupported: '只支持 SRT / ASS / SSA 字幕文件，请选择其他版本',
  subtitle_not_text: '字幕为空、编码异常或返回了网页',
  subtitle_no_dialogue: '字幕没有可解析的有效对白',
  subtitle_timeline_mismatch: '字幕时间轴与实际片长明显不符',
  subtitle_pair_incomplete: 'IDX/SUB 字幕必须成对存在',
  subtitle_invalid_vobsub: 'IDX/SUB 字幕结构或内容校验失败',
  probe_timeout: '媒体探测超时，未下载整部视频',
  probe_byte_budget: '媒体探测超出读取预算，已停止',
  storage_range_ignored: '存储忽略 Range，已停止读取',
  move_capability_unverified: '旧版归档任务已停用，请重新扫描并原地刮削',
  archive_target_conflict: '目标已有同名目录，未覆盖',
  source_changed_before_archive: '旧版归档前文件发生变化，请重新扫描',
  storage_move_partial_failure: 'MOVE 返回子项失败，请核对两侧，未删除源',
  move_unknown: '移动结果不明；重试只核对状态，不盲目重发或删除',
  move_in_flight_unknown: '源完整但无法证明先前 MOVE 已停止，需人工核对',
  archive_existing_intent_requires_recovery: '保留旧版归档记录，当前版本仅原地刮削',

  actor_images_disabled: '演员头像刮削已关闭，请先在配置中启用',
  archive_removed: '归档移动功能已移除，请扫描并在原目录刮削',
  package_outside_scrape_directories: '媒体路径不在已配置的刮削目录范围内',
  source_changed_before_manifest: '生成清单期间文件发生变化，请重新扫描后重试',
  tmdb_credentials_required: '请先保存 TMDB 凭证',
  tmdb_timeout: 'TMDB 连接超时，请检查网络或代理',
  tmdb_not_found: 'TMDB ID 不存在，请核对电影与剧集类型',
  movie_not_found: '影片不存在或已被移除',
  tmdb_match_needs_review: '匹配存在歧义，请选择候选或指定电影 / 剧 ID',
  tmdb_id_conflict: '文件名、现存 NFO 与指定 ID 冲突，已保留原件',
  existing_nfo_id_missing: '已有 NFO 缺少明确 TMDB ID，已保留原件，请人工核对',
  invalid_nfo: '已有 NFO 无法安全解析，已保留原件',
  invalid_image: '图片内容无效，不能解码为受支持的图片',
  upload_verification_failed: '上传后内容不一致，已停止；请核对或移除专用测试坏文件后重试',
  actor_source_missing: '严格模式下演员来源无图，请人工补图后重试',
  actor_filename_collision: '演员头像文件名冲突，请人工核对',
  package_not_stable: '媒体包尚未稳定，请扫描并等待稳定窗口',
  source_changed_scan_again: '源文件发生变化，请重新扫描并等待稳定',
  source_missing: '扫描时未找到源媒体，请检查媒体目录并重新扫描',
  loose_media_requires_directory: '散放视频需要整理为独立目录',
  multiple_media_requires_review: '同一目录包含多个视频，需要人工整理',
  episode_number_unknown: '季集号不明确，请人工修正后重新扫描',
  episode_number_conflict: '文件与目录的季集编号冲突，请人工修正',
  multi_episode_file_unsupported: '多集单文件尚未支持，请拆分或人工整理；不能映射为单集',
  duplicate_episode_mapping: '同一包中多个文件映射到同一集，请核对',
  tmdb_episode_identity_conflict: 'TMDB 集身份与季集编号不一致，已保留原件',
  episode_not_found: '剧集文件不存在',
  asset_retry_not_allowed: '此资产不能单独重试，请恢复原刮削任务',
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
  storage_not_webdav: '存储返回 HTTP 405，拒绝此请求方法；请检查 WebDAV、下载直链或代理',
  storage_redirect_host_denied: '下载跳转主机不在你设置的限制列表中；留空可自动跟随 OpenList 302',
  storage_redirect_rejected: '下载跳转地址不受支持；不允许携带账号密码的 URL 或 HTTPS 降级',
  storage_redirect_limit: '下载跳转次数过多，已停止跟随',
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
  scrape_paths?: string[]
  webdav_password_set?: boolean
  tmdb_token_set?: boolean
  assrt_token_set?: boolean
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
  scrape_actor_images?: boolean
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
  media_id: string | null
  season: number | null
  id: string
  path: string
  kind: string
  required: boolean
  status: string
  error_code: string | null
  managed: boolean
  sha256: string | null
  preview: string | null
  remote_verified_at: number | null
}
export interface MediaItem {
  id: string
  path: string
  subtitle_search_keyword?: string
  title: string
  season: number | null
  episode: number | null
  tmdb_id: string | null
  mapping_status: string
  manual_mapping: boolean
  original_language: string | null
  subtitle_status: string
  subtitle_reason: string | null
  subtitle_evidence: Record<string, unknown>
  probe_status: string
  probe_evidence: Film['probe_evidence']
  error_code: string | null
}
export interface ProbeStream {
  index: number
  type: string
  codec: string
  language?: string
  title?: string
  default?: number
  forced?: number
}
export interface Film {
  scrape_completed: boolean
  actor_images_enabled: boolean
  manual_subtitles_enabled?: boolean
  metadata: {
    source: string | null
    original_title: string
    overview: string
    tagline: string
    release_date: string
    genres: string[]
    rating: number | null
    votes: number
    directors: string[]
    writers: string[]
    imdb_id: string | null
  }
  kind: 'movie' | 'tv'
  context_version: number
  lease_active: boolean
  media: MediaItem[]
  seasons: { number: number; title: string }[]
  subtitle_status: string
  subtitle_reason: string | null
  subtitle_evidence: Record<string, unknown>
  probe_status: string
  probe_evidence: {
    audio?: { language: string; selection: string }
    audio_error?: string
    probe?: {
      bytes_requested?: number
      duration?: number | null
      streams?: ProbeStream[]
    }
  }
  archive_status: string
  archive_enabled: boolean
  archive_intent: {
    id: string
    status: string
    error_code: string | null
    failed_paths?: string[]
  } | null
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
