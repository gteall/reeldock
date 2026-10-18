import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Menu,
  Segmented,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd'
import {
  ApartmentOutlined,
  PlusOutlined,
  MinusCircleOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  AppstoreOutlined,
  InfoCircleOutlined,
  LogoutOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { api, ApiError, explain, setCsrf } from './api'
import { newTaskKey } from './taskKey'
import type { Config, Session, Task, TaskEvent } from './api'
import { MoviesPage } from './MoviesPage'
import { connectEvents } from './liveEvents'
import type { LiveState } from './liveEvents'
import { AboutPage } from './AboutPage'
import { PosterWallPage } from './PosterWallPage'
import type { PosterWallView } from './PosterWallPage'
import { FilmDetailPage } from './FilmDetailPage'
import { readRoute } from './navigation'

const brandLogo = new URL('./assets/reeldock.svg', import.meta.url).href

const { Title, Text, Paragraph } = Typography
const stageNames: Record<string, string> = {
  asset_retry: '资产局部重试',
  connection_check: '只读连接检查',
  scan: '目录扫描',
  match_metadata: 'TMDB 匹配',
  base_assets_verified: '基础资产远程验证',
  subtitle_policy: '字幕策略',
  final_manifest: '最终清单',
  archive: '旧版归档（已停用）',
}
const stateNames: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  retry_wait: '等待重试',
  paused: '已暂停',
  blocked: '已阻塞',
  failed: '失败',
  completed: '已完成',
  skipped: '已跳过',
  pending: '未开始',
}
const colors: Record<string, string> = {
  completed: 'success',
  running: 'processing',
  failed: 'error',
  blocked: 'warning',
  retry_wait: 'warning',
}
function State({ value }: { value: string }) {
  return <Tag color={colors[value]}>{stateNames[value] ?? value}</Tag>
}
function failure(error: unknown) {
  return error instanceof Error ? error.message : '操作失败'
}

function LoginPage({ onLogin }: { onLogin: (value: Session) => void }) {
  const { message } = AntApp.useApp()
  const status = useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api<{ admin_initialized: boolean }>('/auth/status'),
  })
  const login = useMutation({
    mutationFn: (values: { username: string; password: string }) =>
      api<Session>('/auth/login', { method: 'POST', body: JSON.stringify(values) }),
    onSuccess: (result) => {
      setCsrf(result.csrf_token)
      onLogin(result)
    },
    onError: (e) => message.error(failure(e)),
  })
  return (
    <div className="login-page">
      <div className="login-story">
        <Brand />
        <span className="eyebrow">YOUR MEDIA, AT HOME</span>
        <h1>
          让每一部好电影，
          <br />
          都有自己的归处。
        </h1>
        <p>从元数据到随片资产，影坞为你的远程媒体库建立有序、可恢复的工作流。</p>
        <div className="login-phases">
          TMDB 匹配 <span>→</span> 基础资产 <span>→</span> 字幕 <span>→</span> 完成
        </div>
      </div>
      <Card className="login-card">
        <span className="eyebrow">REELDOCK CONSOLE</span>
        <Title level={3}>登录影坞</Title>
        <Paragraph type="secondary">使用本地初始化的管理员账户。</Paragraph>
        {status.data?.admin_initialized === false && (
          <Alert
            type="info"
            showIcon
            title="首次启动"
            description={
              <span>
                请先在服务端运行 <code>uv run reeldock init-admin</code> 初始化管理员。
              </span>
            }
          />
        )}
        {status.isError && (
          <Alert type="error" title="后端暂不可用" description={failure(status.error)} />
        )}
        <Form
          layout="vertical"
          onFinish={(values) => login.mutate(values)}
          initialValues={{ username: 'admin' }}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" size="large" block htmlType="submit" loading={login.isPending}>
            进入控制台
          </Button>
        </Form>
        <Text type="secondary">电影与剧集刮削</Text>
      </Card>
    </div>
  )
}

function Brand() {
  return (
    <div className="brand">
      <img className="brand-mark" src={brandLogo} alt="" />
      <div>
        <strong>ReelDock</strong>
        <small>影坞 · 媒体工作台</small>
      </div>
    </div>
  )
}

