import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../lib/api'
import type { NotificationSettings, OpsWikidata } from '../../lib/api-types'
import { setTimeZoneChoice, TIME_ZONE_KEY } from '../../lib/datetime'
import { resetNavigationHarness } from '../../test/navigation-harness'
import { renderWithQuery } from '../../test/render'
import SettingsPage from './page'

vi.mock('../../lib/api', async importOriginal => ({ ...(await importOriginal<typeof import('../../lib/api')>()), api: { stopWords: vi.fn(), updateStopWords: vi.fn(), nlpStatus: vi.fn(), greekEntities: vi.fn(), updateGreekEntities: vi.fn(), notificationSettings: vi.fn(), updateNotificationSettings: vi.fn(), testNotification: vi.fn(), changePassword: vi.fn(), otherSessions: vi.fn(), signOutOtherSessions: vi.fn(), opsWikidata: vi.fn() } }))

beforeEach(() => {
  vi.clearAllMocks()
  resetNavigationHarness()
  vi.mocked(api.stopWords).mockResolvedValue({ language: 'en', revision: 4, words: ['and', 'the'], configuration_fingerprint: 'fingerprint' })
  vi.mocked(api.nlpStatus).mockResolvedValue({ queued: 0, running: 0, retrying: 0, failed: 0, reprocessing: [], capabilities: [{ name: 'entities', state: 'available', version: '3.8.0', detail: null }] })
  vi.mocked(api.greekEntities).mockResolvedValue({ enabled: false, available: true, detail: null, greek_article_count: 12, reprocessing_run_id: null, queued_article_count: 0 })
  vi.mocked(api.notificationSettings).mockResolvedValue(notifications())
  vi.mocked(api.otherSessions).mockResolvedValue({ others: 0 })
  vi.mocked(api.opsWikidata).mockResolvedValue(wikidata())
})

const wikidata = (overrides: Partial<OpsWikidata> = {}): OpsWikidata => ({
  enabled: true, reason: null, links: 42, open_candidates: 3, due_refresh: 0, runs: [], last_refresh: null, counts: [],
  throttle: { requests_today: 0, daily_budget: 2000, paused_until: null, pause_reason: null } as never, ...overrides,
})

const notifications = (overrides: Partial<NotificationSettings> = {}): NotificationSettings => ({
  enabled: false, server: null, topic: null, has_token: false, public_url: null, timezone: 'UTC', quiet_start: null, quiet_end: null,
  digest_enabled: false, digest_hour: 8, last_sent_at: null, last_error: null, last_error_at: null, ...overrides,
})
afterEach(cleanup)

