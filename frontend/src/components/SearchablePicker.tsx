'use client'

import { useId, useState } from 'react'
import { cn } from '../lib/utils'
import { fieldClass, labelClass } from '../lib/ui-classes'
import { COUNTRY_OPTIONS, LANGUAGE_OPTIONS, countryLabel, languageLabel, matchesOption, type NamedOption } from '../lib/countries'
import { CheckboxPicker } from './CheckboxPicker'

interface PickerProps {
  legend: string
  selected: string[]
  onChange: (values: string[]) => void
  className?: string
}

/** A CheckboxPicker over a fixed list of names with a search box that matches English and Greek names. */
function SearchablePicker({ legend, selected, onChange, className, options, label, emptyText }: PickerProps & {
  options: NamedOption[]
  label: (code: string) => string
  emptyText: string
}) {
  const [term, setTerm] = useState('')
  const shown = options.filter(option => matchesOption(option, term))
  const labels = new Map(selected.map(code => [code, label(code)]))
  return (
    <div className={cn('flex min-w-0 flex-col gap-1.5', className)}>
      <input className={fieldClass} aria-label={`Find a ${legend.toLowerCase()}`} value={term} onChange={event => setTerm(event.target.value)} placeholder={`Find a ${legend.toLowerCase()}`} />
      <CheckboxPicker legend={legend} options={shown} selected={selected} labels={labels} onChange={onChange} emptyText={emptyText} />
    </div>
  )
}

export function CountryPicker(props: PickerProps) {
  return <SearchablePicker {...props} options={COUNTRY_OPTIONS} label={countryLabel} emptyText="No countries match." />
}

export function LanguagePicker(props: PickerProps) {
  return <SearchablePicker {...props} options={LANGUAGE_OPTIONS} label={languageLabel} emptyText="No languages match." />
}

interface SelectProps {
  label: string
  value: string
  emptyLabel: string
  onChange: (value: string) => void
  className?: string
  required?: boolean
}

/** One value from a fixed list of names; a stored value the list lacks stays selectable. */
function NamedSelect({ label, value, emptyLabel, onChange, className, required, options, labelOf }: SelectProps & {
  options: NamedOption[]
  labelOf: (code: string) => string
}) {
  const id = useId()
  const missing = value !== '' && !options.some(option => option.value === value)
  // Labelled by id, not by wrapping: a wrapping label's text would include every option name.
  return (
    <div className={cn(labelClass, className)}>
      <label htmlFor={id}>{label}</label>
      <select id={id} className={fieldClass} value={value} required={required} onChange={event => onChange(event.target.value)}>
        <option value="">{emptyLabel}</option>
        {missing && <option value={value}>{labelOf(value)}</option>}
        {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </div>
  )
}

export function CountrySelect(props: SelectProps) {
  return <NamedSelect {...props} options={COUNTRY_OPTIONS} labelOf={countryLabel} />
}

export function LanguageSelect(props: SelectProps) {
  return <NamedSelect {...props} options={LANGUAGE_OPTIONS} labelOf={languageLabel} />
}