function SettingsPage() {
  const [form] = Form.useForm()
  const queryClient = useQueryClient()
  const { message } = AntApp.useApp()
  const query = useQuery({ queryKey: ['config'], queryFn: () => api<Config>('/config') })
  const checkKey = useRef<string | null>(null)
  const actorImages = Form.useWatch('scrape_actor_images', form)
  useEffect(() => {
    if (query.data)
      form.setFieldsValue({
        http_timeout_seconds: 15,
        stable_seconds: 600,
        actor_limit: 20,
        scrape_actor_images: false,
        scrape_paths: ['/incoming'],
        actor_policy: 'available_only',
        probe_timeout_seconds: 45,
        ...query.data,
        probe_max_mib: (query.data.probe_max_bytes ?? 67108864) / 1048576,
        redirect_hosts_text: query.data.redirect_hosts?.join(', ') ?? '',
        subtitle_download_hosts_text:
          query.data.subtitle_download_hosts?.join(', ') ?? 'www.shooter.cn, shooter.cn',
        chinese_languages_text: query.data.chinese_languages?.join(', ') ?? 'zh, cn',
        webdav_password: '',
        tmdb_token: '',
        clear_webdav_password: false,
        clear_tmdb_token: false,
      })
  }, [query.data, form])
  const save = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const {
        redirect_hosts_text,
        chinese_languages_text,
        subtitle_download_hosts_text,
        probe_max_mib,
        ...rest
      } = values
      return api<Config>('/config', {
        method: 'PUT',
        body: JSON.stringify({
          ...rest,
          expected_revision: query.data?.revision ?? 0,
          policy_version: query.data?.policy_version ?? 1,
          redirect_hosts: String(redirect_hosts_text ?? '')
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean),
          chinese_languages: String(chinese_languages_text ?? '')
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean),
          subtitle_download_hosts: String(subtitle_download_hosts_text ?? '')
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean),
          proxy_url: values.proxy_url || null,
          probe_max_bytes: Number(probe_max_mib) * 1048576,
        }),
      })
    },
    onSuccess: (result) => {
      queryClient.setQueryData(['config'], result)
      message.success('配置已加密保存')
    },
    onError: (error) => message.error(failure(error)),
  })
  const check = useMutation({
    mutationFn: () => {
      checkKey.current ??= newTaskKey()
      return api<{ task_id: string }>('/connection-check', {
        method: 'POST',
        body: JSON.stringify({ idempotency_key: checkKey.current }),
      })
    },
    onSuccess: () => {
      checkKey.current = null
      queryClient.invalidateQueries({ queryKey: ['tasks'] })
      message.success('检查已加入持久化队列，请在任务中心查看结果')
    },
    onError: (error) => message.error(failure(error)),
  })
  if (query.isPending) return <Spin />
  if (query.isError) return <Alert type="error" title={failure(query.error)} />
  return (
    <div className="settings-grid">
      <Form form={form} layout="vertical" onFinish={(values) => save.mutate(values)}>
        <Card
          title={
            <Space>
              <CloudServerOutlined />
              WebDAV 存储
            </Space>
          }
          extra={<Tag>OpenList</Tag>}
        >
          <Paragraph type="secondary">
            使用 WebDAV 端点（通常以 /dav/ 结尾），路径相对于此端点填写。
          </Paragraph>
          <Form.Item name="webdav_url" label="WebDAV 地址" rules={[{ required: true }]}>
            <Input placeholder="https://openlist.example/dav/" />
          </Form.Item>
          <div className="field-pair">
            <Form.Item name="webdav_username" label="账号">
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item
              name="webdav_password"
              label="密码"
              extra={query.data?.webdav_password_set ? '已配置 · 留空保留现有密码' : '尚未配置'}
            >
              <Input.Password autoComplete="new-password" placeholder="仅写入，不回显" />
            </Form.Item>
          </div>
          <Form.Item name="clear_webdav_password" valuePropName="checked">
            <Checkbox>清除已保存密码</Checkbox>
          </Form.Item>
          <Form.Item
            label="刮削目录"
            extra="可添加多个目录，NFO、海报、字幕等直接保存在对应媒体目录中。"
          >
            <Form.List
              name="scrape_paths"
              rules={[
                {
                  validator: async (_, paths) => {
                    if (!paths?.length) throw new Error('请至少添加一个刮削目录')
                  },
                },
              ]}
            >
              {(fields, { add, remove }, { errors }) => (
                <>
                  {fields.map((field, index) => (
                    <div className="scrape-directory-row" key={field.key}>
                      <Form.Item
                        name={field.name}
                        rules={[{ required: true, whitespace: true, message: '请输入刮削目录' }]}
                      >
                        <Input aria-label={`刮削目录 ${index + 1}`} placeholder="/movies" />
                      </Form.Item>
                      <Button
                        aria-label={`移除刮削目录 ${index + 1}`}
                        icon={<MinusCircleOutlined />}
                        disabled={fields.length === 1}
                        onClick={() => remove(field.name)}
                      />
                    </div>
                  ))}
                  <Button
                    type="dashed"
                    icon={<PlusOutlined />}
                    disabled={fields.length >= 32}
                    onClick={() => add('')}
                  >
                    添加刮削目录
                  </Button>
                  <Form.ErrorList errors={errors} />
                </>
              )}
            </Form.List>
          </Form.Item>
          <Text type="secondary">连接检查会逐一验证已保存目录。每部电影或剧集使用独立目录。</Text>
        </Card>
        <Card title="元数据与网络" className="form-card">
          <Form.Item
            name="tmdb_token"
            label="TMDB 凭证"
            extra={
              query.data?.tmdb_token_set
                ? '已配置 · 留空保留现有凭证'
                : '填写后用于 TMDB 元数据和图片查询'
            }
          >
            <Input.Password
              placeholder="TMDB API Read Access Token / API Key"
              autoComplete="new-password"
            />
          </Form.Item>
          <Form.Item name="clear_tmdb_token" valuePropName="checked">
            <Checkbox>清除 TMDB 凭证</Checkbox>
          </Form.Item>
          <Form.Item
            name="tmdb_proxy_url"
            label="TMDB 专用代理（可选）"
            extra="仅 TMDB API / 图片使用，适合 WebDAV 直连、TMDB 走代理。"
          >
            <Input placeholder="http://127.0.0.1:7890" />
          </Form.Item>
          <Form.Item name="proxy_url" label="HTTP(S) 代理（可选）">
            <Input placeholder="http://proxy.example:7890" />
          </Form.Item>
          <Form.Item
            name="redirect_hosts_text"
            label="下载重定向主机限制（可选）"
            extra="留空自动跟随 OpenList 的 302 直链，无需知道 115 下载域名。填写后仅允许列表中的精确小写主机名，以逗号分隔；跨域不携带 WebDAV 账号密码。"
          >
            <Input placeholder="cdn.example.com" />
          </Form.Item>
          <Form.Item name="http_timeout_seconds" label="HTTP 超时（秒）">
            <InputNumber min={1} max={120} />
          </Form.Item>
        </Card>
        <Card title="扫描与演员策略" className="form-card">
          <Form.Item
            name="stable_seconds"
            label="稳定窗口（秒）"
            extra="至少两次扫描文件集合、大小和版本一致；默认 600 秒。"
          >
            <InputNumber min={1} max={86400} />
          </Form.Item>
          <Form.Item name="scrape_actor_images" valuePropName="checked">
            <Checkbox>刮削演员头像（默认关闭）</Checkbox>
          </Form.Item>
          <Paragraph type="secondary">
            关闭时仍保留演员姓名、角色等信息，不下载或生成演员头像。
          </Paragraph>
          <Form.Item name="actor_limit" label="演员范围" extra="默认前 20 位；0 表示全部。">
            <InputNumber min={0} max={500} disabled={!actorImages} />
          </Form.Item>
          <Form.Item name="actor_policy" label="头像要求">
            <Segmented
              disabled={!actorImages}
              options={[
                { label: '仅来源有图者必需', value: 'available_only' },
                { label: '所选演员必须有图', value: 'strict' },
              ]}
            />
          </Form.Item>
        </Card>
        <Card title="字幕阶段预算" className="form-card">
          <Alert
            type="info"
            showIcon
            title="基础资产全部远程验证后才处理字幕；TMDB 中文作品跳过探测。"
          />
          <div className="field-pair">
            <Form.Item name="probe_timeout_seconds" label="探测时限（秒）">
              <InputNumber min={1} max={300} />
            </Form.Item>
            <Form.Item name="probe_max_mib" label="读取上限（MiB）">
              <InputNumber min={0.015625} max={1024} />
            </Form.Item>
          </div>
          <Form.Item
            name="chinese_languages_text"
            label="TMDB 中文语言集合"
            extra="命中 original_language 时跳过探测；不是实际音轨检测结果。"
          >
            <Input />
          </Form.Item>
        </Card>
        <Card title="字幕下载来源" className="form-card">
          <Form.Item
            name="subtitle_download_hosts_text"
            label="射手字幕下载主机"
            extra="精确主机名，逗号分隔；仅允许 HTTPS。"
          >
            <Input />
          </Form.Item>
        </Card>
        <div className="save-bar">
          <Text type="secondary">配置版本 {query.data?.revision ?? 0}</Text>
          <Space>
            <Button
              icon={<CheckCircleOutlined />}
              onClick={() => check.mutate()}
              loading={check.isPending}
              disabled={!query.data?.configured || save.isPending}
            >
              检查已保存连接
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              icon={<SettingOutlined />}
              loading={save.isPending}
            >
              保存配置
            </Button>
          </Space>
        </div>
      </Form>
      <aside>
        <Card title="处理顺序" className="phase-card">
          <ol>
            <li>
              匹配 TMDB <small>保留 original_language</small>
            </li>
            <li>
              基础资产远程验证 <small>NFO · 海报 · 背景 · 可选演员头像</small>
            </li>
            <li>
              字幕策略 <small>中文作品直接跳过探测</small>
            </li>
            <li>
              最终清单验证 <small>检查完整性与源快照</small>
            </li>
          </ol>
        </Card>
        <Card className="form-card" title="本地持久化">
          <Paragraph type="secondary">
            配置、任务、事件和检查点保存在服务端本地
            SQLite。凭证使用独立密钥加密，备份时请同时保存数据库和 master.key。
          </Paragraph>
          <Tag color="blue">Kodi 优先</Tag>
          <Tag>单实例 Worker</Tag>
        </Card>
      </aside>
    </div>
  )
}

