import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Empty, Input, Pagination, Segmented, Select, Skeleton, Tag } from 'antd'
import { ReloadOutlined, SearchOutlined, StarFilled } from '@ant-design/icons'
import { api } from './api'
import type { Film } from './api'
import { CachedImage } from './CachedImage'
import { artwork, posterPackages, scrapedPackages } from './posters'
import { filmLink } from './navigation'

const pageSize = 24

export interface PosterWallView {
  search: string
  kind: string
  state: string
  sort: string
  page: number
}

export function PosterWallPage({
  view,
  onChange,
}: {
  view: PosterWallView
  onChange: (view: PosterWallView) => void
}) {
  const { search, kind, state, sort, page } = view
  const query = useQuery({
    queryKey: ['movies'],
    queryFn: () => api<{ items: Film[] }>('/packages'),
    refetchInterval: 30000,
  })
  const library = scrapedPackages(query.data?.items ?? [])
  const items = posterPackages(query.data?.items ?? [], search, kind, state, sort)
  const current = Math.min(page, Math.max(1, Math.ceil(items.length / pageSize)))
  const change = (key: 'search' | 'kind' | 'state' | 'sort') => (value: string) => {
    onChange({ ...view, [key]: value, page: 1 })
  }

  return (
    <section className="poster-wall" aria-label="已刮削媒体海报墙">
      <div className="poster-toolbar">
        <Segmented
          aria-label="媒体类型"
          value={kind}
          onChange={change('kind')}
          options={[
            { label: '全部', value: 'all' },
            { label: '电影', value: 'movie' },
            { label: '电视剧', value: 'tv' },
          ]}
        />
        <Input
          className="poster-search"
          aria-label="搜索已刮削内容"
          placeholder="搜索片名、演员、导演…"
          prefix={<SearchOutlined />}
          allowClear
          value={search}
          onChange={(event) => change('search')(event.target.value)}
        />
        <Select
          aria-label="归档状态"
          value={state}
          onChange={change('state')}
          options={[
            { label: '全部归档状态', value: 'all' },
            { label: '已归档', value: 'archived' },
            { label: '尚未归档', value: 'unarchived' },
          ]}
        />
        <Select
          aria-label="海报排序"
          value={sort}
          onChange={change('sort')}
          options={[
            { label: '按片名', value: 'title' },
            { label: '年份从新到旧', value: 'year' },
            { label: '评分从高到低', value: 'rating' },
          ]}
        />
        <Button
          icon={<ReloadOutlined />}
          loading={query.isFetching}
          onClick={() => query.refetch()}
        >
          刷新
        </Button>
      </div>
      {query.isError && (
        <Alert
          type="error"
          showIcon
          title="海报墙加载失败"
          description={query.error.message}
          action={<Button onClick={() => query.refetch()}>重试</Button>}
        />
      )}
      {query.isPending ? (
        <div className="poster-grid" aria-label="正在加载海报">
          {Array.from({ length: 8 }, (_, index) => (
            <div className="poster-loading" key={index}>
              <Skeleton.Node active />
              <Skeleton active title={false} paragraph={{ rows: 2 }} />
            </div>
          ))}
        </div>
      ) : query.data ? (
        <>
          <div className="poster-summary" aria-live="polite">
            <span>
              共 <strong>{library.length}</strong> 部已刮削内容
              <span className="poster-summary-divider">/</span>
              {library.filter((film) => film.kind === 'movie').length} 部电影 ·{' '}
              {library.filter((film) => film.kind === 'tv').length} 部电视剧
            </span>
            <span>当前显示 {items.length} 部</span>
          </div>
          {!items.length ? (
            <div className="poster-empty">
              <Empty
                description={
                  library.length
                    ? '没有找到符合条件的影片，试试其他片名或筛选条件。'
                    : '还没有已刮削内容'
                }
              >
                {library.length ? (
                  <Button
                    onClick={() => {
                      onChange({ ...view, search: '', kind: 'all', state: 'all', page: 1 })
                    }}
                  >
                    清除筛选
                  </Button>
                ) : (
                  <Button type="primary" href="#/movies">
                    前往电影与剧集，扫描并刮削
                  </Button>
                )}
              </Empty>
            </div>
          ) : (
            <div className="poster-grid">
              {items.slice((current - 1) * pageSize, current * pageSize).map((film) => (
                <a
                  className="poster-card"
                  key={film.id}
                  href={filmLink(film.id)}
                  aria-label={`查看 ${film.title} 的详情`}
                >
                  <div className="poster-cover">
                    <CachedImage
                      src={artwork(film, 'poster')?.preview}
                      alt={`${film.title} 海报`}
                    />
                    <Tag className="poster-kind">{film.kind === 'tv' ? '电视剧' : '电影'}</Tag>
                    {film.metadata.rating != null && (
                      <span className="poster-rating">
                        <StarFilled /> {film.metadata.rating.toFixed(1)}
                      </span>
                    )}
                    <div className="poster-overlay">
                      <span>查看影片详情 →</span>
                    </div>
                  </div>
                  <h3 title={film.title}>{film.title}</h3>
                  <div className="poster-caption">
                    <span>
                      {film.year ?? '年份未知'}
                      {film.metadata.genres[0] ? ` · ${film.metadata.genres[0]}` : ''}
                    </span>
                    <span className={film.archive_status === 'archived' ? 'poster-archived' : ''}>
                      {film.archive_status === 'archived' ? '已归档' : '已刮削'}
                    </span>
                  </div>
                </a>
              ))}
            </div>
          )}
          <Pagination
            className="poster-pagination"
            current={current}
            total={items.length}
            pageSize={pageSize}
            onChange={(page) => onChange({ ...view, page })}
            showSizeChanger={false}
            hideOnSinglePage
          />
        </>
      ) : null}
    </section>
  )
}
