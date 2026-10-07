import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { CheckboxPicker } from './CheckboxPicker'

afterEach(cleanup)

const options = [{ value: 'a', label: 'Acme' }, { value: 'b', label: 'Beta' }]

it('adds and removes values without a modifier key', () => {
  const onChange = vi.fn()
  const { rerender } = render(<CheckboxPicker legend="Entity" options={options} selected={[]} onChange={onChange} />)
  fireEvent.click(screen.getByRole('checkbox', { name: 'Beta' }))
  expect(onChange).toHaveBeenLastCalledWith(['b'])

  rerender(<CheckboxPicker legend="Entity" options={options} selected={['a', 'b']} onChange={onChange} />)
  fireEvent.click(screen.getByRole('checkbox', { name: 'Acme' }))
  expect(onChange).toHaveBeenLastCalledWith(['b'])
})

it('keeps a selection listed after the options stop including it', () => {
  render(<CheckboxPicker legend="Entity" options={[options[1]]} selected={['a']} labels={new Map([['a', 'Acme']])} onChange={() => {}} />)
  expect(screen.getByRole('group', { name: 'Entity (1)' })).toBeTruthy()
  expect(screen.getByRole('checkbox', { name: 'Acme' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: 'Beta' })).not.toBeChecked()
})

it('says when there is nothing to pick', () => {
  render(<CheckboxPicker legend="Keyword" options={[]} selected={[]} onChange={() => {}} emptyText="No keywords match." />)
  expect(screen.getByText('No keywords match.')).toBeTruthy()
})
