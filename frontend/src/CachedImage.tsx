import { useState } from 'react'
import { PictureOutlined } from '@ant-design/icons'

export function CachedImage({
  src,
  alt,
  className = '',
}: {
  src?: string | null
  alt: string
  className?: string
}) {
  const [failed, setFailed] = useState<string | null>(null)
  return src && failed !== src ? (
    <img
      className={`cached-image ${className}`}
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(src)}
    />
  ) : (
    <div className={`image-placeholder ${className}`} role="img" aria-label={`${alt} · 暂无图片`}>
      <PictureOutlined />
      <span>暂无图片</span>
    </div>
  )
}