function TasksPage() {
  const { message } = AntApp.useApp()
  const client = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [filter, setFilter] = useState('全部')
  const query = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api<{ items: Task[] }>('/tasks'),
    refetchInterval: 30000,
  })
  const control = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) =>
      api(`/tasks/${id}/${action}`, { method: 'POST' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['tasks'] }),
    onError: (e) => message.error(failure(e)),
  })
  const task = query.data?.items.find((item) => item.id === selected)
  const items =
    query.data?.items.filter(
      (item) =>
        filter === '全部' ||
        (filter === '需处理'
          ? ['blocked', 'failed'].includes(item.status)
          : ['queued', 'running', 'retry_wait'].includes(item.status)),
    ) ?? []
  return (
    <>
      <Alert
        type="info"
        showIcon
        className="page-alert"
        title="连接检查为只读；电影页可提交完整刮削任务，数据直接保存在对应媒体目录。"
      />
      <Card
        title={
          <Segmented options={['全部', '进行中', '需处理']} value={filter} onChange={setFilter} />
        }
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => query.refetch()}>
            刷新
          </Button>
        }
      >
        {query.isError ? (
          <Alert type="error" title={failure(query.error)} />
        ) : (
          <Table<Task>
            rowKey="id"
            dataSource={items}
            loading={query.isPending}
            scroll={{ x: 780 }}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            locale={{
              emptyText: <Empty description="暂无任务。保存配置后，可运行一次只读连接检查。" />,
            }}
            columns={[
              {
                title: '任务',
                key: 'kind',
                render: (_, row) => (
                  <Button type="link" onClick={() => setSelected(row.id)}>
                    {row.kind === 'connection_check' ? 'WebDAV 连接检查' : row.package_path}
                  </Button>
                ),
              },
              { title: '当前阶段', dataIndex: 'stage', render: (v) => stageNames[v] ?? v },
              { title: '状态', dataIndex: 'status', render: (v) => <State value={v} /> },
              {
                title: '原因',
                dataIndex: 'error_code',
                render: (v) => <Text type="secondary">{explain(v)}</Text>,
              },
              {
                title: '创建时间',
                dataIndex: 'created_at',
                render: (v) => new Date(v * 1000).toLocaleString('zh-CN', { hour12: false }),
              },
              {
                title: '操作',
                render: (_, row) => (
                  <Space>
                    {['queued', 'running', 'retry_wait'].includes(row.status) && (
                      <Button
                        size="small"
                        disabled={row.pause_requested}
                        onClick={() => control.mutate({ id: row.id, action: 'pause' })}
                      >
                        {row.pause_requested ? '等待暂停' : '暂停'}
                      </Button>
                    )}
                    {['paused', 'blocked', 'failed'].includes(row.status) && (
                      <Button
                        size="small"
                        icon={<PlayCircleOutlined />}
                        onClick={() =>
                          control.mutate({
                            id: row.id,
                            action: row.status === 'paused' ? 'resume' : 'retry',
                          })
                        }
                      >
                        继续
                      </Button>
                    )}
                  </Space>
                ),
              },
            ]}
          />
        )}
      </Card>
      <Drawer
        title="任务详情"
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        size={540}
      >
        {task && (
          <>
            <Descriptions
              column={1}
              items={[
                { key: 'id', label: '任务 ID', children: <Text copyable>{task.id}</Text> },
                { key: 'status', label: '状态', children: <State value={task.status} /> },
                {
                  key: 'lang',
                  label: 'TMDB 原始语言',
                  children: task.original_language ?? '尚未匹配',
                },
                {
                  key: 'probe',
                  label: '实际音轨 / 字幕',
                  children: '请在媒体包 / 单集详情查看字幕证据',
                },
                { key: 'reason', label: '错误原因', children: explain(task.error_code) },
              ]}
            />
            {task.steps.map((step) => (
              <Card
                key={step.stage}
                size="small"
                className="form-card"
                title={stageNames[step.stage] ?? step.stage}
                extra={<State value={step.status} />}
              >
                <Text type="secondary">执行 {step.attempts} 次</Text>
                {step.error_code && <Paragraph>{explain(step.error_code)}</Paragraph>}
                {Object.keys(step.checkpoint).length > 0 && (
                  <pre>{JSON.stringify(step.checkpoint, null, 2)}</pre>
                )}
              </Card>
            ))}
          </>
        )}
      </Drawer>
    </>
  )
}

