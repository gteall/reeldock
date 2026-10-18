import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { ArrowLeftOutlined, ReloadOutlined, StarFilled } from '@ant-design/icons'
import { api, ApiError, explain } from './api'
import type { Asset, Film, MediaItem } from './api'
import { CachedImage } from './CachedImage'
import { ProbeEvidence } from './EpisodesPanel'
import { Preview, Status } from './MediaAssets'
import { artwork, scrapedPackages } from './posters'

const assetNames: Record<string, string> = {
  nfo: 'NFO 元数据',
  poster: '海报',
  fanart: '背景图',
  actor: '演员头像',
  season_poster: '季海报',
  thumb: '剧集缩略图',
  subtitle: '字幕',
  manifest: '最终清单',
}

export function FilmDetailPage({ id }: { id: string }) {
  const [preview, setPreview] = useState<Asset | null>(null)
  const query = useQuery({
    queryKey: ['movies', id],
    queryFn: () => api<Film>(`/packages/${encodeURIComponent(id)}`),
    refetchInterval: 30000,
  })
  const film = query.data
  const back = (
    <Button href="#/posters" icon={<ArrowLeftOutlined />}>
      返回海报墙
    </Button>
  )
  if (query.isPending)
    return (
      <div className="film-loading">
        <Spin size="large" />
      </div>
    )
  if (!film)
    return (
      <div className="film-error">
        {back}
        {query.error instanceof ApiError && query.error.status === 404 ? (
          <Empty description="这部影片不存在或已被移除" />
        ) : (
          <Alert
            type="error"
            showIcon
            title="影片详情加载失败"
            description={query.error?.message}
            action={<Button onClick={() => query.refetch()}>重试</Button>}
          />
        )}
      </div>
    )
  const metadata = film.metadata
  const verified = film.assets.filter((asset) => asset.status === 'remote_verified').length
  const scraped = scrapedPackages([film]).length > 0

  return (
    <article className="film-detail">
      <div className="film-navigation">
        {back}
        <Space wrap>
          <Button href="#/movies">进入刮削工作台</Button>
          <Button
            icon={<ReloadOutlined />}
            loading={query.isFetching}
            onClick={() => query.refetch()}
          >
            刷新
          </Button>
        </Space>
      </div>
      {query.isError && (
        <Alert
          type="warning"
          title="详情更新失败，正在显示上次获取的信息"
          description={query.error.message}
        />
      )}
      <div className="film-hero">
        {artwork(film, 'fanart') && (
          <CachedImage
            className="film-backdrop"
            src={artwork(film, 'fanart')?.preview}
            alt={`${film.title} 背景图`}
          />
        )}
        <div className="film-hero-shade" />
        <CachedImage
          className="film-poster"
          src={artwork(film, 'poster')?.preview}
          alt={`${film.title} 海报`}
        />
        <div className="film-hero-info">
          <div className="film-kicker">
            {film.kind === 'tv' ? '电视剧 / TV SERIES' : '电影 / MOVIE'}
          </div>
          <h1>{film.title}</h1>
          {metadata.original_title && metadata.original_title !== film.title && (
            <p className="film-original-title">{metadata.original_title}</p>
          )}
          <div className="film-hero-meta">
            <span>{film.year ?? '年份未知'}</span>
            {film.original_language && <span>原始语言 · {film.original_language}</span>}
            {film.kind === 'tv' && (
              <span>
                {film.seasons.length} 季 · {film.media.length} 集已识别
              </span>
            )}
          </div>
          <Space wrap className="film-genres">
            {metadata.genres.map((genre) => (
              <Tag key={genre}>{genre}</Tag>
            ))}
          </Space>
          {metadata.tagline && <p className="film-tagline">{metadata.tagline}</p>}
          <div className="film-hero-bottom">
            {metadata.rating != null && (
              <div className="film-score">
                <StarFilled />
                <strong>{metadata.rating.toFixed(1)}</strong>
                <span>TMDB · {metadata.votes.toLocaleString('zh-CN')} 人评分</span>
              </div>
            )}
            <Tag color={film.archive_status === 'archived' ? 'success' : 'cyan'}>
              {film.archive_status === 'archived'
                ? '已刮削 · 已归档'
                : scraped
                  ? '已刮削 · 尚未归档'
                  : '刮削尚未完成'}
            </Tag>
          </div>
        </div>
      </div>
      {film.error_code && <Alert type="warning" showIcon title={explain(film.error_code)} />}
      <Tabs
        className="film-tabs"
        items={[
          {
            key: 'metadata',
            label: '影片资料',
            children: (
              <div className="film-sections">
                <Card title="剧情简介">
                  <Typography.Paragraph className="film-overview">
                    {metadata.overview || '来源暂未提供剧情简介。'}
                  </Typography.Paragraph>
                </Card>
                <Card title="影片信息">
                  <Descriptions
                    column={{ xs: 1, sm: 2, lg: 3 }}
                    items={[
                      {
                        key: 'original',
                        label: '原片名',
                        children: metadata.original_title || '—',
                      },
                      {
                        key: 'release',
                        label: film.kind === 'tv' ? '首播日期' : '上映日期',
                        children: metadata.release_date || film.year || '—',
                      },
                      {
                        key: 'language',
                        label: '原始语言',
                        children: film.original_language ?? '未知',
                      },
                      { key: 'genre', label: '类型', children: metadata.genres.join(' / ') || '—' },
                      {
                        key: 'directors',
                        label: '导演',
                        children: metadata.directors.join(' / ') || '—',
                      },
                      {
                        key: 'writers',
                        label: '编剧',
                        children: metadata.writers.join(' / ') || '—',
                      },
                      {
                        key: 'source',
                        label: '元数据来源',
                        children: metadata.source?.toUpperCase() || '—',
                      },
                      { key: 'tmdb', label: 'TMDB ID', children: film.tmdb_id ?? '—' },
                      { key: 'imdb', label: 'IMDb ID', children: metadata.imdb_id || '—' },
                    ]}
                  />
                </Card>
                <Card title={`演职人员 · ${film.cast.length} 位`}>
                  {film.cast.length ? (
                    <div className="film-cast-grid">
                      {film.cast.map((person) => (
                        <div key={person.id} className="film-cast-card">
                          <CachedImage
                            src={film.assets.find((asset) => asset.path === person.path)?.preview}
                            alt={person.name}
                          />
                          <strong>{person.name}</strong>
                          <span>{person.role || '角色未提供'}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty description="暂无演职人员信息" />
                  )}
                </Card>
              </div>
            ),
          },
          {
            key: 'workflow',
            label: '刮削与归档',
            children: (
              <div className="film-sections">
                <Card title="刮削结果">
                  <Descriptions
                    column={{ xs: 1, sm: 2 }}
                    items={[
                      {
                        key: 'match',
                        label: 'TMDB 匹配',
                        children: <Status value={film.match_status} />,
                      },
                      {
                        key: 'base',
                        label: '基础资产',
                        children: <Status value={film.base_status} />,
                      },
                      {
                        key: 'subtitle',
                        label: '字幕策略',
                        children: <Status value={film.subtitle_status} />,
                      },
                      {
                        key: 'archive',
                        label: '归档状态',
                        children: <Status value={film.archive_status} />,
                      },
                      {
                        key: 'reason',
                        label: '字幕依据',
                        children: explain(film.subtitle_reason),
                        span: 2,
                      },
                      {
                        key: 'task',
                        label: '最近任务',
                        children: film.task_status ? (
                          <Status value={film.task_status} />
                        ) : (
                          '暂无任务'
                        ),
                      },
                      {
                        key: 'verified',
                        label: '远程验证资产',
                        children: `${verified} / ${film.assets.length}`,
                      },
                      {
                        key: 'path',
                        label: '当前远程目录',
                        children: <span className="film-path">{film.path}</span>,
                        span: 2,
                      },
                      {
                        key: 'move',
                        label: 'MOVE 能力',
                        children: film.archive_enabled
                          ? '管理员已确认当前范围'
                          : '尚未确认 · 刮削结果保留',
                      },
                    ]}
                  />
                </Card>
                {film.kind === 'movie' && (
                  <Card title="音轨与字幕探测">
                    <ProbeEvidence item={film} />
                  </Card>
                )}
                {film.archive_intent && (
                  <Card title="归档记录">
                    <Descriptions
                      column={1}
                      items={[
                        { key: 'id', label: '归档编号', children: film.archive_intent.id },
                        {
                          key: 'status',
                          label: '状态',
                          children: <Status value={film.archive_intent.status} />,
                        },
                        {
                          key: 'error',
                          label: '原因',
                          children: explain(film.archive_intent.error_code),
                        },
                      ]}
                    />
                    {!!film.archive_intent.failed_paths?.length && (
                      <pre>{film.archive_intent.failed_paths.join('\n')}</pre>
                    )}
                  </Card>
                )}
              </div>
            ),
          },
          {
            key: 'assets',
            label: `随片资产 · ${film.assets.length}`,
            children: (
              <Card title={`图片、NFO、字幕与清单 · ${verified} 项已远程验证`}>
                <Table<Asset>
                  rowKey="id"
                  dataSource={film.assets}
                  scroll={{ x: 850 }}
                  pagination={{ pageSize: 15, showSizeChanger: false }}
                  columns={[
                    {
                      title: '类型',
                      dataIndex: 'kind',
                      width: 125,
                      render: (kind: string) => assetNames[kind] ?? kind,
                    },
                    {
                      title: '路径',
                      dataIndex: 'path',
                      render: (path: string) => <span className="film-path">{path}</span>,
                    },
                    {
                      title: '要求',
                      dataIndex: 'required',
                      width: 85,
                      render: (required: boolean) => (required ? '必需' : '可选'),
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      render: (value: string) => <Status value={value} />,
                    },
                    {
                      title: '验证时间',
                      dataIndex: 'remote_verified_at',
                      render: (value: number | null) =>
                        value
                          ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
                          : '—',
                    },
                    {
                      title: '原因',
                      dataIndex: 'error_code',
                      render: (value: string | null) => explain(value),
                    },
                    {
                      title: '预览',
                      key: 'preview',
                      width: 80,
                      render: (_, asset) => (
                        <Button
                          size="small"
                          disabled={!asset.preview}
                          onClick={() => setPreview(asset)}
                        >
                          查看
                        </Button>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          ...(film.kind === 'tv'
            ? [
                {
                  key: 'episodes',
                  label: `季与集 · ${film.media.length}`,
                  children: (
                    <Card title="当前媒体包剧集">
                      <Table<MediaItem>
                        rowKey="id"
                        dataSource={film.media}
                        scroll={{ x: 750 }}
                        pagination={{ pageSize: 20, showSizeChanger: false }}
                        expandable={{
                          expandedRowRender: (item) => (
                            <>
                              <Typography.Paragraph className="film-path">
                                {item.path}
                              </Typography.Paragraph>
                              <ProbeEvidence item={item} />
                            </>
                          ),
                        }}
                        columns={[
                          {
                            title: '季 / 集',
                            key: 'episode',
                            render: (_, item) =>
                              `${item.season === 0 ? '特别篇' : `第 ${item.season ?? '?'} 季`} · E${item.episode ?? '?'}`,
                          },
                          { title: '标题', dataIndex: 'title' },
                          {
                            title: 'TMDB ID',
                            dataIndex: 'tmdb_id',
                            render: (value) => value ?? '—',
                          },
                          {
                            title: '字幕',
                            dataIndex: 'subtitle_status',
                            render: (value) => <Status value={value} />,
                          },
                          {
                            title: '原因',
                            dataIndex: 'error_code',
                            render: (value) => explain(value),
                          },
                        ]}
                      />
                    </Card>
                  ),
                },
              ]
            : []),
        ]}
      />
      <Preview asset={preview} close={() => setPreview(null)} />
    </article>
  )
}
