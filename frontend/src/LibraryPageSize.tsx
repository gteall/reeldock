import { useEffect, useState } from 'react'
import { InputNumber } from 'antd'

export function usePageSize(page: 'movies' | 'posters') {
  const key = `reeldock.${page}.pageSize`
  const [pageSize, setPageSize] = useState(() => {
    try {
      const saved = Number(window.localStorage.getItem(key))
      if (Number.isSafeInteger(saved) && saved > 0) return saved
    } catch {
      // Pagination remains available when browser storage is disabled.
    }
    return 100
  })
  useEffect(() => {
    try {
      window.localStorage.setItem(key, String(pageSize))
    } catch {
      // Keep the selection for this visit even if it cannot be persisted.
    }
  }, [key, pageSize])
  return [pageSize, setPageSize] as const
}

export function PageSizeControl({
  value,
  onChange,
}: {
  value: number
  onChange: (value: number) => void
}) {
  return (
    <label className="page-size-control">
      <span>每页显示</span>
      <InputNumber
        aria-label="每页影片数"
        min={1}
        precision={0}
        value={value}
        onChange={(size) => {
          if (size !== null && Number.isSafeInteger(size) && size > 0) onChange(size)
        }}
      />
      <span>部</span>
    </label>
  )
}
