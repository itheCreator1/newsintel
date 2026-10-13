import { renderHook } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { useWhenChanged } from './use-when-changed'

function useMirror(value: string) {
  const [text, setText] = useState(value)
  const [renders, setRenders] = useState(0)
  useWhenChanged(value, () => { setText(value); setRenders(count => count + 1) })
  return { text, renders }
}

describe('useWhenChanged', () => {
  it('leaves the first render alone', () => {
    const { result } = renderHook(() => useMirror('a'))
    expect(result.current).toEqual({ text: 'a', renders: 0 })
  })

  it('adjusts state in the same render the key changes, once per change', () => {
    const { result, rerender } = renderHook(({ value }) => useMirror(value), { initialProps: { value: 'a' } })
    rerender({ value: 'b' })
    expect(result.current).toEqual({ text: 'b', renders: 1 })
    rerender({ value: 'b' })
    expect(result.current).toEqual({ text: 'b', renders: 1 })
    rerender({ value: 'a' })
    expect(result.current).toEqual({ text: 'a', renders: 2 })
  })
})