function EventsPage() {
  const [events, setEvents] = useState<TaskEvent[]>([])
  const cursor = useRef(0)
  const query = useQuery({
    queryKey: ['events'],
    queryFn: async () => {
      const result = await api<{ items: TaskEvent[]; next_cursor: number }>(
        `/events?after=${cursor.current}&limit=200`,
      )
      cursor.current = result.next_cursor
      return result
    },
    refetchInterval: 30000,
  })
  useEffect(() => {
    if (query.data?.items.length)
      setEvents((old) => {
        const combined = new Map([...old, ...query.data.items].map((e) => [e.id, e]))
        return [...combined.values()].slice(-200)
      })
  }, [query.data])
  return (
    <Card title="持久化事件" extra={<Tag>按游标恢复 · SSE + REST</Tag>}>
      <Paragraph type="secondary">
        显示最近读取的 200 条事件。连接中断后沿数据库游标继续读取，SSE
        实时通知变化；断线重连后重新获取数据库状态，REST 游标保留为回源入口。
      </Paragraph>
      {query.isError && <Alert type="error" title={failure(query.error)} />}
      <Table<TaskEvent>
        rowKey="id"
        dataSource={[...events].reverse()}
        scroll={{ x: 600 }}
        pagination={{ pageSize: 12 }}
        columns={[
          { title: '#', dataIndex: 'id', width: 70 },
          { title: '事件', dataIndex: 'code' },
          {
            title: '任务',
            dataIndex: 'task_id',
            render: (value) => (value ? `${value.slice(0, 10)}…` : '系统'),
          },
          {
            title: '时间',
            dataIndex: 'created_at',
            render: (value) => new Date(value * 1000).toLocaleString('zh-CN'),
          },
          {
            title: '详情',
            dataIndex: 'details',
            render: (value) => <code>{JSON.stringify(value)}</code>,
          },
        ]}
      />
    </Card>
  )
}

