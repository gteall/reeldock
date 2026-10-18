import { useQuery } from '@tanstack/react-query'
import { Card, Descriptions, Space, Tag, Typography } from 'antd'
import { api } from './api'

const logo = new URL('./assets/tmdb.svg', import.meta.url).href
const { Paragraph, Link } = Typography

export function AboutPage() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => api<{ version: string; phase: string }>('/health'),
  })
  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Card title="ReelDock · 影坞">
        <Paragraph>从远程媒体到随片元数据、字幕与原地刮削。</Paragraph>
        <Descriptions
          column={1}
          items={[
            { key: 'version', label: '当前构建', children: health.data?.version ?? '加载中' },
            {
              key: 'release',
              label: '发布状态',
              children: <Tag>v0.1.0 发布准备 · 尚未正式发布</Tag>,
            },
            { key: 'player', label: '首要播放端', children: 'Kodi' },
            {
              key: 'source',
              label: '源码与部署说明',
              children: (
                <Link href="https://github.com/gteall/reeldock" target="_blank">
                  GitHub · ReelDock
                </Link>
              ),
            },
          ]}
        />
      </Card>
      <Card title="数据来源与致谢">
        <a href="https://www.themoviedb.org" target="_blank" rel="noreferrer">
          <img className="tmdb-logo" src={logo} alt="TMDB · The Movie Database" />
        </a>
        <Paragraph>电影、剧集、演职员与图片元数据来自 TMDB。</Paragraph>
        <Paragraph>
          This product uses the TMDB API but is not endorsed or certified by TMDB.
        </Paragraph>
        <Paragraph type="secondary">
          TMDB 标识随应用提供，显示此页面不需要访问 TMDB 图片服务。
        </Paragraph>
        <Paragraph type="secondary">
          自动和手动字幕服务由{' '}
          <Link href="https://assrt.net" target="_blank" rel="noreferrer">
            assrt.net
          </Link>{' '}
          提供，共用设置中的 ASSRT Token；内容有效性与时间轴会单独校验。
        </Paragraph>
      </Card>
    </Space>
  )
}