it('updates stop words with revision protection and explains archive reprocessing', async () => {
  vi.mocked(api.updateStopWords).mockResolvedValue({ language: 'en', revision: 5, words: ['and', 'policy'], configuration_fingerprint: 'new' })
  renderWithQuery(() => <SettingsPage />)
  expect(await screen.findByText(/revision 4/)).toBeTruthy()
  expect(screen.getByText(/Existing annotations require reprocessing/)).toBeTruthy()
  fireEvent.change(screen.getByLabelText('English stop words'), { target: { value: 'and\npolicy' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save stop words' }))
  expect(api.updateStopWords).toHaveBeenCalledWith(4, ['and', 'policy'], 'en')
  expect(await screen.findByText('Stop words saved as revision 5.')).toBeTruthy()
})

it('turns Greek entities on and queues the Greek archive', async () => {
  vi.mocked(api.updateGreekEntities).mockResolvedValue({ enabled: true, available: true, detail: null, greek_article_count: 12, reprocessing_run_id: '00000000-0000-0000-0000-000000000001', queued_article_count: 12 })
  renderWithQuery(() => <SettingsPage />)
  const toggle = await screen.findByRole('switch')
  expect(screen.getByLabelText('Also process the 12 Greek articles already in the archive')).toBeTruthy()
  await fireEvent.click(toggle)
  expect(api.updateGreekEntities).toHaveBeenCalledWith(true, true)
  expect(await screen.findByText('Greek entities are on. 12 Greek articles will be processed in the background.')).toBeTruthy()
  expect((screen.getByRole('switch') as HTMLInputElement).checked).toBe(true)
})

it('keeps the Greek switch off and explains why when the model is missing', async () => {
  vi.mocked(api.greekEntities).mockResolvedValue({ enabled: false, available: false, detail: 'Entity recognition is off: start the app with docker/compose.ner.yaml', greek_article_count: 3, reprocessing_run_id: null, queued_article_count: 0 })
  renderWithQuery(() => <SettingsPage />)
  expect(((await screen.findByRole('switch')) as HTMLInputElement).disabled).toBe(true)
  expect(screen.getByText('Not available: Entity recognition is off: start the app with docker/compose.ner.yaml')).toBeTruthy()
})

it('edits the Greek stop words on their own tab, keeping the final sigma', async () => {
  vi.mocked(api.stopWords).mockImplementation(async (language = 'en') => language === 'el'
    ? { language: 'el', revision: 2, words: ['και', 'τους'], configuration_fingerprint: 'el' }
    : { language: 'en', revision: 4, words: ['and', 'the'], configuration_fingerprint: 'en' })
  vi.mocked(api.updateStopWords).mockResolvedValue({ language: 'el', revision: 3, words: ['και', 'τους', 'όμως'], configuration_fingerprint: 'el2' })
  renderWithQuery(() => <SettingsPage />)
  await screen.findByLabelText('English stop words')
  await fireEvent.click(screen.getByRole('tab', { name: 'Greek' }))
  expect(api.stopWords).toHaveBeenCalledWith('el')
  const greek = await screen.findByLabelText('Greek stop words')
  expect((greek as HTMLTextAreaElement).value).toBe('και\nτους')
  expect(screen.getByText(/revision 2/)).toBeTruthy()
  fireEvent.change(greek, { target: { value: 'και\nΤΟΥΣ\nΌμως' } })
  await fireEvent.click(screen.getByRole('button', { name: 'Save stop words' }))
  expect(api.updateStopWords).toHaveBeenCalledWith(2, ['και', 'τους', 'όμως'], 'el')
  expect(await screen.findByText('Stop words saved as revision 3.')).toBeTruthy()
})

it('sets up ntfy notifications with quiet hours and a daily summary, never showing the token', async () => {
  vi.mocked(api.updateNotificationSettings).mockImplementation(async payload => notifications({ ...payload, has_token: true }))
  renderWithQuery(() => <SettingsPage />)
  const panel = await screen.findByRole('region', { name: 'Notifications' })
  expect(within(panel).getByRole('link', { name: 'ntfy' })).toHaveAttribute('href', 'https://ntfy.sh')

  fireEvent.click(await within(panel).findByLabelText('Send notifications'))
  fireEvent.change(within(panel).getByLabelText('ntfy server'), { target: { value: 'https://ntfy.sh' } })
  fireEvent.change(within(panel).getByLabelText('Topic'), { target: { value: 'news-intel' } })
  fireEvent.change(within(panel).getByLabelText('Access token'), { target: { value: 'tk_secret' } })
  fireEvent.change(within(panel).getByLabelText('App address'), { target: { value: 'https://news.example' } })
  fireEvent.change(within(panel).getByLabelText('Time zone'), { target: { value: 'Europe/Athens' } })
  fireEvent.click(within(panel).getByLabelText('Quiet hours'))
  fireEvent.change(within(panel).getByLabelText('From'), { target: { value: '23:00' } })
  fireEvent.change(within(panel).getByLabelText('Until'), { target: { value: '07:30' } })
  fireEvent.click(within(panel).getByLabelText('Daily summary'))
  fireEvent.change(within(panel).getByLabelText('Summary hour'), { target: { value: '9' } })
  await fireEvent.click(within(panel).getByRole('button', { name: 'Save notifications' }))

  await vi.waitFor(() => expect(api.updateNotificationSettings).toHaveBeenCalledWith({
    enabled: true, server: 'https://ntfy.sh', topic: 'news-intel', token: 'tk_secret', public_url: 'https://news.example', timezone: 'Europe/Athens',
    quiet_start: '23:00', quiet_end: '07:30', digest_enabled: true, digest_hour: 9,
  }))
  expect(await within(panel).findByText('Notifications saved.')).toBeTruthy()
  // The token is write-only: the field empties and says one is kept.
  expect(within(panel).getByLabelText('Access token')).toHaveValue('')
  expect(within(panel).getByLabelText('Access token')).toHaveAttribute('placeholder', 'A token is saved')
})

it('keeps a saved token unless removed, and leaves out quiet hours that are off', async () => {
  const saved = notifications({ enabled: true, server: 'https://ntfy.sh', topic: 'news', has_token: true })
  vi.mocked(api.notificationSettings).mockResolvedValue(saved)
  vi.mocked(api.updateNotificationSettings).mockResolvedValueOnce(saved).mockResolvedValue({ ...saved, has_token: false })
  renderWithQuery(() => <SettingsPage />)
  const panel = await screen.findByRole('region', { name: 'Notifications' })
  await within(panel).findByDisplayValue('news')

  await fireEvent.click(within(panel).getByRole('button', { name: 'Save notifications' }))
  await vi.waitFor(() => expect(api.updateNotificationSettings).toHaveBeenLastCalledWith(expect.not.objectContaining({ token: expect.anything() })))
  expect(vi.mocked(api.updateNotificationSettings).mock.lastCall![0]).toMatchObject({ quiet_start: null, quiet_end: null })

  await fireEvent.click(within(panel).getByRole('button', { name: 'Remove token' }))
  await vi.waitFor(() => expect(api.updateNotificationSettings).toHaveBeenLastCalledWith(expect.objectContaining({ token: '' })))
  await vi.waitFor(() => expect(within(panel).queryByRole('button', { name: 'Remove token' })).toBeNull())
})

it('sends a test message and shows the last delivery and the last error', async () => {
  vi.mocked(api.notificationSettings).mockResolvedValue(notifications({
    enabled: true, server: 'https://ntfy.sh', topic: 'news', last_sent_at: '2026-10-12T09:00:00Z',
    last_error: 'ntfy answered 403: forbidden', last_error_at: '2026-10-12T10:00:00Z',
  }))
  vi.mocked(api.testNotification).mockRejectedValueOnce(new ApiError('could not reach https://ntfy.sh', 502)).mockResolvedValue(undefined)
  renderWithQuery(() => <SettingsPage />)
  const panel = await screen.findByRole('region', { name: 'Notifications' })

  expect(await within(panel).findByText(/Last sent/)).toBeTruthy()
  expect(within(panel).getByText(/Last error: ntfy answered 403: forbidden/)).toBeTruthy()
  await fireEvent.click(within(panel).getByRole('button', { name: 'Send test message' }))
  expect(await within(panel).findByRole('alert')).toHaveTextContent('could not reach https://ntfy.sh')
  await fireEvent.click(within(panel).getByRole('button', { name: 'Send test message' }))
  expect(await within(panel).findByText('Test message sent. Check your device.')).toBeTruthy()
})

it('switches between the system, light and dark themes under Appearance', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  renderWithQuery(() => <SettingsPage />)
  const appearance = screen.getByRole('region', { name: 'Appearance' })
  const theme = within(appearance).getByRole('radiogroup', { name: 'Theme' })
  expect(within(theme).getByRole('radio', { name: 'Same as the system' })).toBeChecked()

  fireEvent.click(within(theme).getByRole('radio', { name: 'Dark' }))
  expect(document.documentElement).toHaveClass('dark')
  expect(within(theme).getByRole('radio', { name: 'Dark' })).toBeChecked()

  fireEvent.click(within(theme).getByRole('radio', { name: 'Light' }))
  expect(document.documentElement).not.toHaveClass('dark')
  fireEvent.click(within(theme).getByRole('radio', { name: 'Same as the system' }))
  vi.unstubAllGlobals()
})

it('leaves processor capabilities to the Processes page', async () => {
  renderWithQuery(() => <SettingsPage />)
  await screen.findByText(/revision 4/)
  expect(screen.queryByText('Processor capabilities')).toBeNull()
  expect(screen.queryByText(/entities · available/)).toBeNull()
})

it('chooses the time zone dates are shown in, for this browser', async () => {
  renderWithQuery(() => <SettingsPage />)
  const zone = within(screen.getByRole('region', { name: 'Appearance' })).getByLabelText('Time zone for dates')
  expect(zone).toHaveValue('browser')

  fireEvent.change(zone, { target: { value: 'UTC' } })

  expect(zone).toHaveValue('UTC')
  expect(localStorage.getItem(TIME_ZONE_KEY)).toBe('UTC')
  setTimeZoneChoice('browser')
})

it('changes the password once the two new ones match', async () => {
  vi.mocked(api.changePassword).mockRejectedValueOnce(new ApiError('The current password is not right.', 400)).mockResolvedValueOnce(undefined)
  renderWithQuery(() => <SettingsPage />)
  const account = screen.getByRole('region', { name: 'Account' })
  const fill = (current: string, next: string, again: string) => {
    fireEvent.change(within(account).getByLabelText('Current password'), { target: { value: current } })
    fireEvent.change(within(account).getByLabelText('New password'), { target: { value: next } })
    fireEvent.change(within(account).getByLabelText('New password again'), { target: { value: again } })
    fireEvent.click(within(account).getByRole('button', { name: 'Change password' }))
  }

  fill('old-password-1', 'new-password-2', 'new-password-3')
  expect(await within(account).findByRole('alert')).toHaveTextContent('The new passwords do not match.')
  expect(api.changePassword).not.toHaveBeenCalled()

  fill('wrong-password', 'new-password-2', 'new-password-2')
  expect(await within(account).findByText('The current password is not right.')).toBeTruthy()

  fill('old-password-1', 'new-password-2', 'new-password-2')
  expect(await within(account).findByText('Password changed. Every other browser was signed out.')).toBeTruthy()
  expect(api.changePassword).toHaveBeenLastCalledWith('old-password-1', 'new-password-2')
  expect(within(account).getByLabelText('Current password')).toHaveValue('')
})

it('signs out every other browser and says how many', async () => {
  vi.mocked(api.otherSessions).mockResolvedValueOnce({ others: 2 }).mockResolvedValue({ others: 0 })
  vi.mocked(api.signOutOtherSessions).mockResolvedValue({ signed_out: 2 })
  renderWithQuery(() => <SettingsPage />)
  const account = screen.getByRole('region', { name: 'Account' })

  expect(await within(account).findByText('Signed in on 2 other browsers.')).toBeTruthy()
  fireEvent.click(within(account).getByRole('button', { name: 'Sign out other browsers' }))

  expect(await within(account).findByText('Signed out 2 browsers.')).toBeTruthy()
  expect(await within(account).findByText('No other browser is signed in.')).toBeTruthy()
  expect(within(account).getByRole('button', { name: 'Sign out other browsers' })).toBeDisabled()
})

it('shows whether Wikidata is on and how to change it', async () => {
  vi.mocked(api.opsWikidata).mockResolvedValue(wikidata({ enabled: false, reason: 'Set NEWSINTEL_WIKIDATA_CONTACT to an email or URL' }))
  renderWithQuery(() => <SettingsPage />)
  const panel = screen.getByRole('region', { name: 'Wikidata' })

  expect(await within(panel).findByText('Off: Set NEWSINTEL_WIKIDATA_CONTACT to an email or URL')).toBeTruthy()
  expect(within(panel).getByText(/42 entities linked/)).toBeTruthy()
  expect(within(panel).getByText(/NEWSINTEL_WIKIDATA_ENABLED=false/)).toBeTruthy()
  expect(within(panel).getByRole('link', { name: 'Open Processes' })).toHaveAttribute('href', '/processes/')
})