export default function App() {
  const queryClient = useQueryClient()
  const { message } = AntApp.useApp()
  const [route, setRoute] = useState(() => readRoute())
  const { page, filmId } = route
  const [posterView, setPosterView] = useState<PosterWallView>({
    search: '',
    kind: 'all',
    state: 'all',
    sort: 'title',
    page: 1,
  })
  useEffect(() => {
    const updateRoute = () => setRoute(readRoute())
    window.addEventListener('hashchange', updateRoute)
    return () => window.removeEventListener('hashchange', updateRoute)
  }, [])
  const [liveState, setLiveState] = useState<LiveState>('offline')
  const session = useQuery({
    queryKey: ['session'],
    queryFn: async () => {
      const value = await api<Session>('/auth/session')
      setCsrf(value.csrf_token)
      return value
    },
  })
  useEffect(
    () =>
      queryClient.getQueryCache().subscribe((event) => {
        if (
          event.type === 'updated' &&
          event.query.queryKey[0] !== 'session' &&
          event.query.state.error instanceof ApiError &&
          event.query.state.error.status === 401
        ) {
          setCsrf('')
          queryClient.setQueryData(['session'], null)
        }
      }),
    [queryClient],
  )
  useEffect(() => {
    if (!session.data) return
    return connectEvents(
      () => {
        for (const key of ['movies', 'tasks', 'events'])
          queryClient.invalidateQueries({ queryKey: [key] })
      },
      setLiveState,
      () => queryClient.invalidateQueries({ queryKey: ['session'] }),
    )
  }, [session.data?.username, queryClient])
  const logout = async () => {
    try {
      await api('/auth/logout', { method: 'POST' })
      setCsrf('')
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== 'session' })
      queryClient.setQueryData(['session'], null)
    } catch (error) {
      message.error(failure(error))
    }
  }
  if (session.isPending)
    return (
      <div className="loading">
        <Spin size="large" />
      </div>
    )
  if (!session.data)
    return (
      <LoginPage
        onLogin={(value) => {
          queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== 'session' })
          queryClient.setQueryData(['session'], value)
        }}
      />
    )
  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand />
        <div className="sidebar-label">工作空间</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[page]}
          onClick={({ key }) => {
            window.location.hash = `#/${key}`
          }}
          items={[
            { key: 'posters', icon: <AppstoreOutlined />, label: '海报墙' },
            { key: 'movies', icon: <PlayCircleOutlined />, label: '电影与剧集' },
            { key: 'settings', icon: <SettingOutlined />, label: '连接与配置' },
            { key: 'tasks', icon: <ApartmentOutlined />, label: '任务中心' },
            { key: 'events', icon: <ReloadOutlined />, label: '运行事件' },
            { key: 'about', icon: <InfoCircleOutlined />, label: '关于影坞' },
          ]}
        />
        <div className="sidebar-bottom">
          <Tag color="cyan">P5 · 发布准备</Tag>
          <p>
            原地刮削 ·{' '}
            {
              { live: '实时连接', connecting: '连接中', reconnecting: '重连中', offline: '离线' }[
                liveState
              ]
            }
          </p>
        </div>
      </aside>
      <div className="main">
        <header>
          <span>
            影坞控制台 <span className="header-slash">/</span>{' '}
            {page === 'posters'
              ? filmId
                ? '影片详情'
                : '海报墙'
              : page === 'about'
                ? '关于'
                : page === 'movies'
                  ? '电影'
                  : page === 'settings'
                    ? '配置'
                    : page === 'tasks'
                      ? '任务'
                      : '事件'}
          </span>
          <Space>
            <Text type="secondary">{session.data.username}</Text>
            <Button type="text" icon={<LogoutOutlined />} onClick={logout}>
              退出
            </Button>
          </Space>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <span className="eyebrow">REELDOCK / {page.toUpperCase()}</span>
              <Title level={2}>
                {page === 'posters'
                  ? filmId
                    ? '影片详情'
                    : '海报墙'
                  : page === 'about'
                    ? '关于影坞'
                    : page === 'movies'
                      ? '电影与剧集'
                      : page === 'settings'
                        ? '连接与配置'
                        : page === 'tasks'
                          ? '任务中心'
                          : '运行事件'}
              </Title>
              <Paragraph type="secondary">
                {page === 'posters'
                  ? filmId
                    ? '影片资料、随片资产与刮削结果。'
                    : '已刮削的电影与剧集，一目了然。点击海报，发现更多影片信息。'
                  : page === 'about'
                    ? '版本、数据来源与使用说明。'
                    : page === 'movies'
                      ? '查看匹配、随片图片和基础资产远程验证结果。'
                      : page === 'settings'
                        ? '连接远程存储，为媒体工作流准备一个可靠的起点。'
                        : page === 'tasks'
                          ? '查看持久化阶段、检查点与错误，按需暂停或重试。'
                          : '每一次状态变化都有记录，重启之后仍可追溯。'}
              </Paragraph>
            </div>
            <Tag icon={<CheckCircleOutlined />} color="success">
              本地持久化
            </Tag>
          </div>
          {page === 'posters' ? (
            filmId ? (
              <FilmDetailPage key={filmId} id={filmId} />
            ) : (
              <PosterWallPage view={posterView} onChange={setPosterView} />
            )
          ) : page === 'about' ? (
            <AboutPage />
          ) : page === 'movies' ? (
            <MoviesPage />
          ) : page === 'settings' ? (
            <SettingsPage />
          ) : page === 'tasks' ? (
            <TasksPage />
          ) : (
            <EventsPage />
          )}
          <footer>
            ReelDock · 影坞{' '}
            <span>This product uses the TMDB API but is not endorsed or certified by TMDB.</span>
          </footer>
        </main>
      </div>
    </div>
  )
}
