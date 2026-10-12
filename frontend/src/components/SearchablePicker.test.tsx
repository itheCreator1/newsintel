import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { CountryPicker, CountrySelect, LanguagePicker, LanguageSelect } from './SearchablePicker'

afterEach(cleanup)

it('narrows the countries as you type and adds the one you tick', () => {
  const onChange = vi.fn()
  render(<CountryPicker legend="Story country" selected={[]} onChange={onChange} />)
  fireEvent.change(screen.getByLabelText('Find a story country'), { target: { value: 'γερμαν' } })
  expect(screen.getAllByRole('checkbox')).toHaveLength(1)
  fireEvent.click(screen.getByRole('checkbox', { name: 'Germany (DE)' }))
  expect(onChange).toHaveBeenLastCalledWith(['DE'])
})

it('keeps a selected country listed while the search hides the others', () => {
  render(<CountryPicker legend="Source country" selected={['GR']} onChange={() => {}} />)
  fireEvent.change(screen.getByLabelText('Find a source country'), { target: { value: 'fran' } })
  expect(screen.getByRole('group', { name: 'Source country (1)' })).toBeTruthy()
  expect(screen.getByRole('checkbox', { name: 'Greece (GR)' })).toBeChecked()
  expect(screen.getByRole('checkbox', { name: 'France (FR)' })).not.toBeChecked()
})

it('keeps a code no country has, so a value from the URL is never dropped', () => {
  render(<CountryPicker legend="Source country" selected={['ZZ']} onChange={() => {}} />)
  expect(screen.getByRole('checkbox', { name: 'ZZ' })).toBeChecked()
})

it('picks languages the same way', () => {
  const onChange = vi.fn()
  render(<LanguagePicker legend="Detected language" selected={['en']} onChange={onChange} />)
  fireEvent.change(screen.getByLabelText('Find a detected language'), { target: { value: 'greek' } })
  fireEvent.click(screen.getByRole('checkbox', { name: 'Greek (el)' }))
  expect(onChange).toHaveBeenLastCalledWith(['en', 'el'])
})

it('chooses one country or language from a list of names', () => {
  const onCountry = vi.fn()
  const onLanguage = vi.fn()
  render(<>
    <CountrySelect label="Country" value="" emptyLabel="Any country" onChange={onCountry} />
    <LanguageSelect label="Expected language" value="el" emptyLabel="Not set" onChange={onLanguage} />
  </>)
  const country = screen.getByLabelText('Country') as HTMLSelectElement
  expect(country.value).toBe('')
  expect(screen.getByRole('option', { name: 'Any country' })).toBeTruthy()
  fireEvent.change(country, { target: { value: 'GR' } })
  expect(onCountry).toHaveBeenLastCalledWith('GR')
  expect((screen.getByLabelText('Expected language') as HTMLSelectElement).value).toBe('el')
  expect(screen.getByRole('option', { name: 'Greek (el)' })).toBeTruthy()
})

it('keeps a stored value the list does not have', () => {
  render(<CountrySelect label="Country" value="XK" emptyLabel="Any country" onChange={() => {}} />)
  expect((screen.getByLabelText('Country') as HTMLSelectElement).value).toBe('XK')
})
